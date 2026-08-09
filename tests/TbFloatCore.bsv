package TbFloatCore;

import FloatingPoint::*;
import Vector::*;

import Types::*;
import ExecuteCmd::*;
import IM2PCore::*;


typedef enum {
    TbBeginWeights,
    TbLoadWeight0,
    TbInitAccumulator,
    TbStart,
    TbFeed,
    TbCheck
} TbState deriving (Bits, Eq, FShow);

module mkTbFloatCore(Empty);
    IM2PCoreIfc#(
        1,
        1,
        1,
        2,
        Half,
        Half,
        Half,
        Half,
        Bit#(1)
    ) core <- mkIM2PCore;

    Reg#(TbState) state <- mkReg(TbBeginWeights);
    Reg#(UInt#(9)) watchdog <- mkReg(0);

    rule watch;
        watchdog <= watchdog + 1;
        if (watchdog == 400) begin
            $display("FLOAT CORE: FAIL (timeout)");
            $finish(1);
        end
    endrule

    rule beginWeights (state == TbBeginWeights);
        core.beginWeightLoad;
        state <= TbLoadWeight0;
    endrule

    rule loadWeight0 (state == TbLoadWeight0);
        core.loadWeightRow(0, replicate(fromInteger(3)));
        state <= TbInitAccumulator;
    endrule

    rule initAccumulator (state == TbInitAccumulator && core.idle);
        core.writeAccumulatorRow(0, replicate(fromInteger(4)));
        state <= TbStart;
    endrule

    rule startExecution (state == TbStart && core.weightsReady && core.idle);
        core.startExecution(ExecuteCmd {
            accumulatorBaseRow: 0,
            rowCount: 1,
            accumulate: True,
            vectorOp: VectorBypass
        });
        state <= TbFeed;
    endrule

    rule feedActivation (state == TbFeed && core.activationReady);
        core.putActivationRow(replicate(fromInteger(2)), tagged Invalid);
        state <= TbCheck;
    endrule

    rule checkResult (state == TbCheck && core.executionDone);
        Vector#(1, Half) row0 = core.readAccumulatorRow(0);
        Bool passed = row0[0] == fromInteger(10);

        if (!passed) begin
            $display("FLOAT CORE: FAIL");
            $finish(1);
        end
        else begin
            $display("FLOAT CORE: PASS");
            $finish(0);
        end
    endrule
endmodule

endpackage
