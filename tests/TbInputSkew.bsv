package TbInputSkew;

import Vector::*;

import TestVectorUtils::*;

import InputSkew::*;

// arrayDim=3, peLatency=2일 때 column 0/1/2의 boundary delay가 각각
// 0/2/4 cycle인지 확인한다. Initial partial zero도 activation과 함께 나와야 한다.
module mkTbInputSkew(Empty);
    InputSkewIfc#(3, 2, Int#(8), Int#(32)) dut <- mkInputSkew;
    Reg#(UInt#(4)) state <- mkReg(0);

    rule clearState (state == 0);
        dut.clear;
        state <= 1;
    endrule

    rule feedLogicalRow (state == 1);
        let skewed <- dut.step(tagged Valid vector3(10, 20, 30));
        Bool passed = isValid(skewed.activations[0])
            && fromMaybe(0, skewed.activations[0]) == 10
            && isValid(skewed.partials[0])
            && fromMaybe(1, skewed.partials[0]) == 0
            && !isValid(skewed.activations[1])
            && !isValid(skewed.activations[2]);

        if (!passed) begin
            $display("INPUT SKEW: FAIL (cycle 0)");
            $finish(1);
        end
        state <= 2;
    endrule

    rule firstBubble (state == 2);
        let skewed <- dut.step(tagged Invalid);
        if (isValid(skewed.activations[1])
                || isValid(skewed.activations[2])) begin
            $display("INPUT SKEW: FAIL (early column)");
            $finish(1);
        end
        state <= 3;
    endrule

    rule checkColumn1 (state == 3);
        let skewed <- dut.step(tagged Invalid);
        Bool passed = isValid(skewed.activations[1])
            && fromMaybe(0, skewed.activations[1]) == 20
            && isValid(skewed.partials[1])
            && fromMaybe(1, skewed.partials[1]) == 0
            && !isValid(skewed.activations[2]);

        if (!passed) begin
            $display("INPUT SKEW: FAIL (column 1)");
            $finish(1);
        end
        state <= 4;
    endrule

    rule thirdCycle (state == 4);
        let skewed <- dut.step(tagged Invalid);
        if (isValid(skewed.activations[2])) begin
            $display("INPUT SKEW: FAIL (column 2 early)");
            $finish(1);
        end
        state <= 5;
    endrule

    rule checkColumn2 (state == 5);
        let skewed <- dut.step(tagged Invalid);
        Bool passed = isValid(skewed.activations[2])
            && fromMaybe(0, skewed.activations[2]) == 30
            && isValid(skewed.partials[2])
            && fromMaybe(1, skewed.partials[2]) == 0;

        if (!passed) begin
            $display("INPUT SKEW: FAIL (column 2)");
            $finish(1);
        end
        else begin
            $display("INPUT SKEW: PASS");
            $finish(0);
        end
    endrule
endmodule

endpackage
