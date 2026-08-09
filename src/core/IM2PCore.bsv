package IM2PCore;

import Assert::*;
import Vector::*;

import Types::*;
import Arithmetic::*;
import ExecuteCmd::*;
import SystolicEngine::*;
import VectorUnit::*;
import Accumulator::*;
import Scale::*;

// SystolicEngine -> VectorUnit -> Accumulator를 연결하는 단일 top-level Core다.
interface IM2PCoreIfc#(
    numeric type arrayDim,
    numeric type peLatency,
    numeric type vectorLanes,
    numeric type accRows,
    type input_t,
    type weight_t,
    type product_t,
    type acc_t,
    type scale_t
);
    method Action beginWeightLoad;
    method Action loadWeightRow(
        BoundedIndex#(arrayDim) row,
        Vector#(arrayDim, weight_t) weights
    );
    method Bool weightsReady;

    method Action startExecution(ExecuteCmd#(arrayDim, accRows) command);
    method Bool activationReady;

    // Multiply/Shift execution은 activation row와 같은 logical output row에
    // 대응하는 scale vector를 Valid로 공급한다. Bypass에서는 Invalid가 가능하다.
    method Action putActivationRow(
        Vector#(arrayDim, input_t) activations,
        Maybe#(Vector#(arrayDim, scale_t)) scales
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

    // Scale은 architectural memory가 아니라 execution 동안 output row와 scale을
    // 정렬하기 위한 sideband state다.
    Vector#(
        arrayDim,
        Reg#(Vector#(arrayDim, scale_t))
    ) scaleSidebandRows <- replicateM(mkRegU);

    // 지금까지 Core가 받은 activation/optional-scale row 수다.
    Reg#(BoundedCount#(arrayDim)) acceptedInputRowsReg <- mkReg(0);

    // Complete column partial을 해당 row의 scale과 destination address에 붙여
    // VectorUnit으로 보낸다.
    rule issueVectorRequest (engine.resultValid && vectorUnit.ready);
        SystolicResult#(arrayDim, acc_t) arrayResult = engine.result;

        Vector#(
            arrayDim,
            RowAddress#(accRows)
        ) destinationRowAddresses = newVector;

        Vector#(arrayDim, scale_t) selectedScales = replicate(unpack(0));

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

            if (arrayResult.valids[column]
                    && vectorUnit.scalingSupported
                    && vectorOpUsesScale(commandReg.vectorOp)) begin
                // scaleSidebandRows는 Vector of Reg interfaces다. Dynamic interface
                // selection에 의존하지 않고 static decode로 해당 row를 선택한다.
                for (Integer sidebandRow = 0;
                        sidebandRow < valueOf(arrayDim);
                        sidebandRow = sidebandRow + 1) begin
                    if (rowOffset == fromInteger(sidebandRow)) begin
                        selectedScales[column] =
                            scaleSidebandRows[sidebandRow][column];
                    end
                end
            end
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

    method Action startExecution(ExecuteCmd#(
        arrayDim,
        accRows
    ) command) if (
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

        commandReg <= command;
        acceptedInputRowsReg <= 0;
        engine.start(command.rowCount);
    endmethod

    method Bool activationReady = engine.activationReady;

    method Action putActivationRow(
        Vector#(arrayDim, input_t) activations,
        Maybe#(Vector#(arrayDim, scale_t)) scales
    ) if (engine.activationReady);
        dynamicAssert(
            acceptedInputRowsReg < commandReg.rowCount,
            "more activation rows supplied than rowCount"
        );

        Bool operationNeedsScale =
            vectorUnit.scalingSupported
            && vectorOpUsesScale(commandReg.vectorOp);

        dynamicAssert(
            !operationNeedsScale || isValid(scales),
            "Multiply/Shift execution requires a scale vector per activation row"
        );

        // acceptedInputRowsReg는 증가 전에는 항상 실제 row index 범위에 있다.
        BoundedIndex#(arrayDim) inputRowIndex =
            truncate(acceptedInputRowsReg);

        if (operationNeedsScale) begin
            Vector#(arrayDim, scale_t) scaleRow = fromMaybe(
                replicate(unpack(0)),
                scales
            );

            // Vector of Reg interfaces는 static index로 write한다. inputRowIndex를
            // one-hot decode해 해당 logical row register만 갱신한다.
            for (Integer sidebandRow = 0;
                    sidebandRow < valueOf(arrayDim);
                    sidebandRow = sidebandRow + 1) begin
                if (inputRowIndex == fromInteger(sidebandRow)) begin
                    scaleSidebandRows[sidebandRow] <= scaleRow;
                end
            end
        end

        acceptedInputRowsReg <= acceptedInputRowsReg + 1;
        engine.putActivationRow(activations);
    endmethod

    method Bool idle = engine.idle && vectorUnit.ready;
    method Bool executionDone = engine.done && vectorUnit.ready;

    method Action acknowledgeExecution if (
        engine.done && vectorUnit.ready
    );
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
