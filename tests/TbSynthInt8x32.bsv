package TbSynthInt8x32;

import Vector::*;

import Types::*;
import ExecuteCmd::*;
import Config::*;
import IM2PCore::*;
import SynthInt8x32::*;

// Identity weights make each output column reproduce the corresponding
// activation value, which keeps expected results easy to inspect.
function Vector#(32, Int#(8)) identityWeight(UInt#(5) row);
    Vector#(32, Int#(8)) weights = newVector;

    for (Integer column = 0; column < 32; column = column + 1) begin
        weights[column] = row == fromInteger(column) ? 1 : 0;
    end

    return weights;
endfunction

// Supply two logical rows. Only the first two columns are non-zero so the
// result also verifies that unused columns remain zero.
function Vector#(32, Int#(8)) activationRow(UInt#(1) row);
    Vector#(32, Int#(8)) activations = replicate(0);

    if (row == 0) begin
        activations[0] = 5;
        activations[1] = 6;
    end
    else begin
        activations[0] = 7;
        activations[1] = 8;
    end

    return activations;
endfunction

// Expected accumulator contents after multiplying by the identity matrix.
function Vector#(32, Int#(32)) expectedRow(UInt#(1) row);
    Vector#(32, Int#(32)) expected = replicate(0);

    if (row == 0) begin
        expected[0] = 5;
        expected[1] = 6;
    end
    else begin
        expected[0] = 7;
        expected[1] = 8;
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

// Exercise the synthesized 32x32 INT8 configuration through the public
// block-aware core interface: load weights, run two rows, then read results.
module mkTbSynthInt8x32(Empty);
    IM2PCoreIfc#(
        32,
        1,
        32,
        DefaultAccumulatorRows,
        DefaultScaleBlocks,
        Int#(8),
        Int#(8),
        Int#(16),
        Int#(32),
        Int#(8)
    ) dut <- mkSynthInt8x32;

    Reg#(TbState) state <- mkReg(BeginWeights);
    Reg#(UInt#(5)) weightRow <- mkReg(0);
    Reg#(Vector#(32, Int#(32))) observedRow0 <- mkRegU;
    Reg#(UInt#(12)) watchdog <- mkReg(0);

    // Prevent a deadlocked handshake from leaving simulation running forever.
    rule watch;
        watchdog <= watchdog + 1;
        if (watchdog == 4000) begin
            $display("SYNTH INT8x32: FAIL (timeout)");
            $finish(1);
        end
    endrule

    // Weight loading is a separate phase and accepts one complete matrix row
    // per cycle.
    rule beginWeights (state == BeginWeights);
        dut.beginWeightLoad;
        state <= LoadWeights;
    endrule

    rule loadWeights (state == LoadWeights);
        dut.loadWeightRow(weightRow, identityWeight(weightRow));
        if (weightRow == 31) begin
            state <= Configure;
        end
        else begin
            weightRow <= weightRow + 1;
        end
    endrule

    rule configure (state == Configure && dut.weightsReady && dut.idle);
        dut.configureScaling(32, 32, 1);
        state <= Start;
    endrule

    // Start one non-accumulating, bypass execution after all weights are ready.
    rule startExecution (state == Start && dut.weightsReady && dut.idle);
        dut.startExecution(ExecuteCmd {
            accumulatorBaseRow: 0,
            rowCount: 2,
            accumulate: False,
            vectorOp: VectorBypass
        }, 0, 32);
        state <= FeedRow0;
    endrule

    // Feed activation rows only when the core advertises backpressure relief.
    rule feedRow0 (state == FeedRow0 && dut.activationReady);
        dut.putActivationRow(activationRow(0));
        state <= FeedRow1;
    endrule

    rule feedRow1 (state == FeedRow1 && dut.activationReady);
        dut.putActivationRow(activationRow(1));
        state <= Wait;
    endrule

    // Wait for all systolic, vector, and accumulator stages to drain.
    rule waitExecution (state == Wait && dut.executionDone);
        observedRow0 <= dut.readAccumulatorRow(0);
        state <= CheckRow0;
    endrule

    // Read row 1 in the following cycle and compare both logical rows.
    rule checkResults (state == CheckRow0);
        Vector#(32, Int#(32)) observedRow1 = dut.readAccumulatorRow(1);
        Bool passed = observedRow0 == expectedRow(0)
            && observedRow1 == expectedRow(1);

        if (!passed) begin
            $display("SYNTH INT8x32: FAIL (unexpected result)");
            $finish(1);
        end
        else begin
            $display("SYNTH INT8x32: PASS");
            $finish(0);
        end
    endrule
endmodule

endpackage
