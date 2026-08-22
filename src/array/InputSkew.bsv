package InputSkew;

import Vector::*;
import Arithmetic::*;

// -----------------------------------------------------------------------------
// Logical activation row를 systolic wavefront로 변환하는 입력 정렬기
// -----------------------------------------------------------------------------
//
// 상위 Core는 activation matrix의 한 logical row를 정상적인 Vector로 공급한다.
// PE hop latency가 L일 때 PE(k,j)에서 activation과 initial partial token이 같은
// cycle에 만나려면 array edge에서 다음 지연이 필요하다.
//
//   activation input for PE row k : k * L cycle
//   initial partial for column j   : j * L cycle
//
// activations[index]는 PE row index, partials[index]는 output column index를
// 의미한다. 두 boundary Vector가 같은 크기라서 동일한 index loop로 정렬할 뿐,
// 공간적으로 같은 신호를 뜻하지는 않는다.
//
// InputSkew는 이 timing 변환만 담당한다. PE 배치와 MAC 연산을 알지 않으므로
// SystolicArray와 독립적으로 검증할 수 있다.

typedef struct {
    Vector#(arrayDim, Maybe#(input_t)) activations;
    Vector#(arrayDim, Maybe#(acc_t)) partials;
} SkewedArrayInputs#(
    numeric type arrayDim,
    type input_t,
    type acc_t
) deriving (Bits);

interface InputSkewIfc#(
    numeric type arrayDim,
    numeric type peLatency,
    type input_t,
    type acc_t
);
    method Action clear;

    // Invalid row는 drain bubble이다. 반환값은 현재 입력과 기존 delay register를
    // 조합한 이번 cycle의 array boundary token이다.
    method ActionValue#(SkewedArrayInputs#(
        arrayDim,
        input_t,
        acc_t
    )) step(Maybe#(Vector#(arrayDim, input_t)) activationRow);
endinterface

module mkInputSkew(InputSkewIfc#(
    arrayDim,
    peLatency,
    input_t,
    acc_t
)) provisos (
    Add#(1, arrayDimMinusOne, arrayDim),
    Add#(1, peLatencyMinusOne, peLatency),
    Mul#(arrayDim, peLatency, skewDepth),
    Bits#(input_t, inputBits),
    Bits#(acc_t, accBits),
    AccumulatorArithmetic#(acc_t)
);
    // 모든 boundary token은 같은 logical row에서 나오므로 row 전체를 한 번만
    // 지연한다. Boundary별 scalar pipeline은 동일 row를 arrayDim번 복제하고
    // scheduler write surface를 O(arrayDim^2)로 키운다.
    Vector#(
        skewDepth,
        Reg#(Maybe#(Vector#(arrayDim, input_t)))
    ) rowDelay <- replicateM(mkReg(tagged Invalid));

    method Action clear;
        for (Integer stage = 0;
                stage < valueOf(skewDepth);
                stage = stage + 1) begin
            rowDelay[stage] <= tagged Invalid;
        end
    endmethod

    method ActionValue#(SkewedArrayInputs#(
        arrayDim,
        input_t,
        acc_t
    )) step(Maybe#(Vector#(arrayDim, input_t)) activationRow);
        Vector#(arrayDim, Maybe#(input_t)) skewedActivations = newVector;
        Vector#(arrayDim, Maybe#(acc_t)) skewedPartials = newVector;

        for (Integer boundaryIndex = 0;
                boundaryIndex < valueOf(arrayDim);
                boundaryIndex = boundaryIndex + 1) begin
            Integer delayCycles = boundaryIndex * valueOf(peLatency);
            Maybe#(Vector#(arrayDim, input_t)) delayedRow =
                delayCycles == 0
                    ? activationRow
                    : rowDelay[delayCycles - 1];

            if (isValid(delayedRow)) begin
                Vector#(arrayDim, input_t) row = fromMaybe(?, delayedRow);
                skewedActivations[boundaryIndex] =
                    tagged Valid row[boundaryIndex];
                skewedPartials[boundaryIndex] =
                    tagged Valid accumulatorZero();
            end
            else begin
                skewedActivations[boundaryIndex] = tagged Invalid;
                skewedPartials[boundaryIndex] = tagged Invalid;
            end
        end

        rowDelay[0] <= activationRow;
        for (Integer stage = 1;
                stage < valueOf(skewDepth);
                stage = stage + 1) begin
            rowDelay[stage] <= rowDelay[stage - 1];
        end

        return SkewedArrayInputs {
            activations: skewedActivations,
            partials: skewedPartials
        };
    endmethod
endmodule

endpackage
