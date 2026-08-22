package TbIM2PCoreMultiwidth;

import Vector::*;

import Types::*;
import HostMemoryTypes::*;
import WorkTypes::*;
import IM2PCore::*;

// A4 values remain byte-addressed host elements even though the RTL lane is
// four bits wide. A16 values occupy two host bytes. Both cores use the same
// logical M=1, N=1, K=3 job so their request counts must remain identical.
HostAddress a4Base = 64'h1000;
HostAddress a16Base = 64'h2000;
HostAddress weightBase = 64'h3000;
HostAddress scaleBase = 64'h4000;
HostAddress a4OutputBase = 64'h5000;
HostAddress a16OutputBase = 64'h6000;

function Vector#(2, Int#(4)) a4Values(UInt#(2) request);
    Vector#(2, Int#(4)) values = replicate(0);
    if (request == 0) begin
        values[0] = -8;
        values[1] = 7;
    end
    else begin
        values[0] = -8;
    end
    return values;
endfunction

function Vector#(2, Int#(16)) a16Values(UInt#(2) request);
    Vector#(2, Int#(16)) values = replicate(0);
    if (request == 0) begin
        values[0] = -32768;
        values[1] = 32767;
    end
    else begin
        values[0] = -32768;
    end
    return values;
endfunction

function Vector#(2, Int#(8)) weightValues(HostAddress address);
    Vector#(2, Int#(8)) values = replicate(0);
    HostAddress row = (address - weightBase) / 8;
    values[0] = row == 1 ? -1 : 1;
    return values;
endfunction

module mkTbIM2PCoreMultiwidth(Empty);
    IM2PCoreIfc#(
        2, 1, 1, 8,
        Int#(4), Int#(8), Int#(12), Int#(32), Int#(8)
    ) a4 <- mkIM2PCore;

    IM2PCoreIfc#(
        2, 1, 1, 8,
        Int#(16), Int#(8), Int#(24), Int#(32), Int#(8)
    ) a16 <- mkIM2PCore;

    Reg#(Bool) started <- mkReg(False);
    Reg#(UInt#(2)) a4ActivationRequests <- mkReg(0);
    Reg#(UInt#(2)) a16ActivationRequests <- mkReg(0);
    Reg#(UInt#(3)) a4WeightRequests <- mkReg(0);
    Reg#(UInt#(3)) a16WeightRequests <- mkReg(0);
    Reg#(UInt#(2)) a4OutputRequests <- mkReg(0);
    Reg#(UInt#(2)) a16OutputRequests <- mkReg(0);
    Reg#(Bool) a4Done <- mkReg(False);
    Reg#(Bool) a16Done <- mkReg(False);
    Reg#(UInt#(12)) watchdog <- mkReg(0);

    rule watch;
        watchdog <= watchdog + 1;
        if (watchdog == 3000) begin
            $display("IM2P CORE MULTIWIDTH: FAIL timeout");
            $finish(1);
        end
    endrule

    rule start (!started && a4.idle && a16.idle);
        a4.startMatmul(
            4, FullMatrix, a4Base, weightBase, scaleBase, a4OutputBase,
            8, 8, 8, 8,
            1, 1, 3, 1, 1, 0, 3, 1, 0, False, VectorBypass
        );
        a16.startMatmul(
            16, FullMatrix, a16Base, weightBase, scaleBase, a16OutputBase,
            16, 8, 8, 8,
            1, 1, 3, 1, 1, 0, 3, 1, 0, False, VectorBypass
        );
        started <= True;
    endrule

    rule serveA4Activation (a4.activationReadRequestValid);
        HostAddress expectedAddress = a4Base
            + (a4ActivationRequests == 0 ? 0 : 2);
        BoundedCount#(2) expectedCount = a4ActivationRequests == 0 ? 2 : 1;
        if (a4ActivationRequests > 1
                || a4.activationReadRequestAddress != expectedAddress
                || a4.activationReadRequestElementCount != expectedCount) begin
            $display(
                "IM2P CORE MULTIWIDTH: FAIL A4 activation request index=%0d address=%0h expected=%0h count=%0d expectedCount=%0d",
                a4ActivationRequests, a4.activationReadRequestAddress,
                expectedAddress, a4.activationReadRequestElementCount,
                expectedCount
            );
            $finish(1);
        end
        a4.putActivationReadResponse(
            a4.activationReadRequestTag, a4Values(a4ActivationRequests)
        );
        a4ActivationRequests <= a4ActivationRequests + 1;
    endrule

    rule serveA16Activation (a16.activationReadRequestValid);
        HostAddress expectedAddress = a16Base
            + (a16ActivationRequests == 0 ? 0 : 4);
        BoundedCount#(2) expectedCount = a16ActivationRequests == 0 ? 2 : 1;
        if (a16ActivationRequests > 1
                || a16.activationReadRequestAddress != expectedAddress
                || a16.activationReadRequestElementCount != expectedCount) begin
            $display(
                "IM2P CORE MULTIWIDTH: FAIL A16 activation request index=%0d address=%0h expected=%0h count=%0d expectedCount=%0d",
                a16ActivationRequests, a16.activationReadRequestAddress,
                expectedAddress, a16.activationReadRequestElementCount,
                expectedCount
            );
            $finish(1);
        end
        a16.putActivationReadResponse(
            a16.activationReadRequestTag, a16Values(a16ActivationRequests)
        );
        a16ActivationRequests <= a16ActivationRequests + 1;
    endrule

    rule serveA4Weight (a4.weightReadRequestValid);
        if (a4.weightReadRequestElementCount != 1) begin
            $display("IM2P CORE MULTIWIDTH: FAIL A4 weight logical count=%0d",
                     a4.weightReadRequestElementCount);
            $finish(1);
        end
        a4.putWeightReadResponse(
            a4.weightReadRequestTag, weightValues(a4.weightReadRequestAddress)
        );
        a4WeightRequests <= a4WeightRequests + 1;
    endrule

    rule serveA16Weight (a16.weightReadRequestValid);
        if (a16.weightReadRequestElementCount != 1) begin
            $display("IM2P CORE MULTIWIDTH: FAIL A16 weight logical count=%0d",
                     a16.weightReadRequestElementCount);
            $finish(1);
        end
        a16.putWeightReadResponse(
            a16.weightReadRequestTag, weightValues(a16.weightReadRequestAddress)
        );
        a16WeightRequests <= a16WeightRequests + 1;
    endrule

    rule rejectA4Scale (a4.scaleReadRequestValid);
        $display("IM2P CORE MULTIWIDTH: FAIL A4 bypass requested scale");
        $finish(1);
    endrule

    rule rejectA16Scale (a16.scaleReadRequestValid);
        $display("IM2P CORE MULTIWIDTH: FAIL A16 bypass requested scale");
        $finish(1);
    endrule

    rule serveA4Output (a4.outputWriteRequestValid);
        Vector#(2, Int#(32)) values = a4.outputWriteRequestValues;
        if (a4.outputWriteRequestAddress != a4OutputBase
                || a4.outputWriteRequestElementCount != 1
                || values[0] != -23) begin
            $display(
                "IM2P CORE MULTIWIDTH: FAIL A4 signed extrema output address=%0h count=%0d value=%0d expected=-23",
                a4.outputWriteRequestAddress, a4.outputWriteRequestElementCount,
                values[0]
            );
            $finish(1);
        end
        a4.putOutputWriteResponse(a4.outputWriteRequestTag);
        a4OutputRequests <= a4OutputRequests + 1;
    endrule

    rule serveA16Output (a16.outputWriteRequestValid);
        Vector#(2, Int#(32)) values = a16.outputWriteRequestValues;
        if (a16.outputWriteRequestAddress != a16OutputBase
                || a16.outputWriteRequestElementCount != 1
                || values[0] != -98303) begin
            $display(
                "IM2P CORE MULTIWIDTH: FAIL A16 signed extrema output address=%0h count=%0d value=%0d expected=-98303",
                a16.outputWriteRequestAddress, a16.outputWriteRequestElementCount,
                values[0]
            );
            $finish(1);
        end
        a16.putOutputWriteResponse(a16.outputWriteRequestTag);
        a16OutputRequests <= a16OutputRequests + 1;
    endrule

    rule finishA4 (!a4Done && a4.matmulDone);
        if (a4ActivationRequests != 2 || a4WeightRequests != 3
                || a4OutputRequests != 1) begin
            $display(
                "IM2P CORE MULTIWIDTH: FAIL A4 logical totals a=%0d w=%0d c=%0d",
                a4ActivationRequests, a4WeightRequests, a4OutputRequests
            );
            $finish(1);
        end
        a4.acknowledgeMatmul;
        a4Done <= True;
    endrule

    rule finishA16 (!a16Done && a16.matmulDone);
        if (a16ActivationRequests != 2 || a16WeightRequests != 3
                || a16OutputRequests != 1) begin
            $display(
                "IM2P CORE MULTIWIDTH: FAIL A16 logical totals a=%0d w=%0d c=%0d",
                a16ActivationRequests, a16WeightRequests, a16OutputRequests
            );
            $finish(1);
        end
        a16.acknowledgeMatmul;
        a16Done <= True;
    endrule

    rule pass (a4Done && a16Done);
        $display(
            "IM2P CORE MULTIWIDTH: PASS A4 offsets=0,2 extrema=-8,7 output=-23; A16 offsets=0,4 extrema=-32768,32767 output=-98303"
        );
        $finish(0);
    endrule
endmodule

endpackage
