package IM2PCore;

import Assert::*;
import Vector::*;

import Types::*;
import Arithmetic::*;
import ExecuteCmd::*;
import SystolicEngine::*;
import VectorUnit::*;
import Accumulator::*;
import Scale::*;
import HostMemoryTypes::*;
import WorkTypes::*;
import WorkScheduler::*;
import MatmulScheduler::*;

typedef enum {
    MatrixIdle,
    MatrixWaitWork,
    MatrixWaitFragment,
    MatrixLoadWeights,
    MatrixActivateBank,
    MatrixStartExecution,
    MatrixExecute,
    MatrixFinishFragment,
    MatrixWriteOutput,
    MatrixWaitSchedulerDone,
    MatrixDone
} MatrixCoreState deriving (Bits, Eq, FShow);

// InputSkew -> SystolicArray -> VectorUnit -> Accumulator를 연결하는 단일
// architectural Core다. Scale storage는 synthesis-time table이 아니라 현재와
// 다음 K-block row만 보관하는 context-tagged streaming state다.
interface IM2PCoreIfc#(
    numeric type arrayDim,
    numeric type peLatency,
    numeric type vectorLanes,
    numeric type accRows,
    type input_t,
    type weight_t,
    type product_t,
    type acc_t,
    type scale_t
);
    method Action configureScaling(
        UInt#(32) blockSize,
        UInt#(32) totalK,
        ScaleContext contextId
    );

    method Bool scaleRequestValid;
    method ScaleRowRequest scaleRequest;
    method ScaleContext scaleRequestContext;
    method ScaleBlockIndex scaleRequestBlock;
    method ScaleRequestKind scaleRequestKind;
    method Action putScaleRow(
        ScaleContext contextId,
        ScaleBlockIndex block,
        Vector#(arrayDim, scale_t) columnScales
    );

    method UInt#(64) scaleDemandRequests;
    method UInt#(64) scalePrefetchRequests;
    method UInt#(64) scaleCurrentHits;
    method UInt#(64) scaleNextHits;
    method UInt#(64) scaleDemandMisses;
    method UInt#(64) scaleRowsReceived;
    method UInt#(64) scaleWaitCycles;

    method Action startMatmul(
        MatmulJobId jobId,
        MatmulMode mode,
        HostAddress activationBase,
        HostAddress weightBase,
        HostAddress scaleBase,
        HostAddress outputBase,
        HostStride activationRowStride,
        HostStride weightRowStride,
        HostStride scaleRowStride,
        HostStride outputRowStride,
        MatrixExtent rowCount,
        MatrixExtent columnCount,
        MatrixExtent reductionCount,
        MatrixExtent kOrigin,
        MatrixExtent scaleTotalK,
        MatrixExtent scaleBlockSize,
        ScaleContext scaleContext,
        Bool accumulateFirstFragment,
        VectorOp vectorOp
    );
    method Action publishActivationStripe(
        MatrixExtent rowBegin,
        MatrixExtent rowCount
    );

    method Bool activationReadRequestValid;
    method HostRequestTag activationReadRequestTag;
    method HostAddress activationReadRequestAddress;
    method BoundedCount#(arrayDim) activationReadRequestElementCount;
    method Action putActivationReadResponse(
        HostRequestTag tag,
        Vector#(arrayDim, input_t) values
    );

    method Bool weightReadRequestValid;
    method HostRequestTag weightReadRequestTag;
    method HostAddress weightReadRequestAddress;
    method BoundedCount#(arrayDim) weightReadRequestElementCount;
    method Action putWeightReadResponse(
        HostRequestTag tag,
        Vector#(arrayDim, weight_t) values
    );

    method Bool scaleReadRequestValid;
    method HostRequestTag scaleReadRequestTag;
    method HostAddress scaleReadRequestAddress;
    method BoundedCount#(arrayDim) scaleReadRequestElementCount;
    method Action putScaleReadResponse(
        HostRequestTag tag,
        Vector#(arrayDim, scale_t) values
    );

    method Bool outputWriteRequestValid;
    method HostRequestTag outputWriteRequestTag;
    method HostAddress outputWriteRequestAddress;
    method BoundedCount#(arrayDim) outputWriteRequestElementCount;
    method Vector#(arrayDim, acc_t) outputWriteRequestValues;
    method Action putOutputWriteResponse(HostRequestTag tag);
    method Bool stripeCompletionValid;
    method UInt#(32) stripeCompletionId;
    method MatrixExtent stripeCompletionRowBegin;
    method MatrixExtent stripeCompletionRowCount;
    method UInt#(64) stripeCompletionContext;
    method Action acknowledgeStripeCompletion;

    method Bool matmulDone;
    method Action acknowledgeMatmul;
    method Bool activeWeightBank;
    method Bool inactiveWeightBankLoading;
    method Bool executionActive;
    method BoundedCount#(arrayDim) debugAcceptedRows;
    method BoundedCount#(arrayDim) debugConfiguredRows;
    method BoundedCount#(arrayDim) debugFirstColumnIssued;
    method BoundedCount#(arrayDim) debugFirstColumnCommitted;
    method Bool debugEngineResultValid;
    method Bool debugVectorBusy;
    method UInt#(8) matmulSchedulerState;
    method UInt#(8) workSchedulerState;
    method UInt#(8) matrixCoreState;
    method UInt#(64) matmulFragmentsCompleted;
    method UInt#(64) matmulWorksCompleted;
    method UInt#(64) stripesPublished;
    method UInt#(64) stripeRowsPublished;
    method UInt#(64) activationReadRequests;
    method UInt#(64) weightReadRequests;
    method UInt#(64) scaleReadRequests;
    method UInt#(64) outputWriteRequests;
    method UInt#(64) outputWriteResponses;
    method UInt#(64) weightBankActivations;
    method UInt#(64) activationWaitCycles;
    method UInt#(64) weightWaitCycles;
    method UInt#(64) outputWaitCycles;
    method UInt#(64) stripeHostWaitCycles;
    method UInt#(64) computeCycles;
    method UInt#(64) drainCycles;
    method UInt#(64) weightPreloadCycles;
    method UInt#(64) activationOverlapCycles;
    method UInt#(64) weightOverlapCycles;
    method UInt#(64) scaleOverlapCycles;
    method UInt#(64) overlapCycles;

    method Action beginWeightLoad;
    method Action loadWeightRow(
        BoundedIndex#(arrayDim) row,
        Vector#(arrayDim, weight_t) weights
    );
    method Bool weightsReady;

    method Action startExecution(
        ExecuteCmd#(arrayDim, accRows) command,
        UInt#(32) kStart,
        BoundedCount#(arrayDim) kCount
    );
    method Bool activationReady;
    method Action putActivationRow(
        Vector#(arrayDim, input_t) activations
    );

    method Bool idle;
    method Bool executionDone;
    method Action acknowledgeExecution;

    method Action writeAccumulatorRow(
        RowAddress#(accRows) row,
        Vector#(arrayDim, acc_t) values
    );
    method Vector#(arrayDim, acc_t) readAccumulatorRow(
        RowAddress#(accRows) row
    );
endinterface

module mkIM2PCore(IM2PCoreIfc#(
    arrayDim,
    peLatency,
    vectorLanes,
    accRows,
    input_t,
    weight_t,
    product_t,
    acc_t,
    scale_t
)) provisos (
    Mul#(vectorGroups, vectorLanes, arrayDim),
    Add#(1, vectorGroupsMinusOne, vectorGroups),
    Add#(1, vectorLanesMinusOne, vectorLanes),
    Add#(1, arrayDimMinusOne, arrayDim),
    Add#(1, peLatencyMinusOne, peLatency),
    Add#(1, accRowsMinusOne, accRows),
    Add#(arrayDim, freeAccumulatorRows, accRows),
    Add#(TLog#(arrayDim), accumulatorAddressPadding, TLog#(accRows)),
    Add#(
        boundedCountPadding,
        TLog#(arrayDim),
        TLog#(TAdd#(arrayDim, 1))
    ),
    Add#(
        TLog#(TAdd#(arrayDim, 1)),
        kCountToPositionPadding,
        32
    ),
    Add#(
        TLog#(arrayDim),
        indexToPositionPadding,
        32
    ),
    Bits#(input_t, inputBits),
    Bits#(weight_t, weightBits),
    Bits#(acc_t, accBits),
    Bits#(scale_t, scaleBits),
    Multiplier#(input_t, weight_t, product_t),
    ProductAccumulator#(product_t, acc_t),
    AccumulatorArithmetic#(acc_t),
    VectorScaleCapability#(input_t),
    VectorTransform#(input_t, acc_t, scale_t)
);
    SystolicEngineIfc#(
        arrayDim,
        peLatency,
        input_t,
        weight_t,
        product_t,
        acc_t
    ) engine <- mkSystolicEngine;

    VectorUnitIfc#(
        input_t,
        arrayDim,
        vectorLanes,
        acc_t,
        scale_t
    ) vectorUnit <- mkVectorUnit;

    AccumulatorIfc#(accRows, arrayDim, acc_t) accumulator <-
        mkAccumulator;

    MatmulSchedulerIfc#(arrayDim) matmulScheduler <- mkMatmulScheduler;
    WorkSchedulerIfc#(arrayDim) workScheduler <- mkWorkScheduler;

    Reg#(ExecuteCmd#(arrayDim, accRows)) commandReg <- mkRegU;
    Reg#(
        Vector#(arrayDim, RowAddress#(accRows))
    ) destinationRowAddressesReg <- mkRegU;

    Reg#(UInt#(32)) blockSizeReg <- mkReg(0);
    Reg#(UInt#(32)) totalKReg <- mkReg(0);
    Reg#(ScaleContext) scalingContextReg <- mkReg(0);
    Reg#(Bool) configurationValidReg <- mkReg(False);

    Reg#(Bool) currentScaleValidReg <- mkReg(False);
    Reg#(ScaleContext) currentScaleContextReg <- mkRegU;
    Reg#(ScaleBlockIndex) currentScaleBlockReg <- mkRegU;
    Reg#(Vector#(arrayDim, scale_t)) currentScaleRowReg <- mkRegU;

    Reg#(Bool) nextScaleValidReg <- mkReg(False);
    Reg#(ScaleContext) nextScaleContextReg <- mkRegU;
    Reg#(ScaleBlockIndex) nextScaleBlockReg <- mkRegU;
    Reg#(Vector#(arrayDim, scale_t)) nextScaleRowReg <- mkRegU;

    // A request remains visible until acknowledged; outstanding remains set
    // until the exactly tagged response is returned.
    Reg#(Bool) scaleRequestValidReg <- mkReg(False);
    Reg#(ScaleRowRequest) scaleRequestReg <- mkRegU;
    Reg#(Bool) scaleOutstandingReg <- mkReg(False);
    Reg#(ScaleRowRequest) outstandingRequestReg <- mkRegU;
    Reg#(Bool) prefetchNeededReg <- mkReg(False);

    // A scaled miss reserves the execution command but does not start the
    // systolic engine. The demand response supplies the immutable snapshot.
    Reg#(Bool) pendingExecutionReg <- mkReg(False);
    Reg#(ExecuteCmd#(arrayDim, accRows)) pendingCommandReg <- mkRegU;
    Reg#(ScaleContext) pendingContextReg <- mkRegU;
    Reg#(ScaleBlockIndex) pendingBlockReg <- mkRegU;

    Reg#(Vector#(arrayDim, scale_t)) executionScaleRowReg <- mkRegU;
    Reg#(Bool) executionUsesScaleReg <- mkReg(False);
    Reg#(Bool) scaledResultHeldReg <- mkReg(False);
    Reg#(BoundedCount#(arrayDim)) acceptedInputRowsReg <- mkReg(0);

    Reg#(UInt#(64)) scaleDemandRequestsReg <- mkReg(0);
    Reg#(UInt#(64)) scalePrefetchRequestsReg <- mkReg(0);
    Reg#(UInt#(64)) scaleCurrentHitsReg <- mkReg(0);
    Reg#(UInt#(64)) scaleNextHitsReg <- mkReg(0);
    Reg#(UInt#(64)) scaleDemandMissesReg <- mkReg(0);
    Reg#(UInt#(64)) scaleRowsReceivedReg <- mkReg(0);
    Reg#(UInt#(64)) scaleWaitCyclesReg <- mkReg(0);

    Reg#(MatrixCoreState) matrixStateReg <- mkReg(MatrixIdle);
    Reg#(MatmulWork#(arrayDim)) matrixWorkReg <- mkRegU;
    Reg#(MatmulJobId) matrixJobIdReg <- mkReg(0);
    Reg#(MatmulMode) matrixModeReg <- mkReg(FullMatrix);
    Reg#(HostAddress) matrixActivationBaseReg <- mkReg(0);
    Reg#(HostStride) matrixActivationStrideReg <- mkReg(0);
    Reg#(MatrixExtent) matrixKOriginReg <- mkReg(0);
    Reg#(MatrixExtent) matrixScaleTotalKReg <- mkReg(0);
    Reg#(MatrixExtent) matrixScaleBlockSizeReg <- mkReg(0);
    Reg#(ScaleContext) matrixScaleContextReg <- mkReg(0);
    Reg#(Bool) matrixAccumulateFirstReg <- mkReg(False);
    Reg#(UInt#(32)) nextStripeIdReg <- mkReg(0);

    Reg#(MatrixExtent) matrixFragmentKStartReg <- mkReg(0);
    Reg#(BoundedCount#(arrayDim)) matrixFragmentKCountReg <- mkReg(0);
    Reg#(Bool) matrixFragmentAccumulateReg <- mkReg(False);
    Reg#(Bool) matrixFragmentBankReg <- mkReg(False);

    Reg#(Bool) weightLoadingReg <- mkReg(False);
    Reg#(Bool) weightLoadIsNextReg <- mkReg(False);
    Reg#(Bool) weightLoadBankReg <- mkReg(False);
    Reg#(MatrixExtent) weightLoadKStartReg <- mkReg(0);
    Reg#(BoundedCount#(arrayDim)) weightLoadKCountReg <- mkReg(0);
    Reg#(BoundedCount#(arrayDim)) weightLoadRowReg <- mkReg(0);
    Reg#(Bool) preloadedFragmentValidReg <- mkReg(False);
    Reg#(Bool) preloadedFragmentBankReg <- mkReg(False);
    Reg#(MatrixExtent) preloadedFragmentKStartReg <- mkReg(0);
    Reg#(BoundedCount#(arrayDim)) preloadedFragmentKCountReg <- mkReg(0);

    // Matrix activations live in two physical fragment slots. The selector
    // names one physical slot current; promotion only flips the selector after
    // the engine has fully drained, so an outstanding response retains stable
    // slot identity across fragment boundaries.
    Vector#(2, Reg#(Bool)) activationSlotMetadataValid <- replicateM(mkReg(False));
    Vector#(2, Reg#(MatrixExtent)) activationSlotKStart <- replicateM(mkReg(0));
    Vector#(2, Reg#(BoundedCount#(arrayDim))) activationSlotKCount <-
        replicateM(mkReg(0));
    Vector#(2, Reg#(BoundedCount#(arrayDim))) activationSlotRequestRow <-
        replicateM(mkReg(0));
    Vector#(
        2,
        Vector#(arrayDim, Reg#(Vector#(arrayDim, input_t)))
    ) activationSlotRows <- replicateM(replicateM(mkRegU));
    Vector#(2, Vector#(arrayDim, Reg#(Bool))) activationSlotRowValid <-
        replicateM(replicateM(mkReg(False)));
    Reg#(Bool) currentActivationSlotReg <- mkReg(False);
    Reg#(BoundedCount#(arrayDim)) activationFeedRowReg <- mkReg(0);

    Reg#(Bool) activationRequestValidReg <- mkReg(False);
    Reg#(HostRequestTag) activationRequestTagReg <- mkRegU;
    Reg#(HostAddress) activationRequestAddressReg <- mkRegU;
    Reg#(Bool) activationRequestSlotReg <- mkRegU;
    Reg#(BoundedIndex#(arrayDim)) activationRequestRowReg <- mkRegU;
    Reg#(MatrixExtent) activationRequestKStartReg <- mkRegU;
    Reg#(BoundedCount#(arrayDim)) activationRequestKCountReg <- mkRegU;
    Reg#(Bool) activationResponsePendingReg <- mkReg(False);
    Reg#(Bool) activationResponseSlotReg <- mkRegU;
    Reg#(BoundedIndex#(arrayDim)) activationResponseRowReg <- mkRegU;
    Reg#(Vector#(arrayDim, input_t)) activationResponseValuesReg <- mkRegU;
    Reg#(BoundedCount#(arrayDim)) activationRowsAcceptedReg <- mkReg(0);

    Reg#(Bool) weightRequestValidReg <- mkReg(False);
    Reg#(HostRequestTag) weightRequestTagReg <- mkRegU;
    Reg#(HostAddress) weightRequestAddressReg <- mkRegU;

    Reg#(Bool) outputRequestValidReg <- mkReg(False);
    Reg#(HostRequestTag) outputRequestTagReg <- mkRegU;
    Reg#(HostAddress) outputRequestAddressReg <- mkRegU;
    Reg#(BoundedIndex#(arrayDim)) outputRowReg <- mkReg(0);

    Reg#(UInt#(32)) activationTagSequenceReg <- mkReg(0);
    Reg#(UInt#(32)) weightTagSequenceReg <- mkReg(0);
    Reg#(UInt#(32)) outputTagSequenceReg <- mkReg(0);
    Reg#(UInt#(64)) matmulFragmentsCompletedReg <- mkReg(0);
    Reg#(UInt#(64)) matmulWorksCompletedReg <- mkReg(0);
    Reg#(UInt#(64)) stripesPublishedReg <- mkReg(0);
    Reg#(UInt#(64)) stripeRowsPublishedReg <- mkReg(0);
    Reg#(UInt#(64)) activationReadRequestsReg <- mkReg(0);
    Reg#(UInt#(64)) weightReadRequestsReg <- mkReg(0);
    Reg#(UInt#(64)) scaleReadRequestsReg <- mkReg(0);
    Reg#(UInt#(64)) outputWriteRequestsReg <- mkReg(0);
    Reg#(UInt#(64)) outputWriteResponsesReg <- mkReg(0);
    Reg#(UInt#(64)) weightBankActivationsReg <- mkReg(0);
    Reg#(UInt#(64)) activationWaitCyclesReg <- mkReg(0);
    Reg#(UInt#(64)) weightWaitCyclesReg <- mkReg(0);
    Reg#(UInt#(64)) outputWaitCyclesReg <- mkReg(0);
    Reg#(UInt#(64)) stripeHostWaitCyclesReg <- mkReg(0);
    Reg#(UInt#(64)) computeCyclesReg <- mkReg(0);
    Reg#(UInt#(64)) drainCyclesReg <- mkReg(0);
    Reg#(UInt#(64)) weightPreloadCyclesReg <- mkReg(0);
    Reg#(UInt#(64)) activationOverlapCyclesReg <- mkReg(0);
    Reg#(UInt#(64)) weightOverlapCyclesReg <- mkReg(0);
    Reg#(UInt#(64)) scaleOverlapCyclesReg <- mkReg(0);
    Reg#(UInt#(64)) overlapCyclesReg <- mkReg(0);

    rule countMatrixWorkCycles (
        matrixStateReg != MatrixIdle && matrixStateReg != MatrixDone
    );
        Bool activationOverlap = engine.active && activationRequestValidReg;
        Bool weightOverlap = engine.active && weightRequestValidReg;
        Bool scaleOverlap = engine.active && scaleRequestValidReg;
        if (activationRequestValidReg) begin
            activationWaitCyclesReg <= activationWaitCyclesReg + 1;
        end
        if (weightRequestValidReg) begin
            weightWaitCyclesReg <= weightWaitCyclesReg + 1;
        end
        if (outputRequestValidReg) begin
            outputWaitCyclesReg <= outputWaitCyclesReg + 1;
        end
        if (matrixStateReg == MatrixWaitSchedulerDone
                && matmulScheduler.active
                && !matmulScheduler.workValid) begin
            stripeHostWaitCyclesReg <= stripeHostWaitCyclesReg + 1;
        end
        if (matrixStateReg == MatrixExecute) begin
            computeCyclesReg <= computeCyclesReg + 1;
            if (engine.acceptedRows == engine.configuredRows && !engine.done) begin
                drainCyclesReg <= drainCyclesReg + 1;
            end
        end
        if (engine.active && weightLoadingReg) begin
            weightPreloadCyclesReg <= weightPreloadCyclesReg + 1;
        end
        if (activationOverlap) begin
            activationOverlapCyclesReg <= activationOverlapCyclesReg + 1;
        end
        if (weightOverlap) begin
            weightOverlapCyclesReg <= weightOverlapCyclesReg + 1;
        end
        if (scaleOverlap) begin
            scaleOverlapCyclesReg <= scaleOverlapCyclesReg + 1;
        end
        if (activationOverlap || weightOverlap || scaleOverlap) begin
            overlapCyclesReg <= overlapCyclesReg + 1;
        end
    endrule

    function Bit#(1) activationSlotIndex(Bool slot);
        return slot ? 1 : 0;
    endfunction

    function HostRequestTag matrixTag(UInt#(32) tagIndex);
        return unpack({ pack(matrixJobIdReg), pack(tagIndex) });
    endfunction

    function HostAddress matrixRowAddress(
        HostAddress base,
        MatrixExtent row,
        HostStride stride
    );
        UInt#(96) offset = zeroExtend(row) * zeroExtend(stride);
        return base + truncate(offset);
    endfunction

    function HostAddress matrixElementAddress(
        HostAddress base,
        MatrixExtent element,
        ElementBytes bytes
    );
        UInt#(72) offset = zeroExtend(element) * zeroExtend(bytes);
        return base + truncate(offset);
    endfunction

    rule countScaleWait (pendingExecutionReg);
        scaleWaitCyclesReg <= scaleWaitCyclesReg + 1;
    endrule

    rule startPendingExecution (
        pendingExecutionReg
        && (
            (
                currentScaleValidReg
                && currentScaleContextReg == pendingContextReg
                && currentScaleBlockReg == pendingBlockReg
            )
            || (
                nextScaleValidReg
                && nextScaleContextReg == pendingContextReg
                && nextScaleBlockReg == pendingBlockReg
            )
        )
        && engine.idle
        && vectorUnit.ready
        && (
            matrixStateReg == MatrixIdle
            || activationSlotRowValid[
                activationSlotIndex(currentActivationSlotReg)
            ][0]
        )
    );
        Bool useNext = !currentScaleValidReg
            || currentScaleContextReg != pendingContextReg
            || currentScaleBlockReg != pendingBlockReg;
        Vector#(arrayDim, scale_t) selectedRow =
            useNext ? nextScaleRowReg : currentScaleRowReg;

        if (useNext) begin
            currentScaleValidReg <= True;
            currentScaleContextReg <= nextScaleContextReg;
            currentScaleBlockReg <= nextScaleBlockReg;
            currentScaleRowReg <= nextScaleRowReg;
            nextScaleValidReg <= False;
            prefetchNeededReg <= True;
            scaleNextHitsReg <= scaleNextHitsReg + 1;
        end

        executionScaleRowReg <= selectedRow;
        executionUsesScaleReg <= True;
        commandReg <= pendingCommandReg;
        acceptedInputRowsReg <= 0;
        pendingExecutionReg <= False;
        engine.start(pendingCommandReg.rowCount);
    endrule

    // Prefetch is deliberately issued only after an execution has started.
    // This gives a startExecution demand miss priority while preserving the
    // one-outstanding invariant and full-drain execution scheduling.
    rule issueScalePrefetch (
        prefetchNeededReg
        && !scaleOutstandingReg
        && !scaleRequestValidReg
        && !engine.idle
    );
        ScaleBlockIndex finalBlock =
            (totalKReg - 1) / blockSizeReg;
        prefetchNeededReg <= False;

        if (currentScaleBlockReg < finalBlock) begin
            ScaleRowRequest request = ScaleRowRequest {
                contextId: currentScaleContextReg,
                block: currentScaleBlockReg + 1,
                kind: ScalePrefetch
            };
            scaleRequestReg <= request;
            outstandingRequestReg <= request;
            scaleRequestValidReg <= True;
            scaleOutstandingReg <= True;
            scalePrefetchRequestsReg <= scalePrefetchRequestsReg + 1;
            if (matrixStateReg != MatrixIdle) begin
                scaleReadRequestsReg <= scaleReadRequestsReg + 1;
            end
        end
    endrule

    // This path is used only when a demand miss arrived while an earlier
    // prefetch was still outstanding.
    rule issueDeferredScaleDemand (
        pendingExecutionReg
        && !scaleOutstandingReg
        && !scaleRequestValidReg
        && !(
            currentScaleValidReg
            && currentScaleContextReg == pendingContextReg
            && currentScaleBlockReg == pendingBlockReg
        )
        && !(
            nextScaleValidReg
            && nextScaleContextReg == pendingContextReg
            && nextScaleBlockReg == pendingBlockReg
        )
    );
        ScaleRowRequest request = ScaleRowRequest {
            contextId: pendingContextReg,
            block: pendingBlockReg,
            kind: ScaleDemand
        };
        scaleRequestReg <= request;
        outstandingRequestReg <= request;
        scaleRequestValidReg <= True;
        scaleOutstandingReg <= True;
        scaleDemandRequestsReg <= scaleDemandRequestsReg + 1;
        if (matrixStateReg != MatrixIdle) begin
            scaleReadRequestsReg <= scaleReadRequestsReg + 1;
        end
    endrule

    rule acquireMatrixWork (
        matrixStateReg == MatrixWaitWork
        && matmulScheduler.workValid
        && !weightLoadingReg
        && !pendingExecutionReg
    );
        MatmulWork#(arrayDim) work = matmulScheduler.work;
        Bool usesScale = vectorOpUsesScale(work.vectorOp);
        ScaleContext workScaleContext = matrixScaleContextReg
            + zeroExtend(work.jStart);

        matrixWorkReg <= work;
        matmulScheduler.acceptWork;
        workScheduler.start(
            matrixKOriginReg,
            work.reductionCount,
            matrixScaleBlockSizeReg,
            usesScale,
            matrixAccumulateFirstReg
        );

        if (usesScale) begin
            blockSizeReg <= matrixScaleBlockSizeReg;
            totalKReg <= matrixScaleTotalKReg;
            scalingContextReg <= workScaleContext;
            configurationValidReg <= True;
            currentScaleValidReg <= False;
            nextScaleValidReg <= False;
            prefetchNeededReg <= False;
        end

        preloadedFragmentValidReg <= False;
        activationSlotMetadataValid[0] <= False;
        activationSlotMetadataValid[1] <= False;
        currentActivationSlotReg <= False;
        activationRequestValidReg <= False;
        for (Integer slot = 0; slot < 2; slot = slot + 1) begin
            for (Integer row = 0; row < valueOf(arrayDim); row = row + 1) begin
                activationSlotRowValid[slot][row] <= False;
            end
        end
        matrixStateReg <= MatrixWaitFragment;
    endrule

    rule beginMatrixFragment (
        matrixStateReg == MatrixWaitFragment
        && workScheduler.fragmentValid
        && !weightLoadingReg
    );
        MatrixExtent kStart = workScheduler.fragmentKStart;
        BoundedCount#(arrayDim) kCount = workScheduler.fragmentKCount;
        Bool usePreload = preloadedFragmentValidReg
            && preloadedFragmentKStartReg == kStart
            && preloadedFragmentKCountReg == kCount;
        Bool targetBank = usePreload
            ? preloadedFragmentBankReg
            : !engine.activeWeightBank;

        dynamicAssert(kCount > 0, "matrix K fragment must be positive");
        matrixFragmentKStartReg <= kStart;
        matrixFragmentKCountReg <= kCount;
        matrixFragmentAccumulateReg <= workScheduler.fragmentAccumulate;
        matrixFragmentBankReg <= targetBank;
        workScheduler.acceptFragment;

        Bit#(1) currentSlot = activationSlotIndex(currentActivationSlotReg);
        Bit#(1) nextSlot = activationSlotIndex(!currentActivationSlotReg);
        Bool currentSlotMatches = activationSlotMetadataValid[currentSlot]
            && activationSlotKStart[currentSlot] == kStart
            && activationSlotKCount[currentSlot] == kCount;

        if (!currentSlotMatches) begin
            activationSlotMetadataValid[currentSlot] <= True;
            activationSlotKStart[currentSlot] <= kStart;
            activationSlotKCount[currentSlot] <= kCount;
            activationSlotRequestRow[currentSlot] <= 0;
            for (Integer row = 0; row < valueOf(arrayDim); row = row + 1) begin
                activationSlotRowValid[currentSlot][row] <= False;
            end
        end

        if (workScheduler.hasNextFragment) begin
            MatrixExtent nextKStart = workScheduler.nextFragmentKStart;
            BoundedCount#(arrayDim) nextKCount =
                workScheduler.nextFragmentKCount;
            Bool nextSlotMatches = activationSlotMetadataValid[nextSlot]
                && activationSlotKStart[nextSlot] == nextKStart
                && activationSlotKCount[nextSlot] == nextKCount;
            if (!nextSlotMatches) begin
                activationSlotMetadataValid[nextSlot] <= True;
                activationSlotKStart[nextSlot] <= nextKStart;
                activationSlotKCount[nextSlot] <= nextKCount;
                activationSlotRequestRow[nextSlot] <= 0;
                for (Integer row = 0; row < valueOf(arrayDim); row = row + 1) begin
                    activationSlotRowValid[nextSlot][row] <= False;
                end
            end
        end
        else begin
            activationSlotMetadataValid[nextSlot] <= False;
        end

        if (usePreload) begin
            preloadedFragmentValidReg <= False;
            matrixStateReg <= MatrixActivateBank;
        end
        else begin
            dynamicAssert(
                targetBank != engine.activeWeightBank,
                "matrix preload must target the inactive bank"
            );
            engine.beginWeightLoadBank(targetBank);
            weightLoadingReg <= True;
            weightLoadIsNextReg <= False;
            weightLoadBankReg <= targetBank;
            weightLoadKStartReg <= kStart;
            weightLoadKCountReg <= kCount;
            weightLoadRowReg <= 0;
            matrixStateReg <= MatrixLoadWeights;
        end
    endrule

    rule issueMatrixWeightRequest (
        weightLoadingReg
        && !weightRequestValidReg
        && weightLoadRowReg < weightLoadKCountReg
    );
        MatrixExtent localK = weightLoadKStartReg
            - matrixKOriginReg
            + zeroExtend(weightLoadRowReg);
        HostAddress rowBase = matrixRowAddress(
            matrixWorkReg.weightBase,
            localK,
            matrixWorkReg.weightRowStride
        );
        HostRequestTag tag = matrixTag(weightTagSequenceReg);

        weightRequestTagReg <= tag;
        weightRequestAddressReg <= rowBase;
        weightRequestValidReg <= True;
        weightTagSequenceReg <= weightTagSequenceReg + 1;
        weightReadRequestsReg <= weightReadRequestsReg + 1;
    endrule

    rule padMatrixWeightRows (
        weightLoadingReg
        && !weightRequestValidReg
        && weightLoadRowReg >= weightLoadKCountReg
        && weightLoadRowReg < fromInteger(valueOf(arrayDim))
    );
        dynamicAssert(
            weightLoadBankReg != engine.activeWeightBank,
            "weight padding must target the inactive bank"
        );
        engine.loadWeightRowBank(
            weightLoadBankReg,
            truncate(weightLoadRowReg),
            replicate(unpack(0))
        );
        weightLoadRowReg <= weightLoadRowReg + 1;
    endrule

    rule finishCurrentMatrixWeightLoad (
        matrixStateReg == MatrixLoadWeights
        && weightLoadingReg
        && !weightLoadIsNextReg
        && !weightRequestValidReg
        && weightLoadRowReg == fromInteger(valueOf(arrayDim))
    );
        weightLoadingReg <= False;
        matrixStateReg <= MatrixActivateBank;
    endrule

    rule finishNextMatrixWeightLoad (
        matrixStateReg != MatrixIdle
        && matrixStateReg != MatrixLoadWeights
        && weightLoadingReg
        && weightLoadIsNextReg
        && !weightRequestValidReg
        && weightLoadRowReg == fromInteger(valueOf(arrayDim))
    );
        weightLoadingReg <= False;
        preloadedFragmentValidReg <= True;
        preloadedFragmentBankReg <= weightLoadBankReg;
        preloadedFragmentKStartReg <= weightLoadKStartReg;
        preloadedFragmentKCountReg <= weightLoadKCountReg;
    endrule

    rule activateMatrixWeightBank (
        matrixStateReg == MatrixActivateBank
        && engine.idle
        && engine.weightsReadyBank(matrixFragmentBankReg)
    );
        engine.activateWeightBank(matrixFragmentBankReg);
        weightBankActivationsReg <= weightBankActivationsReg + 1;
        matrixStateReg <= MatrixStartExecution;
    endrule

    rule requestMatrixExecution (
        matrixStateReg == MatrixStartExecution
        && engine.idle
        && vectorUnit.ready
        && engine.weightsReady
        && !pendingExecutionReg
        && activationSlotRowValid[
            activationSlotIndex(currentActivationSlotReg)
        ][0]
    );
        ExecuteCmd#(arrayDim, accRows) command = ExecuteCmd {
            accumulatorBaseRow: 0,
            rowCount: truncate(matrixWorkReg.iCount),
            accumulate: matrixFragmentAccumulateReg,
            vectorOp: matrixWorkReg.vectorOp
        };
        Bool operationNeedsScale = vectorUnit.scalingSupported
            && vectorOpUsesScale(command.vectorOp);
        UInt#(32) safeBlockSize = blockSizeReg == 0 ? 1 : blockSizeReg;
        UInt#(32) kCountWide = zeroExtend(matrixFragmentKCountReg);
        UInt#(32) remainingK = matrixFragmentKStartReg < totalKReg
            ? totalKReg - matrixFragmentKStartReg
            : 0;
        UInt#(32) blockOffset = matrixFragmentKStartReg % safeBlockSize;
        UInt#(32) blockRemaining = safeBlockSize - blockOffset;
        ScaleBlockIndex selectedBlock =
            matrixFragmentKStartReg / safeBlockSize;

        if (operationNeedsScale) begin
            dynamicAssert(
                configurationValidReg,
                "scaled matrix execution requires a scale snapshot"
            );
            dynamicAssert(
                kCountWide <= remainingK && kCountWide <= blockRemaining,
                "matrix K fragment crosses configured scale bounds"
            );
            Bool currentHit = currentScaleValidReg
                && currentScaleContextReg == scalingContextReg
                && currentScaleBlockReg == selectedBlock;
            Bool nextHit = nextScaleValidReg
                && nextScaleContextReg == scalingContextReg
                && nextScaleBlockReg == selectedBlock;

            if (currentHit) begin
                executionScaleRowReg <= currentScaleRowReg;
                executionUsesScaleReg <= True;
                commandReg <= command;
                acceptedInputRowsReg <= 0;
                scaleCurrentHitsReg <= scaleCurrentHitsReg + 1;
                engine.start(command.rowCount);
            end
            else if (nextHit) begin
                executionScaleRowReg <= nextScaleRowReg;
                executionUsesScaleReg <= True;
                commandReg <= command;
                acceptedInputRowsReg <= 0;
                currentScaleValidReg <= True;
                currentScaleContextReg <= nextScaleContextReg;
                currentScaleBlockReg <= nextScaleBlockReg;
                currentScaleRowReg <= nextScaleRowReg;
                nextScaleValidReg <= False;
                scaleNextHitsReg <= scaleNextHitsReg + 1;
                engine.start(command.rowCount);
            end
            else begin
                pendingExecutionReg <= True;
                pendingCommandReg <= command;
                pendingContextReg <= scalingContextReg;
                pendingBlockReg <= selectedBlock;
                scaleDemandMissesReg <= scaleDemandMissesReg + 1;

                if (!scaleOutstandingReg && !scaleRequestValidReg) begin
                    ScaleRowRequest request = ScaleRowRequest {
                        contextId: scalingContextReg,
                        block: selectedBlock,
                        kind: ScaleDemand
                    };
                    scaleRequestReg <= request;
                    outstandingRequestReg <= request;
                    scaleRequestValidReg <= True;
                    scaleOutstandingReg <= True;
                    scaleDemandRequestsReg <= scaleDemandRequestsReg + 1;
                    scaleReadRequestsReg <= scaleReadRequestsReg + 1;
                end
            end
        end
        else begin
            executionScaleRowReg <= replicate(unpack(0));
            executionUsesScaleReg <= False;
            commandReg <= command;
            acceptedInputRowsReg <= 0;
            engine.start(command.rowCount);
        end
    endrule

    rule observeMatrixExecutionStart (
        matrixStateReg == MatrixStartExecution && engine.active
    );
        activationRowsAcceptedReg <= 0;
        activationFeedRowReg <= 0;
        matrixStateReg <= MatrixExecute;

        if (workScheduler.hasNextFragment && !weightLoadingReg) begin
            Bool nextBank = !engine.activeWeightBank;
            dynamicAssert(
                nextBank != engine.activeWeightBank,
                "next fragment preload selected active bank"
            );
            engine.beginWeightLoadBank(nextBank);
            weightLoadingReg <= True;
            weightLoadIsNextReg <= True;
            weightLoadBankReg <= nextBank;
            weightLoadKStartReg <= workScheduler.nextFragmentKStart;
            weightLoadKCountReg <= workScheduler.nextFragmentKCount;
            weightLoadRowReg <= 0;
        end
    endrule

    // Current rows may be fetched while weights load. Once every current row
    // has been requested, execution gives the same channel to the published
    // next fragment. A request remains visible until its tagged response.
    rule issueMatrixActivationRequest (
        matrixStateReg != MatrixIdle
        && matrixStateReg != MatrixWaitWork
        && matrixStateReg != MatrixDone
        && !activationRequestValidReg
    );
        Bit#(1) currentSlot = activationSlotIndex(currentActivationSlotReg);
        Bit#(1) nextSlot = activationSlotIndex(!currentActivationSlotReg);
        BoundedCount#(arrayDim) requiredRows = truncate(matrixWorkReg.iCount);
        Bool requestCurrent = activationSlotMetadataValid[currentSlot]
            && activationSlotRequestRow[currentSlot] < requiredRows;
        Bool requestNext = matrixStateReg == MatrixExecute
            && activationSlotMetadataValid[nextSlot]
            && activationSlotRequestRow[currentSlot] >= requiredRows
            && activationSlotRequestRow[nextSlot] < requiredRows;

        if (requestCurrent || requestNext) begin
            Bit#(1) selectedSlot = requestCurrent ? currentSlot : nextSlot;
            BoundedCount#(arrayDim) selectedRow =
                activationSlotRequestRow[selectedSlot];
            MatrixExtent kStart = activationSlotKStart[selectedSlot];
            BoundedCount#(arrayDim) kCount =
                activationSlotKCount[selectedSlot];

            dynamicAssert(
                matrixModeReg == FullMatrix
                    || matrixWorkReg.iStart + matrixWorkReg.iCount
                        <= matmulScheduler.publishedRows,
                "activation request precedes stripe publication"
            );
            HostAddress rowBase = matrixRowAddress(
                matrixWorkReg.activationBase,
                zeroExtend(selectedRow),
                matrixWorkReg.activationRowStride
            );
            HostAddress address = matrixElementAddress(
                rowBase,
                kStart - matrixKOriginReg,
                fromInteger(valueOf(inputBits) / 8)
            );
            HostRequestTag tag = matrixTag(activationTagSequenceReg);

            activationRequestTagReg <= tag;
            activationRequestAddressReg <= address;
            activationRequestSlotReg <= selectedSlot == 1;
            activationRequestRowReg <= truncate(selectedRow);
            activationRequestKStartReg <= kStart;
            activationRequestKCountReg <= kCount;
            activationRequestValidReg <= True;
            activationSlotRequestRow[selectedSlot] <= selectedRow + 1;
            activationTagSequenceReg <= activationTagSequenceReg + 1;
            activationReadRequestsReg <= activationReadRequestsReg + 1;
        end
    endrule

    // Responses cross a register boundary before becoming executable slot
    // state. This keeps the external response method out of the combinational
    // execution-admission schedule while preserving exact tag/slot identity.
    rule publishMatrixActivationResponse (activationResponsePendingReg);
        Bit#(1) responseSlot =
            activationSlotIndex(activationResponseSlotReg);
        BoundedIndex#(arrayDim) responseRow = activationResponseRowReg;
        dynamicAssert(
            !activationSlotRowValid[responseSlot][responseRow],
            "duplicate activation row publication"
        );
        activationSlotRows[responseSlot][responseRow] <=
            activationResponseValuesReg;
        activationSlotRowValid[responseSlot][responseRow] <= True;
        activationResponsePendingReg <= False;
    endrule

    // Only the exact next logical current row can enter the engine. A valid
    // later row or any prefetched next-fragment row cannot bypass this index.
    rule feedBufferedMatrixActivation (
        matrixStateReg == MatrixExecute
        && engine.active
        && engine.activationReady
        && activationFeedRowReg < truncate(matrixWorkReg.iCount)
        && activationSlotRowValid[
            activationSlotIndex(currentActivationSlotReg)
        ][activationFeedRowReg]
    );
        Bit#(1) currentSlot = activationSlotIndex(currentActivationSlotReg);
        BoundedIndex#(arrayDim) row = truncate(activationFeedRowReg);
        engine.putActivationRow(activationSlotRows[currentSlot][row]);
        activationSlotRowValid[currentSlot][row] <= False;
        activationFeedRowReg <= activationFeedRowReg + 1;
        activationRowsAcceptedReg <= activationRowsAcceptedReg + 1;
        acceptedInputRowsReg <= acceptedInputRowsReg + 1;
    endrule

    rule finishMatrixExecution (
        matrixStateReg == MatrixExecute
        && engine.done
        && vectorUnit.ready
    );
        engine.acknowledge;
        if (workScheduler.hasNextFragment) begin
            Bit#(1) oldCurrentSlot =
                activationSlotIndex(currentActivationSlotReg);
            currentActivationSlotReg <= !currentActivationSlotReg;
            activationSlotMetadataValid[oldCurrentSlot] <= False;
        end
        workScheduler.completeFragment;
        matmulFragmentsCompletedReg <= matmulFragmentsCompletedReg + 1;
        matrixStateReg <= MatrixFinishFragment;
    endrule

    rule continueMatrixFragments (
        matrixStateReg == MatrixFinishFragment
        && workScheduler.fragmentValid
    );
        matrixStateReg <= MatrixWaitFragment;
    endrule

    rule beginMatrixWriteback (
        matrixStateReg == MatrixFinishFragment && workScheduler.done
    );
        workScheduler.acknowledge;
        outputRowReg <= 0;
        matrixStateReg <= MatrixWriteOutput;
    endrule

    rule issueMatrixOutputRequest (
        matrixStateReg == MatrixWriteOutput
        && !outputRequestValidReg
        && zeroExtend(outputRowReg) < matrixWorkReg.iCount
    );
        HostRequestTag tag = matrixTag(outputTagSequenceReg);
        HostAddress address = matrixRowAddress(
            matrixWorkReg.outputBase,
            zeroExtend(outputRowReg),
            matrixWorkReg.outputRowStride
        );

        outputRequestTagReg <= tag;
        outputRequestAddressReg <= address;
        outputRequestValidReg <= True;
        outputTagSequenceReg <= outputTagSequenceReg + 1;
        outputWriteRequestsReg <= outputWriteRequestsReg + 1;
    endrule

    rule observeMatmulSchedulerDone (
        matrixStateReg == MatrixWaitSchedulerDone && matmulScheduler.done
    );
        dynamicAssert(
            !outputRequestValidReg,
            "matmul completed before C write acknowledgement"
        );
        matrixStateReg <= MatrixDone;
    endrule

    rule acquireNextMatrixWork (
        matrixStateReg == MatrixWaitSchedulerDone
        && matmulScheduler.workValid
    );
        matrixStateReg <= MatrixWaitWork;
    endrule

    rule issueVectorRequest (
        engine.resultValid && vectorUnit.ready && !scaledResultHeldReg
    );
        SystolicResult#(arrayDim, acc_t) arrayResult = engine.result;
        Vector#(
            arrayDim,
            RowAddress#(accRows)
        ) destinationRowAddresses = newVector;
        Vector#(arrayDim, scale_t) selectedScales = executionUsesScaleReg
            ? executionScaleRowReg
            : replicate(unpack(0));

        for (Integer column = 0;
                column < valueOf(arrayDim);
                column = column + 1) begin
            BoundedIndex#(arrayDim) rowOffset =
                truncate(arrayResult.rowOffsets[column]);
            RowAddress#(accRows) extendedOffset = zeroExtend(rowOffset);

            destinationRowAddresses[column] = arrayResult.valids[column]
                ? commandReg.accumulatorBaseRow + extendedOffset
                : commandReg.accumulatorBaseRow;
        end

        destinationRowAddressesReg <= destinationRowAddresses;
        vectorUnit.put(
            arrayResult.valids,
            arrayResult.partialSums,
            selectedScales,
            commandReg.vectorOp
        );
        if (executionUsesScaleReg) begin
            // Keep a scaled result at the engine boundary until every physical
            // vector group has consumed the immutable scale snapshot. This
            // also prevents a following skewed column result from replacing
            // the held FIFO head while the vector unit is busy.
            scaledResultHeldReg <= True;
        end
        else begin
            engine.consumeResult;
        end
    endrule

    rule releaseScaledEngineResult (
        scaledResultHeldReg && vectorUnit.ready
    );
        engine.consumeResult;
        scaledResultHeldReg <= False;
    endrule

    rule commitVectorResult (vectorUnit.resultValid);
        VectorResult#(arrayDim, acc_t) transformed = vectorUnit.result;

        accumulator.commit(
            transformed.valids,
            destinationRowAddressesReg,
            transformed.contributions,
            commandReg.accumulate
        );

        if (anyTrue(transformed.valids)) begin
            engine.noteCommitted(transformed.valids);
        end

        vectorUnit.consume;
    endrule

    method Action configureScaling(
        UInt#(32) blockSize,
        UInt#(32) totalK,
        ScaleContext contextId
    ) if (
        matrixStateReg == MatrixIdle
        && engine.idle
        && vectorUnit.ready
        && !pendingExecutionReg
        && !scaleOutstandingReg
        && !scaleRequestValidReg
    );
        dynamicAssert(blockSize > 0, "scaling block size must be positive");
        dynamicAssert(totalK > 0, "scaling total K must be positive");

        Bool metadataChanged = !configurationValidReg
            || blockSize != blockSizeReg
            || totalK != totalKReg
            || contextId != scalingContextReg;

        blockSizeReg <= blockSize;
        totalKReg <= totalK;
        scalingContextReg <= contextId;
        configurationValidReg <= True;

        if (metadataChanged) begin
            currentScaleValidReg <= False;
            nextScaleValidReg <= False;
            prefetchNeededReg <= False;
        end
    endmethod

    method Bool scaleRequestValid = scaleRequestValidReg;

    method ScaleRowRequest scaleRequest if (scaleRequestValidReg);
        return scaleRequestReg;
    endmethod

    method ScaleContext scaleRequestContext if (scaleRequestValidReg);
        return scaleRequestReg.contextId;
    endmethod

    method ScaleBlockIndex scaleRequestBlock if (scaleRequestValidReg);
        return scaleRequestReg.block;
    endmethod

    method ScaleRequestKind scaleRequestKind if (scaleRequestValidReg);
        return scaleRequestReg.kind;
    endmethod

    method Action putScaleRow(
        ScaleContext contextId,
        ScaleBlockIndex block,
        Vector#(arrayDim, scale_t) columnScales
    ) if (scaleOutstandingReg && scaleRequestValidReg);
        dynamicAssert(
            contextId == outstandingRequestReg.contextId,
            "scale response context does not match request"
        );
        dynamicAssert(
            block == outstandingRequestReg.block,
            "scale response block does not match request"
        );

        scaleRequestValidReg <= False;
        scaleOutstandingReg <= False;
        scaleRowsReceivedReg <= scaleRowsReceivedReg + 1;

        if (outstandingRequestReg.kind == ScaleDemand) begin
            dynamicAssert(
                pendingExecutionReg,
                "demand scale response has no pending execution"
            );
            dynamicAssert(
                contextId == pendingContextReg && block == pendingBlockReg,
                "demand scale response does not match pending execution"
            );

            currentScaleValidReg <= True;
            currentScaleContextReg <= contextId;
            currentScaleBlockReg <= block;
            currentScaleRowReg <= columnScales;
            nextScaleValidReg <= False;
            prefetchNeededReg <= True;
        end
        else begin
            nextScaleValidReg <= True;
            nextScaleContextReg <= contextId;
            nextScaleBlockReg <= block;
            nextScaleRowReg <= columnScales;
        end
    endmethod

    method UInt#(64) scaleDemandRequests = scaleDemandRequestsReg;
    method UInt#(64) scalePrefetchRequests = scalePrefetchRequestsReg;
    method UInt#(64) scaleCurrentHits = scaleCurrentHitsReg;
    method UInt#(64) scaleNextHits = scaleNextHitsReg;
    method UInt#(64) scaleDemandMisses = scaleDemandMissesReg;
    method UInt#(64) scaleRowsReceived = scaleRowsReceivedReg;
    method UInt#(64) scaleWaitCycles = scaleWaitCyclesReg;

    method Action startMatmul(
        MatmulJobId jobId,
        MatmulMode mode,
        HostAddress activationBase,
        HostAddress weightBase,
        HostAddress scaleBase,
        HostAddress outputBase,
        HostStride activationRowStride,
        HostStride weightRowStride,
        HostStride scaleRowStride,
        HostStride outputRowStride,
        MatrixExtent rowCount,
        MatrixExtent columnCount,
        MatrixExtent reductionCount,
        MatrixExtent kOrigin,
        MatrixExtent scaleTotalK,
        MatrixExtent scaleBlockSize,
        ScaleContext scaleContext,
        Bool accumulateFirstFragment,
        VectorOp vectorOp
    ) if (
        matrixStateReg == MatrixIdle
        && engine.idle
        && vectorUnit.ready
        && !pendingExecutionReg
        && !scaleOutstandingReg
        && !scaleRequestValidReg
    );
        MatrixExtent dimension = fromInteger(valueOf(arrayDim));
        dynamicAssert(reductionCount > 0, "matmul K must be positive");
        dynamicAssert(
            !vectorOpUsesScale(vectorOp) || scaleBlockSize > 0,
            "scaled matmul block size must be positive"
        );
        dynamicAssert(
            !vectorOpUsesScale(vectorOp) || scaleTotalK >= kOrigin + reductionCount,
            "scaled matmul exceeds total K"
        );

        matmulScheduler.start(MatmulDescriptor {
            jobId: jobId,
            mode: mode,
            activationBase: activationBase,
            weightBase: weightBase,
            scaleBase: scaleBase,
            outputBase: outputBase,
            activationRowStride: activationRowStride,
            weightRowStride: weightRowStride,
            scaleRowStride: scaleRowStride,
            outputRowStride: outputRowStride,
            rowCount: rowCount,
            columnCount: columnCount,
            reductionCount: reductionCount,
            tileIRows: dimension,
            tileJColumns: dimension,
            blockSize: scaleBlockSize,
            activationElementBytes: fromInteger(valueOf(inputBits) / 8),
            weightElementBytes: fromInteger(valueOf(weightBits) / 8),
            scaleElementBytes: fromInteger(valueOf(scaleBits) / 8),
            outputElementBytes: fromInteger(valueOf(accBits) / 8),
            vectorOp: vectorOp,
            workContext: scaleContext
        });

        matrixJobIdReg <= jobId;
        matrixModeReg <= mode;
        matrixActivationBaseReg <= activationBase;
        matrixActivationStrideReg <= activationRowStride;
        matrixKOriginReg <= kOrigin;
        matrixScaleTotalKReg <= scaleTotalK;
        matrixScaleBlockSizeReg <= scaleBlockSize;
        matrixScaleContextReg <= scaleContext;
        matrixAccumulateFirstReg <= accumulateFirstFragment;
        nextStripeIdReg <= 0;
        matrixStateReg <= MatrixWaitWork;
    endmethod

    method Action publishActivationStripe(
        MatrixExtent rowBegin,
        MatrixExtent rowCount
    ) if (
        matrixStateReg != MatrixIdle
        && matrixStateReg != MatrixDone
        && matrixModeReg == AsyncStripes
    );
        HostAddress stripeBase = matrixRowAddress(
            matrixActivationBaseReg,
            rowBegin,
            matrixActivationStrideReg
        );
        matmulScheduler.publishStripe(ActivationStripe {
            stripeId: nextStripeIdReg,
            rowBegin: rowBegin,
            rowCount: rowCount,
            activationBase: stripeBase,
            stripeContext: zeroExtend(nextStripeIdReg)
        });
        nextStripeIdReg <= nextStripeIdReg + 1;
        stripesPublishedReg <= stripesPublishedReg + 1;
        stripeRowsPublishedReg <= stripeRowsPublishedReg + zeroExtend(rowCount);
    endmethod

    method Bool activationReadRequestValid = activationRequestValidReg;
    method HostRequestTag activationReadRequestTag
            if (activationRequestValidReg);
        return activationRequestTagReg;
    endmethod
    method HostAddress activationReadRequestAddress
            if (activationRequestValidReg);
        return activationRequestAddressReg;
    endmethod
    method BoundedCount#(arrayDim) activationReadRequestElementCount
            if (activationRequestValidReg);
        return activationRequestKCountReg;
    endmethod
    method Action putActivationReadResponse(
        HostRequestTag tag,
        Vector#(arrayDim, input_t) values
    );
        dynamicAssert(
            activationRequestValidReg,
            "stale activation response has no outstanding request"
        );
        dynamicAssert(
            !activationResponsePendingReg,
            "activation response publication is already pending"
        );
        Bit#(1) responseSlot =
            activationSlotIndex(activationRequestSlotReg);
        BoundedIndex#(arrayDim) responseRow = activationRequestRowReg;
        dynamicAssert(tag == activationRequestTagReg, "activation response tag mismatch");
        dynamicAssert(
            (tag >> 32) == zeroExtend(matrixJobIdReg),
            "activation response job mismatch"
        );
        dynamicAssert(
            activationSlotMetadataValid[responseSlot]
                && activationSlotKStart[responseSlot]
                    == activationRequestKStartReg
                && activationSlotKCount[responseSlot]
                    == activationRequestKCountReg,
            "activation response slot metadata mismatch"
        );
        dynamicAssert(
            !activationSlotRowValid[responseSlot][responseRow],
            "duplicate activation row response"
        );
        dynamicAssert(
            zeroExtend(responseRow) < matrixWorkReg.iCount,
            "activation response row is outside current work"
        );

        Vector#(arrayDim, input_t) padded = replicate(unpack(0));
        for (Integer lane = 0; lane < valueOf(arrayDim); lane = lane + 1) begin
            if (fromInteger(lane) < activationRequestKCountReg) begin
                padded[lane] = values[lane];
            end
        end
        activationResponseSlotReg <= activationRequestSlotReg;
        activationResponseRowReg <= responseRow;
        activationResponseValuesReg <= padded;
        activationResponsePendingReg <= True;
        activationRequestValidReg <= False;
    endmethod

    method Bool weightReadRequestValid = weightRequestValidReg;
    method HostRequestTag weightReadRequestTag if (weightRequestValidReg);
        return weightRequestTagReg;
    endmethod
    method HostAddress weightReadRequestAddress if (weightRequestValidReg);
        return weightRequestAddressReg;
    endmethod
    method BoundedCount#(arrayDim) weightReadRequestElementCount
            if (weightRequestValidReg);
        return truncate(matrixWorkReg.jCount);
    endmethod
    method Action putWeightReadResponse(
        HostRequestTag tag,
        Vector#(arrayDim, weight_t) values
    ) if (weightRequestValidReg && weightLoadingReg);
        dynamicAssert(tag == weightRequestTagReg, "weight response tag mismatch");
        dynamicAssert(
            weightLoadBankReg != engine.activeWeightBank,
            "weight response attempted an active-bank write"
        );
        Vector#(arrayDim, weight_t) padded = replicate(unpack(0));
        for (Integer lane = 0; lane < valueOf(arrayDim); lane = lane + 1) begin
            if (fromInteger(lane) < matrixWorkReg.jCount) begin
                padded[lane] = values[lane];
            end
        end
        engine.loadWeightRowBank(
            weightLoadBankReg,
            truncate(weightLoadRowReg),
            padded
        );
        weightLoadRowReg <= weightLoadRowReg + 1;
        weightRequestValidReg <= False;
    endmethod

    method Bool scaleReadRequestValid =
        matrixStateReg != MatrixIdle && scaleRequestValidReg;
    method HostRequestTag scaleReadRequestTag
            if (matrixStateReg != MatrixIdle && scaleRequestValidReg);
        return unpack({ pack(matrixJobIdReg), pack(scaleRequestReg.block) });
    endmethod
    method HostAddress scaleReadRequestAddress
            if (matrixStateReg != MatrixIdle && scaleRequestValidReg);
        return matrixRowAddress(
            matrixWorkReg.scaleBase,
            scaleRequestReg.block,
            matrixWorkReg.scaleRowStride
        );
    endmethod
    method BoundedCount#(arrayDim) scaleReadRequestElementCount
            if (matrixStateReg != MatrixIdle && scaleRequestValidReg);
        return truncate(matrixWorkReg.jCount);
    endmethod
    method Action putScaleReadResponse(
        HostRequestTag tag,
        Vector#(arrayDim, scale_t) values
    ) if (
        matrixStateReg != MatrixIdle
        && scaleOutstandingReg
        && scaleRequestValidReg
    );
        HostRequestTag expected = unpack({
            pack(matrixJobIdReg), pack(outstandingRequestReg.block)
        });
        dynamicAssert(tag == expected, "scale response tag mismatch");
        Vector#(arrayDim, scale_t) padded = replicate(unpack(0));
        for (Integer lane = 0; lane < valueOf(arrayDim); lane = lane + 1) begin
            if (fromInteger(lane) < matrixWorkReg.jCount) begin
                padded[lane] = values[lane];
            end
        end

        scaleRequestValidReg <= False;
        scaleOutstandingReg <= False;
        scaleRowsReceivedReg <= scaleRowsReceivedReg + 1;
        if (outstandingRequestReg.kind == ScaleDemand) begin
            dynamicAssert(
                pendingExecutionReg,
                "matrix demand scale response has no pending execution"
            );
            dynamicAssert(
                outstandingRequestReg.contextId == pendingContextReg
                    && outstandingRequestReg.block == pendingBlockReg,
                "matrix demand scale response does not match pending execution"
            );
            currentScaleValidReg <= True;
            currentScaleContextReg <= outstandingRequestReg.contextId;
            currentScaleBlockReg <= outstandingRequestReg.block;
            currentScaleRowReg <= padded;
            nextScaleValidReg <= False;
            prefetchNeededReg <= True;
        end
        else begin
            nextScaleValidReg <= True;
            nextScaleContextReg <= outstandingRequestReg.contextId;
            nextScaleBlockReg <= outstandingRequestReg.block;
            nextScaleRowReg <= padded;
        end
    endmethod

    method Bool outputWriteRequestValid = outputRequestValidReg;
    method HostRequestTag outputWriteRequestTag if (outputRequestValidReg);
        return outputRequestTagReg;
    endmethod
    method HostAddress outputWriteRequestAddress if (outputRequestValidReg);
        return outputRequestAddressReg;
    endmethod
    method BoundedCount#(arrayDim) outputWriteRequestElementCount
            if (outputRequestValidReg);
        return truncate(matrixWorkReg.jCount);
    endmethod
    method Vector#(arrayDim, acc_t) outputWriteRequestValues
            if (outputRequestValidReg);
        return accumulator.readRow(zeroExtend(outputRowReg));
    endmethod
    method Action putOutputWriteResponse(HostRequestTag tag)
            if (outputRequestValidReg);
        dynamicAssert(tag == outputRequestTagReg, "output response tag mismatch");
        outputRequestValidReg <= False;
        outputWriteResponsesReg <= outputWriteResponsesReg + 1;

        if (zeroExtend(outputRowReg) + 1 == matrixWorkReg.iCount) begin
            matmulScheduler.completeWork;
            matmulWorksCompletedReg <= matmulWorksCompletedReg + 1;
            matrixStateReg <= MatrixWaitSchedulerDone;
        end
        else begin
            outputRowReg <= outputRowReg + 1;
        end
    endmethod

    method Bool stripeCompletionValid = matmulScheduler.completionValid;
    method UInt#(32) stripeCompletionId
            if (matmulScheduler.completionValid);
        return matmulScheduler.completion.stripeId;
    endmethod
    method MatrixExtent stripeCompletionRowBegin
            if (matmulScheduler.completionValid);
        return matmulScheduler.completion.rowBegin;
    endmethod
    method MatrixExtent stripeCompletionRowCount
            if (matmulScheduler.completionValid);
        return matmulScheduler.completion.rowCount;
    endmethod
    method UInt#(64) stripeCompletionContext
            if (matmulScheduler.completionValid);
        return matmulScheduler.completion.stripeContext;
    endmethod
    method Action acknowledgeStripeCompletion
            if (matmulScheduler.completionValid);
        matmulScheduler.acknowledgeCompletion;
    endmethod

    method Bool matmulDone = matrixStateReg == MatrixDone;
    method Action acknowledgeMatmul if (matrixStateReg == MatrixDone);
        dynamicAssert(
            !outputRequestValidReg,
            "matmul acknowledgement precedes C acknowledgement"
        );
        matmulScheduler.acknowledge;
        matrixStateReg <= MatrixIdle;
    endmethod

    method Bool activeWeightBank = engine.activeWeightBank;
    method Bool inactiveWeightBankLoading = weightLoadingReg
        && weightLoadBankReg != engine.activeWeightBank;
    method Bool executionActive = engine.active;
    method BoundedCount#(arrayDim) debugAcceptedRows = engine.acceptedRows;
    method BoundedCount#(arrayDim) debugConfiguredRows = engine.configuredRows;
    method BoundedCount#(arrayDim) debugFirstColumnIssued =
        engine.firstColumnIssued;
    method BoundedCount#(arrayDim) debugFirstColumnCommitted =
        engine.firstColumnCommitted;
    method Bool debugEngineResultValid = engine.resultValid;
    method Bool debugVectorBusy = !vectorUnit.ready;
    method UInt#(8) matmulSchedulerState = matmulScheduler.debugState;
    method UInt#(8) workSchedulerState = workScheduler.debugState;
    method UInt#(8) matrixCoreState = unpack(zeroExtend(pack(matrixStateReg)));
    method UInt#(64) matmulFragmentsCompleted = matmulFragmentsCompletedReg;
    method UInt#(64) matmulWorksCompleted = matmulWorksCompletedReg;
    method UInt#(64) stripesPublished = stripesPublishedReg;
    method UInt#(64) stripeRowsPublished = stripeRowsPublishedReg;
    method UInt#(64) activationReadRequests = activationReadRequestsReg;
    method UInt#(64) weightReadRequests = weightReadRequestsReg;
    method UInt#(64) scaleReadRequests = scaleReadRequestsReg;
    method UInt#(64) outputWriteRequests = outputWriteRequestsReg;
    method UInt#(64) outputWriteResponses = outputWriteResponsesReg;
    method UInt#(64) weightBankActivations = weightBankActivationsReg;
    method UInt#(64) activationWaitCycles = activationWaitCyclesReg;
    method UInt#(64) weightWaitCycles = weightWaitCyclesReg;
    method UInt#(64) outputWaitCycles = outputWaitCyclesReg;
    method UInt#(64) stripeHostWaitCycles = stripeHostWaitCyclesReg;
    method UInt#(64) computeCycles = computeCyclesReg;
    method UInt#(64) drainCycles = drainCyclesReg;
    method UInt#(64) weightPreloadCycles = weightPreloadCyclesReg;
    method UInt#(64) activationOverlapCycles = activationOverlapCyclesReg;
    method UInt#(64) weightOverlapCycles = weightOverlapCyclesReg;
    method UInt#(64) scaleOverlapCycles = scaleOverlapCyclesReg;
    method UInt#(64) overlapCycles = overlapCyclesReg;

    method Action beginWeightLoad if (
        matrixStateReg == MatrixIdle
        && engine.idle && vectorUnit.ready && !pendingExecutionReg
    );
        engine.beginWeightLoad;
    endmethod

    method Action loadWeightRow(
        BoundedIndex#(arrayDim) row,
        Vector#(arrayDim, weight_t) weights
    ) if (
        matrixStateReg == MatrixIdle
        && engine.idle && vectorUnit.ready && !pendingExecutionReg
    );
        engine.loadWeightRow(row, weights);
    endmethod

    method Bool weightsReady = engine.weightsReady;

    method Action startExecution(
        ExecuteCmd#(arrayDim, accRows) command,
        UInt#(32) kStart,
        BoundedCount#(arrayDim) kCount
    ) if (
        matrixStateReg == MatrixIdle
        && engine.idle
        && vectorUnit.ready
        && engine.weightsReady
        && !pendingExecutionReg
    );
        dynamicAssert(
            command.accumulatorBaseRow
                <= fromInteger(valueOf(freeAccumulatorRows)),
            "accumulator row range exceeds storage"
        );
        dynamicAssert(
            command.vectorOp == VectorBypass
                || vectorUnit.scalingSupported,
            "selected numeric format supports only VectorBypass"
        );
        dynamicAssert(kCount > 0, "execution K count must be positive");

        Bool operationNeedsScale = vectorUnit.scalingSupported
            && vectorOpUsesScale(command.vectorOp);
        UInt#(32) safeBlockSize = blockSizeReg == 0 ? 1 : blockSizeReg;
        UInt#(32) kCountWide = zeroExtend(kCount);
        UInt#(32) remainingK =
            kStart < totalKReg ? totalKReg - kStart : 0;
        UInt#(32) blockOffset = kStart % safeBlockSize;
        UInt#(32) blockRemaining = safeBlockSize - blockOffset;
        ScaleBlockIndex selectedBlock = kStart / safeBlockSize;

        if (operationNeedsScale) begin
            dynamicAssert(
                configurationValidReg,
                "scaled execution requires scaling metadata"
            );
            dynamicAssert(
                kCountWide <= remainingK,
                "execution K range exceeds total K"
            );
            dynamicAssert(
                kCountWide <= blockRemaining,
                "hardware K partial crosses a scale block boundary"
            );

            Bool currentHit = currentScaleValidReg
                && currentScaleContextReg == scalingContextReg
                && currentScaleBlockReg == selectedBlock;
            Bool nextHit = nextScaleValidReg
                && nextScaleContextReg == scalingContextReg
                && nextScaleBlockReg == selectedBlock;

            if (currentHit) begin
                executionScaleRowReg <= currentScaleRowReg;
                executionUsesScaleReg <= True;
                commandReg <= command;
                acceptedInputRowsReg <= 0;
                scaleCurrentHitsReg <= scaleCurrentHitsReg + 1;
                engine.start(command.rowCount);
            end
            else if (nextHit) begin
                executionScaleRowReg <= nextScaleRowReg;
                executionUsesScaleReg <= True;
                commandReg <= command;
                acceptedInputRowsReg <= 0;
                currentScaleValidReg <= True;
                currentScaleContextReg <= nextScaleContextReg;
                currentScaleBlockReg <= nextScaleBlockReg;
                currentScaleRowReg <= nextScaleRowReg;
                nextScaleValidReg <= False;
                prefetchNeededReg <= True;
                scaleNextHitsReg <= scaleNextHitsReg + 1;
                engine.start(command.rowCount);
            end
            else begin
                pendingExecutionReg <= True;
                pendingCommandReg <= command;
                pendingContextReg <= scalingContextReg;
                pendingBlockReg <= selectedBlock;
                scaleDemandMissesReg <= scaleDemandMissesReg + 1;

                if (!scaleOutstandingReg && !scaleRequestValidReg) begin
                    ScaleRowRequest request = ScaleRowRequest {
                        contextId: scalingContextReg,
                        block: selectedBlock,
                        kind: ScaleDemand
                    };
                    scaleRequestReg <= request;
                    outstandingRequestReg <= request;
                    scaleRequestValidReg <= True;
                    scaleOutstandingReg <= True;
                    scaleDemandRequestsReg <=
                        scaleDemandRequestsReg + 1;
                end
            end
        end
        else begin
            executionScaleRowReg <= replicate(unpack(0));
            executionUsesScaleReg <= False;
            commandReg <= command;
            acceptedInputRowsReg <= 0;
            engine.start(command.rowCount);
        end
    endmethod

    method Bool activationReady =
        matrixStateReg == MatrixIdle && engine.activationReady;

    method Action putActivationRow(
        Vector#(arrayDim, input_t) activations
    ) if (matrixStateReg == MatrixIdle && engine.activationReady);
        dynamicAssert(
            acceptedInputRowsReg < commandReg.rowCount,
            "more activation rows supplied than rowCount"
        );

        acceptedInputRowsReg <= acceptedInputRowsReg + 1;
        engine.putActivationRow(activations);
    endmethod

    method Bool idle = matrixStateReg == MatrixIdle
        && engine.idle && vectorUnit.ready && !pendingExecutionReg;
    method Bool executionDone = engine.done && vectorUnit.ready;

    method Action acknowledgeExecution if (
        matrixStateReg == MatrixIdle && engine.done && vectorUnit.ready
    );
        engine.acknowledge;
    endmethod

    method Action writeAccumulatorRow(
        RowAddress#(accRows) row,
        Vector#(arrayDim, acc_t) values
    ) if (
        matrixStateReg == MatrixIdle
        && engine.idle && vectorUnit.ready && !pendingExecutionReg
    );
        accumulator.writeRow(row, values);
    endmethod

    method Vector#(arrayDim, acc_t) readAccumulatorRow(
        RowAddress#(accRows) row
    ) if (
        matrixStateReg == MatrixIdle
        && !engine.active && vectorUnit.ready && !pendingExecutionReg
    );
        return accumulator.readRow(row);
    endmethod

endmodule

endpackage
