package DensePipeline;

import Assert::*;
import BRAMCore::*;
import Vector::*;
import Types::*;
import HostMemoryTypes::*;
import WorkTypes::*;
import IM2PCore::*;
import SynthA8W8D16::*;

typedef Vector#(16, Int#(8)) FullInput;
typedef Vector#(16, Int#(32)) FullOutput;

interface DensePipelineIfc;
    method Action loadActivation(UInt#(12) word, FullInput values);
    method Action loadWeight(UInt#(9) word, FullInput values);
    method Action start(Bool striped, UInt#(9) m, UInt#(6) n, UInt#(7) k,
                        MatmulJobId job, ScaleContext contextId);
    method Action publish(UInt#(9) rowBegin, UInt#(9) rowCount);
    method Bool stripeCompletionValid;
    method UInt#(32) stripeId;
    method MatrixExtent stripeRowBegin;
    method MatrixExtent stripeRowCount;
    method UInt#(64) stripePublishCycle;
    method UInt#(64) stripeCompletionCycle;
    method Action acknowledgeStripe;
    method UInt#(9) publishedRows;
    method UInt#(9) completedRows;
    method UInt#(64) liveCycles;
    method UInt#(64) hostWaitCycles;
    method UInt#(64) overlapCycles;
    method UInt#(64) firstActivationCycle;
    method UInt#(9) firstActivationPublishedRows;
    method Bool done;
    method Bool protocolError;
    method UInt#(64) cycles;
    method UInt#(64) fragments;
    method UInt#(64) works;
    method UInt#(64) activationRequests;
    method UInt#(64) weightRequests;
    method UInt#(64) outputWrites;
    method UInt#(64) outputAcks;
    method Action requestOutput(UInt#(12) word);
    method Bool outputValid;
    method FullOutput outputResponse;
    method Action consumeOutput;
    method Action acknowledge;
endinterface

// ponytail: bounded M<=336/N<=48/K<=96, native K32 blocks, one logical
// invocation. Full A backing avoids stripe-slot address remapping. Port A
// receives unpublished A; port B services autonomous core reads. Raw C uses
// separate core-write and transport-read ports; the shell only reads rows
// whose stripe completion has been observed. A/W/C strides remain 128/64/256.
(* synthesize *)
module mkDensePipeline(DensePipelineIfc);
    let core <- mkSynthA8W8D16;
    BRAM_DUAL_PORT#(UInt#(12), FullInput) activations <- mkBRAMCore2(4096, False);
    BRAM_PORT#(UInt#(9), FullInput) weights <- mkBRAMCore1(512, False);
    BRAM_DUAL_PORT#(UInt#(12), FullOutput) outputs <- mkBRAMCore2(4096, False);
    Reg#(Bool) running <- mkReg(False);
    Reg#(Bool) stripedReg <- mkReg(False);
    Reg#(UInt#(9)) rowsReg <- mkReg(0);
    Reg#(UInt#(9)) publishedRowsReg <- mkReg(0);
    Reg#(UInt#(9)) completedRowsReg <- mkReg(0);
    Reg#(Bool) completionObserved <- mkReg(False);
    Reg#(Bool) firstActivationSeen <- mkReg(False);
    Reg#(UInt#(64)) firstActivationCycleReg <- mkReg(0);
    Reg#(UInt#(9)) firstActivationPublishedRowsReg <- mkReg(0);
    Reg#(Bool) doneReg <- mkReg(False);
    Reg#(Bool) errorA <- mkReg(False);
    Reg#(Bool) errorW <- mkReg(False);
    Reg#(Bool) errorS <- mkReg(False);
    Reg#(Bool) errorC <- mkReg(False);
    Reg#(UInt#(64)) startCycle <- mkReg(0);
    Reg#(UInt#(64)) cyclesReg <- mkReg(0);
    Reg#(Bool) activationPending <- mkReg(False);
    Reg#(HostRequestTag) activationTag <- mkRegU;
    Reg#(Bool) weightPending <- mkReg(False);
    Reg#(HostRequestTag) weightTag <- mkRegU;
    Reg#(Bool) outputPending <- mkReg(False);
    Reg#(Bool) outputValidReg <- mkReg(False);
    Reg#(FullOutput) outputReg <- mkRegU;
    Reg#(UInt#(64)) aLimit <- mkReg(0);
    Reg#(UInt#(64)) wLimit <- mkReg(0);
    Reg#(UInt#(64)) cLimit <- mkReg(0);

    rule issueActivation (running && !activationPending
            && core.activationReadRequestValid);
        let address = core.activationReadRequestAddress;
        errorA <= errorA || address < 'h10000 || address >= aLimit
            || (address & 15) != 0 || core.activationReadRequestElementCount != 16;
        activations.b.put(False, truncate((core.activationReadRequestAddress - 'h10000) >> 4), ?);
        if (!firstActivationSeen) begin
            firstActivationSeen <= True;
            firstActivationCycleReg <= core.rtlCycleCount - startCycle;
            firstActivationPublishedRowsReg <= publishedRowsReg;
        end
        activationTag <= core.activationReadRequestTag;
        activationPending <= True;
    endrule
    rule returnActivation (activationPending);
        core.putActivationReadResponse(activationTag, activations.b.read);
        activationPending <= False;
    endrule
    rule issueWeight (running && !weightPending && core.weightReadRequestValid);
        let address = core.weightReadRequestAddress;
        errorW <= errorW || address < 'h20000 || address >= wLimit
            || (address & 15) != 0 || core.weightReadRequestElementCount == 0;
        weights.put(False, truncate((core.weightReadRequestAddress - 'h20000) >> 4), ?);
        weightTag <= core.weightReadRequestTag;
        weightPending <= True;
    endrule
    rule returnWeight (weightPending);
        core.putWeightReadResponse(weightTag, weights.read);
        weightPending <= False;
    endrule
    // The existing native frontend supplies the identity scale for External;
    // floating block scales remain in its owned host reconstruction metadata.
    rule returnScale (running && core.scaleReadRequestValid);
        let address = core.scaleReadRequestAddress;
        errorS <= errorS || address < 'h30000 || address >= 'h300c0
            || (address & 15) != 0 || core.scaleReadRequestElementCount == 0;
        core.putScaleReadResponse(core.scaleReadRequestTag, replicate(1));
    endrule
    rule writeOutput (running && core.outputWriteRequestValid);
        let address = core.outputWriteRequestAddress;
        errorC <= errorC || address < 'h40000 || address >= cLimit
            || (address & 63) != 0 || core.outputWriteRequestElementCount == 0;
        FullOutput values = core.outputWriteRequestValues;
        for (Integer lane = 0; lane < 16; lane = lane + 1)
            if (fromInteger(lane) >= core.outputWriteRequestElementCount)
                values[lane] = 0;
        outputs.a.put(True, truncate((core.outputWriteRequestAddress - 'h40000) >> 6), values);
        core.putOutputWriteResponse(core.outputWriteRequestTag);
    endrule
    rule finish (running && core.matmulDone);
        cyclesReg <= core.rtlCycleCount - startCycle;
        running <= False;
        doneReg <= True;
        core.acknowledgeMatmul;
    endrule
    // Record raw completion independently of a delayed host acknowledgement.
    rule observeStripeCompletion (core.stripeCompletionValid && !completionObserved);
        completedRowsReg <= truncate(core.stripeCompletionRowBegin + core.stripeCompletionRowCount);
        completionObserved <= True;
    endrule
    rule captureOutput (outputPending && !outputValidReg);
        outputReg <= outputs.b.read;
        outputPending <= False;
        outputValidReg <= True;
    endrule

    method Action loadActivation(UInt#(12) word, FullInput values) if (!doneReg);
        dynamicAssert(!running || (stripedReg && (word >> 3) >= zeroExtend(publishedRowsReg)
            && (word >> 3) < zeroExtend(rowsReg)), "A write overlaps a published stripe");
        activations.a.put(True, word, values);
    endmethod
    method Action loadWeight(UInt#(9) word, FullInput values) if (!running && !doneReg);
        weights.put(True, word, values);
    endmethod
    method Action start(Bool striped, UInt#(9) m, UInt#(6) n, UInt#(7) k,
                        MatmulJobId job, ScaleContext contextId)
            if (!running && !doneReg && !outputPending && !outputValidReg
                && !core.stripeCompletionValid && !completionObserved);
        dynamicAssert(m > 0 && m <= 336 && n > 0 && n <= 48
            && (k == 32 || k == 64 || k == 96), "DensePipeline shape/capacity");
        core.startMatmul(job, striped ? AsyncStripes : FullMatrix, 'h10000, 'h20000, 'h30000, 'h40000,
            128, 64, 64, 256, zeroExtend(m), zeroExtend(n), zeroExtend(k),
            16, 16, 0, zeroExtend(k), 32, contextId, False, VectorExternal);
        aLimit <= 'h10000 + (zeroExtend(m) << 7);
        wLimit <= 'h20000 + (zeroExtend(k) << 6);
        UInt#(64) blocks = zeroExtend(k >> 5);
        cLimit <= 'h40000 + (blocks * zeroExtend(m) << 8);
        startCycle <= core.rtlCycleCount;
        cyclesReg <= 0;
        errorA <= False; errorW <= False; errorS <= False; errorC <= False;
        stripedReg <= striped;
        rowsReg <= m;
        publishedRowsReg <= striped ? 0 : m;
        completedRowsReg <= 0;
        completionObserved <= False;
        firstActivationSeen <= False;
        firstActivationCycleReg <= 0;
        firstActivationPublishedRowsReg <= 0;
        running <= True;
    endmethod
    method Action publish(UInt#(9) rowBegin, UInt#(9) rowCount)
            if (running && stripedReg);
        dynamicAssert(rowBegin == publishedRowsReg && rowCount > 0
            && rowBegin <= rowsReg && rowCount <= rowsReg - rowBegin,
            "DensePipeline publication bounds/order");
        core.publishActivationStripe(zeroExtend(rowBegin), zeroExtend(rowCount), 128);
        publishedRowsReg <= rowBegin + rowCount;
    endmethod
    method Bool stripeCompletionValid = core.stripeCompletionValid && completionObserved;
    method UInt#(32) stripeId if (core.stripeCompletionValid && completionObserved);
        return core.stripeCompletionId;
    endmethod
    method MatrixExtent stripeRowBegin if (core.stripeCompletionValid && completionObserved);
        return core.stripeCompletionRowBegin;
    endmethod
    method MatrixExtent stripeRowCount if (core.stripeCompletionValid && completionObserved);
        return core.stripeCompletionRowCount;
    endmethod
    method UInt#(64) stripePublishCycle if (core.stripeCompletionValid && completionObserved);
        return core.stripeCompletionPublishCycle;
    endmethod
    method UInt#(64) stripeCompletionCycle if (core.stripeCompletionValid && completionObserved);
        return core.stripeCompletionCompletionCycle;
    endmethod
    method Action acknowledgeStripe if (core.stripeCompletionValid && completionObserved);
        core.acknowledgeStripeCompletion;
        completionObserved <= False;
    endmethod
    method UInt#(9) publishedRows = publishedRowsReg;
    method UInt#(9) completedRows = completedRowsReg;
    method UInt#(64) liveCycles = running ? core.rtlCycleCount - startCycle : cyclesReg;
    method UInt#(64) hostWaitCycles = core.stripeHostWaitCycles;
    method UInt#(64) overlapCycles = core.overlapCycles;
    method UInt#(64) firstActivationCycle = firstActivationCycleReg;
    method UInt#(9) firstActivationPublishedRows = firstActivationPublishedRowsReg;
    method Bool done = doneReg;
    method Bool protocolError = errorA || errorW || errorS || errorC;
    method UInt#(64) cycles = cyclesReg;
    method UInt#(64) fragments = core.matmulFragmentsCompleted;
    method UInt#(64) works = core.matmulWorksCompleted;
    method UInt#(64) activationRequests = core.activationReadRequests;
    method UInt#(64) weightRequests = core.weightReadRequests;
    method UInt#(64) outputWrites = core.outputWriteRequests;
    method UInt#(64) outputAcks = core.outputWriteResponses;
    method Action requestOutput(UInt#(12) word)
            if ((running || doneReg) && !outputPending && !outputValidReg);
        outputs.b.put(False, word, ?);
        outputPending <= True;
    endmethod
    method Bool outputValid = outputValidReg;
    method FullOutput outputResponse if (outputValidReg);
        return outputReg;
    endmethod
    method Action consumeOutput if (outputValidReg);
        outputValidReg <= False;
    endmethod
    method Action acknowledge if (doneReg && !outputPending && !outputValidReg
            && !core.stripeCompletionValid && !completionObserved);
        doneReg <= False;
    endmethod
endmodule
endpackage
