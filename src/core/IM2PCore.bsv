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
    Reg#(BoundedCount#(arrayDim)) acceptedInputRowsReg <- mkReg(0);

    Reg#(UInt#(64)) scaleDemandRequestsReg <- mkReg(0);
    Reg#(UInt#(64)) scalePrefetchRequestsReg <- mkReg(0);
    Reg#(UInt#(64)) scaleCurrentHitsReg <- mkReg(0);
    Reg#(UInt#(64)) scaleNextHitsReg <- mkReg(0);
    Reg#(UInt#(64)) scaleDemandMissesReg <- mkReg(0);
    Reg#(UInt#(64)) scaleRowsReceivedReg <- mkReg(0);
    Reg#(UInt#(64)) scaleWaitCyclesReg <- mkReg(0);

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
    endrule

    rule issueVectorRequest (engine.resultValid && vectorUnit.ready);
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
        engine.consumeResult;
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
        engine.idle
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

    method Action beginWeightLoad if (
        engine.idle && vectorUnit.ready && !pendingExecutionReg
    );
        engine.beginWeightLoad;
    endmethod

    method Action loadWeightRow(
        BoundedIndex#(arrayDim) row,
        Vector#(arrayDim, weight_t) weights
    ) if (engine.idle && vectorUnit.ready && !pendingExecutionReg);
        engine.loadWeightRow(row, weights);
    endmethod

    method Bool weightsReady = engine.weightsReady;

    method Action startExecution(
        ExecuteCmd#(arrayDim, accRows) command,
        UInt#(32) kStart,
        BoundedCount#(arrayDim) kCount
    ) if (
        engine.idle
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

    method Bool activationReady = engine.activationReady;

    method Action putActivationRow(
        Vector#(arrayDim, input_t) activations
    ) if (engine.activationReady);
        dynamicAssert(
            acceptedInputRowsReg < commandReg.rowCount,
            "more activation rows supplied than rowCount"
        );

        acceptedInputRowsReg <= acceptedInputRowsReg + 1;
        engine.putActivationRow(activations);
    endmethod

    method Bool idle =
        engine.idle && vectorUnit.ready && !pendingExecutionReg;
    method Bool executionDone = engine.done && vectorUnit.ready;

    method Action acknowledgeExecution if (
        engine.done && vectorUnit.ready
    );
        engine.acknowledge;
    endmethod

    method Action writeAccumulatorRow(
        RowAddress#(accRows) row,
        Vector#(arrayDim, acc_t) values
    ) if (
        engine.idle && vectorUnit.ready && !pendingExecutionReg
    );
        accumulator.writeRow(row, values);
    endmethod

    method Vector#(arrayDim, acc_t) readAccumulatorRow(
        RowAddress#(accRows) row
    ) if (!engine.active && vectorUnit.ready && !pendingExecutionReg);
        return accumulator.readRow(row);
    endmethod

endmodule

endpackage
