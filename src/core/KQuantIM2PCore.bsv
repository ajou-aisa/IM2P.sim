package KQuantIM2PCore;

import Assert::*;
import RegFile::*;
import Vector::*;

import Types::*;
import Arithmetic::*;
import ExecuteCmd::*;
import IM2PCore::*;
import Scale::*;

// INT synthesis boundary that preloads K-quant scale[b,j] rows and selects
// one row from execution K metadata. The wrapped datapath remains:
// SystolicEngine -> VectorUnit -> Accumulator.
interface KQuantIM2PCoreIfc#(
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
    method Action configureKQuant(
        UInt#(32) blockSize,
        UInt#(32) totalK,
        BoundedCount#(scaleBlocks) blockCount
    );
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

    method Action writeAccumulatorRow(
        RowAddress#(accRows) row,
        Vector#(arrayDim, acc_t) values
    );
    method Vector#(arrayDim, acc_t) readAccumulatorRow(
        RowAddress#(accRows) row
    );
endinterface

module mkKQuantIM2PCore(KQuantIM2PCoreIfc#(
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
    Mul#(vectorGroups, vectorLanes, arrayDim),
    Add#(1, vectorGroupsMinusOne, vectorGroups),
    Add#(1, vectorLanesMinusOne, vectorLanes),
    Add#(1, arrayDimMinusOne, arrayDim),
    Add#(1, peLatencyMinusOne, peLatency),
    Add#(1, accRowsMinusOne, accRows),
    Add#(1, scaleBlocksMinusOne, scaleBlocks),
    Add#(arrayDim, freeAccumulatorRows, accRows),
    Add#(
        TLog#(arrayDim),
        accumulatorAddressPadding,
        TLog#(accRows)
    ),
    Add#(
        boundedCountPadding,
        TLog#(arrayDim),
        TLog#(TAdd#(arrayDim, 1))
    ),
    Add#(
        scaleCountPadding,
        TLog#(scaleBlocks),
        TLog#(TAdd#(scaleBlocks, 1))
    ),
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
    IM2PCoreIfc#(
        arrayDim,
        peLatency,
        vectorLanes,
        accRows,
        input_t,
        weight_t,
        product_t,
        acc_t,
        scale_t
    ) core <- mkIM2PCore;

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

    Reg#(Vector#(arrayDim, scale_t)) executionScalesReg <- mkRegU;
    Reg#(Bool) executionUsesScaleReg <- mkReg(False);

    method Action configureKQuant(
        UInt#(32) blockSize,
        UInt#(32) totalK,
        BoundedCount#(scaleBlocks) blockCount
    ) if (
        core.idle
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

        dynamicAssert(blockSize > 0, "K-quant block_size must be positive");
        dynamicAssert(totalK > 0, "K-quant total_k must be positive");
        dynamicAssert(blockCount > 0, "K-quant scale table must not be empty");
        dynamicAssert(
            zeroExtend(blockCount) == expectedBlockCount,
            "K-quant scale block count does not match total_k/block_size"
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
        core.idle
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

    method Action beginWeightLoad if (core.idle);
        core.beginWeightLoad;
    endmethod

    method Action loadWeightRow(
        BoundedIndex#(arrayDim) row,
        Vector#(arrayDim, weight_t) weights
    ) if (core.idle);
        core.loadWeightRow(row, weights);
    endmethod

    method Bool weightsReady = core.weightsReady;

    method Action startExecution(
        ExecuteCmd#(arrayDim, accRows) command,
        UInt#(32) kStart,
        BoundedCount#(arrayDim) kCount
    ) if (core.idle && core.weightsReady);
        UInt#(32) safeBlockSize =
            blockSizeReg == 0 ? 1 : blockSizeReg;
        UInt#(32) kCountWide = zeroExtend(kCount);
        UInt#(32) remainingK =
            kStart < totalKReg ? totalKReg - kStart : 0;
        UInt#(32) blockOffset = kStart % safeBlockSize;
        UInt#(32) blockRemaining = safeBlockSize - blockOffset;
        UInt#(32) selectedBlockWide = kStart / safeBlockSize;
        Bool operationNeedsScale = vectorOpUsesScale(command.vectorOp);

        dynamicAssert(
            configurationValidReg,
            "K-quant metadata must be configured before execution"
        );
        dynamicAssert(kCount > 0, "execution K count must be positive");
        dynamicAssert(
            kCountWide <= remainingK,
            "execution K range exceeds total_k"
        );
        dynamicAssert(
            kCountWide <= blockRemaining,
            "hardware K partial crosses a K-quant block boundary"
        );

        if (operationNeedsScale) begin
            dynamicAssert(
                loadedBlockCountReg == blockCountReg,
                "scaled execution requires the complete scale table"
            );
            dynamicAssert(
                selectedBlockWide < zeroExtend(blockCountReg),
                "selected K-quant scale block is out of range"
            );

            BoundedIndex#(scaleBlocks) selectedBlock =
                truncate(selectedBlockWide);
            executionScalesReg <= scaleTable.sub(selectedBlock);
        end
        else begin
            executionScalesReg <= replicate(unpack(0));
        end

        executionUsesScaleReg <= operationNeedsScale;
        core.startExecution(command);
    endmethod

    method Bool activationReady = core.activationReady;

    method Action putActivationRow(
        Vector#(arrayDim, input_t) activations
    ) if (core.activationReady);
        Maybe#(Vector#(arrayDim, scale_t)) scales =
            executionUsesScaleReg
                ? tagged Valid executionScalesReg
                : tagged Invalid;
        core.putActivationRow(activations, scales);
    endmethod

    method Bool idle = core.idle;
    method Bool executionDone = core.executionDone;

    method Action acknowledgeExecution if (core.executionDone);
        configurationConsumedReg <= True;
        core.acknowledgeExecution;
    endmethod

    method Action writeAccumulatorRow(
        RowAddress#(accRows) row,
        Vector#(arrayDim, acc_t) values
    ) if (core.idle);
        core.writeAccumulatorRow(row, values);
    endmethod

    method Vector#(arrayDim, acc_t) readAccumulatorRow(
        RowAddress#(accRows) row
    );
        return core.readAccumulatorRow(row);
    endmethod
endmodule

endpackage
