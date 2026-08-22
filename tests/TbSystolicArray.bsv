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
    InputSkewIfc#(2, 1, Int#(8), Int#(64)) skew <- mkInputSkew;
    SystolicArrayIfc#(
        2,
        1,
        Int#(8),
        Int#(8),
        Int#(16),
        Int#(64)
    ) array <- mkSystolicArray;
    SystolicArrayIfc#(
        1,
        1,
        Int#(8),
        Int#(8),
        Int#(16),
        Int#(64)
    ) boundaryArray <- mkSystolicArray;

    Reg#(TbState) state <- mkReg(TbBeginWeightLoad);
    Reg#(BoundedCount#(2)) fedRows <- mkReg(0);
    Vector#(2, Reg#(BoundedCount#(2))) receivedRows <-
        replicateM(mkReg(0));
    Vector#(2, Reg#(Int#(64))) firstRow <- replicateM(mkReg(0));
    Vector#(2, Reg#(Int#(64))) secondRow <- replicateM(mkReg(0));
    Reg#(UInt#(3)) boundaryState <- mkReg(0);
    Reg#(Bool) boundaryDone <- mkReg(False);
    Reg#(UInt#(8)) watchdog <- mkReg(0);

    rule beginBoundaryWeights (boundaryState == 0);
        boundaryArray.beginWeightLoad;
        boundaryState <= 1;
    endrule

    rule loadBoundaryWeight (boundaryState == 1);
        boundaryArray.loadWeightRow(0, replicate(1));
        boundaryState <= 2;
    endrule

    rule clearBoundaryPositive (
        boundaryState == 2 && boundaryArray.weightsReady
    );
        boundaryArray.clearPipeline;
        boundaryState <= 3;
    endrule

    rule driveBoundaryPositive (boundaryState == 3);
        boundaryArray.step(
            replicate(tagged Valid 1),
            replicate(tagged Valid 2147483647)
        );
        boundaryState <= 4;
    endrule

    rule checkBoundaryPositive (boundaryState == 4);
        Maybe#(Int#(64)) value = boundaryArray.partialSums[0];
        Maybe#(Int#(8)) activation = boundaryArray.activationOutputs[0];
        if (!isValid(value)
                || fromMaybe(0, value) != 2147483648
                || !isValid(activation)
                || fromMaybe(0, activation) != 1) begin
            $display(
                "SYSTOLIC ARRAY: FAIL positive boundary=%0d activation=%0d",
                fromMaybe(0, value),
                fromMaybe(0, activation)
            );
            $finish(1);
        end
        boundaryArray.clearPipeline;
        boundaryState <= 5;
    endrule

    rule driveBoundaryNegative (boundaryState == 5);
        boundaryArray.step(
            replicate(tagged Valid (-1)),
            replicate(tagged Valid (-2147483648))
        );
        boundaryState <= 6;
    endrule

    rule checkBoundaryNegative (boundaryState == 6);
        Maybe#(Int#(64)) value = boundaryArray.partialSums[0];
        Maybe#(Int#(8)) activation = boundaryArray.activationOutputs[0];
        if (!isValid(value)
                || fromMaybe(0, value) != -2147483649
                || !isValid(activation)
                || fromMaybe(0, activation) != -1) begin
            $display(
                "SYSTOLIC ARRAY: FAIL negative boundary=%0d activation=%0d",
                fromMaybe(0, value),
                fromMaybe(0, activation)
            );
            $finish(1);
        end
        boundaryDone <= True;
        boundaryState <= 7;
    endrule

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
        Vector#(2, Maybe#(Int#(64))) outputs = array.partialSums;
        Bool completeAfterThisCycle = True;

        for (Integer column = 0; column < 2; column = column + 1) begin
            BoundedCount#(2) nextCount = receivedRows[column];

            if (isValid(outputs[column])) begin
                Int#(64) value = fromMaybe(0, outputs[column]);

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

    rule checkResult (state == TbCheck && boundaryDone);
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
            $display("SYSTOLIC ARRAY: PASS boundaries=(2147483648,-2147483649)");
            $finish(0);
        end
    endrule
endmodule

endpackage
