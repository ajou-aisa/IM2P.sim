package TbArithmetic;

import FloatingPoint::*;
import Config::*;
import Arithmetic::*;

// INT operand/product/accumulator 폭 분리와 scale-free FLOAT 산술을 직접 확인한다.
module mkTbArithmetic(Empty);
    Reg#(Bool) finished <- mkReg(False);

    rule run (!finished);
        Int#(8) intA = -7;
        Int#(8) intB = 9;
        Int#(16) intProduct = arithmeticMultiply(intA, intB);
        Int#(DefaultAccumulatorWidth) intAccumulated =
            arithmeticAccumulate(100, intProduct);
        Int#(16) positiveOne = 1;
        Int#(16) negativeOne = -1;
        Int#(DefaultAccumulatorWidth) beyondInt32Max =
            arithmeticAccumulate(2147483647, positiveOne);
        Int#(DefaultAccumulatorWidth) beyondInt32Min =
            arithmeticAccumulate(-2147483648, negativeOne);

        Half fpA = fromInteger(2);
        Half fpB = fromInteger(3);
        Half fpD = fromInteger(4);
        Half fpProduct = arithmeticMultiply(fpA, fpB);
        Half fpAccumulated = arithmeticAccumulate(fpD, fpProduct);
        Half fpExpected = fromInteger(10);

        Bool failed = False;
        if (intProduct != -63) begin
            $display("FAIL: INT product expected=-63 actual=%0d", intProduct);
            failed = True;
        end
        if (intAccumulated != 37) begin
            $display("FAIL: INT accumulate expected=37 actual=%0d", intAccumulated);
            failed = True;
        end
        if (beyondInt32Max != 2147483648) begin
            $display(
                "FAIL: INT positive boundary expected=2147483648 actual=%0d",
                beyondInt32Max
            );
            failed = True;
        end
        if (beyondInt32Min != -2147483649) begin
            $display(
                "FAIL: INT negative boundary expected=-2147483649 actual=%0d",
                beyondInt32Min
            );
            failed = True;
        end
        if (fpAccumulated != fpExpected) begin
            $display(
                "FAIL: FP16 accumulate expected=10 actual=%h",
                pack(fpAccumulated)
            );
            failed = True;
        end

        finished <= True;
        if (failed) begin
            $display("ARITHMETIC: FAIL");
            $finish(1);
        end
        else begin
            $display("ARITHMETIC: PASS");
            $finish(0);
        end
    endrule
endmodule

endpackage
