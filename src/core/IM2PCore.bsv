package IM2PCore;

import Assert::*;
import RegFile::*;
import Vector::*;

import Types::*;
import Arithmetic::*;
import ExecuteCmd::*;
import SystolicEngine::*;
import VectorUnit::*;
import Accumulator::*;
import Scale::*;

// InputSkew -> SystolicArray -> VectorUnit -> Accumulator를 연결하는 단일
// architectural Core다. Bypass/Multiply/Shift는 별도 core가 아니라 runtime
// VectorOp으로 선택하며, block metadata와 scale table도 이 Core의 execution
// control state다.
interface IM2PCoreIfc#(
    numeric type arrayDim,
    numeric type peLatency,
    numeric type vectorLanes,
    numeric type accRows,
    numeric type scaleBlocks,
    type input_t,
    type weight_t,
    type product_t,
    type acc_t,
    type scale_t
);
    // Scaled execution이 사용할 block size와 global K extent를 설정한다.
    // VectorBypass만 실행하는 경우에는 호출하지 않아도 된다.
    method Action configureScaling(
        UInt#(32) blockSize,
        UInt#(32) totalK,
        BoundedCount#(scaleBlocks) blockCount
    );

    // scale[b,:] 한 row를 block 순서대로 적재한다.
    method Action loadScaleBlock(
        Vector#(arrayDim, scale_t) columnScales
    );
    method Bool scaleLoadReady;

    method Action beginWeightLoad;
    method Action loadWeightRow(
        BoundedIndex#(arrayDim) row,
        Vector#(arrayDim, weight_t) weights
    );
    method Bool weightsReady;

    // kStart/kCount는 이번 hardware partial의 global K 위치다. Core가 이 값에서
    // scale block을 선택하며, Bypass에서는 scale 선택이 일어나지 않는다.
    method Action startExecution(
        ExecuteCmd#(arrayDim, accRows) command,
        UInt#(32) kStart,
        BoundedCount#(arrayDim) kCount
    );
    method Bool activationReady;
    method Action putActivationRow(
        Vector#(arrayDim, input_t) activations
    );

    method Bool idle;
    method Bool executionDone;
    method Action acknowledgeExecution;

    // DMA를 모델링하지 않으므로 상위 model이 accumulator를 초기화하고 읽을 수
    // 있는 합성 가능한 boundary를 제공한다.
    method Action writeAccumulatorRow(
        RowAddress#(accRows) row,
        Vector#(arrayDim, acc_t) values
    );
    method Vector#(arrayDim, acc_t) readAccumulatorRow(
        RowAddress#(accRows) row
    );
endinterface

module mkIM2PCore(IM2PCoreIfc#(
    arrayDim,
    peLatency,
    vectorLanes,
    accRows,
    scaleBlocks,
    input_t,
    weight_t,
    product_t,
    acc_t,
    scale_t
)) provisos (
    // arrayDim개의 output column을 vectorLanes개씩 처리한다.
    Mul#(vectorGroups, vectorLanes, arrayDim),
    Add#(1, vectorGroupsMinusOne, vectorGroups),
    Add#(1, vectorLanesMinusOne, vectorLanes),
    Add#(1, arrayDimMinusOne, arrayDim),
    Add#(1, peLatencyMinusOne, peLatency),
    Add#(1, accRowsMinusOne, accRows),
    Add#(1, scaleBlocksMinusOne, scaleBlocks),

    // 한 execution의 최대 arrayDim개 output row가 accumulator에 들어갈 수 있다.
    Add#(arrayDim, freeAccumulatorRows, accRows),

    // Array row index를 Accumulator row address로 zeroExtend할 수 있어야 한다.
    Add#(
        TLog#(arrayDim),
        accumulatorAddressPadding,
        TLog#(accRows)
    ),

    // BoundedCount#(arrayDim)을 BoundedIndex#(arrayDim)으로 truncate할 때 필요한
    // count/index 폭 관계다.
    Add#(
        boundedCountPadding,
        TLog#(arrayDim),
        TLog#(TAdd#(arrayDim, 1))
    ),

    // 적재된 block 수를 scale table index로 truncate하기 위한 폭 관계다.
    Add#(
        scaleCountPadding,
        TLog#(scaleBlocks),
        TLog#(TAdd#(scaleBlocks, 1))
    ),

    // K/block 계산은 32-bit global position 위에서 수행한다.
    Add#(
        TLog#(TAdd#(arrayDim, 1)),
        kCountToPositionPadding,
        32
    ),
    Add#(
        TLog#(TAdd#(scaleBlocks, 1)),
        scaleCountToPositionPadding,
        32
    ),
    Add#(
        TLog#(scaleBlocks),
        scaleIndexToPositionPadding,
        32
    ),

    Bits#(input_t, inputBits),
    Bits#(weight_t, weightBits),
    Bits#(acc_t, accBits),
    Bits#(scale_t, scaleBits),
    Multiplier#(input_t, weight_t, product_t),
    ProductAccumulator#(product_t, acc_t),
    AccumulatorArithmetic#(acc_t),
    VectorScaleCapability#(input_t),
    VectorTransform#(input_t, acc_t, scale_t)
);
    SystolicEngineIfc#(
        arrayDim,
        peLatency,
        input_t,
        weight_t,
        product_t,
        acc_t
    ) engine <- mkSystolicEngine;

    VectorUnitIfc#(
        input_t,
        arrayDim,
        vectorLanes,
        acc_t,
        scale_t
    ) vectorUnit <- mkVectorUnit;

    AccumulatorIfc#(accRows, arrayDim, acc_t) accumulator <-
        mkAccumulator;

    Reg#(ExecuteCmd#(arrayDim, accRows)) commandReg <- mkRegU;

    // VectorUnit이 하나의 array result를 여러 group으로 처리하는 동안에도 각
    // column의 destination row를 유지하는 routing metadata다.
    Reg#(
        Vector#(arrayDim, RowAddress#(accRows))
    ) destinationRowAddressesReg <- mkRegU;

    // scale[b,j] table이다. Architectural memory가 아니라 현재 scaled
    // execution을 위한 control state다.
    RegFile#(
        BoundedIndex#(scaleBlocks),
        Vector#(arrayDim, scale_t)
    ) scaleTable <- mkRegFileFull;

    Reg#(UInt#(32)) blockSizeReg <- mkReg(0);
    Reg#(UInt#(32)) totalKReg <- mkReg(0);
    Reg#(BoundedCount#(scaleBlocks)) blockCountReg <- mkReg(0);
    Reg#(BoundedCount#(scaleBlocks)) loadedBlockCountReg <- mkReg(0);
    Reg#(Bool) configurationValidReg <- mkReg(False);
    Reg#(Bool) configurationConsumedReg <- mkReg(False);

    // 이번 execution이 사용할 scale vector를 drain이 끝날 때까지 고정한다.
    Reg#(Vector#(arrayDim, scale_t)) executionScalesReg <- mkRegU;
    Reg#(Bool) executionUsesScaleReg <- mkReg(False);

    // 지금까지 Core가 받은 activation row 수다.
    Reg#(BoundedCount#(arrayDim)) acceptedInputRowsReg <- mkReg(0);

    // Complete column partial을 이번 execution의 scale과 destination address에
    // 붙여 VectorUnit으로 보낸다.
    rule issueVectorRequest (engine.resultValid && vectorUnit.ready);
        SystolicResult#(arrayDim, acc_t) arrayResult = engine.result;

        Vector#(
            arrayDim,
            RowAddress#(accRows)
        ) destinationRowAddresses = newVector;

        // 같은 execution의 모든 output row는 선택된 K-block의 column scale을
        // 공유한다.
        Vector#(arrayDim, scale_t) selectedScales = executionUsesScaleReg
            ? executionScalesReg
            : replicate(unpack(0));

        for (Integer column = 0;
                column < valueOf(arrayDim);
                column = column + 1) begin
            // rowOffsets는 count 폭이지만 Valid column에서는 항상 0~arrayDim-1이다.
            BoundedIndex#(arrayDim) rowOffset =
                truncate(arrayResult.rowOffsets[column]);
            RowAddress#(accRows) extendedOffset = zeroExtend(rowOffset);

            // Column index는 Accumulator bank를 정적으로 결정하고, 여기서는 bank
            // 내부 row 주소만 계산한다.
            destinationRowAddresses[column] = arrayResult.valids[column]
                ? commandReg.accumulatorBaseRow + extendedOffset
                : commandReg.accumulatorBaseRow;
        end

        destinationRowAddressesReg <= destinationRowAddresses;
        vectorUnit.put(
            arrayResult.valids,
            arrayResult.partialSums,
            selectedScales,
            commandReg.vectorOp
        );
        engine.consumeResult;
    endrule

    // VectorUnit은 contribution만 만든다. 주소 해석과 optional accumulation은
    // Accumulator가 담당한다.
    rule commitVectorResult (vectorUnit.resultValid);
        VectorResult#(arrayDim, acc_t) transformed = vectorUnit.result;

        accumulator.commit(
            transformed.valids,
            destinationRowAddressesReg,
            transformed.contributions,
            commandReg.accumulate
        );

        if (anyTrue(transformed.valids)) begin
            engine.noteCommitted(transformed.valids);
        end

        vectorUnit.consume;
    endrule

    // 새 scale table을 적재하기 전 또는 이전 configuration이 소비된 뒤에만
    // metadata를 갱신한다.
    method Action configureScaling(
        UInt#(32) blockSize,
        UInt#(32) totalK,
        BoundedCount#(scaleBlocks) blockCount
    ) if (
        engine.idle
        && vectorUnit.ready
        && (
            !configurationValidReg
            || loadedBlockCountReg == blockCountReg
            || configurationConsumedReg
        )
    );
        UInt#(32) safeBlockSize = blockSize == 0 ? 1 : blockSize;
        UInt#(32) safeTotalK = totalK == 0 ? 1 : totalK;
        UInt#(32) expectedBlockCount =
            ((safeTotalK - 1) / safeBlockSize) + 1;

        dynamicAssert(blockSize > 0, "scaling block size must be positive");
        dynamicAssert(totalK > 0, "scaling total K must be positive");
        dynamicAssert(blockCount > 0, "scale table must not be empty");
        dynamicAssert(
            zeroExtend(blockCount) == expectedBlockCount,
            "scale block count does not match total K / block size"
        );

        blockSizeReg <= blockSize;
        totalKReg <= totalK;
        blockCountReg <= blockCount;
        loadedBlockCountReg <= 0;
        configurationValidReg <= True;
        configurationConsumedReg <= False;
    endmethod

    method Action loadScaleBlock(
        Vector#(arrayDim, scale_t) columnScales
    ) if (
        engine.idle
        && vectorUnit.ready
        && configurationValidReg
        && !configurationConsumedReg
        && loadedBlockCountReg < blockCountReg
    );
        BoundedIndex#(scaleBlocks) blockIndex =
            truncate(loadedBlockCountReg);
        scaleTable.upd(blockIndex, columnScales);
        loadedBlockCountReg <= loadedBlockCountReg + 1;
    endmethod

    method Bool scaleLoadReady =
        configurationValidReg
        && loadedBlockCountReg == blockCountReg;

    method Action beginWeightLoad if (engine.idle && vectorUnit.ready);
        engine.beginWeightLoad;
    endmethod

    method Action loadWeightRow(
        BoundedIndex#(arrayDim) row,
        Vector#(arrayDim, weight_t) weights
    ) if (engine.idle && vectorUnit.ready);
        engine.loadWeightRow(row, weights);
    endmethod

    method Bool weightsReady = engine.weightsReady;

    method Action startExecution(
        ExecuteCmd#(arrayDim, accRows) command,
        UInt#(32) kStart,
        BoundedCount#(arrayDim) kCount
    ) if (
        engine.idle
        && vectorUnit.ready
        && engine.weightsReady
    );
        // 현재 reference Core는 최대 arrayDim개 row가 들어갈 연속 공간을
        // execution 시작 시 예약한다.
        dynamicAssert(
            command.accumulatorBaseRow
                <= fromInteger(valueOf(freeAccumulatorRows)),
            "accumulator row range exceeds storage"
        );
        dynamicAssert(
            command.vectorOp == VectorBypass
                || vectorUnit.scalingSupported,
            "selected numeric format supports only VectorBypass"
        );
        dynamicAssert(kCount > 0, "execution K count must be positive");

        Bool operationNeedsScale =
            vectorUnit.scalingSupported
            && vectorOpUsesScale(command.vectorOp);

        UInt#(32) safeBlockSize =
            blockSizeReg == 0 ? 1 : blockSizeReg;
        UInt#(32) kCountWide = zeroExtend(kCount);
        UInt#(32) remainingK =
            kStart < totalKReg ? totalKReg - kStart : 0;
        UInt#(32) blockOffset = kStart % safeBlockSize;
        UInt#(32) blockRemaining = safeBlockSize - blockOffset;

        // 현재 hardware partial이 속한 K-block을 선택한다. VectorUnit은 block
        // metadata를 모르며 여기서 선택된 scale만 받는다.
        UInt#(32) selectedBlockWide = kStart / safeBlockSize;

        if (operationNeedsScale) begin
            dynamicAssert(
                configurationValidReg,
                "scaled execution requires scaling metadata"
            );
            dynamicAssert(
                loadedBlockCountReg == blockCountReg,
                "scaled execution requires the complete scale table"
            );
            dynamicAssert(
                kCountWide <= remainingK,
                "execution K range exceeds total K"
            );
            dynamicAssert(
                kCountWide <= blockRemaining,
                "hardware K partial crosses a scale block boundary"
            );
            dynamicAssert(
                selectedBlockWide < zeroExtend(blockCountReg),
                "selected scale block is out of range"
            );

            BoundedIndex#(scaleBlocks) selectedBlock =
                truncate(selectedBlockWide);
            executionScalesReg <= scaleTable.sub(selectedBlock);
        end
        else begin
            // Bypass는 남아 있는 scale table 값이 결과에 영향을 주지 않도록
            // sideband를 중립값으로 되돌린다.
            executionScalesReg <= replicate(unpack(0));
        end

        executionUsesScaleReg <= operationNeedsScale;
        commandReg <= command;
        acceptedInputRowsReg <= 0;
        engine.start(command.rowCount);
    endmethod

    method Bool activationReady = engine.activationReady;

    method Action putActivationRow(
        Vector#(arrayDim, input_t) activations
    ) if (engine.activationReady);
        dynamicAssert(
            acceptedInputRowsReg < commandReg.rowCount,
            "more activation rows supplied than rowCount"
        );

        acceptedInputRowsReg <= acceptedInputRowsReg + 1;
        engine.putActivationRow(activations);
    endmethod

    method Bool idle = engine.idle && vectorUnit.ready;
    method Bool executionDone = engine.done && vectorUnit.ready;

    method Action acknowledgeExecution if (
        engine.done && vectorUnit.ready
    );
        configurationConsumedReg <= True;
        engine.acknowledge;
    endmethod

    // Accumulator 초기화는 execution 사이의 Idle 상태에서만 허용한다.
    method Action writeAccumulatorRow(
        RowAddress#(accRows) row,
        Vector#(arrayDim, acc_t) values
    ) if (engine.idle && vectorUnit.ready);
        accumulator.writeRow(row, values);
    endmethod

    // 완료 결과는 Done 또는 Idle 상태에서 읽을 수 있다.
    method Vector#(arrayDim, acc_t) readAccumulatorRow(
        RowAddress#(accRows) row
    ) if (!engine.active && vectorUnit.ready);
        return accumulator.readRow(row);
    endmethod

endmodule

endpackage
