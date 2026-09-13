package ScuPipeline;

import Assert::*;
import BRAMCore::*;
import Vector::*;
import Types::*;
import HostMemoryTypes::*;
import WorkTypes::*;
import IM2PCore::*;
import SynthA8W8D16::*;
import WindowBuffer::*;

typedef Vector#(16, Int#(8)) FullInput;
typedef Vector#(16, Int#(32)) FullOutput;

interface ScuPipelineIfc;
    method Action startLogical(Bool striped, MatrixExtent m, MatrixExtent n, MatrixExtent k,
        UInt#(5) kStrideLog, UInt#(5) nStrideLog, UInt#(3) windowKLog, UInt#(3) windowNLog,
        VectorOp op, MatmulJobId job, ScaleContext contextId);
    method Action publishLogical(MatrixExtent rowBegin, MatrixExtent rowCount);
    interface WindowRefillIfc aWindow;
    interface WindowRefillIfc wWindow;
    interface WindowRefillIfc sWindow;
    method Bool streamOutputValid;
    method HostAddress streamOutputAddress;
    method HostRequestTag streamOutputTag;
    method BoundedCount#(16) streamOutputCount;
    method FullOutput streamOutputValues;
    method Action consumeStreamOutput(HostRequestTag tag);
    method Action loadActivation(UInt#(12) word, FullInput values);
    method Action loadWeight(UInt#(9) word, FullInput values);
    method Action loadScale(UInt#(8) element, UInt#(32) value);
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
    method MatrixExtent publishedRows;
    method MatrixExtent completedRows;
    method UInt#(64) liveCycles;
    method UInt#(64) hostWaitCycles;
    method UInt#(64) overlapCycles;
    method UInt#(64) firstActivationCycle;
    method MatrixExtent firstActivationPublishedRows;
    method Bool done;
    method Bool protocolError;
    method UInt#(64) cycles;
    method UInt#(64) fragments;
    method UInt#(64) works;
    method UInt#(64) activationRequests;
    method UInt#(64) weightRequests;
    method UInt#(64) outputWrites;
    method UInt#(64) outputAcks;
    method Action requestOutput(UInt#(11) word);
    method Bool outputValid;
    method FullOutput outputResponse;
    method Action consumeOutput;
    method Action acknowledge;
endinterface

// One core owns the logical reduction across bounded A/W/S refills. The
// IM2P_BOUNDED_ONLY build removes legacy whole-matrix retention. Its legacy
// entry points are permanently not-ready; UART4 advertises the new contract.
// Without the define, the sealed IFR3 entry points and backing remain available.
(* synthesize *)
module mkScuPipeline(ScuPipelineIfc);
    let core <- mkSynthA8W8D16;
    WindowBufferIfc aBuffer <- mkWindowBuffer(64);
    WindowBufferIfc wBuffer <- mkWindowBuffer(256);
    WindowBufferIfc sBuffer <- mkWindowBuffer(32);
    Reg#(Bool) logicalMode <- mkReg(False);
    Reg#(Bool) logicalError <- mkReg(False);
    Reg#(MatrixExtent) columnsReg <- mkReg(0);
    Reg#(MatrixExtent) reductionReg <- mkReg(0);
    Reg#(UInt#(5)) kLogReg <- mkReg(0);
    Reg#(UInt#(5)) nLogReg <- mkReg(0);
    Reg#(VectorOp) logicalOp <- mkReg(VectorBypass);
    Reg#(Bool) errorScaleCarrier <- mkReg(False);
    Reg#(Bool) logicalAPending <- mkReg(False);
    Reg#(Bool) logicalWPending <- mkReg(False);
    Reg#(Bool) logicalSPending <- mkReg(False);
    Reg#(Bool) scaleWindowPending <- mkReg(False);
    Reg#(UInt#(32)) logicalSRow <- mkReg(0);
    Reg#(UInt#(32)) logicalSWord <- mkReg(0);
    Reg#(UInt#(2)) scaleQuad <- mkReg(0);
    Reg#(HostRequestTag) logicalSTag <- mkReg(0);
    Reg#(Vector#(16, UInt#(32))) logicalSValues <- mkRegU;
`ifndef IM2P_BOUNDED_ONLY
    BRAM_DUAL_PORT#(UInt#(12), FullInput) activations <- mkBRAMCore2(4096, False);
`endif
`ifndef IM2P_BOUNDED_ONLY
    BRAM_PORT#(UInt#(9), FullInput) weights <- mkBRAMCore1(512, False);
`endif
`ifndef IM2P_BOUNDED_ONLY
    BRAM_DUAL_PORT#(UInt#(11), FullOutput) outputs <- mkBRAMCore2(2048, False);
`endif
`ifndef IM2P_BOUNDED_ONLY
    BRAM_PORT#(UInt#(8), UInt#(32)) scales <- mkBRAMCore1(256, False);
`endif
    Reg#(Bool) scalePending <- mkReg(False);
    Reg#(HostRequestTag) scaleTag <- mkRegU;
    Reg#(UInt#(8)) scaleIndex <- mkRegU;
    Reg#(UInt#(4)) scaleLane <- mkRegU;
    Reg#(Vector#(16, UInt#(32))) scaleValues <- mkRegU;
    Reg#(UInt#(64)) sLimit <- mkReg(0);
    Reg#(Bool) invalidScaleUpload <- mkReg(False);
    Reg#(Bool) running <- mkReg(False);
    Reg#(Bool) stripedReg <- mkReg(False);
    Reg#(MatrixExtent) rowsReg <- mkReg(0);
    Reg#(MatrixExtent) publishedRowsReg <- mkReg(0);
    Reg#(MatrixExtent) completedRowsReg <- mkReg(0);
    Reg#(Bool) completionObserved <- mkReg(False);
    Reg#(Bool) firstActivationSeen <- mkReg(False);
    Reg#(UInt#(64)) firstActivationCycleReg <- mkReg(0);
    Reg#(MatrixExtent) firstActivationPublishedRowsReg <- mkReg(0);
    Reg#(Bool) doneReg <- mkReg(False);
    Reg#(Bool) errorA <- mkReg(False);
    Reg#(Bool) errorW <- mkReg(False);
    Reg#(Bool) errorS <- mkReg(False);
    Reg#(Bool) errorC <- mkReg(False);
    Reg#(Bool) errorPublication <- mkReg(False);
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

    // Logical addresses use checked power-of-two strides. Only the provider
    // maps these addresses to resident slots; core K/block state never resets
    // on a refill. Output stays in the core until transport accepts its tag.
    rule issueLogicalActivation (running && logicalMode && !logicalAPending
            && core.activationReadRequestValid && !logicalError);
        let address = core.activationReadRequestAddress;
        UInt#(32) row = truncate(address >> kLogReg);
        UInt#(32) column = truncate(address & ((1 << kLogReg) - 1));
        UInt#(32) count = zeroExtend(core.activationReadRequestElementCount);
        if (row >= rowsReg || column >= reductionReg || count == 0
                || count > reductionReg - column || (column & 15) != 0)
            errorA <= True;
        else begin
            aBuffer.request(row, column >> 4, publishedRowsReg, core.activationReadRequestTag);
            logicalAPending <= True;
            if (!firstActivationSeen) begin
                firstActivationSeen <= True;
                firstActivationCycleReg <= core.rtlCycleCount - startCycle;
                firstActivationPublishedRowsReg <= publishedRowsReg;
            end
        end
    endrule
    rule returnLogicalActivation (running && logicalMode && logicalAPending && aBuffer.responseValid);
        core.putActivationReadResponse(aBuffer.responseTag, unpack(aBuffer.response));
        aBuffer.consume;
        logicalAPending <= False;
    endrule
    rule issueLogicalWeight (running && logicalMode && !logicalWPending
            && core.weightReadRequestValid && !logicalError);
        let address = core.weightReadRequestAddress;
        UInt#(32) row = truncate(address >> nLogReg);
        UInt#(32) column = truncate(address & ((1 << nLogReg) - 1));
        UInt#(32) count = zeroExtend(core.weightReadRequestElementCount);
        if (row >= reductionReg || column >= columnsReg || count == 0
                || count > columnsReg - column || (column & 15) != 0)
            errorW <= True;
        else begin
            wBuffer.request(row, column >> 4, reductionReg, core.weightReadRequestTag);
            logicalWPending <= True;
        end
    endrule
    rule returnLogicalWeight (running && logicalMode && logicalWPending && wBuffer.responseValid);
        core.putWeightReadResponse(wBuffer.responseTag, unpack(wBuffer.response));
        wBuffer.consume;
        logicalWPending <= False;
    endrule
    rule issueLogicalScale (running && logicalMode && !logicalSPending
            && core.scaleReadRequestValid && !logicalError);
        let address = core.scaleReadRequestAddress;
        UInt#(6) shift = zeroExtend(nLogReg) + 2;
        UInt#(32) row = truncate(address >> shift);
        UInt#(32) column = truncate((address & ((1 << shift) - 1)) >> 2);
        UInt#(32) count = zeroExtend(core.scaleReadRequestElementCount);
        if (row >= ((reductionReg + 31) >> 5)
                || column >= columnsReg || count == 0
                || count > columnsReg - column || (address & 63) != 0)
            errorS <= True;
        else begin
            logicalSRow <= row;
            logicalSWord <= column >> 2;
            logicalSTag <= core.scaleReadRequestTag;
            logicalSPending <= True;
            scaleQuad <= 0;
        end
    endrule
    rule issueScaleWindow (logicalSPending && !scaleWindowPending);
        sBuffer.request(logicalSRow, logicalSWord + zeroExtend(scaleQuad),
            (reductionReg + 31) >> 5, logicalSTag);
        scaleWindowPending <= True;
    endrule
    rule returnScaleWindow (scaleWindowPending && sBuffer.responseValid);
        Vector#(4, UInt#(32)) quartet = unpack(sBuffer.response);
        Vector#(16, UInt#(32)) values = logicalSValues;
        Bool invalid = False;
        for (Integer lane = 0; lane < 4; lane = lane + 1) begin
            UInt#(4) index = (zeroExtend(scaleQuad) << 2) + fromInteger(lane);
            values[index] = quartet[lane];
            invalid = invalid || (logicalOp == VectorUnsignedMultiply && quartet[lane] > 65790)
                || (logicalOp == VectorLeftShift && quartet[lane] != 'h80000000 && quartet[lane] > 32767);
        end
        if (invalid) errorScaleCarrier <= True;
        logicalSValues <= values;
        sBuffer.consume;
        scaleWindowPending <= False;
        if (scaleQuad == 3) begin
            if (!invalid && !errorScaleCarrier) core.putScaleReadResponse(logicalSTag, values);
            logicalSPending <= False;
        end
        else scaleQuad <= scaleQuad + 1;
    endrule

`ifndef IM2P_BOUNDED_ONLY
    rule issueActivation (running && !logicalMode && !activationPending
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
    rule returnActivation (running && !logicalMode && activationPending);
        core.putActivationReadResponse(activationTag, activations.b.read);
        activationPending <= False;
    endrule
    rule issueWeight (running && !logicalMode && !weightPending && core.weightReadRequestValid);
        let address = core.weightReadRequestAddress;
        errorW <= errorW || address < 'h20000 || address >= wLimit
            || (address & 15) != 0 || core.weightReadRequestElementCount == 0;
        weights.put(False, truncate((core.weightReadRequestAddress - 'h20000) >> 4), ?);
        weightTag <= core.weightReadRequestTag;
        weightPending <= True;
    endrule
    rule returnWeight (running && !logicalMode && weightPending);
        core.putWeightReadResponse(weightTag, weights.read);
        weightPending <= False;
    endrule
    // A single 32-bit BRAM services sixteen lanes in order. Transport stages
    // metadata only; the SCU performs the unsigned multiplication itself.
    rule issueScale (running && !logicalMode && !scalePending && core.scaleReadRequestValid);
        let address = core.scaleReadRequestAddress;
        errorS <= errorS || address < 'h30000 || address >= sLimit
            || (address & 63) != 0 || core.scaleReadRequestElementCount == 0;
        UInt#(8) index = truncate((address - 'h30000) >> 2);
        scales.put(False, index, ?);
        scaleIndex <= index;
        scaleLane <= 0;
        scaleTag <= core.scaleReadRequestTag;
        scalePending <= True;
    endrule
    rule returnScale (running && !logicalMode && scalePending);
        Vector#(16, UInt#(32)) values = scaleValues;
        values[scaleLane] = scales.read;
        scaleValues <= values;
        if (scaleLane == 15) begin
            core.putScaleReadResponse(scaleTag, values);
            scalePending <= False;
        end
        else begin
            scaleIndex <= scaleIndex + 1;
            scaleLane <= scaleLane + 1;
            scales.put(False, scaleIndex + 1, ?);
        end
    endrule
    rule writeOutput (running && !logicalMode && core.outputWriteRequestValid);
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
`endif
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
`ifndef IM2P_BOUNDED_ONLY
    rule captureOutput (outputPending && !outputValidReg);
        outputReg <= outputs.b.read;
        outputPending <= False;
        outputValidReg <= True;
    endrule

`endif
`ifndef IM2P_BOUNDED_ONLY
    method Action loadActivation(UInt#(12) word, FullInput values) if (!doneReg && !logicalMode);
        dynamicAssert(!running || (stripedReg && zeroExtend(word >> 3) >= publishedRowsReg
            && zeroExtend(word >> 3) < rowsReg), "A write overlaps a published stripe");
        activations.a.put(True, word, values);
    endmethod
`else
    method Action loadActivation(UInt#(12) word, FullInput values) if (False);
        noAction;
    endmethod
`endif
`ifndef IM2P_BOUNDED_ONLY
    method Action loadWeight(UInt#(9) word, FullInput values) if (!running && !doneReg);
        weights.put(True, word, values);
    endmethod
`else
    method Action loadWeight(UInt#(9) word, FullInput values) if (False);
        noAction;
    endmethod
`endif
`ifndef IM2P_BOUNDED_ONLY
    method Action loadScale(UInt#(8) element, UInt#(32) value) if (!running && !doneReg);
        invalidScaleUpload <= invalidScaleUpload || value > 65790;
        scales.put(True, element, value);
    endmethod
`else
    method Action loadScale(UInt#(8) element, UInt#(32) value) if (False);
        noAction;
    endmethod
`endif
    method Action startLogical(Bool striped, MatrixExtent m, MatrixExtent n, MatrixExtent k,
        UInt#(5) kStrideLog, UInt#(5) nStrideLog, UInt#(3) windowKLog, UInt#(3) windowNLog,
        VectorOp op, MatmulJobId job, ScaleContext contextId)
            if (!running && !doneReg && !outputPending && !outputValidReg && !logicalError
                && !core.stripeCompletionValid && !completionObserved);
        // Match im2p_scu_logical_extent_valid: checked 64-bit virtual span,
        // including padded External block planes. Resident windows stay fixed.
        UInt#(6) outputShift = zeroExtend(nStrideLog) + 2;
        if (op == VectorExternal && kStrideLog > 5)
            outputShift = outputShift + zeroExtend(kStrideLog) - 5;
        UInt#(64) addressMax = maxBound;
        Bool valid = m > 0 && n > 0 && k > 0 && kStrideLog >= 4
            && nStrideLog >= 4 && zeroExtend(m) <= (addressMax >> outputShift)
            && k <= (1 << kStrideLog)
            && n <= (1 << nStrideLog) && windowKLog >= 5 && windowKLog <= 6
            && windowNLog >= 4 && windowNLog <= 6 && pack(op) <= 5;
        if (!valid) logicalError <= True;
        else begin
            HostStride aStride = 1 << kStrideLog;
            HostStride wStride = 1 << nStrideLog;
            core.startMatmul(job, striped ? AsyncStripes : FullMatrix, 0, 0, 0, 0,
                aStride, wStride, wStride << 2, wStride << 2, m, n, k,
                16, 16, 0, k, 32, contextId, False, op);
            aBuffer.configure(4, zeroExtend(windowKLog) - 4);
            wBuffer.configure(zeroExtend(windowKLog), zeroExtend(windowNLog) - 4);
            sBuffer.configure(zeroExtend(windowKLog) - 5, zeroExtend(windowNLog) - 2);
            logicalMode <= True;
            logicalOp <= op;
            kLogReg <= kStrideLog;
            nLogReg <= nStrideLog;
            rowsReg <= m;
            columnsReg <= n;
            reductionReg <= k;
            stripedReg <= striped;
            publishedRowsReg <= striped ? 0 : m;
            completedRowsReg <= 0;
            completionObserved <= False;
            firstActivationSeen <= False;
            firstActivationCycleReg <= 0;
            firstActivationPublishedRowsReg <= 0;
            startCycle <= core.rtlCycleCount;
            cyclesReg <= 0;
            errorA <= False; errorW <= False; errorS <= False; errorC <= False;
            running <= True;
        end
    endmethod
    method Action publishLogical(MatrixExtent rowBegin, MatrixExtent rowCount)
            if (running && stripedReg && logicalMode && !logicalError);
        if (rowBegin != publishedRowsReg || rowCount == 0 || rowBegin > rowsReg
                || rowCount > rowsReg - rowBegin)
            errorPublication <= True;
        else begin
            core.publishActivationStripe(rowBegin, rowCount, 1 << kLogReg);
            publishedRowsReg <= rowBegin + rowCount;
        end
    endmethod
`ifndef IM2P_BOUNDED_ONLY
    method Action start(Bool striped, UInt#(9) m, UInt#(6) n, UInt#(7) k,
                        MatmulJobId job, ScaleContext contextId)
            if (!invalidScaleUpload && !running && !doneReg && !outputPending && !outputValidReg
                && !core.stripeCompletionValid && !completionObserved);
        dynamicAssert(m > 0 && m <= 336 && n > 0 && n <= 48
            && (k == 32 || k == 64 || k == 96), "ScuPipeline shape/capacity");
        core.startMatmul(job, striped ? AsyncStripes : FullMatrix, 'h10000, 'h20000, 'h30000, 'h40000,
            128, 64, 256, 256, zeroExtend(m), zeroExtend(n), zeroExtend(k),
            16, 16, 0, zeroExtend(k), 32, contextId, False, VectorUnsignedMultiply);
        logicalMode <= False;
        aLimit <= 'h10000 + (zeroExtend(m) << 7);
        wLimit <= 'h20000 + (zeroExtend(k) << 6);
        UInt#(64) blocks = zeroExtend(k >> 5);
        cLimit <= 'h40000 + (zeroExtend(m) << 8);
        sLimit <= 'h30000 + (blocks << 8);
        startCycle <= core.rtlCycleCount;
        cyclesReg <= 0;
        errorA <= False; errorW <= False; errorS <= invalidScaleUpload; errorC <= False;
        stripedReg <= striped;
        rowsReg <= zeroExtend(m);
        publishedRowsReg <= striped ? 0 : zeroExtend(m);
        completedRowsReg <= 0;
        completionObserved <= False;
        firstActivationSeen <= False;
        firstActivationCycleReg <= 0;
        firstActivationPublishedRowsReg <= 0;
        running <= True;
    endmethod
`else
    method Action start(Bool striped, UInt#(9) m, UInt#(6) n, UInt#(7) k,
                        MatmulJobId job, ScaleContext contextId) if (False);
        noAction;
    endmethod
`endif
`ifndef IM2P_BOUNDED_ONLY
    method Action publish(UInt#(9) rowBegin, UInt#(9) rowCount)
            if (running && stripedReg && !logicalMode);
        dynamicAssert(zeroExtend(rowBegin) == publishedRowsReg && rowCount > 0
            && zeroExtend(rowBegin) <= rowsReg && zeroExtend(rowCount) <= rowsReg - zeroExtend(rowBegin),
            "ScuPipeline publication bounds/order");
        core.publishActivationStripe(zeroExtend(rowBegin), zeroExtend(rowCount), 128);
        publishedRowsReg <= zeroExtend(rowBegin) + zeroExtend(rowCount);
    endmethod
`else
    method Action publish(UInt#(9) rowBegin, UInt#(9) rowCount) if (False);
        noAction;
    endmethod
`endif
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
    method MatrixExtent publishedRows = publishedRowsReg;
    method MatrixExtent completedRows = completedRowsReg;
    method UInt#(64) liveCycles = running ? core.rtlCycleCount - startCycle : cyclesReg;
    method UInt#(64) hostWaitCycles = core.stripeHostWaitCycles;
    method UInt#(64) overlapCycles = core.overlapCycles;
    method UInt#(64) firstActivationCycle = firstActivationCycleReg;
    method MatrixExtent firstActivationPublishedRows = firstActivationPublishedRowsReg;
    method Bool done = doneReg;
    method Bool protocolError = invalidScaleUpload || errorA || errorW || errorS || errorC
        || logicalError || errorPublication || errorScaleCarrier
        || aBuffer.error || wBuffer.error || sBuffer.error;
    method UInt#(64) cycles = cyclesReg;
    method UInt#(64) fragments = core.matmulFragmentsCompleted;
    method UInt#(64) works = core.matmulWorksCompleted;
    method UInt#(64) activationRequests = core.activationReadRequests;
    method UInt#(64) weightRequests = core.weightReadRequests;
    method UInt#(64) outputWrites = core.outputWriteRequests;
    method UInt#(64) outputAcks = core.outputWriteResponses;
`ifndef IM2P_BOUNDED_ONLY
    method Action requestOutput(UInt#(11) word)
            if (!logicalMode && (running || doneReg) && !outputPending && !outputValidReg);
        outputs.b.put(False, word, ?);
        outputPending <= True;
    endmethod
`else
    method Action requestOutput(UInt#(11) word) if (False);
        noAction;
    endmethod
`endif
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
        logicalMode <= False;
    endmethod
    interface aWindow = aBuffer.refill;
    interface wWindow = wBuffer.refill;
    interface sWindow = sBuffer.refill;
    method Bool streamOutputValid = logicalMode && core.outputWriteRequestValid;
    method HostAddress streamOutputAddress if (logicalMode && core.outputWriteRequestValid);
        return core.outputWriteRequestAddress;
    endmethod
    method HostRequestTag streamOutputTag if (logicalMode && core.outputWriteRequestValid);
        return core.outputWriteRequestTag;
    endmethod
    method BoundedCount#(16) streamOutputCount if (logicalMode && core.outputWriteRequestValid);
        return core.outputWriteRequestElementCount;
    endmethod
    method FullOutput streamOutputValues if (logicalMode && core.outputWriteRequestValid);
        FullOutput values = core.outputWriteRequestValues;
        for (Integer lane = 0; lane < 16; lane = lane + 1)
            if (fromInteger(lane) >= core.outputWriteRequestElementCount) values[lane] = 0;
        return values;
    endmethod
    method Action consumeStreamOutput(HostRequestTag tag)
            if (logicalMode && core.outputWriteRequestValid && !logicalError);
        if (tag != core.outputWriteRequestTag) errorC <= True;
        else core.putOutputWriteResponse(tag);
    endmethod
endmodule
endpackage
