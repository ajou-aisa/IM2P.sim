package FullReplay;

import BRAMCore::*;
import Vector::*;
import Types::*;
import HostMemoryTypes::*;
import WorkTypes::*;
import IM2PCore::*;
import SynthA8W8D16::*;

typedef Vector#(16, Int#(8)) FullInput;
typedef Vector#(16, Int#(32)) FullOutput;

interface FullReplayIfc;
    method Action load(Bool weight, UInt#(9) word, FullInput values);
    method Action start(UInt#(6) m, UInt#(6) n, UInt#(7) k,
                        MatmulJobId job, ScaleContext contextId);
    method Bool done;
    method Bool protocolError;
    method UInt#(64) cycles;
    method UInt#(64) fragments;
    method UInt#(64) works;
    method UInt#(64) activationRequests;
    method UInt#(64) weightRequests;
    method UInt#(64) outputWrites;
    method UInt#(64) outputAcks;
    method Action requestOutput(UInt#(9) word);
    method Bool outputValid;
    method FullOutput outputResponse;
    method Action consumeOutput;
    method Action acknowledge;
endinterface

// ponytail: FULL Q8 native blocks only, M<=32/N<=48/K<=96. Extend the
// descriptor and capacity proof before supporting another profile or route.
// Fixed byte strides A=128, W=64, C=256 keep bounded staging address decode
// small. External reconstruction receives one raw C plane per K32 block.
(* synthesize *)
module mkFullReplay(FullReplayIfc);
    let core <- mkSynthA8W8D16;
    BRAM_PORT#(UInt#(8), FullInput) activations <- mkBRAMCore1(256, False);
    BRAM_PORT#(UInt#(9), FullInput) weights <- mkBRAMCore1(512, False);
    BRAM_PORT#(UInt#(9), FullOutput) outputs <- mkBRAMCore1(512, False);
    Reg#(Bool) running <- mkReg(False);
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
        activations.put(False, truncate((core.activationReadRequestAddress - 'h10000) >> 4), ?);
        activationTag <= core.activationReadRequestTag;
        activationPending <= True;
    endrule
    rule returnActivation (activationPending);
        core.putActivationReadResponse(activationTag, activations.read);
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
        outputs.put(True, truncate((core.outputWriteRequestAddress - 'h40000) >> 6), values);
        core.putOutputWriteResponse(core.outputWriteRequestTag);
    endrule
    rule finish (running && core.matmulDone);
        cyclesReg <= core.rtlCycleCount - startCycle;
        running <= False;
        doneReg <= True;
        core.acknowledgeMatmul;
    endrule
    rule captureOutput (outputPending && !outputValidReg);
        outputReg <= outputs.read;
        outputPending <= False;
        outputValidReg <= True;
    endrule

    method Action load(Bool weight, UInt#(9) word, FullInput values)
            if (!running && !doneReg);
        if (weight) weights.put(True, word, values);
        else activations.put(True, truncate(word), values);
    endmethod
    method Action start(UInt#(6) m, UInt#(6) n, UInt#(7) k,
                        MatmulJobId job, ScaleContext contextId)
            if (!running && !doneReg && !outputPending && !outputValidReg);
        core.startMatmul(job, FullMatrix, 'h10000, 'h20000, 'h30000, 'h40000,
            128, 64, 64, 256, zeroExtend(m), zeroExtend(n), zeroExtend(k),
            16, 16, 0, zeroExtend(k), 32, contextId, False, VectorExternal);
        aLimit <= 'h10000 + (zeroExtend(m) << 7);
        wLimit <= 'h20000 + (zeroExtend(k) << 6);
        UInt#(64) blocks = zeroExtend(k >> 5);
        cLimit <= 'h40000 + (blocks * zeroExtend(m) << 8);
        startCycle <= core.rtlCycleCount;
        cyclesReg <= 0;
        errorA <= False; errorW <= False; errorS <= False; errorC <= False;
        running <= True;
    endmethod
    method Bool done = doneReg;
    method Bool protocolError = errorA || errorW || errorS || errorC;
    method UInt#(64) cycles = cyclesReg;
    method UInt#(64) fragments = core.matmulFragmentsCompleted;
    method UInt#(64) works = core.matmulWorksCompleted;
    method UInt#(64) activationRequests = core.activationReadRequests;
    method UInt#(64) weightRequests = core.weightReadRequests;
    method UInt#(64) outputWrites = core.outputWriteRequests;
    method UInt#(64) outputAcks = core.outputWriteResponses;
    method Action requestOutput(UInt#(9) word)
            if (doneReg && !outputPending && !outputValidReg);
        outputs.put(False, word, ?);
        outputPending <= True;
    endmethod
    method Bool outputValid = outputValidReg;
    method FullOutput outputResponse if (outputValidReg);
        return outputReg;
    endmethod
    method Action consumeOutput if (outputValidReg);
        outputValidReg <= False;
    endmethod
    method Action acknowledge if (doneReg && !outputPending && !outputValidReg);
        doneReg <= False;
    endmethod
endmodule
endpackage
