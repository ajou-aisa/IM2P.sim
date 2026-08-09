package TbSystolicArray;

import Vector::*;

import TestVectorUtils::*;

import Types::*;
import InputSkew::*;
import SystolicArray::*;


// A=[[5,6],[7,8]], B=[[1,2],[3,4]]이면 C=[[23,34],[31,46]]이다.
// Column마다 result arrival가 다르므로 column별 수신 순서로 output row를 복원한다.
typedef enum {
    TbBeginWeightLoad,
    TbLoadWeight0,
    TbLoadWeight1,
    TbClearPipeline,
    TbRun,
    TbCheck
} TbState deriving (Bits, Eq, FShow);

module mkTbSystolicArray(Empty);
    InputSkewIfc#(2, 1, Int#(8), Int#(32)) skew <- mkInputSkew;
    SystolicArrayIfc#(
        2,
        1,
        Int#(8),
        Int#(8),
        Int#(16),
        Int#(32)
    ) array <- mkSystolicArray;

    Reg#(TbState) state <- mkReg(TbBeginWeightLoad);
    Reg#(BoundedCount#(2)) fedRows <- mkReg(0);
    Vector#(2, Reg#(BoundedCount#(2))) receivedRows <-
        replicateM(mkReg(0));
    Vector#(2, Reg#(Int#(32))) firstRow <- replicateM(mkReg(0));
    Vector#(2, Reg#(Int#(32))) secondRow <- replicateM(mkReg(0));
    Reg#(UInt#(8)) watchdog <- mkReg(0);

    rule watch;
        watchdog <= watchdog + 1;
        if (watchdog == 200) begin
            $display("SYSTOLIC ARRAY: FAIL (timeout)");
            $finish(1);
        end
    endrule

    rule beginWeightLoad (state == TbBeginWeightLoad);
        array.beginWeightLoad;
        state <= TbLoadWeight0;
    endrule

    rule loadWeight0 (state == TbLoadWeight0);
        array.loadWeightRow(0, vector2(1, 2));
        state <= TbLoadWeight1;
    endrule

    rule loadWeight1 (state == TbLoadWeight1);
        array.loadWeightRow(1, vector2(3, 4));
        state <= TbClearPipeline;
    endrule

    rule clearPipeline (state == TbClearPipeline && array.weightsReady);
        skew.clear;
        array.clearPipeline;
        fedRows <= 0;
        state <= TbRun;
    endrule

    rule runArray (state == TbRun);
        Vector#(2, Maybe#(Int#(32))) outputs = array.partialSums;
        Bool completeAfterThisCycle = True;

        for (Integer column = 0; column < 2; column = column + 1) begin
            BoundedCount#(2) nextCount = receivedRows[column];

            if (isValid(outputs[column])) begin
                Int#(32) value = fromMaybe(0, outputs[column]);

                if (receivedRows[column] == 0) begin
                    firstRow[column] <= value;
                end
                else if (receivedRows[column] == 1) begin
                    secondRow[column] <= value;
                end

                nextCount = receivedRows[column] + 1;
                receivedRows[column] <= nextCount;
            end

            completeAfterThisCycle =
                completeAfterThisCycle && nextCount == 2;
        end

        Maybe#(Vector#(2, Int#(8))) logicalRow = tagged Invalid;
        if (fedRows == 0) begin
            logicalRow = tagged Valid vector2(5, 6);
            fedRows <= 1;
        end
        else if (fedRows == 1) begin
            logicalRow = tagged Valid vector2(7, 8);
            fedRows <= 2;
        end

        let skewed <- skew.step(logicalRow);
        array.step(skewed.activations, skewed.partials);

        if (completeAfterThisCycle) begin
            state <= TbCheck;
        end
    endrule

    rule checkResult (state == TbCheck);
        Bool passed = firstRow[0] == 23
            && firstRow[1] == 34
            && secondRow[0] == 31
            && secondRow[1] == 46;

        if (!passed) begin
            $display(
                "SYSTOLIC ARRAY: FAIL row0=(%0d,%0d) row1=(%0d,%0d)",
                firstRow[0], firstRow[1], secondRow[0], secondRow[1]
            );
            $finish(1);
        end
        else begin
            $display("SYSTOLIC ARRAY: PASS");
            $finish(0);
        end
    endrule
endmodule

endpackage
