package Arithmetic;

import FloatingPoint::*;

// -----------------------------------------------------------------------------
// Systolic datapath 산술 추상화
// -----------------------------------------------------------------------------
//
// activation, weight, product, accumulator를 서로 다른 타입으로 분리한다.
// 따라서 INT8 x INT8 configuration은 실제 8x8 multiplier, 16-bit full product,
// 32-bit partial/accumulator를 표현할 수 있다.
//
// Runtime vector scaling은 array 밖 VectorUnit의 책임이다. FLOAT arithmetic을
// 선택해도 scale multiplier나 shifter가 이 패키지에서 따라오지 않는다.

// A와 stationary weight B를 곱해 full-precision product를 만든다.
typeclass Multiplier#(
    type input_t,
    type weight_t,
    type product_t
);
    function product_t arithmeticMultiply(input_t activation, weight_t weight);
endtypeclass

// 위쪽에서 전달된 partial D에 product를 더해 C를 만든다.
typeclass ProductAccumulator#(
    type product_t,
    type acc_t
);
    function acc_t arithmeticAccumulate(acc_t partial, product_t product);
endtypeclass

// VectorUnit output을 기존 accumulator state와 더할 때 사용하는 공통 연산이다.
typeclass AccumulatorArithmetic#(type acc_t);
    function acc_t accumulatorZero();
    function acc_t accumulatorAdd(acc_t left, acc_t right);
endtypeclass

// -----------------------------------------------------------------------------
// Signed integer instances
// -----------------------------------------------------------------------------

instance Multiplier#(
    Int#(inputWidth),
    Int#(weightWidth),
    Int#(productWidth)
) provisos (
    Add#(inputWidth, weightWidth, productWidth)
);
    function Int#(productWidth) arithmeticMultiply(
        Int#(inputWidth) activation,
        Int#(weightWidth) weight
    );
        return signedMul(activation, weight);
    endfunction
endinstance

instance ProductAccumulator#(
    Int#(productWidth),
    Int#(accWidth)
) provisos (
    Add#(productWidth, productPadding, accWidth)
);
    function Int#(accWidth) arithmeticAccumulate(
        Int#(accWidth) partial,
        Int#(productWidth) product
    );
        return partial + signExtend(product);
    endfunction
endinstance

instance AccumulatorArithmetic#(Int#(accWidth));
    function Int#(accWidth) accumulatorZero();
        return 0;
    endfunction

    function Int#(accWidth) accumulatorAdd(
        Int#(accWidth) left,
        Int#(accWidth) right
    );
        return left + right;
    endfunction
endinstance

// -----------------------------------------------------------------------------
// BSC FloatingPoint library 기반 homogeneous FP instances
// -----------------------------------------------------------------------------
//
// 현재 reference implementation은 FP16->FP16, FP32->FP32처럼 입력/weight/
// product/accumulator가 같은 floating-point format인 경우를 제공한다. FP16 input과
// FP32 accumulation을 함께 쓰려면 conversion 및 별도 FMA pipeline instance를
// 추가해야 한다.
//
// multFP/addFP는 조합 함수다. PE의 peLatency를 늘리는 것만으로 이 조합 연산이
// 자동 pipeline되지 않는다. 실제 Fmax/area 평가에서는 vendor FPU 또는 명시적인
// pipelined operator로 이 instance를 교체해야 한다.

instance Multiplier#(
    FloatingPoint#(exponentWidth, fractionWidth),
    FloatingPoint#(exponentWidth, fractionWidth),
    FloatingPoint#(exponentWidth, fractionWidth)
) provisos (
    Add#(
        multiplyPadding,
        TLog#(TAdd#(
            1,
            TAdd#(
                TAdd#(fractionWidth, 1),
                TAdd#(fractionWidth, 1)
            )
        )),
        TAdd#(exponentWidth, 1)
    )
);
    function FloatingPoint#(exponentWidth, fractionWidth) arithmeticMultiply(
        FloatingPoint#(exponentWidth, fractionWidth) activation,
        FloatingPoint#(exponentWidth, fractionWidth) weight
    );
        return tpl_1(multFP(activation, weight, Rnd_Nearest_Even));
    endfunction
endinstance

instance ProductAccumulator#(
    FloatingPoint#(exponentWidth, fractionWidth),
    FloatingPoint#(exponentWidth, fractionWidth)
) provisos (
    Add#(
        addPadding,
        TLog#(TAdd#(1, TAdd#(fractionWidth, 5))),
        TAdd#(exponentWidth, 1)
    )
);
    function FloatingPoint#(exponentWidth, fractionWidth) arithmeticAccumulate(
        FloatingPoint#(exponentWidth, fractionWidth) partial,
        FloatingPoint#(exponentWidth, fractionWidth) product
    );
        return tpl_1(addFP(partial, product, Rnd_Nearest_Even));
    endfunction
endinstance

instance AccumulatorArithmetic#(
    FloatingPoint#(exponentWidth, fractionWidth)
) provisos (
    Add#(
        addPadding,
        TLog#(TAdd#(1, TAdd#(fractionWidth, 5))),
        TAdd#(exponentWidth, 1)
    )
);
    function FloatingPoint#(exponentWidth, fractionWidth) accumulatorZero();
        return zero(False);
    endfunction

    function FloatingPoint#(exponentWidth, fractionWidth) accumulatorAdd(
        FloatingPoint#(exponentWidth, fractionWidth) left,
        FloatingPoint#(exponentWidth, fractionWidth) right
    );
        return tpl_1(addFP(left, right, Rnd_Nearest_Even));
    endfunction
endinstance

endpackage
