package WorkScheduler;

import Assert::*;

import Types::*;

typedef enum {
    WorkIdle,
    WorkOfferFragment,
    WorkWaitFragment,
    WorkDone,
    WorkPrepare
} WorkSchedulerState deriving (Bits, Eq, FShow);

interface WorkSchedulerIfc#(numeric type arrayDim);
    method Action start(
        MatrixExtent kOrigin,
        MatrixExtent reductionCount,
        MatrixExtent blockSize,
        Bool usesScale,
        Bool accumulateFirstFragment,
        Bool resetAtBlockBoundary
    );

    method Action prepareLookahead(
        MatrixExtent kOrigin,
        MatrixExtent reductionCount,
        MatrixExtent blockSize,
        Bool usesScale,
        Bool accumulateFirstFragment,
        Bool resetAtBlockBoundary
    );
    method Bool lookaheadValid;
    method MatrixExtent lookaheadKStart;
    method BoundedCount#(arrayDim) lookaheadKCount;
    method ScaleBlockIndex lookaheadBlockIndex;
    method MatrixExtent lookaheadBlockRemaining;
    method Action startPrepared;

    method Bool fragmentValid;
    method MatrixExtent fragmentKStart;
    method BoundedCount#(arrayDim) fragmentKCount;
    method Bool fragmentAccumulate;
    method Bool fragmentEndsBlock;
    method ScaleBlockIndex fragmentBlockIndex;
    method MatrixExtent fragmentBlockRemaining;
    method Bool hasNextFragment;
    method MatrixExtent nextFragmentKStart;
    method BoundedCount#(arrayDim) nextFragmentKCount;
    method Action acceptFragment;
    method Action completeFragment;

    method Bool active;
    method Bool done;
    method Action acknowledge;
    method UInt#(8) debugState;
endinterface

interface BlockPositionIfc;
    method Action start(MatrixExtent origin, MatrixExtent blockSize);
    method Bool ready;
    method ScaleBlockIndex blockIndex;
    method MatrixExtent offset;
endinterface

// One restoring-division bit per RTL cycle. Independent instances allow
// current and lookahead preparation to overlap without arbitration.
module mkBlockPosition(BlockPositionIfc);
    Reg#(MatrixExtent) dividendReg <- mkReg(0);
    Reg#(MatrixExtent) divisorReg <- mkReg(1);
    Reg#(ScaleBlockIndex) quotientReg <- mkReg(0);
    Reg#(MatrixExtent) remainderReg <- mkReg(0);
    Reg#(UInt#(6)) stepsReg <- mkReg(0);

    rule divide (stepsReg != 0);
        UInt#(33) shifted = (zeroExtend(remainderReg) << 1)
            | zeroExtend(dividendReg >> 31);
        Bool subtract = shifted >= zeroExtend(divisorReg);
        remainderReg <= truncate(subtract
            ? shifted - zeroExtend(divisorReg) : shifted);
        quotientReg <= (quotientReg << 1) | (subtract ? 1 : 0);
        dividendReg <= dividendReg << 1;
        stepsReg <= stepsReg - 1;
    endrule

    method Action start(MatrixExtent origin, MatrixExtent blockSize)
            if (stepsReg == 0);
        dynamicAssert(blockSize > 0, "block position divisor must be positive");
        dividendReg <= origin;
        divisorReg <= blockSize;
        quotientReg <= 0;
        remainderReg <= origin < blockSize ? origin : 0;
        stepsReg <= origin < blockSize ? 0 : 32;
    endmethod
    method Bool ready = stepsReg == 0;
    method ScaleBlockIndex blockIndex if (stepsReg == 0);
        return quotientReg;
    endmethod
    method MatrixExtent offset if (stepsReg == 0);
        return remainderReg;
    endmethod
endmodule

function MatrixExtent boundedFragmentCount(
    MatrixExtent dimension, MatrixExtent remainingK,
    MatrixExtent remainingInBlock, Bool usesScale
);
    MatrixExtent count = remainingK < dimension ? remainingK : dimension;
    return usesScale && remainingInBlock < count ? remainingInBlock : count;
endfunction

function MatrixExtent nextKFragmentCount(
    MatrixExtent arrayDimension,
    MatrixExtent kStart,
    MatrixExtent totalK,
    MatrixExtent blockSize,
    Bool usesScale
);
    MatrixExtent remainingK = totalK - kStart;
    MatrixExtent fragmentCount = remainingK < arrayDimension
        ? remainingK
        : arrayDimension;

    if (usesScale) begin
        MatrixExtent remainingInBlock =
            blockSize - (kStart % blockSize);
        fragmentCount = fragmentCount < remainingInBlock
            ? fragmentCount
            : remainingInBlock;
    end

    return fragmentCount;
endfunction

module mkWorkScheduler(WorkSchedulerIfc#(arrayDim)) provisos (
    Add#(1, arrayDimMinusOne, arrayDim),
    Add#(
        boundedCountPadding,
        TLog#(arrayDim),
        TLog#(TAdd#(arrayDim, 1))
    ),
    Add#(
        TLog#(TAdd#(arrayDim, 1)),
        countToExtentPadding,
        32
    )
);
    Reg#(WorkSchedulerState) stateReg <- mkReg(WorkIdle);
    Reg#(MatrixExtent) totalKReg <- mkReg(0);
    Reg#(MatrixExtent) blockSizeReg <- mkReg(0);
    Reg#(Bool) usesScaleReg <- mkReg(False);
    Reg#(Bool) firstFragmentReg <- mkReg(True);
    Reg#(Bool) accumulateFirstReg <- mkReg(False);
    Reg#(Bool) resetAtBlockBoundaryReg <- mkReg(False);
    Reg#(MatrixExtent) kStartReg <- mkReg(0);
    Reg#(ScaleBlockIndex) blockIndexReg <- mkReg(0);
    Reg#(MatrixExtent) blockRemainingReg <- mkReg(0);
    BlockPositionIfc initialPosition <- mkBlockPosition;
    BlockPositionIfc lookaheadPosition <- mkBlockPosition;
    Reg#(Bool) lookaheadPendingReg <- mkReg(False);
    Reg#(Bool) lookaheadValidReg <- mkReg(False);
    Reg#(MatrixExtent) lookaheadKOriginReg <- mkReg(0);
    Reg#(MatrixExtent) lookaheadReductionReg <- mkReg(0);
    Reg#(MatrixExtent) lookaheadBlockSizeReg <- mkReg(0);
    Reg#(Bool) lookaheadUsesScaleReg <- mkReg(False);
    Reg#(Bool) lookaheadAccumulateReg <- mkReg(False);
    Reg#(Bool) lookaheadResetAtBlockBoundaryReg <- mkReg(False);
    Reg#(ScaleBlockIndex) lookaheadBlockIndexReg <- mkReg(0);
    Reg#(MatrixExtent) lookaheadBlockRemainingReg <- mkReg(0);

    function MatrixExtent currentCount();
        return boundedFragmentCount(
            fromInteger(valueOf(arrayDim)),
            totalKReg - kStartReg,
            blockRemainingReg,
            usesScaleReg
        );
    endfunction

    rule finishPreparation (stateReg == WorkPrepare && initialPosition.ready);
        blockIndexReg <= initialPosition.blockIndex;
        blockRemainingReg <= blockSizeReg - initialPosition.offset;
        stateReg <= WorkOfferFragment;
    endrule

    rule finishLookaheadPreparation (
        lookaheadPendingReg && !lookaheadValidReg && lookaheadPosition.ready
    );
        lookaheadBlockIndexReg <= lookaheadPosition.blockIndex;
        lookaheadBlockRemainingReg <= lookaheadBlockSizeReg - lookaheadPosition.offset;
        lookaheadPendingReg <= False;
        lookaheadValidReg <= True;
    endrule

    method Action prepareLookahead(
        MatrixExtent kOrigin,
        MatrixExtent reductionCount,
        MatrixExtent blockSize,
        Bool usesScale,
        Bool accumulateFirstFragment,
        Bool resetAtBlockBoundary
    ) if (!lookaheadValidReg && !lookaheadPendingReg);
        dynamicAssert(reductionCount > 0, "lookahead K must be positive");
        dynamicAssert(!usesScale || blockSize > 0,
                      "scaled lookahead block size must be positive");
        dynamicAssert(reductionCount <= maxBound - kOrigin,
                      "lookahead K extent overflows");
        lookaheadKOriginReg <= kOrigin;
        lookaheadReductionReg <= reductionCount;
        lookaheadBlockSizeReg <= blockSize;
        lookaheadUsesScaleReg <= usesScale;
        lookaheadAccumulateReg <= accumulateFirstFragment;
        lookaheadResetAtBlockBoundaryReg <= resetAtBlockBoundary;
        Bool prepare = usesScale && kOrigin >= blockSize;
        lookaheadValidReg <= !prepare;
        lookaheadPendingReg <= prepare;
        lookaheadBlockIndexReg <= 0;
        lookaheadBlockRemainingReg <= usesScale ? blockSize - kOrigin : 0;
        if (prepare) lookaheadPosition.start(kOrigin, blockSize);
    endmethod

    method Bool lookaheadValid = lookaheadValidReg;
    method MatrixExtent lookaheadKStart if (lookaheadValidReg);
        return lookaheadKOriginReg;
    endmethod
    method BoundedCount#(arrayDim) lookaheadKCount if (lookaheadValidReg);
        return truncate(boundedFragmentCount(
            fromInteger(valueOf(arrayDim)), lookaheadReductionReg,
            lookaheadBlockRemainingReg, lookaheadUsesScaleReg));
    endmethod
    method ScaleBlockIndex lookaheadBlockIndex if (lookaheadValidReg);
        return lookaheadBlockIndexReg;
    endmethod
    method MatrixExtent lookaheadBlockRemaining if (lookaheadValidReg);
        return lookaheadBlockRemainingReg;
    endmethod
    method Action startPrepared if (stateReg == WorkIdle);
        dynamicAssert(lookaheadValidReg, "no prepared lookahead fragment");
        totalKReg <= lookaheadKOriginReg + lookaheadReductionReg;
        blockSizeReg <= lookaheadBlockSizeReg;
        usesScaleReg <= lookaheadUsesScaleReg;
        firstFragmentReg <= True;
        accumulateFirstReg <= lookaheadAccumulateReg;
        resetAtBlockBoundaryReg <= lookaheadResetAtBlockBoundaryReg;
        kStartReg <= lookaheadKOriginReg;
        blockIndexReg <= lookaheadBlockIndexReg;
        blockRemainingReg <= lookaheadBlockRemainingReg;
        lookaheadValidReg <= False;
        stateReg <= WorkOfferFragment;
    endmethod

    method Action start(
        MatrixExtent kOrigin,
        MatrixExtent reductionCount,
        MatrixExtent blockSize,
        Bool usesScale,
        Bool accumulateFirstFragment,
        Bool resetAtBlockBoundary
    ) if (stateReg == WorkIdle);
        dynamicAssert(reductionCount > 0, "work K must be positive");
        dynamicAssert(
            !usesScale || blockSize > 0,
            "scaled work block size must be positive"
        );

        dynamicAssert(reductionCount <= maxBound - kOrigin,
                      "work K extent overflows");
        totalKReg <= kOrigin + reductionCount;
        blockSizeReg <= blockSize;
        usesScaleReg <= usesScale;
        firstFragmentReg <= True;
        accumulateFirstReg <= accumulateFirstFragment;
        resetAtBlockBoundaryReg <= resetAtBlockBoundary;
        kStartReg <= kOrigin;
        blockIndexReg <= 0;
        blockRemainingReg <= usesScale ? blockSize - kOrigin : 0;
        Bool prepare = usesScale && kOrigin >= blockSize;
        if (prepare) initialPosition.start(kOrigin, blockSize);
        stateReg <= prepare ? WorkPrepare : WorkOfferFragment;
    endmethod

    method Bool fragmentValid = stateReg == WorkOfferFragment;
    method MatrixExtent fragmentKStart if (stateReg == WorkOfferFragment);
        return kStartReg;
    endmethod
    method BoundedCount#(arrayDim) fragmentKCount
            if (stateReg == WorkOfferFragment);
        MatrixExtent count = currentCount;
        return truncate(count);
    endmethod
    method Bool fragmentAccumulate if (stateReg == WorkOfferFragment);
        Bool startsBlock = usesScaleReg
            && blockRemainingReg == blockSizeReg;
        return resetAtBlockBoundaryReg && startsBlock
            ? False
            : !firstFragmentReg || accumulateFirstReg;
    endmethod

    method Bool fragmentEndsBlock if (stateReg == WorkOfferFragment);
        MatrixExtent count = currentCount;
        MatrixExtent nextStart = kStartReg + count;
        return usesScaleReg
            && (nextStart >= totalKReg || count == blockRemainingReg);
    endmethod

    method ScaleBlockIndex fragmentBlockIndex
            if (stateReg == WorkOfferFragment);
        return blockIndexReg;
    endmethod
    method MatrixExtent fragmentBlockRemaining
            if (stateReg == WorkOfferFragment);
        return blockRemainingReg;
    endmethod

    method Bool hasNextFragment
            if (stateReg == WorkOfferFragment
                || stateReg == WorkWaitFragment);
        MatrixExtent nextStart = kStartReg + currentCount;
        return nextStart < totalKReg;
    endmethod

    method MatrixExtent nextFragmentKStart
            if (stateReg == WorkOfferFragment
                || stateReg == WorkWaitFragment);
        return kStartReg + currentCount;
    endmethod

    method BoundedCount#(arrayDim) nextFragmentKCount
            if (stateReg == WorkOfferFragment
                || stateReg == WorkWaitFragment);
        MatrixExtent count = currentCount;
        MatrixExtent nextRemaining = count == blockRemainingReg
            ? blockSizeReg : blockRemainingReg - count;
        return truncate(boundedFragmentCount(fromInteger(valueOf(arrayDim)),
            totalKReg - (kStartReg + count), nextRemaining, usesScaleReg));
    endmethod

    method Action acceptFragment if (stateReg == WorkOfferFragment);
        MatrixExtent count = currentCount;
        dynamicAssert(count > 0, "K fragment must be positive");
        dynamicAssert(
            !usesScaleReg
                || count <= blockRemainingReg,
            "K fragment crosses a scale block"
        );
        stateReg <= WorkWaitFragment;
    endmethod

    method Action completeFragment if (stateReg == WorkWaitFragment);
        MatrixExtent count = currentCount;
        MatrixExtent nextStart = kStartReg + count;

        if (nextStart < totalKReg) begin
            kStartReg <= nextStart;
            if (usesScaleReg) begin
                Bool endsBlock = count == blockRemainingReg;
                blockIndexReg <= endsBlock ? blockIndexReg + 1 : blockIndexReg;
                blockRemainingReg <= endsBlock ? blockSizeReg : blockRemainingReg - count;
            end
            firstFragmentReg <= False;
            stateReg <= WorkOfferFragment;
        end
        else begin
            stateReg <= WorkDone;
        end
    endmethod

    method Bool active = stateReg != WorkIdle && stateReg != WorkDone;
    method Bool done = stateReg == WorkDone;

    method Action acknowledge if (stateReg == WorkDone);
        stateReg <= WorkIdle;
    endmethod

    method UInt#(8) debugState = unpack(zeroExtend(pack(stateReg)));
endmodule

endpackage
