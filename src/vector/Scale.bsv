package Scale;

import FloatingPoint::*;

import Types::*;

// Numeric format이 runtime Multiply/Shift를 지원하는지 정의한다.
// acc_t와 scale_t에 무관한 format capability이므로 실제 transform typeclass와
// 분리한다.
typeclass VectorScaleCapability#(type format_t);
    function Bool vectorScalingSupported(format_t formatProxy);
endtypeclass

// Signed integer configuration은 Bypass/Multiply/Shift를 모두 지원한다.
instance VectorScaleCapability#(Int#(inputWidth));
    function Bool vectorScalingSupported(Int#(inputWidth) formatProxy);
        return True;
    endfunction
endinstance

// Floating-point configuration은 Bypass만 지원한다.
instance VectorScaleCapability#(
    FloatingPoint#(exponentWidth, fractionWidth)
);
    function Bool vectorScalingSupported(
        FloatingPoint#(exponentWidth, fractionWidth) formatProxy
    );
        return False;
    endfunction
endinstance

// 한 partial sum element에 VectorOp을 적용해 contribution을 만든다.
typeclass VectorTransform#(
    type format_t,
    type acc_t,
    type scale_t
);
    function acc_t transformVectorElement(
        format_t formatProxy,
        VectorOp op,
        acc_t partial,
        scale_t scale
    );
endtypeclass

// Signed integer transform policy.
//
// Multiply : full product의 accumulator-width 하위 bit를 유지한다.
// Shift    : scale을 signed exponent로 해석하며 음수는 arithmetic right shift다.
// 현재 reference policy에는 rounding과 saturation이 없다.
instance VectorTransform#(
    Int#(inputWidth),
    Int#(accWidth),
    Int#(scaleWidth)
) provisos (
    Add#(accWidth, scaleWidth, scaledWidth)
);
    function Int#(accWidth) transformVectorElement(
        Int#(inputWidth) formatProxy,
        VectorOp op,
        Int#(accWidth) partial,
        Int#(scaleWidth) scale
    );
        case (op)
            VectorBypass: begin
                return partial;
            end

            VectorMultiply: begin
                // Widen both operands before multiplication. Besides making the
                // full signed width explicit, this avoids a Bluesim mixed
                // scalar/WideData constructor ambiguity when accWidth is 64.
                Int#(scaledWidth) widePartial = signExtend(partial);
                Int#(scaledWidth) wideScale = signExtend(scale);
                Int#(scaledWidth) wideProduct = widePartial * wideScale;
                Bit#(accWidth) lowBits = truncate(pack(wideProduct));
                return unpack(lowBits);
            end

            VectorShift: begin
                // 한 bit 넓혀 최솟값 exponent의 절댓값도 overflow 없이 만든다.
                Int#(TAdd#(scaleWidth, 1)) wideExponent = signExtend(scale);
                UInt#(TAdd#(scaleWidth, 1)) amount = unpack(pack(
                    wideExponent < 0 ? -wideExponent : wideExponent
                ));

                if (wideExponent < 0) begin
                    return partial >> amount;
                end
                else begin
                    return partial << amount;
                end
            end

            default: begin
                return partial;
            end
        endcase
    endfunction
endinstance

// FLOAT는 같은 VectorUnit source를 사용하지만 partial을 그대로 통과시킨다.
// VectorScaleCapability가 False이므로 Core는 VectorBypass 외의 command를 거부한다.
instance VectorTransform#(
    FloatingPoint#(inputExponent, inputFraction),
    FloatingPoint#(accExponent, accFraction),
    scale_t
) provisos (
    Bits#(scale_t, scaleBits)
);
    function FloatingPoint#(accExponent, accFraction) transformVectorElement(
        FloatingPoint#(inputExponent, inputFraction) formatProxy,
        VectorOp op,
        FloatingPoint#(accExponent, accFraction) partial,
        scale_t scale
    );
        return partial;
    endfunction
endinstance

endpackage
