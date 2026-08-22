package TbIM2PCoreOutputAddressing;

import Vector::*;

import Types::*;
import HostMemoryTypes::*;
import WorkTypes::*;
import IM2PCore::*;

HostAddress activationBase = 64'h1000;
HostAddress weightBase = 64'h2000;
HostAddress scaleBase = 64'h3000;
HostAddress outputBase = 64'h4000;

// Raw/V2 output storage remains signed int32 even when the internal
// accumulator and provider transport lanes are Int#(64). Three DIM1 column
// tiles therefore have byte offsets 0, 4, and 8.
module mkTbIM2PCoreOutputAddressing(Empty);
    IM2PCoreIfc#(
        1, 1, 1, 4,
        Int#(8), Int#(8), Int#(16), Int#(64), Int#(8)
    ) core <- mkIM2PCore;

    Reg#(Bool) started <- mkReg(False);
    Reg#(UInt#(2)) outputRequests <- mkReg(0);
    Reg#(UInt#(12)) watchdog <- mkReg(0);

    rule watch;
        watchdog <= watchdog + 1;
        if (watchdog == 1000) begin
            $display(
                "IM2P CORE OUTPUT ADDRESSING: FAIL timeout outputs=%0d",
                outputRequests
            );
            $finish(1);
        end
    endrule

    rule start (!started && core.idle);
        core.startMatmul(
            81, FullMatrix,
            activationBase, weightBase, scaleBase, outputBase,
            1, 3, 1, 12,
            1, 3, 1, 1, 1, 0, 1, 1, 0, False, VectorBypass
        );
        started <= True;
    endrule

    rule serveActivation (core.activationReadRequestValid);
        core.putActivationReadResponse(
            core.activationReadRequestTag,
            replicate(1)
        );
    endrule

    rule serveWeight (core.weightReadRequestValid);
        core.putWeightReadResponse(core.weightReadRequestTag, replicate(1));
    endrule

    rule rejectScale (core.scaleReadRequestValid);
        $display("IM2P CORE OUTPUT ADDRESSING: FAIL bypass requested scale");
        $finish(1);
    endrule

    rule serveOutput (core.outputWriteRequestValid);
        HostAddress expectedAddress = outputBase
            + zeroExtend(outputRequests) * 4;
        Vector#(1, Int#(64)) values = core.outputWriteRequestValues;

        if (outputRequests > 2
                || core.outputWriteRequestAddress != expectedAddress
                || core.outputWriteRequestElementCount != 1
                || values[0] != 1) begin
            $display(
                "IM2P CORE OUTPUT ADDRESSING: FAIL index=%0d address=%0h expected=%0h value=%0d",
                outputRequests,
                core.outputWriteRequestAddress,
                expectedAddress,
                values[0]
            );
            $finish(1);
        end

        core.putOutputWriteResponse(core.outputWriteRequestTag);
        outputRequests <= outputRequests + 1;
    endrule

    rule finish (started && core.matmulDone);
        if (outputRequests != 3) begin
            $display(
                "IM2P CORE OUTPUT ADDRESSING: FAIL output count=%0d",
                outputRequests
            );
            $finish(1);
        end
        $display("IM2P CORE OUTPUT ADDRESSING: PASS offsets=0,4,8");
        $finish(0);
    endrule
endmodule

endpackage
