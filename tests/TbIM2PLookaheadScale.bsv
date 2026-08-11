package TbIM2PLookaheadScale;
import Vector::*;
import Types::*;
import HostMemoryTypes::*;
import WorkTypes::*;
import IM2PCore::*;

typedef enum { Start, Publish0, Run0, RunBoth, Finish } State
    deriving (Bits, Eq, FShow);

module mkTbIM2PLookaheadScale(Empty);
    IM2PCoreIfc#(2,1,1,4,Int#(8),Int#(8),Int#(16),Int#(32),Int#(8))
        core <- mkIM2PCore;
    Reg#(State) state <- mkReg(Start);
    Reg#(UInt#(16)) watchdog <- mkReg(0);
    Reg#(Bool) activationPending <- mkReg(False);
    Reg#(HostRequestTag) activationTag <- mkRegU;
    Reg#(Bool) weightPending <- mkReg(False);
    Reg#(HostRequestTag) weightTag <- mkRegU;
    Reg#(Bool) scalePending <- mkReg(False);
    Reg#(HostRequestTag) scaleTag <- mkRegU;
    Reg#(Bool) outputPending <- mkReg(False);
    Reg#(HostRequestTag) outputTag <- mkRegU;
    Reg#(UInt#(4)) lookaheadScaleEvents <- mkReg(0);
    Reg#(UInt#(64)) lookaheadScaleSeenCycle <- mkReg(0);

    rule watch;
        watchdog <= watchdog + 1;
        if (watchdog == 5000) begin
            $display("IM2P LOOKAHEAD SCALE: FAIL timeout state=", fshow(state));
            $finish(1);
        end
    endrule

    rule start (state == Start && core.idle);
        core.startMatmul(
            61, AsyncStripes,
            64'h1000, 64'h2000, 64'h3000, 64'h4000,
            8, 8, 8, 16,
            4, 6, 2, 2, 2, 0, 2, 2, 40, False, VectorMultiply
        );
        state <= Publish0;
    endrule

    rule publish0 (state == Publish0);
        core.publishActivationStripe(0, 2, 8);
        state <= Run0;
    endrule

    // Wait for s0/J0 to finish.  s0/J1 then owns context 42 while the
    // published s1 lookahead needs context 40, forcing a real lookahead miss.
    rule publish1 (state == Run0 && core.matmulWorksCompleted == 2
            && core.executionActive);
        core.publishActivationStripe(2, 2, 12);
        state <= RunBoth;
    endrule

    rule captureActivation (
        core.activationReadRequestValid && !activationPending
    );
        activationTag <= core.activationReadRequestTag;
        activationPending <= True;
    endrule
    rule returnActivation (activationPending);
        core.putActivationReadResponse(activationTag, replicate(1));
        activationPending <= False;
    endrule

    rule captureWeight (core.weightReadRequestValid && !weightPending);
        weightTag <= core.weightReadRequestTag;
        weightPending <= True;
    endrule
    rule returnWeight (weightPending);
        UInt#(32) rowTag = truncate(weightTag);
        Int#(8) value = ((rowTag & 1) == 0) ? 1 : 2;
        core.putWeightReadResponse(weightTag, replicate(value));
        weightPending <= False;
    endrule

    rule captureScale (core.scaleReadRequestValid && !scalePending);
        HostRequestTag tag = core.scaleReadRequestTag;
        UInt#(32) lowTag = truncate(tag);
        if ((lowTag & 32'hf0000000) == 32'ha0000000) begin
            if (core.scaleReadRequestAddress != 64'h3000
                    || core.scaleReadRequestElementCount != 2
                    || core.currentStripeCompletionCycle != 0) begin
                $display(
                    "IM2P LOOKAHEAD SCALE: FAIL request address=%0h count=%0d complete=%0d",
                    core.scaleReadRequestAddress,
                    core.scaleReadRequestElementCount,
                    core.currentStripeCompletionCycle
                );
                $finish(1);
            end
            lookaheadScaleEvents <= lookaheadScaleEvents + 1;
            lookaheadScaleSeenCycle <= core.lookaheadScaleCycle;
        end
        scaleTag <= tag;
        scalePending <= True;
    endrule
    rule returnScale (scalePending);
        core.putScaleReadResponse(scaleTag, replicate(1));
        scalePending <= False;
    endrule

    rule captureOutput (core.outputWriteRequestValid && !outputPending);
        outputTag <= core.outputWriteRequestTag;
        outputPending <= True;
    endrule
    rule returnOutput (outputPending);
        core.putOutputWriteResponse(outputTag);
        outputPending <= False;
    endrule

    rule done (state == RunBoth && core.matmulDone);
        if (lookaheadScaleEvents != 1 || core.lookaheadScaleRequests != 1
                || core.lookaheadScaleReuses != 0
                || core.lookaheadWeightRequests != 2
                || core.lookaheadWeightReuseHits != 0
                || core.lookaheadFirstWeightCycle == 0
                || core.lookaheadFirstWeightCycle
                    >= core.currentStripeCompletionCycle
                || lookaheadScaleSeenCycle == 0
                || lookaheadScaleSeenCycle >= core.currentStripeCompletionCycle
                || core.lookaheadStartCycle <= core.currentStripeCompletionCycle) begin
            $display(
                "IM2P LOOKAHEAD SCALE: FAIL events=%0d srequests=%0d sreuse=%0d whost=%0d whits=%0d wcycle=%0d scale=%0d complete=%0d start=%0d",
                lookaheadScaleEvents, core.lookaheadScaleRequests,
                core.lookaheadScaleReuses, core.lookaheadWeightRequests,
                core.lookaheadWeightReuseHits, core.lookaheadFirstWeightCycle,
                lookaheadScaleSeenCycle,
                core.currentStripeCompletionCycle, core.lookaheadStartCycle
            );
            $finish(1);
        end
        $display(
            "LOOKAHEAD NONRESIDENT wfetch=%0d miss=%0d complete=%0d start=%0d",
            core.lookaheadFirstWeightCycle, lookaheadScaleSeenCycle,
            core.currentStripeCompletionCycle,
            core.lookaheadStartCycle
        );
        core.acknowledgeMatmul;
        state <= Finish;
    endrule

    rule finish (state == Finish && core.idle);
        $display("IM2P LOOKAHEAD SCALE: PASS");
        $finish(0);
    endrule
endmodule
endpackage
