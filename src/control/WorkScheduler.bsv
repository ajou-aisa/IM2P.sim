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
        Bool accumulateFirstFragment
    );

    method Bool fragmentValid;
    method MatrixExtent fragmentKStart;
    method BoundedCount#(arrayDim) fragmentKCount;
    method Bool fragmentAccumulate;
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
    Reg#(MatrixExtent) kStartReg <- mkReg(0);

    function MatrixExtent countAt(MatrixExtent kStart);
        return nextKFragmentCount(
            fromInteger(valueOf(arrayDim)),
            kStart,
            totalKReg,
            blockSizeReg,
            usesScaleReg
        );
    endfunction

    method Action start(
        MatrixExtent kOrigin,
        MatrixExtent reductionCount,
        MatrixExtent blockSize,
        Bool usesScale,
        Bool accumulateFirstFragment
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
        return !firstFragmentReg || accumulateFirstReg;
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
