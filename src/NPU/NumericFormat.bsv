package NumericFormat;

import FloatingPoint::*;

// 아래 두 타입은 실행 중 전달되는 값이 아니라 합성 시 수치 형식을 고르는 표식이다.
// 모듈 사용자는 format 자리에 INT 또는 FLOAT를 넣고 precision을 비트 수로 넣는다.
typedef struct { Bool unused; } INT deriving (Bits, Eq, FShow);
typedef struct { Bit#(2) unused; } FLOAT deriving (Bits, Eq, FShow);

// format을 타입에 남기는 nominal wrapper다. 실제 저장 field는 precision bit
// 하나뿐이므로 추가 하드웨어 없이 INT/FLOAT 교차 연결을 compile-time에 막는다.
typedef struct {
    Bit#(precision) bits;
} NumericElement#(
    type format,
    numeric type precision
) deriving (Bits, Eq, FShow);

function NumericElement#(format, precision) numericElement(
    Bit#(precision) bits
);
    return NumericElement { bits: bits };
endfunction

function Bit#(precision) numericBits(
    NumericElement#(format, precision) element
);
    return element.bits;
endfunction

// FLOAT는 총 비트 수만으로 지수부와 가수부를 임의 결정할 수 없다.
// 따라서 BSC가 제공하는 IEEE half/single/double 배치만 명시적으로 지원한다.
typeclass FloatLayout#(
    numeric type precision,
    numeric type exponentWidth,
    numeric type fractionWidth
) dependencies (precision determines (exponentWidth, fractionWidth));
endtypeclass

instance FloatLayout#(16, 5, 10);
endinstance

instance FloatLayout#(32, 8, 23);
endinstance

instance FloatLayout#(64, 11, 52);
endinstance

// format과 precision에 맞는 산술 정책이다. NumericFormat instance가 없으면
// 해당 조합은 elaboration 단계에서 거부된다.
typeclass NumericFormat#(type format, numeric type precision);
    function NumericElement#(format, precision) numericZero(format formatTag);
    function NumericElement#(format, precision) numericMac(
        format formatTag,
        NumericElement#(format, precision) x,
        NumericElement#(format, precision) weight,
        NumericElement#(format, precision) psum
    );
    function NumericElement#(format, precision) numericAdd(
        format formatTag,
        NumericElement#(format, precision) left,
        NumericElement#(format, precision) right
    );
    function NumericElement#(format, precision) numericScaleMac(
        format formatTag,
        NumericElement#(format, precision) value,
        UInt#(8) scale
    );
    function NumericElement#(format, precision) numericScaleShift(
        format formatTag,
        NumericElement#(format, precision) value,
        UInt#(8) scale
    );
endtypeclass

// INT 연산은 precision 폭에서 수행한다. 곱셈과 덧셈 overflow는 같은 폭으로
// 자연스럽게 wrap되며, 별도의 넓은 accumulator 타입은 만들지 않는다.
instance NumericFormat#(INT, precision);
    function NumericElement#(INT, precision) numericZero(INT formatTag);
        return numericElement(0);
    endfunction

    function NumericElement#(INT, precision) numericMac(
        INT formatTag,
        NumericElement#(INT, precision) x,
        NumericElement#(INT, precision) weight,
        NumericElement#(INT, precision) psum
    );
        Int#(precision) xValue = unpack(numericBits(x));
        Int#(precision) weightValue = unpack(numericBits(weight));
        Int#(precision) psumValue = unpack(numericBits(psum));
        return numericElement(pack(psumValue + xValue * weightValue));
    endfunction

    function NumericElement#(INT, precision) numericAdd(
        INT formatTag,
        NumericElement#(INT, precision) left,
        NumericElement#(INT, precision) right
    );
        Int#(precision) leftValue = unpack(numericBits(left));
        Int#(precision) rightValue = unpack(numericBits(right));
        return numericElement(pack(leftValue + rightValue));
    endfunction

    function NumericElement#(INT, precision) numericScaleMac(
        INT formatTag,
        NumericElement#(INT, precision) value,
        UInt#(8) scale
    );
        Int#(precision) signedValue = unpack(numericBits(value));
        Bit#(precision) scaleBits = 0;

        for (Integer bitIndex = 0;
                bitIndex < valueOf(TMin#(precision, 8));
                bitIndex = bitIndex + 1) begin
            scaleBits[bitIndex] = pack(scale)[bitIndex];
        end

        Int#(precision) scaleValue = unpack(scaleBits);
        return numericElement(pack(signedValue * scaleValue));
    endfunction

    function NumericElement#(INT, precision) numericScaleShift(
        INT formatTag,
        NumericElement#(INT, precision) value,
        UInt#(8) scale
    );
        Int#(precision) signedValue = unpack(numericBits(value));
        return numericElement(pack(signedValue << scale));
    endfunction
endinstance

// FLOAT MAC은 BSC 조합 함수 multFP/addFP를 nearest-even rounding으로 연결한다.
// Exception flag는 현재 PE 인터페이스에 없으므로 결과값만 전달한다.
instance NumericFormat#(FLOAT, precision) provisos (
    FloatLayout#(precision, exponentWidth, fractionWidth),
    Add#(
        addPadding,
        TLog#(TAdd#(1, TAdd#(fractionWidth, 5))),
        TAdd#(exponentWidth, 1)
    ),
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
    ),
    Add#(1, TAdd#(exponentWidth, fractionWidth), precision),
    FixedFloatCVT#(
        FloatingPoint#(exponentWidth, fractionWidth),
        UInt#(8)
    )
);
    function NumericElement#(FLOAT, precision) numericZero(FLOAT formatTag);
        FloatingPoint#(exponentWidth, fractionWidth) value = zero(False);
        return numericElement(pack(value));
    endfunction

    function NumericElement#(FLOAT, precision) numericMac(
        FLOAT formatTag,
        NumericElement#(FLOAT, precision) x,
        NumericElement#(FLOAT, precision) weight,
        NumericElement#(FLOAT, precision) psum
    );
        FloatingPoint#(exponentWidth, fractionWidth) xValue =
            unpack(numericBits(x));
        FloatingPoint#(exponentWidth, fractionWidth) weightValue =
            unpack(numericBits(weight));
        FloatingPoint#(exponentWidth, fractionWidth) psumValue =
            unpack(numericBits(psum));
        let product = tpl_1(multFP(xValue, weightValue, Rnd_Nearest_Even));
        return numericElement(pack(tpl_1(addFP(
            psumValue,
            product,
            Rnd_Nearest_Even
        ))));
    endfunction

    function NumericElement#(FLOAT, precision) numericAdd(
        FLOAT formatTag,
        NumericElement#(FLOAT, precision) left,
        NumericElement#(FLOAT, precision) right
    );
        FloatingPoint#(exponentWidth, fractionWidth) leftValue =
            unpack(numericBits(left));
        FloatingPoint#(exponentWidth, fractionWidth) rightValue =
            unpack(numericBits(right));
        return numericElement(pack(tpl_1(addFP(
            leftValue,
            rightValue,
            Rnd_Nearest_Even
        ))));
    endfunction

    function NumericElement#(FLOAT, precision) numericScaleMac(
        FLOAT formatTag,
        NumericElement#(FLOAT, precision) value,
        UInt#(8) scale
    );
        FloatingPoint#(exponentWidth, fractionWidth) floatValue =
            unpack(numericBits(value));
        UInt#(1) fractionBits = 0;
        let scaleFloat = tpl_1(vFixedToFloat(
            scale,
            fractionBits,
            Rnd_Nearest_Even
        ));
        return numericElement(pack(tpl_1(multFP(
            floatValue,
            scaleFloat,
            Rnd_Nearest_Even
        ))));
    endfunction

    function NumericElement#(FLOAT, precision) numericScaleShift(
        FLOAT formatTag,
        NumericElement#(FLOAT, precision) value,
        UInt#(8) scale
    );
        FloatingPoint#(exponentWidth, fractionWidth) result =
            unpack(numericBits(value));
        FloatingPoint#(exponentWidth, fractionWidth) oneValue = one(False);
        UInt#(TAdd#(exponentWidth, 9)) bias =
            zeroExtend(unpack(pack(oneValue.exp)));
        UInt#(TAdd#(exponentWidth, 9)) remaining = zeroExtend(scale);

        // 가장 작은 IEEE layout인 half도 bias=15이므로 최대 255-bit shift를
        // 17개의 유한한 2^chunk factor로 처리할 수 있다. 2의 거듭제곱 곱셈은
        // 정상수와 subnormal 모두에서 유효 숫자를 바꾸지 않는다.
        for (Integer chunkIndex = 0; chunkIndex < 17; chunkIndex = chunkIndex + 1) begin
            UInt#(TAdd#(exponentWidth, 9)) chunk = remaining > bias
                ? bias
                : remaining;

            if (chunk != 0) begin
                FloatingPoint#(exponentWidth, fractionWidth) factor = one(False);
                factor.exp = truncate(pack(bias + chunk));
                result = tpl_1(multFP(result, factor, Rnd_Nearest_Even));
                remaining = remaining - chunk;
            end
        end

        return numericElement(pack(result));
    endfunction
endinstance

endpackage
