package WorkScheduler;

import Assert::*;

import Types::*;

typedef enum {
    WorkIdle,
    WorkOfferFragment,
    WorkWaitFragment,
    WorkDone
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
    method Action startPrepared;

    method Bool fragmentValid;
    method MatrixExtent fragmentKStart;
    method BoundedCount#(arrayDim) fragmentKCount;
    method Bool fragmentAccumulate;
    method Bool fragmentEndsBlock;
    method ScaleBlockIndex fragmentBlockIndex;
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
    Reg#(MatrixExtent) kOriginReg <- mkReg(0);
    Reg#(MatrixExtent) totalKReg <- mkReg(0);
    Reg#(MatrixExtent) blockSizeReg <- mkReg(0);
    Reg#(Bool) usesScaleReg <- mkReg(False);
    Reg#(Bool) firstFragmentReg <- mkReg(True);
    Reg#(Bool) accumulateFirstReg <- mkReg(False);
    Reg#(Bool) resetAtBlockBoundaryReg <- mkReg(False);
    Reg#(MatrixExtent) kStartReg <- mkReg(0);
    Reg#(Bool) lookaheadValidReg <- mkReg(False);
    Reg#(MatrixExtent) lookaheadKOriginReg <- mkReg(0);
    Reg#(MatrixExtent) lookaheadReductionReg <- mkReg(0);
    Reg#(MatrixExtent) lookaheadBlockSizeReg <- mkReg(0);
    Reg#(Bool) lookaheadUsesScaleReg <- mkReg(False);
    Reg#(Bool) lookaheadAccumulateReg <- mkReg(False);
    Reg#(Bool) lookaheadResetAtBlockBoundaryReg <- mkReg(False);

    function MatrixExtent countAt(MatrixExtent kStart);
        return nextKFragmentCount(
            fromInteger(valueOf(arrayDim)),
            kStart,
            totalKReg,
            blockSizeReg,
            usesScaleReg
        );
    endfunction

    method Action prepareLookahead(
        MatrixExtent kOrigin,
        MatrixExtent reductionCount,
        MatrixExtent blockSize,
        Bool usesScale,
        Bool accumulateFirstFragment,
        Bool resetAtBlockBoundary
    ) if (!lookaheadValidReg);
        dynamicAssert(reductionCount > 0, "lookahead K must be positive");
        dynamicAssert(!usesScale || blockSize > 0,
                      "scaled lookahead block size must be positive");
        lookaheadKOriginReg <= kOrigin;
        lookaheadReductionReg <= reductionCount;
        lookaheadBlockSizeReg <= blockSize;
        lookaheadUsesScaleReg <= usesScale;
        lookaheadAccumulateReg <= accumulateFirstFragment;
        lookaheadResetAtBlockBoundaryReg <= resetAtBlockBoundary;
        lookaheadValidReg <= True;
    endmethod

    method Bool lookaheadValid = lookaheadValidReg;
    method MatrixExtent lookaheadKStart if (lookaheadValidReg);
        return lookaheadKOriginReg;
    endmethod
    method BoundedCount#(arrayDim) lookaheadKCount if (lookaheadValidReg);
        return truncate(nextKFragmentCount(
            fromInteger(valueOf(arrayDim)), lookaheadKOriginReg,
            lookaheadKOriginReg + lookaheadReductionReg,
            lookaheadBlockSizeReg, lookaheadUsesScaleReg));
    endmethod
    method Action startPrepared if (stateReg == WorkIdle);
        dynamicAssert(lookaheadValidReg, "no prepared lookahead fragment");
        kOriginReg <= lookaheadKOriginReg;
        totalKReg <= lookaheadKOriginReg + lookaheadReductionReg;
        blockSizeReg <= lookaheadBlockSizeReg;
        usesScaleReg <= lookaheadUsesScaleReg;
        firstFragmentReg <= True;
        accumulateFirstReg <= lookaheadAccumulateReg;
        resetAtBlockBoundaryReg <= lookaheadResetAtBlockBoundaryReg;
        kStartReg <= lookaheadKOriginReg;
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

        kOriginReg <= kOrigin;
        totalKReg <= kOrigin + reductionCount;
        blockSizeReg <= blockSize;
        usesScaleReg <= usesScale;
        firstFragmentReg <= True;
        accumulateFirstReg <= accumulateFirstFragment;
        resetAtBlockBoundaryReg <= resetAtBlockBoundary;
        kStartReg <= kOrigin;
        stateReg <= WorkOfferFragment;
    endmethod

    method Bool fragmentValid = stateReg == WorkOfferFragment;
    method MatrixExtent fragmentKStart if (stateReg == WorkOfferFragment);
        return kStartReg;
    endmethod
    method BoundedCount#(arrayDim) fragmentKCount
            if (stateReg == WorkOfferFragment);
        MatrixExtent count = countAt(kStartReg);
        return truncate(count);
    endmethod
    method Bool fragmentAccumulate if (stateReg == WorkOfferFragment);
        MatrixExtent safeBlockSize = blockSizeReg == 0 ? 1 : blockSizeReg;
        Bool startsBlock = usesScaleReg
            && kStartReg % safeBlockSize == 0;
        return resetAtBlockBoundaryReg && startsBlock
            ? False
            : !firstFragmentReg || accumulateFirstReg;
    endmethod

    method Bool fragmentEndsBlock if (stateReg == WorkOfferFragment);
        MatrixExtent nextStart = kStartReg + countAt(kStartReg);
        MatrixExtent safeBlockSize = blockSizeReg == 0 ? 1 : blockSizeReg;
        return usesScaleReg
            && (nextStart >= totalKReg || nextStart % safeBlockSize == 0);
    endmethod

    method ScaleBlockIndex fragmentBlockIndex
            if (stateReg == WorkOfferFragment);
        MatrixExtent safeBlockSize = blockSizeReg == 0 ? 1 : blockSizeReg;
        return usesScaleReg ? kStartReg / safeBlockSize : 0;
    endmethod

    method Bool hasNextFragment
            if (stateReg == WorkOfferFragment
                || stateReg == WorkWaitFragment);
        MatrixExtent nextStart = kStartReg + countAt(kStartReg);
        return nextStart < totalKReg;
    endmethod

    method MatrixExtent nextFragmentKStart
            if (stateReg == WorkOfferFragment
                || stateReg == WorkWaitFragment);
        return kStartReg + countAt(kStartReg);
    endmethod

    method BoundedCount#(arrayDim) nextFragmentKCount
            if (stateReg == WorkOfferFragment
                || stateReg == WorkWaitFragment);
        MatrixExtent nextStart = kStartReg + countAt(kStartReg);
        return truncate(countAt(nextStart));
    endmethod

    method Action acceptFragment if (stateReg == WorkOfferFragment);
        MatrixExtent count = countAt(kStartReg);
        dynamicAssert(count > 0, "K fragment must be positive");
        dynamicAssert(
            !usesScaleReg
                || count <= blockSizeReg - (kStartReg % blockSizeReg),
            "K fragment crosses a scale block"
        );
        stateReg <= WorkWaitFragment;
    endmethod

    method Action completeFragment if (stateReg == WorkWaitFragment);
        MatrixExtent count = countAt(kStartReg);
        MatrixExtent nextStart = kStartReg + count;

        if (nextStart < totalKReg) begin
            kStartReg <= nextStart;
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
