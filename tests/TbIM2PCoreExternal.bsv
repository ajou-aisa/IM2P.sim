package TbIM2PCoreExternal;

import Vector::*;

import Types::*;
import HostMemoryTypes::*;
import WorkTypes::*;
import IM2PCore::*;

HostAddress activationBase = 64'h1000;
HostAddress weightBase = 64'h2000;
HostAddress scaleBase = 64'h3000;
HostAddress outputBase = 64'h4000;
HostStride rowStride = 16;

module mkTbIM2PCoreExternal(Empty);
    IM2PCoreIfc#(
        2, 1, 1, 4,
        Int#(8), Int#(8), Int#(16), Int#(32), Int#(8)
    ) core <- mkIM2PCore;

    Reg#(Bool) started <- mkReg(False);
    Reg#(UInt#(16)) watchdog <- mkReg(0);

    Reg#(Bool) activationPending <- mkReg(False);
    Reg#(HostRequestTag) activationTag <- mkRegU;
    Reg#(Bool) weightPending <- mkReg(False);
    Reg#(HostRequestTag) weightTag <- mkRegU;
    Reg#(Bool) scalePending <- mkReg(False);
    Reg#(HostRequestTag) scaleTag <- mkRegU;
    Reg#(ScaleBlockIndex) scaleBlock <- mkRegU;
    Reg#(Bool) outputPending <- mkReg(False);
    Reg#(HostRequestTag) outputTag <- mkRegU;

    Reg#(UInt#(4)) scaleRequests <- mkReg(0);
    Reg#(UInt#(4)) outputRequests <- mkReg(0);

    rule watch;
        watchdog <= watchdog + 1;
        if (watchdog == 4000) begin
            $display(
                "IM2P EXTERNAL: FAIL timeout state=%0d fragments=%0d outputs=%0d coreOutputs=%0d valid=%0d",
                core.matrixCoreState, core.matmulFragmentsCompleted,
                outputRequests, core.outputWriteRequests,
                core.outputWriteRequestValid
            );
            $finish(1);
        end
    endrule

    rule start (!started && core.idle);
        // DIM=2, K=6, scale block=4. Block zero has two RTL-owned
        // fragments and block one has one tail fragment.
        core.startMatmul(
            71, FullMatrix,
            activationBase, weightBase, scaleBase, outputBase,
            rowStride, rowStride, rowStride, rowStride,
            2, 2, 6, 2, 2, 0, 6, 4, 500, True, VectorExternal
        );
        started <= True;
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
        core.putWeightReadResponse(weightTag, replicate(1));
        weightPending <= False;
    endrule

    rule captureScale (core.scaleReadRequestValid && !scalePending);
        ScaleBlockIndex block = core.scaleRequestBlock;
        ScaleRequestKind kind = core.scaleRequestKind;
        HostAddress expectedAddress = scaleBase + zeroExtend(block) * rowStride;
        Bool legal = core.scaleReadRequestAddress == expectedAddress
            && core.scaleRequestContext == 500
            && core.scaleReadRequestElementCount == 2
            && ((scaleRequests == 0 && block == 0 && kind == ScaleDemand)
                || (scaleRequests == 1 && block == 1
                    && kind == ScalePrefetch));

        if (!legal) begin
            $display(
                "IM2P EXTERNAL: FAIL scale index=%0d block=%0d kind=%0d address=%0h",
                scaleRequests, block, kind, core.scaleReadRequestAddress
            );
            $finish(1);
        end

        scaleTag <= core.scaleReadRequestTag;
        scaleBlock <= block;
        scalePending <= True;
        scaleRequests <= scaleRequests + 1;
    endrule

    rule returnScale (scalePending);
        // Deliberately non-identity values: VectorExternal must ignore them.
        Vector#(2, Int#(8)) values = scaleBlock == 0
            ? cons(7, cons(-3, nil))
            : cons(11, cons(5, nil));
        core.putScaleReadResponse(scaleTag, values);
        scalePending <= False;
    endrule

    rule captureOutput (core.outputWriteRequestValid && !outputPending);
        UInt#(4) block = outputRequests / 2;
        UInt#(4) row = outputRequests % 2;
        HostAddress blockStride = 2 * rowStride;
        HostAddress expectedAddress = outputBase
            + zeroExtend(block) * blockStride
            + zeroExtend(row) * rowStride;
        Int#(32) expected = block == 0 ? 4 : 2;
        Vector#(2, Int#(32)) values = core.outputWriteRequestValues;

        if (core.outputWriteRequestAddress != expectedAddress
                || core.outputWriteRequestElementCount != 2
                || values[0] != expected || values[1] != expected) begin
            $display(
                "IM2P EXTERNAL: FAIL output=%0d address=%0h expectedAddress=%0h values=(%0d,%0d) expected=%0d",
                outputRequests, core.outputWriteRequestAddress,
                expectedAddress, values[0], values[1], expected
            );
            $finish(1);
        end

        outputTag <= core.outputWriteRequestTag;
        outputPending <= True;
        outputRequests <= outputRequests + 1;
    endrule

    rule returnOutput (outputPending);
        core.putOutputWriteResponse(outputTag);
        outputPending <= False;
    endrule

    rule finish (started && core.matmulDone);
        if (scaleRequests != 2 || outputRequests != 4
                || core.matmulFragmentsCompleted != 3
                || core.matmulWorksCompleted != 1
                || core.scaleReadRequests != 2) begin
            $display(
                "IM2P EXTERNAL: FAIL counters scale=%0d output=%0d fragments=%0d works=%0d reads=%0d",
                scaleRequests, outputRequests, core.matmulFragmentsCompleted,
                core.matmulWorksCompleted, core.scaleReadRequests
            );
            $finish(1);
        end
        $display("IM2P EXTERNAL: PASS");
        $finish(0);
    endrule
endmodule

endpackage
