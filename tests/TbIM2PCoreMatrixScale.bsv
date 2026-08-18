package TbIM2PCoreMatrixScale;

import Vector::*;

import Types::*;
import HostMemoryTypes::*;
import WorkTypes::*;
import IM2PCore::*;

typedef enum {
    TbReuseStart,
    TbReuseRun,
    TbReuseDone,
    TbBypassStart,
    TbBypassRun,
    TbBypassDone,
    TbPrefetchStart,
    TbPrefetchRun,
    TbPrefetchDone,
    TbPass
} TbPhase deriving (Bits, Eq, FShow);

HostAddress activationBase = 64'h1000;
HostAddress weightBase = 64'h2000;
HostAddress scaleBase = 64'h3000;
HostAddress outputBase = 64'h4000;
HostStride activationStride = 8;
HostStride weightStride = 8;
HostStride scaleStride = 16;
HostStride outputStride = 16;

module mkTbIM2PCoreMatrixScale(Empty);
    IM2PCoreIfc#(
        2, 1, 1, 4,
        Int#(8), Int#(8), Int#(16), Int#(32), Int#(8)
    ) core <- mkIM2PCore;

    Reg#(TbPhase) phase <- mkReg(TbReuseStart);
    Reg#(UInt#(16)) watchdog <- mkReg(0);

    Reg#(Bool) activationPending <- mkReg(False);
    Reg#(HostRequestTag) activationTag <- mkRegU;
    Reg#(UInt#(8)) activationResponses <- mkReg(0);

    Reg#(Bool) weightPending <- mkReg(False);
    Reg#(HostRequestTag) weightTag <- mkRegU;
    Reg#(MatrixExtent) weightRow <- mkRegU;
    Reg#(MatrixExtent) weightColumn <- mkRegU;
    Reg#(UInt#(8)) weightResponses <- mkReg(0);

    Reg#(Bool) scalePending <- mkReg(False);
    Reg#(HostRequestTag) scaleTag <- mkRegU;
    Reg#(BoundedCount#(2)) scaleCount <- mkRegU;
    Reg#(ScaleBlockIndex) scaleBlock <- mkRegU;
    Reg#(ScaleRequestKind) scaleKind <- mkRegU;
    Reg#(Bool) scaleReleaseArmed <- mkReg(False);
    Reg#(UInt#(4)) phaseScaleRequests <- mkReg(0);
    Reg#(Bool) delayedReadyObserved <- mkReg(False);
    Reg#(Bool) prefetchResponseDuringExecution <- mkReg(False);

    Reg#(Bool) outputPending <- mkReg(False);
    Reg#(HostRequestTag) outputTag <- mkRegU;
    Reg#(UInt#(4)) phaseOutputs <- mkReg(0);


    rule watch;
        watchdog <= watchdog + 1;
        if (watchdog == 12000) begin
            $display(
                "IM2P MATRIX SCALE: FAIL timeout phase=", fshow(phase),
                " scale=%0d active=%0d state=%0d fragments=%0d a=%0d/%0d w=%0d/%0d",
                phaseScaleRequests, core.executionActive, core.matrixCoreState,
                core.matmulFragmentsCompleted,
                core.activationReadRequests, activationResponses,
                core.weightReadRequests, weightResponses
            );
            $finish(1);
        end
    endrule

    // The address-driven providers return A and W promptly. Every supplied
    // element is one, so each two-wide K fragment contributes two before scale.
    rule captureActivation (
        core.activationReadRequestValid && !activationPending
    );
        activationTag <= core.activationReadRequestTag;
        activationPending <= True;
    endrule

    rule returnActivation (activationPending);
        core.putActivationReadResponse(activationTag, replicate(1));
        activationPending <= False;
        activationResponses <= activationResponses + 1;
    endrule

    rule captureWeight (core.weightReadRequestValid && !weightPending);
        HostAddress offset = core.weightReadRequestAddress - weightBase;
        weightTag <= core.weightReadRequestTag;
        weightRow <= truncate(offset / weightStride);
        weightColumn <= truncate(offset % weightStride);
        weightPending <= True;
    endrule

    rule returnWeight (weightPending);
        Vector#(2, Int#(8)) values = replicate(0);
        for (Integer lane = 0; lane < 2; lane = lane + 1) begin
            if (weightRow == weightColumn + fromInteger(lane)) begin
                values[lane] = 1;
            end
        end
        core.putWeightReadResponse(weightTag, values);
        weightPending <= False;
        weightResponses <= weightResponses + 1;
    endrule

    // Requests are checked as tagged events, not against a fixed response
    // latency. The first demand is deliberately held after A/W are ready.
    rule captureScale (core.scaleReadRequestValid && !scalePending);
        HostAddress address = core.scaleReadRequestAddress;
        HostRequestTag tag = core.scaleReadRequestTag;
        ScaleBlockIndex block = core.scaleRequestBlock;
        ScaleRequestKind kind = core.scaleRequestKind;
        ScaleContext contextId = core.scaleRequestContext;
        BoundedCount#(2) count = core.scaleReadRequestElementCount;
        Bool legal = False;

        if (phase == TbReuseRun) begin
            if (phaseScaleRequests == 0) begin
                legal = address == scaleBase
                    && block == 0 && kind == ScaleDemand
                    && contextId == 100 && count == 2;
            end
            else if (phaseScaleRequests == 1) begin
                legal = address == scaleBase + 2
                    && block == 0 && kind == ScaleDemand
                    && contextId == 102 && count == 1;
            end
        end
        else if (phase == TbPrefetchRun) begin
            if (phaseScaleRequests == 0) begin
                legal = address == scaleBase + 2 * scaleStride
                    && block == 2 && kind == ScaleDemand
                    && contextId == 200 && count == 2;
            end
            else if (phaseScaleRequests == 1) begin
                legal = address == scaleBase + 3 * scaleStride
                    && block == 3 && kind == ScalePrefetch
                    && contextId == 200 && count == 2
                    && core.executionActive;
            end
        end

        if (!legal || (tag >> 32) != (phase == TbReuseRun ? 31 : 33)
                || truncate(tag) != block) begin
            $display(
                "IM2P MATRIX SCALE: FAIL request phase=", fshow(phase),
                " index=%0d address=%0h block=%0d kind=%0d context=%0d count=%0d tag=%0h active=%0d",
                phaseScaleRequests, address, block, kind, contextId,
                count, tag, core.executionActive
            );
            $finish(1);
        end

        scaleTag <= tag;
        scaleCount <= count;
        scaleBlock <= block;
        scaleKind <= kind;
        scalePending <= True;
        scaleReleaseArmed <= phaseScaleRequests != 0;
        phaseScaleRequests <= phaseScaleRequests + 1;
    endrule

    // The first demand response is released by this observed pending event,
    // not by elapsed time. A/W were necessarily ready when demand was issued.
    rule observeHeldDemand (
        scalePending && scaleKind == ScaleDemand && !scaleReleaseArmed
    );
        if (core.executionActive) begin
            $display("IM2P MATRIX SCALE: FAIL execution started before scale response");
            $finish(1);
        end
        delayedReadyObserved <= True;
        scaleReleaseArmed <= True;
    endrule

    rule returnScale (scalePending && scaleReleaseArmed);
        Vector#(2, Int#(8)) values = replicate(0);
        if (phase == TbReuseRun) begin
            if (scaleCount == 1) begin
                // Lane one is poison: the tail response must zero-pad it.
                values[0] = 5;
                values[1] = 99;
            end
            else begin
                values[0] = 2;
                values[1] = 3;
            end
        end
        else begin
            values = scaleBlock == 2 ? cons(2, cons(3, nil))
                                     : cons(5, cons(7, nil));
            if (scaleKind == ScalePrefetch && core.executionActive) begin
                prefetchResponseDuringExecution <= True;
            end
        end
        core.putScaleReadResponse(scaleTag, values);
        scalePending <= False;
    endrule

    rule rejectBypassScale (
        phase == TbBypassRun && core.scaleReadRequestValid
    );
        $display("IM2P MATRIX SCALE: FAIL bypass issued scale request");
        $finish(1);
    endrule

    rule captureOutput (core.outputWriteRequestValid && !outputPending);
        HostAddress address = core.outputWriteRequestAddress;
        BoundedCount#(2) count = core.outputWriteRequestElementCount;
        Vector#(2, Int#(32)) values = core.outputWriteRequestValues;
        Bool legal = False;

        if (phase == TbReuseRun) begin
            HostAddress columnBytes = (address - outputBase) % outputStride;
            if (columnBytes == 0 && count == 2) begin
                legal = values[0] == 2 && values[1] == 3;
            end
            else if (columnBytes == 8 && count == 1) begin
                legal = values[0] == 5 && values[1] == 0;
            end
        end
        else if (phase == TbBypassRun) begin
            legal = ((address - outputBase) % outputStride) == 0 && count == 1
                && values[0] == 1 && values[1] == 0;
        end
        else if (phase == TbPrefetchRun) begin
            HostAddress row = (address - outputBase) / outputStride;
            legal = row < 2 && count == 2
                && values[0] == 2 && values[1] == 3;
        end

        if (!legal) begin
            $display(
                "IM2P MATRIX SCALE: FAIL output phase=", fshow(phase),
                " address=%0h count=%0d values=(%0d,%0d)",
                address, count, values[0], values[1]
            );
            $finish(1);
        end

        outputTag <= core.outputWriteRequestTag;
        outputPending <= True;
        phaseOutputs <= phaseOutputs + 1;
    endrule

    rule returnOutput (outputPending);
        core.putOutputWriteResponse(outputTag);
        outputPending <= False;
    endrule

    // DIM=2, K=4, block=4: each J tile has two hardware fragments but only
    // one demand read. The second fragment must be an exact current-row hit.
    rule startReuse (phase == TbReuseStart && core.idle);
        core.startMatmul(
            31, FullMatrix,
            activationBase, weightBase, scaleBase, outputBase,
            activationStride, weightStride, scaleStride, outputStride,
            1, 3, 4, 2, 2, 0, 4, 4, 100, False, VectorMultiply
        );
        phase <= TbReuseRun;
    endrule

    rule finishReuse (phase == TbReuseRun && core.matmulDone);
        if (phaseScaleRequests != 2 || phaseOutputs != 2
                || core.scaleReadRequests != 2
                || core.scaleDemandRequests != 2
                || core.scalePrefetchRequests != 0
                || core.scaleDemandMisses != 2
                || core.scaleCurrentHits != 2
                || core.scaleNextHits != 0
                || core.scaleRowsReceived != 2
                || !delayedReadyObserved || core.scaleWaitCycles == 0) begin
            $display(
                "IM2P MATRIX SCALE: FAIL reuse counters reads=%0d demand=%0d prefetch=%0d miss=%0d current=%0d next=%0d rows=%0d wait=%0d",
                core.scaleReadRequests, core.scaleDemandRequests,
                core.scalePrefetchRequests, core.scaleDemandMisses,
                core.scaleCurrentHits, core.scaleNextHits,
                core.scaleRowsReceived, core.scaleWaitCycles
            );
            $finish(1);
        end
        core.acknowledgeMatmul;
        phase <= TbReuseDone;
    endrule

    rule prepareBypass (phase == TbReuseDone && core.idle);
        phaseScaleRequests <= 0;
        phaseOutputs <= 0;
        phase <= TbBypassStart;
    endrule

    rule startBypass (phase == TbBypassStart && core.idle);
        core.startMatmul(
            32, FullMatrix,
            activationBase, weightBase, scaleBase, outputBase,
            activationStride, weightStride, scaleStride, outputStride,
            1, 1, 4, 2, 2, 0, 4, 4, 100, False, VectorBypass
        );
        phase <= TbBypassRun;
    endrule

    rule finishBypass (phase == TbBypassRun && core.matmulDone);
        if (phaseScaleRequests != 0 || phaseOutputs != 1
                || core.scaleReadRequests != 0
                || core.scaleWaitCycles != 0) begin
            $display(
                "IM2P MATRIX SCALE: FAIL bypass requests=%0d reads=%0d wait=%0d",
                phaseScaleRequests, core.scaleReadRequests,
                core.scaleWaitCycles
            );
            $finish(1);
        end
        core.acknowledgeMatmul;
        phase <= TbBypassDone;
    endrule

    rule preparePrefetch (phase == TbBypassDone && core.idle);
        phaseScaleRequests <= 0;
        phaseOutputs <= 0;
        delayedReadyObserved <= False;
        phase <= TbPrefetchStart;
    endrule

    // Nonzero K origin makes the required addresses global blocks 2 and 3.
    // The block-3 response arrives during block-2 execution and must not alter
    // its immutable execution snapshot.
    rule startPrefetch (phase == TbPrefetchStart && core.idle);
        core.startMatmul(
            33, FullMatrix,
            activationBase, weightBase, scaleBase, outputBase,
            activationStride, weightStride, scaleStride, outputStride,
            2, 2, 4, 2, 2, 4, 8, 2, 200, False, VectorMultiply
        );
        phase <= TbPrefetchRun;
    endrule

    rule finishPrefetch (phase == TbPrefetchRun && core.matmulDone);
        if (phaseScaleRequests != 2 || phaseOutputs != 2
                || !prefetchResponseDuringExecution
                || core.scaleDemandRequests != 1
                || core.scalePrefetchRequests != 1
                || core.scaleDemandMisses != 1
                || core.scaleNextHits != 1
                || core.scaleRowsReceived != 2) begin
            $display(
                "IM2P MATRIX SCALE: FAIL prefetch requests=%0d outputs=%0d during=%0d demand=%0d prefetch=%0d miss=%0d next=%0d rows=%0d",
                phaseScaleRequests, phaseOutputs,
                prefetchResponseDuringExecution, core.scaleDemandRequests,
                core.scalePrefetchRequests, core.scaleDemandMisses,
                core.scaleNextHits, core.scaleRowsReceived
            );
            $finish(1);
        end
        core.acknowledgeMatmul;
        phase <= TbPrefetchDone;
    endrule

    rule finish (phase == TbPrefetchDone && core.idle);
        phase <= TbPass;
    endrule

    rule pass (phase == TbPass);
        $display("IM2P MATRIX SCALE: PASS");
        $finish(0);
    endrule
endmodule

endpackage
