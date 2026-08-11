package TbIM2PCoreActivationBuffer;

import Vector::*;

import Types::*;
import ExecuteCmd::*;
import HostMemoryTypes::*;
import WorkTypes::*;
import IM2PCore::*;

typedef enum {
    TbStart,
    TbPublish,
    TbRun,
    TbDone
} TbState deriving (Bits, Eq, FShow);

function Vector#(2, Int#(8)) activationValues(HostAddress address);
    Vector#(2, Int#(8)) values = replicate(0);
    HostAddress localK = address % 8;
    values[0] = localK == 0 ? 1 : 3;
    values[1] = localK == 0 ? 2 : 4;
    return values;
endfunction

module mkTbIM2PCoreActivationBuffer(Empty);
    IM2PCoreIfc#(
        2, 1, 1, 4,
        Int#(8), Int#(8), Int#(16), Int#(32), Int#(8)
    ) core <- mkIM2PCore;

    Reg#(TbState) state <- mkReg(TbStart);
    Reg#(UInt#(16)) watchdog <- mkReg(0);
    Reg#(Bool) published <- mkReg(False);

    Reg#(Bool) weightPending <- mkReg(False);
    Reg#(HostRequestTag) weightTag <- mkRegU;

    Reg#(Bool) activationPending <- mkReg(False);
    Reg#(HostRequestTag) activationTag <- mkRegU;
    Reg#(HostAddress) activationAddress <- mkRegU;
    Reg#(Bool) row0GateObserved <- mkReg(False);
    Reg#(Bool) delayedRowObserved <- mkReg(False);
    Reg#(Bool) nextPrefetchDuringExecution <- mkReg(False);
    Reg#(UInt#(4)) activationResponses <- mkReg(0);

    Reg#(Bool) outputPending <- mkReg(False);
    Reg#(HostRequestTag) outputTag <- mkRegU;
    Reg#(UInt#(2)) outputResponses <- mkReg(0);

    rule watch;
        watchdog <= watchdog + 1;
        if (watchdog == 4000) begin
            $display("IM2P ACTIVATION BUFFER: FAIL timeout state=", fshow(state), " core=%0d ms=%0d ws=%0d a=%0d w=%0d", core.matrixCoreState, core.matmulSchedulerState, core.workSchedulerState, core.activationReadRequests, core.weightReadRequests);
            $finish(1);
        end
    endrule

    rule rejectUnpublishedActivation (!published && core.activationReadRequestValid);
        $display("IM2P ACTIVATION BUFFER: FAIL activation requested before async publication");
        $finish(1);
    endrule

    rule start (state == TbStart && core.idle);
        core.startMatmul(
            9, AsyncStripes,
            64'h1000, 64'h2000, 64'h3000, 64'h4000,
            8, 2, 2, 8,
            2, 2, 4,
            2, 2,
            0, 4, 2, 0, False, VectorBypass
        );
        state <= TbPublish;
    endrule

    rule publish (state == TbPublish);
        core.publishActivationStripe(0, 2, 8);
        published <= True;
        state <= TbRun;
    endrule

    rule captureWeight (core.weightReadRequestValid && !weightPending);
        weightTag <= core.weightReadRequestTag;
        weightPending <= True;
    endrule

    rule returnWeight (weightPending);
        core.putWeightReadResponse(weightTag, replicate(1));
        weightPending <= False;
    endrule

    rule captureActivation (core.activationReadRequestValid && !activationPending);
        HostAddress address = core.activationReadRequestAddress;
        HostAddress localK = address % 8;

        if (localK == 2 && core.executionActive) begin
            nextPrefetchDuringExecution <= True;
        end

        activationTag <= core.activationReadRequestTag;
        activationAddress <= address;
        activationPending <= True;
    endrule

    // Row 0 is deliberately held for one state transition. Execution must not
    // begin merely because weights are ready and the row request is visible.
    rule observeRow0Gate (
        activationPending && activationResponses == 0 && !row0GateObserved
    );
        if (core.executionActive) begin
            $display("IM2P ACTIVATION BUFFER: FAIL execution started before current row 0 response");
            $finish(1);
        end
        row0GateObserved <= True;
    endrule

    rule returnRow0 (
        activationPending && activationResponses == 0 && row0GateObserved
    );
        core.putActivationReadResponse(
            activationTag, activationValues(activationAddress)
        );
        activationPending <= False;
        activationResponses <= 1;
    endrule

    // Hold current row 1 after row 0 has started the engine. This checks that
    // the ordered feed can stall on the exact missing row rather than bypassing
    // it with data from the future fragment.
    rule observeDelayedCurrentRow (
        activationPending && activationResponses == 1
        && core.executionActive && !delayedRowObserved
    );
        delayedRowObserved <= True;
    endrule

    rule returnOtherActivation (
        activationPending
        && (activationResponses > 1
            || (activationResponses == 1 && delayedRowObserved))
    );
        core.putActivationReadResponse(
            activationTag, activationValues(activationAddress)
        );
        activationPending <= False;
        activationResponses <= activationResponses + 1;
    endrule

    rule captureOutput (core.outputWriteRequestValid && !outputPending);
        Vector#(2, Int#(32)) values = core.outputWriteRequestValues;
        if (values[0] != 10 || values[1] != 10) begin
            $display(
                "IM2P ACTIVATION BUFFER: FAIL ordered/tail result=(%0d,%0d)",
                values[0], values[1]
            );
            $finish(1);
        end
        outputTag <= core.outputWriteRequestTag;
        outputPending <= True;
    endrule

    rule returnOutput (outputPending);
        core.putOutputWriteResponse(outputTag);
        outputPending <= False;
        outputResponses <= outputResponses + 1;
    endrule

    rule finish (state == TbRun && core.matmulDone);
        if (!row0GateObserved || !delayedRowObserved
                || !nextPrefetchDuringExecution
                || activationResponses != 4
                || outputResponses != 2) begin
            $display(
                "IM2P ACTIVATION BUFFER: FAIL observations gate=%0d delayed=%0d prefetch=%0d a=%0d c=%0d",
                row0GateObserved, delayedRowObserved,
                nextPrefetchDuringExecution, activationResponses,
                outputResponses
            );
            $finish(1);
        end
        core.acknowledgeMatmul;
        state <= TbDone;
    endrule

    rule pass (state == TbDone && core.idle);
        $display("IM2P ACTIVATION BUFFER: PASS");
        $finish(0);
    endrule
endmodule

endpackage
