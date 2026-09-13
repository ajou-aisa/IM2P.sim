package TbIM2PRequestTags;
import Assert::*;
import Vector::*;
import Types::*;
import HostMemoryTypes::*;
import WorkTypes::*;
import IM2PCore::*;

// Compile with -D IM2P_TEST_TAG_BOUNDARY -check-assert. The same core runs
// FULL, five two-row stripes plus a tail, and FULL again without reset.
module mkTbIM2PRequestTags(Empty);
    IM2PCoreIfc#(2,1,1,4,Int#(8),Int#(8),Int#(16),Int#(32),Int#(8)) core <- mkIM2PCore;
    Reg#(UInt#(2)) job <- mkReg(0);
    Reg#(Bool) running <- mkReg(False);
    Reg#(UInt#(32)) ticks <- mkReg(0);
    Reg#(UInt#(32)) published <- mkReg(0);
    Reg#(UInt#(32)) retired <- mkReg(0);
    Reg#(UInt#(32)) writes <- mkReg(0);
    Vector#(33, Reg#(Bool)) seen <- replicateM(mkReg(False));
    Reg#(Bool) ap <- mkReg(False), wp <- mkReg(False), sp <- mkReg(False), cp <- mkReg(False);
    Reg#(HostRequestTag) at <- mkRegU, wt <- mkRegU, st <- mkRegU, ct <- mkRegU;
    Reg#(HostAddress) aa <- mkRegU, wa <- mkRegU, ca <- mkRegU;
    Reg#(Vector#(2,Int#(32))) cv <- mkRegU;
    Reg#(Bool) aBoundary <- mkReg(False), wBoundary <- mkReg(False), cWrap <- mkReg(False);
    Reg#(Bool) aLookahead <- mkReg(False), wLookahead <- mkReg(False), sLookahead <- mkReg(False);
    Reg#(UInt#(32)) completions <- mkReg(0);

    rule watch;
        ticks <= ticks + 1;
        dynamicAssert(ticks < 100000, "request tag test watchdog");
    endrule
    rule start (!running && core.idle && job < 3);
        core.startMatmul(90 + zeroExtend(job), job == 1 ? AsyncStripes : FullMatrix,
            'h1000, 'h2000, 'h3000, 'h4000, 64, 8, 8, 32,
            11, 6, 64, 2, 2, 0, 64, 4, 20, False, VectorMultiply);
        running <= True; published <= job == 1 ? 0 : 11; retired <= 0; writes <= 0;
        for (Integer i = 0; i < 33; i = i + 1) seen[i] <= False;
    endrule
    rule publish (running && job == 1 && published < 11 && published <= retired + 2);
        UInt#(32) count = published == 10 ? 1 : 2;
        core.publishActivationStripe(published, count, 64);
        published <= published + count;
    endrule
    rule captureA (core.activationReadRequestValid && !ap);
        HostRequestTag tag = core.activationReadRequestTag;
        UInt#(32) owner = truncate(tag >> 32), index = truncate(tag);
        UInt#(32) expected = 90 + zeroExtend(job);
        dynamicAssert(owner == expected || owner == (expected ^ 'h80000000), "A owner/job mismatch");
        at <= tag; aa <= core.activationReadRequestAddress; ap <= True;
        if (owner == expected && index >= 'h80000000) aBoundary <= True;
        if (owner != expected) aLookahead <= True;
    endrule
    rule returnA (ap && ticks % 5 == 0);
        Int#(8) value = unpack(truncate(pack(((aa - 'h1000) >> 6) + 1)));
        core.putActivationReadResponse(at, replicate(value)); ap <= False;
    endrule
    rule captureW (core.weightReadRequestValid && !wp);
        HostRequestTag tag = core.weightReadRequestTag;
        UInt#(32) owner = truncate(tag >> 32), index = truncate(tag);
        UInt#(32) expected = 90 + zeroExtend(job);
        dynamicAssert(owner == expected || owner == (expected ^ 'h80000000), "W owner/job mismatch");
        wt <= tag; wa <= core.weightReadRequestAddress; wp <= True;
        if (owner == expected && index >= 'h90000000) wBoundary <= True;
        if (owner != expected) wLookahead <= True;
    endrule
    rule returnW (wp && ticks % 7 == 0);
        Int#(8) first = unpack(truncate(pack(((wa - 'h2000) & 7) + 1)));
        Vector#(2,Int#(8)) values = replicate(first);
        values[1] = first + 1;
        core.putWeightReadResponse(wt, values); wp <= False;
    endrule
    rule captureS (core.scaleReadRequestValid && !sp);
        HostRequestTag tag = core.scaleReadRequestTag;
        UInt#(32) owner = truncate(tag >> 32), expected = 90 + zeroExtend(job);
        dynamicAssert(owner == expected || owner == (expected ^ 'h80000000), "S owner/job mismatch");
        st <= tag; sp <= True;
        if (owner != expected) sLookahead <= True;
    endrule
    rule returnS (sp && ticks % 11 == 0);
        core.putScaleReadResponse(st, replicate(1)); sp <= False;
    endrule
    rule captureC (core.outputWriteRequestValid && !cp);
        HostAddress offset = core.outputWriteRequestAddress - 'h4000;
        UInt#(32) row = truncate(offset >> 5), column = truncate((offset & 31) >> 2);
        UInt#(6) index = truncate(row * 3 + (column >> 1));
        let values = core.outputWriteRequestValues;
        dynamicAssert(row < 11 && column < 6 && (column & 1) == 0 && !seen[index], "C extent/duplicate");
        dynamicAssert(row < published && core.outputWriteRequestElementCount == 2, "C before publication/count");
        dynamicAssert(values[0] == unpack(pack(64 * (row + 1) * (column + 1))) &&
            values[1] == unpack(pack(64 * (row + 1) * (column + 2))), "C exact numerical mismatch");
        ct <= core.outputWriteRequestTag; ca <= core.outputWriteRequestAddress; cv <= values; cp <= True;
        seen[index] <= True;
        UInt#(32) tagIndex = truncate(core.outputWriteRequestTag);
        if (tagIndex < 16) cWrap <= True;
    endrule
    rule heldC (cp);
        dynamicAssert(core.outputWriteRequestTag == ct && core.outputWriteRequestAddress == ca &&
            core.outputWriteRequestValues == cv, "C payload changed under backpressure");
    endrule
    rule returnC (cp && ticks % 13 == 0);
        core.putOutputWriteResponse(ct); cp <= False; writes <= writes + 1;
    endrule
    rule retireStripe (core.stripeCompletionValid);
        dynamicAssert(job == 1 && core.stripeCompletionRowBegin == retired, "stripe retirement order");
        retired <= retired + core.stripeCompletionRowCount;
        completions <= completions + 1;
        core.acknowledgeStripeCompletion;
    endrule
    rule finish (running && core.matmulDone);
        dynamicAssert(writes == 33 && !ap && !wp && !sp && !cp, "completion before response drain");
        dynamicAssert(job != 1 || (retired == 11 && completions == 6), "stripe semantic completion");
        core.acknowledgeMatmul; running <= False; job <= job + 1;
    endrule
    rule done (job == 3 && core.idle);
        $display("TAG COVERAGE A-boundary=%0d W-boundary=%0d C-wrap=%0d A-lookahead=%0d W-lookahead=%0d S-lookahead=%0d",
            aBoundary, wBoundary, cWrap, aLookahead, wLookahead, sLookahead);
        dynamicAssert(aBoundary && wBoundary && cWrap && aLookahead && wLookahead,
            "request boundary/lookahead coverage missing");
        $display("IM2P REQUEST TAGS: PASS jobs=3 outputs=198 stripes=6 A/W reserved-boundary=1 C-wrap=1 A/W-lookahead=1");
        $finish(0);
    endrule
endmodule
endpackage
