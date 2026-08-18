package MatmulScheduler;

import Assert::*;
import FIFOF::*;

import Types::*;
import HostMemoryTypes::*;
import WorkTypes::*;

typedef enum {
    MatmulIdle,
    MatmulWaitStripe,
    MatmulOfferWork,
    MatmulWaitWork,
    MatmulDone
} MatmulSchedulerState deriving (Bits, Eq, FShow);

interface MatmulSchedulerIfc#(numeric type arrayDim);
    method Action start(MatmulDescriptor descriptor);
    method Action publishStripe(ActivationStripe stripe);

    method Bool workValid;
    method MatmulWork#(arrayDim) work;
    method Bool lookaheadValid;
    method MatmulWork#(arrayDim) lookaheadWork;
    method Bool lookaheadPreloadSafe;
    method Action acceptWork;
    method Action completeWork;
    method Bool completionValid;
    method StripeCompletion completion;
    method Action acknowledgeCompletion;

    method Bool active;
    method Bool waitingForStripe;
    method Bool done;
    method Action acknowledge;

    method UInt#(8) debugState;
    method MatrixExtent currentI;
    method MatrixExtent currentJ;
    method MatrixExtent publishedRows;
endinterface

function MatrixExtent boundedTileCount(
    MatrixExtent dimension,
    MatrixExtent start,
    MatrixExtent total,
    MatrixExtent requested
);
    MatrixExtent maximum = requested < dimension ? requested : dimension;
    MatrixExtent remaining = total - start;
    MatrixExtent count = remaining < maximum ? remaining : maximum;
    return count;
endfunction

function HostAddress rowAddress(
    HostAddress base,
    MatrixExtent row,
    HostStride stride
);
    UInt#(96) wideOffset = zeroExtend(row) * zeroExtend(stride);
    return base + truncate(wideOffset);
endfunction

function HostAddress columnAddress(
    HostAddress base,
    MatrixExtent column,
    ElementBytes elementBytes
);
    UInt#(72) wideOffset = zeroExtend(column) * zeroExtend(elementBytes);
    return base + truncate(wideOffset);
endfunction

module mkMatmulScheduler(MatmulSchedulerIfc#(arrayDim));
    Reg#(MatmulSchedulerState) stateReg <- mkReg(MatmulIdle);
    Reg#(MatmulDescriptor) descriptorReg <- mkRegU;
    Reg#(Bool) startPendingReg <- mkReg(False);
    Reg#(Bool) acknowledgePendingReg <- mkReg(False);

    FIFOF#(ActivationStripe) stripeFifo <- mkSizedFIFOF(2);
    FIFOF#(StripeCompletion) completionFifo <- mkSizedFIFOF(2);
    Reg#(UInt#(32)) stripeIdReg <- mkReg(0);
    Reg#(UInt#(64)) stripeContextReg <- mkReg(0);
    Reg#(MatrixExtent) stripeRowBeginReg <- mkReg(0);
    Reg#(MatrixExtent) stripeRowCountReg <- mkReg(0);
    Reg#(HostAddress) stripeActivationBaseReg <- mkReg(0);
    Reg#(HostStride) stripeActivationStrideReg <- mkReg(0);
    Reg#(Bool) lookaheadStripeValidReg <- mkReg(False);
    Reg#(ActivationStripe) lookaheadStripeReg <- mkRegU;
    Reg#(MatrixExtent) publishedRowsReg <- mkReg(0);

    Reg#(MatrixExtent) iStartReg <- mkReg(0);
    Reg#(MatrixExtent) jStartReg <- mkReg(0);
    Reg#(Bool) completionPendingReg <- mkReg(False);

    rule beginMatmul (stateReg == MatmulIdle && startPendingReg);
        MatmulDescriptor descriptor = descriptorReg;

        iStartReg <= 0;
        jStartReg <= 0;
        startPendingReg <= False;
        if (descriptor.mode == FullMatrix) begin
            stripeIdReg <= 0;
            stripeContextReg <= descriptor.workContext;
            stripeRowBeginReg <= 0;
            stripeRowCountReg <= descriptor.rowCount;
            stripeActivationBaseReg <= descriptor.activationBase;
            stripeActivationStrideReg <= descriptor.activationRowStride;
            publishedRowsReg <= descriptor.rowCount;
            stateReg <= MatmulOfferWork;
        end
        else begin
            publishedRowsReg <= 0;
            stateReg <= MatmulWaitStripe;
        end
    endrule

    rule finishAcknowledge (
        stateReg == MatmulDone && acknowledgePendingReg
    );
        acknowledgePendingReg <= False;
        stateReg <= MatmulIdle;
    endrule

    rule activatePublishedStripe (
        stateReg == MatmulWaitStripe && stripeFifo.notEmpty
    );
        ActivationStripe stripe = stripeFifo.first;
        stripeFifo.deq;

        stripeIdReg <= stripe.stripeId;
        stripeContextReg <= stripe.stripeContext;
        stripeRowBeginReg <= stripe.rowBegin;
        stripeRowCountReg <= stripe.rowCount;
        stripeActivationBaseReg <= stripe.activationBase;
        stripeActivationStrideReg <= stripe.activationRowStride;
        iStartReg <= stripe.rowBegin;
        jStartReg <= 0;
        stateReg <= MatmulOfferWork;
    endrule

    rule capturePublishedLookahead (
        descriptorReg.mode == AsyncStripes
        && stateReg != MatmulIdle && stateReg != MatmulWaitStripe
        && stateReg != MatmulDone && !lookaheadStripeValidReg
        && stripeFifo.notEmpty
    );
        lookaheadStripeReg <= stripeFifo.first;
        stripeFifo.deq;
        lookaheadStripeValidReg <= True;
    endrule

    rule advanceCompletedWork (
        stateReg == MatmulWaitWork
        && completionPendingReg
        && (descriptorReg.mode == FullMatrix || completionFifo.notFull)
    );
        MatrixExtent iCount = boundedTileCount(
            fromInteger(valueOf(arrayDim)),
            iStartReg,
            stripeRowBeginReg + stripeRowCountReg,
            descriptorReg.tileIRows
        );
        MatrixExtent jCount = boundedTileCount(
            fromInteger(valueOf(arrayDim)),
            jStartReg,
            descriptorReg.columnCount,
            descriptorReg.tileJColumns
        );
        MatrixExtent nextI = iStartReg + iCount;
        MatrixExtent nextJ = jStartReg + jCount;
        MatrixExtent stripeEnd = stripeRowBeginReg + stripeRowCountReg;

        completionPendingReg <= False;
        if (nextJ < descriptorReg.columnCount) begin
            jStartReg <= nextJ;
            stateReg <= MatmulOfferWork;
        end
        else if (nextI < stripeEnd) begin
            iStartReg <= nextI;
            jStartReg <= 0;
            stateReg <= MatmulOfferWork;
        end
        else if (descriptorReg.mode == FullMatrix
                || (publishedRowsReg == descriptorReg.rowCount
                    && !stripeFifo.notEmpty && !lookaheadStripeValidReg)) begin
            if (descriptorReg.mode == AsyncStripes) begin
                completionFifo.enq(StripeCompletion {
                    stripeId: stripeIdReg,
                    rowBegin: stripeRowBeginReg,
                    rowCount: stripeRowCountReg,
                    stripeContext: stripeContextReg
                });
            end
            stateReg <= MatmulDone;
        end
        else begin
            completionFifo.enq(StripeCompletion {
                stripeId: stripeIdReg,
                rowBegin: stripeRowBeginReg,
                rowCount: stripeRowCountReg,
                stripeContext: stripeContextReg
            });
            if (lookaheadStripeValidReg) begin
                ActivationStripe stripe = lookaheadStripeReg;
                stripeIdReg <= stripe.stripeId;
                stripeContextReg <= stripe.stripeContext;
                stripeRowBeginReg <= stripe.rowBegin;
                stripeRowCountReg <= stripe.rowCount;
                stripeActivationBaseReg <= stripe.activationBase;
                stripeActivationStrideReg <= stripe.activationRowStride;
                iStartReg <= stripe.rowBegin;
                jStartReg <= 0;
                lookaheadStripeValidReg <= False;
                stateReg <= MatmulOfferWork;
            end
            else begin
                stateReg <= MatmulWaitStripe;
            end
        end
    endrule

    function Bool hasLookahead();
        MatrixExtent iCount = boundedTileCount(
            fromInteger(valueOf(arrayDim)), iStartReg,
            stripeRowBeginReg + stripeRowCountReg, descriptorReg.tileIRows);
        return (descriptorReg.mode == FullMatrix
                && stateReg != MatmulIdle && stateReg != MatmulDone
                && iStartReg + iCount < descriptorReg.rowCount)
            || (descriptorReg.mode == AsyncStripes && lookaheadStripeValidReg);
    endfunction

    method Action start(MatmulDescriptor descriptor)
            if (stateReg == MatmulIdle && !startPendingReg);
        MatrixExtent dimension = fromInteger(valueOf(arrayDim));

        dynamicAssert(descriptor.rowCount > 0, "matmul M must be positive");
        dynamicAssert(descriptor.columnCount > 0, "matmul N must be positive");
        dynamicAssert(
            descriptor.reductionCount > 0,
            "matmul K must be positive"
        );
        dynamicAssert(
            descriptor.tileIRows > 0 && descriptor.tileIRows <= dimension,
            "tile I must fit the array"
        );
        dynamicAssert(
            descriptor.tileJColumns > 0
                && descriptor.tileJColumns <= dimension,
            "tile J must fit the array"
        );

        descriptorReg <= descriptor;
        startPendingReg <= True;
    endmethod

    method Action publishStripe(ActivationStripe stripe)
            if (stateReg != MatmulIdle
                && stateReg != MatmulDone
                && descriptorReg.mode == AsyncStripes
                && stripeFifo.notFull);
        dynamicAssert(stripe.rowCount > 0, "stripe rows must be positive");
        dynamicAssert(
            stripe.rowBegin == publishedRowsReg,
            "stripes must be contiguous and ordered"
        );
        dynamicAssert(
            stripe.rowBegin + stripe.rowCount <= descriptorReg.rowCount,
            "stripe exceeds matmul M"
        );

        stripeFifo.enq(stripe);
        publishedRowsReg <= publishedRowsReg + stripe.rowCount;
    endmethod

    method Bool workValid = stateReg == MatmulOfferWork;

    method MatmulWork#(arrayDim) work
            if (stateReg == MatmulOfferWork);
        MatrixExtent iCount = boundedTileCount(
            fromInteger(valueOf(arrayDim)),
            iStartReg,
            stripeRowBeginReg + stripeRowCountReg,
            descriptorReg.tileIRows
        );
        MatrixExtent jCount = boundedTileCount(
            fromInteger(valueOf(arrayDim)),
            jStartReg,
            descriptorReg.columnCount,
            descriptorReg.tileJColumns
        );
        MatrixExtent stripeLocalRow = iStartReg - stripeRowBeginReg;
        HostAddress activationBase = rowAddress(
            stripeActivationBaseReg,
            stripeLocalRow,
            stripeActivationStrideReg
        );
        HostAddress weightBase = columnAddress(
            descriptorReg.weightBase,
            jStartReg,
            descriptorReg.weightElementBytes
        );
        HostAddress scaleBase = columnAddress(
            descriptorReg.scaleBase,
            jStartReg,
            descriptorReg.scaleElementBytes
        );
        HostAddress outputRowBase = rowAddress(
            descriptorReg.outputBase,
            iStartReg,
            descriptorReg.outputRowStride
        );
        HostAddress outputBase = columnAddress(
            outputRowBase,
            jStartReg,
            descriptorReg.outputElementBytes
        );

        return MatmulWork {
            jobId: descriptorReg.jobId,
            stripeId: stripeIdReg,
            stripeContext: stripeContextReg,
            iStart: iStartReg,
            jStart: jStartReg,
            iCount: iCount,
            jCount: jCount,
            activationBase: activationBase,
            weightBase: weightBase,
            scaleBase: scaleBase,
            outputBase: outputBase,
            activationRowStride: stripeActivationStrideReg,
            weightRowStride: descriptorReg.weightRowStride,
            scaleRowStride: descriptorReg.scaleRowStride,
            outputRowStride: descriptorReg.outputRowStride,
            reductionCount: descriptorReg.reductionCount,
            blockSize: descriptorReg.blockSize,
            vectorOp: descriptorReg.vectorOp,
            workContext: descriptorReg.workContext
        };
    endmethod

    method Bool lookaheadValid = hasLookahead;

    method MatmulWork#(arrayDim) lookaheadWork if (hasLookahead);
        MatrixExtent dimension = fromInteger(valueOf(arrayDim));
        Bool async = descriptorReg.mode == AsyncStripes;
        MatrixExtent currentICount = boundedTileCount(
            dimension, iStartReg, stripeRowBeginReg + stripeRowCountReg,
            descriptorReg.tileIRows);
        MatrixExtent nextI = async ? lookaheadStripeReg.rowBegin
                                   : iStartReg + currentICount;
        MatrixExtent nextEnd = async
            ? lookaheadStripeReg.rowBegin + lookaheadStripeReg.rowCount
            : descriptorReg.rowCount;
        HostAddress aBase = async ? lookaheadStripeReg.activationBase
            : rowAddress(descriptorReg.activationBase, nextI,
                         descriptorReg.activationRowStride);
        HostStride aStride = async ? lookaheadStripeReg.activationRowStride
                                   : descriptorReg.activationRowStride;
        return MatmulWork {
            jobId: descriptorReg.jobId,
            stripeId: async ? lookaheadStripeReg.stripeId : 0,
            stripeContext: async ? lookaheadStripeReg.stripeContext
                                 : descriptorReg.workContext,
            iStart: nextI, jStart: 0,
            iCount: boundedTileCount(dimension, nextI, nextEnd,
                                     descriptorReg.tileIRows),
            jCount: boundedTileCount(dimension, 0, descriptorReg.columnCount,
                                     descriptorReg.tileJColumns),
            activationBase: aBase, weightBase: descriptorReg.weightBase,
            scaleBase: descriptorReg.scaleBase,
            outputBase: rowAddress(descriptorReg.outputBase, nextI,
                                   descriptorReg.outputRowStride),
            activationRowStride: aStride,
            weightRowStride: descriptorReg.weightRowStride,
            scaleRowStride: descriptorReg.scaleRowStride,
            outputRowStride: descriptorReg.outputRowStride,
            reductionCount: descriptorReg.reductionCount,
            blockSize: descriptorReg.blockSize,
            vectorOp: descriptorReg.vectorOp,
            workContext: descriptorReg.workContext
        };
    endmethod

    method Bool lookaheadPreloadSafe if (hasLookahead);
        MatrixExtent iCount = boundedTileCount(
            fromInteger(valueOf(arrayDim)), iStartReg,
            stripeRowBeginReg + stripeRowCountReg, descriptorReg.tileIRows);
        MatrixExtent jCount = boundedTileCount(
            fromInteger(valueOf(arrayDim)), jStartReg,
            descriptorReg.columnCount, descriptorReg.tileJColumns);
        return jStartReg + jCount >= descriptorReg.columnCount
            && (descriptorReg.mode == FullMatrix
                || iStartReg + iCount >= stripeRowBeginReg + stripeRowCountReg);
    endmethod

    method Action acceptWork if (stateReg == MatmulOfferWork);
        stateReg <= MatmulWaitWork;
    endmethod

    method Action completeWork
            if (stateReg == MatmulWaitWork && !completionPendingReg);
        completionPendingReg <= True;
    endmethod

    method Bool completionValid = completionFifo.notEmpty;
    method StripeCompletion completion if (completionFifo.notEmpty);
        return completionFifo.first;
    endmethod
    method Action acknowledgeCompletion if (completionFifo.notEmpty);
        completionFifo.deq;
    endmethod

    method Bool active =
        stateReg != MatmulIdle && stateReg != MatmulDone;

    method Bool waitingForStripe = stateReg == MatmulWaitStripe;

    method Bool done = stateReg == MatmulDone;

    method Action acknowledge
            if (stateReg == MatmulDone && !acknowledgePendingReg);
        acknowledgePendingReg <= True;
    endmethod

    method UInt#(8) debugState = unpack(zeroExtend(pack(stateReg)));
    method MatrixExtent currentI = iStartReg;
    method MatrixExtent currentJ = jStartReg;
    method MatrixExtent publishedRows = publishedRowsReg;
endmodule

endpackage
