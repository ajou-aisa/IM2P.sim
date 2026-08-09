package VectorUnit;

import Assert::*;
import Vector::*;

import Types::*;
import Scale::*;

// VectorUnit이 한 physical-lane group에서 만든 sparse contribution Vector다.
// 반환 Vector의 index는 원래 SystolicArray output column index를 유지한다.
typedef struct {
    Vector#(arrayDim, Bool) valids;
    Vector#(arrayDim, acc_t) contributions;
} VectorResult#(
    numeric type arrayDim,
    type acc_t
) deriving (Bits);

interface VectorUnitIfc#(
    type format_t,
    numeric type arrayDim,
    numeric type vectorLanes,
    type acc_t,
    type scale_t
);
    method Bool ready;

    // 현재 numeric format이 runtime Multiply/Shift를 지원하는지 나타낸다.
    method Bool scalingSupported;

    // complete partial sum에 선택 연산만 적용한다.
    // Accumulator 주소, 기존 값, 누산 여부는 이 모듈의 입력이 아니다.
    method Action put(
        Vector#(arrayDim, Bool) valids,
        Vector#(arrayDim, acc_t) partialSums,
        Vector#(arrayDim, scale_t) scales,
        VectorOp op
    );

    method Bool resultValid;
    method VectorResult#(arrayDim, acc_t) result;
    method Action consume;
endinterface

module mkVectorUnit(VectorUnitIfc#(
    format_t,
    arrayDim,
    vectorLanes,
    acc_t,
    scale_t
)) provisos (
    // arrayDim개의 output column을 vectorLanes개의 physical lane으로 정확히 나눈다.
    Mul#(vectorGroups, vectorLanes, arrayDim),
    Add#(1, vectorGroupsMinusOne, vectorGroups),
    Add#(1, vectorLanesMinusOne, vectorLanes),
    Add#(1, arrayDimMinusOne, arrayDim),
    Bits#(format_t, formatBits),
    Bits#(acc_t, accBits),
    Bits#(scale_t, scaleBits),
    VectorScaleCapability#(format_t),
    VectorTransform#(format_t, acc_t, scale_t)
);
    // 하나의 full-width array result를 모든 physical-lane group이 소비할 때까지
    // 보존한다.
    Reg#(Bool) busyReg <- mkReg(False);
    Reg#(BoundedCount#(vectorGroups)) groupIndexReg <- mkReg(0);

    Reg#(Vector#(arrayDim, Bool)) validReg <- mkRegU;
    Reg#(Vector#(arrayDim, acc_t)) partialReg <- mkRegU;
    Reg#(Vector#(arrayDim, scale_t)) scaleReg <- mkRegU;
    Reg#(VectorOp) operationReg <- mkReg(VectorBypass);

    // Typeclass instance를 선택하기 위한 값이며 실제 데이터 연산에는 쓰지 않는다.
    format_t formatProxy = unpack(0);

    method Bool ready = !busyReg;
    method Bool scalingSupported = vectorScalingSupported(formatProxy);

    method Action put(
        Vector#(arrayDim, Bool) valids,
        Vector#(arrayDim, acc_t) partialSums,
        Vector#(arrayDim, scale_t) scales,
        VectorOp op
    ) if (!busyReg);
        dynamicAssert(
            anyTrue(valids),
            "VectorUnit request must contain at least one valid column"
        );
        dynamicAssert(
            op == VectorBypass || vectorScalingSupported(formatProxy),
            "selected format supports only VectorBypass"
        );

        validReg <= valids;
        partialReg <= partialSums;
        scaleReg <= scales;
        operationReg <= op;
        groupIndexReg <= 0;
        busyReg <= True;
    endmethod

    method Bool resultValid = busyReg;

    method VectorResult#(arrayDim, acc_t) result if (busyReg);
        // 현재 group에 속한 array output column들을 physical vector lane으로 모은다.
        Vector#(vectorLanes, Bool) selectedValid = replicate(False);
        Vector#(vectorLanes, acc_t) selectedPartial = replicate(unpack(0));
        Vector#(vectorLanes, scale_t) selectedScale = replicate(unpack(0));

        for (Integer group = 0;
                group < valueOf(vectorGroups);
                group = group + 1) begin
            if (groupIndexReg == fromInteger(group)) begin
                for (Integer vectorLane = 0;
                        vectorLane < valueOf(vectorLanes);
                        vectorLane = vectorLane + 1) begin
                    Integer arrayColumn =
                        group * valueOf(vectorLanes) + vectorLane;

                    selectedValid[vectorLane] = validReg[arrayColumn];
                    selectedPartial[vectorLane] = partialReg[arrayColumn];
                    selectedScale[vectorLane] = scaleReg[arrayColumn];
                end
            end
        end

        // 각 physical vector lane에 같은 runtime operation을 적용한다.
        Vector#(vectorLanes, acc_t) selectedContribution = newVector;
        for (Integer vectorLane = 0;
                vectorLane < valueOf(vectorLanes);
                vectorLane = vectorLane + 1) begin
            selectedContribution[vectorLane] = selectedValid[vectorLane]
                ? transformVectorElement(
                    formatProxy,
                    operationReg,
                    selectedPartial[vectorLane],
                    selectedScale[vectorLane]
                )
                : unpack(0);
        end

        // Accumulator bank와의 정적 column 대응을 유지하도록 결과를 원래
        // arrayDim 위치에 sparse vector로 되돌린다.
        Vector#(arrayDim, Bool) validsOut = replicate(False);
        Vector#(arrayDim, acc_t) contributionsOut = replicate(unpack(0));

        for (Integer group = 0;
                group < valueOf(vectorGroups);
                group = group + 1) begin
            if (groupIndexReg == fromInteger(group)) begin
                for (Integer vectorLane = 0;
                        vectorLane < valueOf(vectorLanes);
                        vectorLane = vectorLane + 1) begin
                    Integer arrayColumn =
                        group * valueOf(vectorLanes) + vectorLane;

                    validsOut[arrayColumn] = selectedValid[vectorLane];
                    contributionsOut[arrayColumn] =
                        selectedContribution[vectorLane];
                end
            end
        end

        return VectorResult {
            valids: validsOut,
            contributions: contributionsOut
        };
    endmethod

    method Action consume if (busyReg);
        if (groupIndexReg == fromInteger(valueOf(vectorGroups) - 1)) begin
            busyReg <= False;
            groupIndexReg <= 0;
        end
        else begin
            groupIndexReg <= groupIndexReg + 1;
        end
    endmethod

endmodule

endpackage
