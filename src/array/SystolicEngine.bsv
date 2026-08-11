package SystolicEngine;

import Assert::*;
import FIFOF::*;
import Vector::*;

import Types::*;
import Arithmetic::*;
import ExecuteController::*;
import InputSkew::*;
import SystolicArray::*;

// 현재 K tile의 bottom-row column 결과와 logical output row offset을 함께 전달한다.
typedef struct {
    Vector#(arrayDim, Bool) valids;
    Vector#(
        arrayDim,
        BoundedCount#(arrayDim)
    ) rowOffsets;
    Vector#(arrayDim, acc_t) partialSums;
} SystolicResult#(
    numeric type arrayDim,
    type acc_t
) deriving (Bits);

// InputSkew, SystolicArray, output-row tracking을 묶은 array subsystem이다.
// Scale transform과 Accumulator state는 알지 못한다.
interface SystolicEngineIfc#(
    numeric type arrayDim,
    numeric type peLatency,
    type input_t,
    type weight_t,
    type product_t,
    type acc_t
);
    method Action beginWeightLoad;
    method Action beginWeightLoadBank(Bool bank);
    method Action loadWeightRow(
        BoundedIndex#(arrayDim) row,
        Vector#(arrayDim, weight_t) weights
    );
    method Action loadWeightRowBank(
        Bool bank,
        BoundedIndex#(arrayDim) row,
        Vector#(arrayDim, weight_t) weights
    );
    method Bool weightsReady;
    method Bool weightsReadyBank(Bool bank);
    method Action activateWeightBank(Bool bank);
    method Bool activeWeightBank;

    method Action start(BoundedCount#(arrayDim) rowCount);
    method Bool activationReady;
    method Action putActivationRow(Vector#(arrayDim, input_t) row);

    method Bool resultValid;
    method SystolicResult#(arrayDim, acc_t) result;
    method Action consumeResult;

    // Accumulator에 실제 commit된 output column을 execution tracker에 전달한다.
    method Action noteCommitted(Vector#(arrayDim, Bool) valids);

    method Bool idle;
    method Bool active;
    method Bool done;
    method BoundedCount#(arrayDim) acceptedRows;
    method BoundedCount#(arrayDim) configuredRows;
    method BoundedCount#(arrayDim) firstColumnIssued;
    method BoundedCount#(arrayDim) firstColumnCommitted;
    method Action acknowledge;
endinterface

module mkSystolicEngine(SystolicEngineIfc#(
    arrayDim,
    peLatency,
    input_t,
    weight_t,
    product_t,
    acc_t
)) provisos (
    Add#(1, arrayDimMinusOne, arrayDim),
    Add#(1, peLatencyMinusOne, peLatency),
    Bits#(input_t, inputBits),
    Bits#(weight_t, weightBits),
    Bits#(acc_t, accBits),
    Multiplier#(input_t, weight_t, product_t),
    ProductAccumulator#(product_t, acc_t),
    AccumulatorArithmetic#(acc_t)
);
    InputSkewIfc#(arrayDim, peLatency, input_t, acc_t) inputSkew <-
        mkInputSkew;

    SystolicArrayIfc#(
        arrayDim,
        peLatency,
        input_t,
        weight_t,
        product_t,
        acc_t
    ) systolicArray <- mkSystolicArray;

    ExecuteControllerIfc#(arrayDim) controller <- mkExecuteController;

    // Explicit notEmpty 분기로 보호하므로 empty cycle에도 drain rule을 실행한다.
    FIFOF#(Vector#(arrayDim, input_t)) activationRows <- mkGFIFOF(False, True);
    FIFOF#(SystolicResult#(arrayDim, acc_t)) results <- mkFIFOF;

    Reg#(BoundedCount#(arrayDim)) acceptedRowsReg <- mkReg(0);
    Reg#(BoundedCount#(arrayDim)) rowCountReg <- mkReg(0);
    Reg#(Bool) activeWeightBankReg <- mkReg(False);

    // result FIFO가 가득 차면 InputSkew와 모든 PE를 함께 정지시켜 wavefront의
    // 상대 timing을 유지한다.
    rule advanceArray (controller.active && results.notFull);
        // step 전에 읽는 값은 이전 cycle에 PE pipeline이 만든 bottom-row output이다.
        Vector#(arrayDim, Maybe#(acc_t)) outputs =
            systolicArray.partialSums;

        Vector#(arrayDim, Bool) valids = newVector;
        Vector#(arrayDim, acc_t) partials = newVector;

        for (Integer column = 0;
                column < valueOf(arrayDim);
                column = column + 1) begin
            valids[column] = isValid(outputs[column]);
            partials[column] = fromMaybe(
                accumulatorZero(),
                outputs[column]
            );
        end

        if (anyTrue(valids)) begin
            results.enq(SystolicResult {
                valids: valids,
                rowOffsets: controller.currentRowOffsets,
                partialSums: partials
            });
            controller.noteArrayOutputs(valids);
        end

        // 입력 FIFO가 비어 있으면 Invalid bubble을 넣어 기존 wavefront를 drain한다.
        Maybe#(Vector#(arrayDim, input_t)) activationRow = tagged Invalid;
        if (activationRows.notEmpty) begin
            activationRow = tagged Valid activationRows.first;
            activationRows.deq;
        end

        let skewed <- inputSkew.step(activationRow);
        systolicArray.step(skewed.activations, skewed.partials);
    endrule

    method Action beginWeightLoad if (controller.idle);
        systolicArray.beginWeightLoad;
    endmethod

    method Action beginWeightLoadBank(Bool bank);
        dynamicAssert(
            bank != systolicArray.activeWeightBank,
            "weight preload must target inactive bank"
        );
        if (bank != activeWeightBankReg)
            systolicArray.beginWeightLoadBank(bank);
    endmethod

    method Action loadWeightRow(
        BoundedIndex#(arrayDim) row,
        Vector#(arrayDim, weight_t) weights
    ) if (controller.idle);
        systolicArray.loadWeightRow(row, weights);
    endmethod

    method Action loadWeightRowBank(
        Bool bank,
        BoundedIndex#(arrayDim) row,
        Vector#(arrayDim, weight_t) weights
    );
        dynamicAssert(
            bank != systolicArray.activeWeightBank,
            "weight preload must target inactive bank"
        );
        if (bank != activeWeightBankReg)
            systolicArray.loadWeightRowBank(bank, row, weights);
    endmethod

    method Bool weightsReady = systolicArray.weightsReady;

    method Bool weightsReadyBank(Bool bank) = systolicArray.weightsReadyBank(bank);

    method Action activateWeightBank(Bool bank) if (controller.idle);
        systolicArray.activateWeightBank(bank);
        activeWeightBankReg <= bank;
    endmethod

    method Bool activeWeightBank = systolicArray.activeWeightBank;

    method Action start(BoundedCount#(arrayDim) rowCount) if (
        controller.idle
        && !activationRows.notEmpty
        && !results.notEmpty
        && systolicArray.weightsReady
    );
        acceptedRowsReg <= 0;
        rowCountReg <= rowCount;
        inputSkew.clear;
        systolicArray.clearPipeline;
        controller.start(rowCount);
    endmethod

    method Bool activationReady = controller.active
        && activationRows.notFull
        && acceptedRowsReg < rowCountReg;

    method Action putActivationRow(Vector#(arrayDim, input_t) row) if (
        controller.active
        && activationRows.notFull
        && acceptedRowsReg < rowCountReg
    );
        activationRows.enq(row);
        acceptedRowsReg <= acceptedRowsReg + 1;
    endmethod

    method Bool resultValid = results.notEmpty;

    method SystolicResult#(arrayDim, acc_t) result if (results.notEmpty);
        return results.first;
    endmethod

    method Action consumeResult if (results.notEmpty);
        results.deq;
    endmethod

    method Action noteCommitted(Vector#(arrayDim, Bool) valids)
            if (controller.active);
        controller.noteCommitted(valids);
    endmethod

    method Bool idle = controller.idle;
    method Bool active = controller.active;
    method Bool done = controller.done;
    method BoundedCount#(arrayDim) acceptedRows = acceptedRowsReg;
    method BoundedCount#(arrayDim) configuredRows = rowCountReg;
    method BoundedCount#(arrayDim) firstColumnIssued =
        controller.firstColumnIssued;
    method BoundedCount#(arrayDim) firstColumnCommitted =
        controller.firstColumnCommitted;

    method Action acknowledge if (controller.done);
        controller.acknowledge;
    endmethod

endmodule

endpackage
