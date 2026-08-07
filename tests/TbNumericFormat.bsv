package TbNumericFormat;

import FloatingPoint::*;
import NumericFormat::*;
import PE::*;

typedef NumericElement#(FLOAT, 16) FloatElement;

// format이 typeclass 인자에 명시되도록 FLOAT16 scale 연산을 감싼다.
function FloatElement scaleMacFloat16(FloatElement value, UInt#(8) scale)
provisos (NumericFormat#(FLOAT, 16));
    FLOAT formatTag = ?;
    return numericScaleMac(formatTag, value, scale);
endfunction

function FloatElement scaleShiftFloat16(FloatElement value, UInt#(8) scale)
provisos (NumericFormat#(FLOAT, 16));
    FLOAT formatTag = ?;
    return numericScaleShift(formatTag, value, scale);
endfunction

module mkTbNumericFormat(Empty);
    PE#(FLOAT, 16) pe <- mkPE;
    Reg#(UInt#(2)) state <- mkReg(0);

    Half oneValue = fromInteger(1);
    Half twoValue = fromInteger(2);
    Half threeValue = fromInteger(3);
    Half fourValue = fromInteger(4);
    Half zeroValue = zero(False);
    Half smallestSubnormal = unpack(16'h0001);
    Half shiftedSubnormalExpected = unpack(16'h1c00);

    rule preload (state == 0);
        pe.preloadWeight(numericElement(pack(oneValue)));
        state <= 1;
    endrule

    rule feed (state == 1);
        pe.step(
            True,
            numericElement(pack(oneValue)),
            numericElement(pack(oneValue))
        );
        state <= 2;
    endrule

    rule check (state == 2);
        Half macResult = unpack(numericBits(pe.psumOut));
        Half scaleMacResult = unpack(numericBits(scaleMacFloat16(
            numericElement(pack(oneValue)),
            3
        )));
        Half scaleShiftResult = unpack(numericBits(scaleShiftFloat16(
            numericElement(pack(oneValue)),
            2
        )));
        Half shiftedZero = unpack(numericBits(scaleShiftFloat16(
            numericElement(pack(zeroValue)),
            16
        )));
        Half shiftedSubnormal = unpack(numericBits(scaleShiftFloat16(
            numericElement(pack(smallestSubnormal)),
            16
        )));

        if (!pe.outValid
                || macResult != twoValue
                || scaleMacResult != threeValue
                || scaleShiftResult != fourValue
                || shiftedZero != zeroValue
                || shiftedSubnormal != shiftedSubnormalExpected) begin
            $display("NUMERIC FORMAT FLOAT16: FAIL");
            $finish(1);
        end
        else begin
            $display("NUMERIC FORMAT FLOAT16: PASS");
            $finish(0);
        end
    endrule
endmodule

endpackage
