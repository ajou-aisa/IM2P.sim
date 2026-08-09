package TbIM2PCoreGrouped;

import Vector::*;

import TestVectorUtils::*;

import Types::*;
import ExecuteCmd::*;
import IM2PCore::*;


function Vector#(4, Int#(8)) identityWeightRow(UInt#(2) row);
    Vector#(4, Int#(8)) weights = replicate(0);

    for (Integer column = 0; column < 4; column = column + 1) begin
        if (row == fromInteger(column)) begin
            weights[column] = 1;
        end
    end

    return weights;
endfunction

// arrayDim=4, vectorLanes=2에서 한 array result가 두 vector group으로 처리될 때
// row routing, base address, scale alignment, completion tracking을 함께 검증한다.
typedef enum {
    TbBeginWeights,
    TbLoadWeight0,
    TbLoadWeight1,
    TbLoadWeight2,
    TbLoadWeight3,
    TbStart,
    TbFeed0,
    TbFeed1,
    TbWait,
    TbCheck
} TbState deriving (Bits, Eq, FShow);

module mkTbIM2PCoreGrouped(Empty);
    IM2PCoreIfc#(
        4,
        1,
        2,
        8,
        Int#(8),
        Int#(8),
        Int#(16),
        Int#(32),
        Int#(8)
    ) core <- mkIM2PCore;

    Reg#(TbState) state <- mkReg(TbBeginWeights);
    Reg#(Vector#(4, Int#(32))) observedRow2 <- mkRegU;
    Reg#(UInt#(10)) watchdog <- mkReg(0);

    rule watch;
        watchdog <= watchdog + 1;

        if (watchdog == 800) begin
            $display("IM2P GROUPED CORE: FAIL (timeout)");
            $finish(1);
        end
    endrule

    rule beginWeights (state == TbBeginWeights);
        core.beginWeightLoad;
        state <= TbLoadWeight0;
    endrule

    rule loadWeight0 (state == TbLoadWeight0);
        core.loadWeightRow(0, identityWeightRow(0));
        state <= TbLoadWeight1;
    endrule

    rule loadWeight1 (state == TbLoadWeight1);
        core.loadWeightRow(1, identityWeightRow(1));
        state <= TbLoadWeight2;
    endrule

    rule loadWeight2 (state == TbLoadWeight2);
        core.loadWeightRow(2, identityWeightRow(2));
        state <= TbLoadWeight3;
    endrule

    rule loadWeight3 (state == TbLoadWeight3);
        core.loadWeightRow(3, identityWeightRow(3));
        state <= TbStart;
    endrule

    rule startExecution (state == TbStart && core.weightsReady && core.idle);
        core.startExecution(ExecuteCmd {
            accumulatorBaseRow: 2,
            rowCount: 2,
            accumulate: False,
            vectorOp: VectorMultiply
        });
        state <= TbFeed0;
    endrule

    rule feed0 (state == TbFeed0 && core.activationReady);
        core.putActivationRow(
            vector4(1, 2, 3, 4),
            tagged Valid vector4(2, 3, 4, 5)
        );
        state <= TbFeed1;
    endrule

    rule feed1 (state == TbFeed1 && core.activationReady);
        core.putActivationRow(
            vector4(5, 6, 7, 8),
            tagged Valid vector4(-1, 1, 2, -2)
        );
        state <= TbWait;
    endrule

    rule readFirstResultRow (state == TbWait && core.executionDone);
        observedRow2 <= core.readAccumulatorRow(2);
        state <= TbCheck;
    endrule

    rule checkResult (state == TbCheck);
        Vector#(4, Int#(32)) row3 = core.readAccumulatorRow(3);

        Bool passed = observedRow2 == vector4(2, 6, 12, 20)
            && row3 == vector4(-5, 6, 14, -16);

        if (!passed) begin
            $display(
                "IM2P GROUPED CORE: FAIL row2=(%0d,%0d,%0d,%0d) row3=(%0d,%0d,%0d,%0d)",
                observedRow2[0], observedRow2[1],
                observedRow2[2], observedRow2[3],
                row3[0], row3[1], row3[2], row3[3]
            );
            $finish(1);
        end
        else begin
            $display("IM2P GROUPED CORE: PASS");
            $finish(0);
        end
    endrule

endmodule

endpackage
