package ResidentP0;

import BRAMCore::*;
import Vector::*;
import Types::*;
import HostMemoryTypes::*;
import WorkTypes::*;
import IM2PCore::*;
import SynthA8W8D16::*;

typedef Vector#(16, Int#(8)) InputRow;
typedef Vector#(16, Int#(32)) OutputRow;

interface ResidentP0Ifc;
    // A is row-major 16x32, grouped into two 16-byte words per row.
    // B is row-major 32x16, one 16-byte word per row.
    method Action loadActivation(UInt#(5) word, InputRow values);
    method Action loadWeight(UInt#(5) word, InputRow values);
    method Action loadScale(InputRow values);
    method Action start(UInt#(5) m, UInt#(5) n, UInt#(6) k, Bool shift);
    method Bool done;
    method UInt#(64) cycles;
    method UInt#(64) fragments;
    method Bool protocolError;
    method Action requestOutput(UInt#(4) row);
    method Bool outputValid;
    method OutputRow outputResponse;
    method Action consumeOutput;
    method Action acknowledge;
endinterface

// TEST ONLY: bounded shapes M/N=1..16, K=1..32 in existing fixed-stride memories.
// Full preload and BRAM request/return timing remain unchanged.
(* synthesize *)
module mkResidentP0(ResidentP0Ifc);
    let core <- mkSynthA8W8D16;
    BRAM_PORT#(UInt#(5), InputRow) activations <- mkBRAMCore1(32, False);
    BRAM_PORT#(UInt#(5), InputRow) weights <- mkBRAMCore1(32, False);
    BRAM_PORT#(UInt#(4), OutputRow) outputs <- mkBRAMCore1(16, False);
    Reg#(Vector#(16, UInt#(32))) scales <- mkRegU;
    Reg#(Bit#(32)) activationLoaded <- mkReg(0);
    Reg#(Bit#(32)) weightLoaded <- mkReg(0);
    Reg#(Bool) scaleLoaded <- mkReg(False);
    Reg#(Bool) running <- mkReg(False);
    Reg#(Bool) doneReg <- mkReg(False);
    Reg#(Bool) errorReg <- mkReg(False);
    Reg#(UInt#(64)) startCycle <- mkReg(0);
    Reg#(UInt#(64)) cyclesReg <- mkReg(0);
    Reg#(Bool) activationPending <- mkReg(False);
    Reg#(HostRequestTag) activationTag <- mkRegU;
    Reg#(Bool) weightPending <- mkReg(False);
    Reg#(HostRequestTag) weightTag <- mkRegU;
    Reg#(Bool) outputPending <- mkReg(False);
    Reg#(Bool) outputValidReg <- mkReg(False);
    Reg#(OutputRow) outputReg <- mkRegU;

    rule issueActivation (running && !activationPending
            && core.activationReadRequestValid);
        HostAddress address = core.activationReadRequestAddress;
        if (address >= 'h1000 && address < 'h1200
                && (address & 15) == 0
                && core.activationReadRequestElementCount > 0
                && core.activationReadRequestElementCount <= 16) begin
            activations.put(False, truncate((address - 'h1000) >> 4), ?);
            activationTag <= core.activationReadRequestTag;
            activationPending <= True;
        end
    endrule

    rule returnActivation (activationPending);
        core.putActivationReadResponse(activationTag, activations.read);
        activationPending <= False;
    endrule

    rule issueWeight (running && !weightPending && core.weightReadRequestValid);
        HostAddress address = core.weightReadRequestAddress;
        if (address >= 'h2000 && address < 'h2200
                && (address & 15) == 0
                && core.weightReadRequestElementCount > 0
                && core.weightReadRequestElementCount <= 16) begin
            weights.put(False, truncate((address - 'h2000) >> 4), ?);
            weightTag <= core.weightReadRequestTag;
            weightPending <= True;
        end
    endrule

    rule returnWeight (weightPending);
        core.putWeightReadResponse(weightTag, weights.read);
        weightPending <= False;
    endrule

    rule returnScale (running && core.scaleReadRequestValid
            && core.scaleReadRequestAddress == 'h3000
            && core.scaleReadRequestElementCount > 0
                && core.scaleReadRequestElementCount <= 16);
        core.putScaleReadResponse(core.scaleReadRequestTag, scales);
    endrule

    rule writeOutput (running && core.outputWriteRequestValid);
        HostAddress address = core.outputWriteRequestAddress;
        if (address >= 'h4000 && address < 'h4400
                && (address & 63) == 0
                && core.outputWriteRequestElementCount > 0
                && core.outputWriteRequestElementCount <= 16) begin
            outputs.put(True, truncate((address - 'h4000) >> 6),
                core.outputWriteRequestValues);
            // The response acknowledges the same edge that makes the local
            // BRAM write durable; completion cannot precede this edge.
            core.putOutputWriteResponse(core.outputWriteRequestTag);
        end
    endrule

    rule checkProtocol (running);
        Bool bad = False;
        if (core.activationReadRequestValid) begin
            HostAddress address = core.activationReadRequestAddress;
            bad = bad || address < 'h1000 || address >= 'h1200
                || (address & 15) != 0 || core.activationReadRequestElementCount == 0
                || core.activationReadRequestElementCount > 16;
        end
        if (core.weightReadRequestValid) begin
            HostAddress address = core.weightReadRequestAddress;
            bad = bad || address < 'h2000 || address >= 'h2200
                || (address & 15) != 0 || core.weightReadRequestElementCount == 0
                || core.weightReadRequestElementCount > 16;
        end
        if (core.scaleReadRequestValid)
            bad = bad || core.scaleReadRequestAddress != 'h3000
                || core.scaleReadRequestElementCount == 0
                || core.scaleReadRequestElementCount > 16;
        if (core.outputWriteRequestValid) begin
            HostAddress address = core.outputWriteRequestAddress;
            bad = bad || address < 'h4000 || address >= 'h4400
                || (address & 63) != 0 || core.outputWriteRequestElementCount == 0
                || core.outputWriteRequestElementCount > 16;
        end
        errorReg <= errorReg || bad;
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

    method Action loadActivation(UInt#(5) word, InputRow values)
            if (!running && !doneReg);
        activations.put(True, word, values);
        activationLoaded <= activationLoaded | (1 << word);
    endmethod

    method Action loadWeight(UInt#(5) word, InputRow values)
            if (!running && !doneReg);
        weights.put(True, word, values);
        weightLoaded <= weightLoaded | (1 << word);
    endmethod

    method Action loadScale(InputRow values) if (!running && !doneReg);
        Vector#(16, UInt#(32)) carriers = newVector;
        for (Integer lane = 0; lane < 16; lane = lane + 1)
            carriers[lane] = unpack(pack(signExtend(values[lane])));
        scales <= carriers;
        scaleLoaded <= True;
    endmethod

    method Action start(UInt#(5) m, UInt#(5) n, UInt#(6) k, Bool shift)
            if (!running && !doneReg
            && activationLoaded == '1 && weightLoaded == '1 && scaleLoaded
            && !outputPending && !outputValidReg);
        if (m >= 1 && m <= 16 && n >= 1 && n <= 16 && k >= 1 && k <= 32) begin
            core.startMatmul(1, FullMatrix, 'h1000, 'h2000, 'h3000, 'h4000,
                32, 16, 64, 64, zeroExtend(m), zeroExtend(n), zeroExtend(k),
                16, 16, 0, 32, 32, 1, False,
                shift ? VectorShift : VectorMultiply);
            startCycle <= core.rtlCycleCount;
            cyclesReg <= 0;
            errorReg <= False;
            running <= True;
        end
        else errorReg <= True;
    endmethod

    method Bool done = doneReg;
    method UInt#(64) cycles = cyclesReg;
    method UInt#(64) fragments = core.matmulFragmentsCompleted;
    method Bool protocolError = errorReg;

    method Action requestOutput(UInt#(4) row)
            if (doneReg && !outputPending && !outputValidReg);
        outputs.put(False, row, ?);
        outputPending <= True;
    endmethod

    method Bool outputValid = outputValidReg;
    method OutputRow outputResponse if (outputValidReg);
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
