package PE;

import Vector::*;
import Arithmetic::*;

// -----------------------------------------------------------------------------
// Registered weight-stationary Processing Element
// -----------------------------------------------------------------------------
//
// 전형적인 WS systolic PE의 A/B/D/C 의미를 따른다.
//
//   A : activation. 오른쪽 PE로 전달한다.
//   B : stationary weight. 실행 전에 local register에 preload한다.
//   D : 위쪽 PE에서 들어오는 partial sum이다.
//   C : D + A x B. 아래쪽 PE로 전달한다.
//
// A와 C를 동일한 peLatency만큼 register forwarding하므로 수평/수직 wavefront의
// 상대 timing이 유지된다. 현재 arithmeticMultiply/arithmeticAccumulate 자체는
// stage 0의 조합 연산이다. peLatency를 늘리는 것만으로 연산기가 자동 retiming되는
// 것은 아니며, 실제 multi-cycle FP 연산은 Arithmetic 구현을 교체해야 한다.

interface PEIfc#(
    numeric type peLatency,
    type input_t,
    type weight_t,
    type product_t,
    type acc_t
);
    method Action loadWeight(weight_t weight);
    method Action invalidateWeight;

    // activation/partial pipeline만 비우고 stationary weight는 유지한다.
    method Action clearPipeline;

    method Action step(
        Maybe#(input_t) activationIn,
        Maybe#(acc_t) partialIn
    );

    method Maybe#(input_t) activationOut;
    method Maybe#(acc_t) partialOut;
    method Bool weightLoaded;
endinterface

module mkPE(PEIfc#(
    peLatency,
    input_t,
    weight_t,
    product_t,
    acc_t
)) provisos (
    Add#(1, peLatencyMinusOne, peLatency),
    Bits#(input_t, inputBits),
    Bits#(weight_t, weightBits),
    Bits#(acc_t, accBits),
    Multiplier#(input_t, weight_t, product_t),
    ProductAccumulator#(product_t, acc_t)
);
    Reg#(weight_t) weightReg <- mkRegU;
    Reg#(Bool) weightValidReg <- mkReg(False);

    Vector#(peLatency, Reg#(Maybe#(input_t))) activationPipe <-
        replicateM(mkReg(tagged Invalid));
    Vector#(peLatency, Reg#(Maybe#(acc_t))) partialPipe <-
        replicateM(mkReg(tagged Invalid));

    method Action loadWeight(weight_t weight);
        weightReg <= weight;
        weightValidReg <= True;
    endmethod

    method Action invalidateWeight;
        weightValidReg <= False;
    endmethod

    method Action clearPipeline;
        for (Integer stage = 0;
                stage < valueOf(peLatency);
                stage = stage + 1) begin
            activationPipe[stage] <= tagged Invalid;
            partialPipe[stage] <= tagged Invalid;
        end
    endmethod

    method Action step(
        Maybe#(input_t) activationIn,
        Maybe#(acc_t) partialIn
    );
        Maybe#(acc_t) nextPartial = tagged Invalid;

        if (isValid(activationIn) && isValid(partialIn) && weightValidReg) begin
            input_t activation = fromMaybe(?, activationIn);
            acc_t partial = fromMaybe(?, partialIn);
            product_t product = arithmeticMultiply(activation, weightReg);
            nextPartial = tagged Valid arithmeticAccumulate(partial, product);
        end

        activationPipe[0] <= activationIn;
        partialPipe[0] <= nextPartial;

        for (Integer stage = 1;
                stage < valueOf(peLatency);
                stage = stage + 1) begin
            activationPipe[stage] <= activationPipe[stage - 1];
            partialPipe[stage] <= partialPipe[stage - 1];
        end
    endmethod

    method Maybe#(input_t) activationOut =
        activationPipe[valueOf(peLatency) - 1];

    method Maybe#(acc_t) partialOut =
        partialPipe[valueOf(peLatency) - 1];

    method Bool weightLoaded = weightValidReg;
endmodule

endpackage
