package TbSynthInt8x64;

import Vector::*;

import Types::*;
import ExecuteCmd::*;
import Config::*;
import IM2PCore::*;
import SynthInt8x64::*;

function Vector#(64, Int#(8)) identityWeight(UInt#(6) row);
    Vector#(64, Int#(8)) weights = newVector;

    for (Integer column = 0; column < 64; column = column + 1) begin
        weights[column] = row == fromInteger(column) ? 1 : 0;
    end

    return weights;
endfunction

function Vector#(64, Int#(8)) activationRow(UInt#(1) row);
    Vector#(64, Int#(8)) activations = replicate(0);

    if (row == 0) begin
        activations[0] = 5;
        activations[15] = 6;
        activations[16] = 7;
        activations[31] = 8;
        activations[32] = 9;
        activations[47] = 10;
        activations[48] = 11;
        activations[63] = 12;
    end
    else begin
        activations[0] = -5;
        activations[15] = -6;
        activations[16] = -7;
        activations[31] = -8;
        activations[32] = -9;
        activations[47] = -10;
        activations[48] = -11;
        activations[63] = -12;
    end

    return activations;
endfunction

function Vector#(64, Int#(DefaultAccumulatorWidth)) expectedRow(UInt#(1) row);
    Vector#(64, Int#(DefaultAccumulatorWidth)) expected = replicate(0);

    if (row == 0) begin
        expected[0] = 5;
        expected[15] = 6;
        expected[16] = 7;
        expected[31] = 8;
        expected[32] = 9;
        expected[47] = 10;
        expected[48] = 11;
        expected[63] = 12;
    end
    else begin
        expected[0] = -5;
        expected[15] = -6;
        expected[16] = -7;
        expected[31] = -8;
        expected[32] = -9;
        expected[47] = -10;
        expected[48] = -11;
        expected[63] = -12;
    end

    return expected;
endfunction

typedef enum {
    BeginWeights,
    LoadWeights,
    Configure,
    Start,
    FeedRow0,
    FeedRow1,
    Wait,
    CheckRow0
} TbState deriving (Bits, Eq, FShow);

module mkTbSynthInt8x64(Empty);
    IM2PCoreIfc#(
        64,
        1,
        64,
        DefaultAccumulatorRows,
        Int#(8),
        Int#(8),
        Int#(16),
        Int#(DefaultAccumulatorWidth),
        Int#(8)
    ) dut <- mkSynthInt8x64;

    Reg#(TbState) state <- mkReg(BeginWeights);
    Reg#(UInt#(6)) weightRow <- mkReg(0);
    Reg#(Vector#(64, Int#(DefaultAccumulatorWidth))) observedRow0 <- mkRegU;
    Reg#(UInt#(14)) watchdog <- mkReg(0);

    rule watch;
        watchdog <= watchdog + 1;
        if (watchdog == 8000) begin
            $display("SYNTH INT8x64: FAIL (timeout)");
            $finish(1);
        end
    endrule

    rule beginWeights (state == BeginWeights);
        dut.beginWeightLoad;
        state <= LoadWeights;
    endrule

    rule loadWeights (state == LoadWeights);
        dut.loadWeightRow(weightRow, identityWeight(weightRow));
        if (weightRow == 63) begin
            state <= Configure;
        end
        else begin
            weightRow <= weightRow + 1;
        end
    endrule

    rule advanceToStart (state == Configure && dut.weightsReady && dut.idle);
        state <= Start;
    endrule

    rule startExecution (state == Start && dut.weightsReady && dut.idle);
        dut.startExecution(ExecuteCmd {
            accumulatorBaseRow: 0,
            rowCount: 2,
            accumulate: False,
            vectorOp: VectorBypass
        }, 0, 64);
        state <= FeedRow0;
    endrule

    rule feedRow0 (state == FeedRow0 && dut.activationReady);
        dut.putActivationRow(activationRow(0));
        state <= FeedRow1;
    endrule

    rule feedRow1 (state == FeedRow1 && dut.activationReady);
        dut.putActivationRow(activationRow(1));
        state <= Wait;
    endrule

    rule waitExecution (state == Wait && dut.executionDone);
        observedRow0 <= dut.readAccumulatorRow(0);
        state <= CheckRow0;
    endrule

    rule checkResults (state == CheckRow0);
        Vector#(64, Int#(DefaultAccumulatorWidth)) observedRow1 =
            dut.readAccumulatorRow(1);
        Bool passed = observedRow0 == expectedRow(0)
            && observedRow1 == expectedRow(1);

        if (!passed) begin
            $display("SYNTH INT8x64: FAIL (unexpected result)");
            $finish(1);
        end
        else begin
            $display("SYNTH INT8x64: PASS");
            $finish(0);
        end
    endrule
endmodule

endpackage
