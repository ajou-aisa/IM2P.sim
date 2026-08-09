package TbAccumulator;

import Vector::*;

import TestVectorUtils::*;

import Accumulator::*;


// Column bank별 서로 다른 row address, valid mask, optional accumulation을 검증한다.
module mkTbAccumulator(Empty);
    AccumulatorIfc#(8, 4, Int#(32)) dut <- mkAccumulator;
    Reg#(UInt#(3)) state <- mkReg(0);
    Reg#(Vector#(4, Int#(32))) observedRow0 <- mkRegU;

    rule initializeRow0 (state == 0);
        dut.writeRow(0, vector4(10, 20, 30, 40));
        state <= 1;
    endrule

    rule initializeRow2 (state == 1);
        dut.writeRow(2, vector4(100, 200, 300, 400));
        state <= 2;
    endrule

    rule accumulateMasked (state == 2);
        Vector#(4, Bool) valids = newVector;
        valids[0] = True;
        valids[1] = False;
        valids[2] = True;
        valids[3] = False;

        Vector#(4, UInt#(3)) rowAddresses = newVector;
        rowAddresses[0] = 0;
        rowAddresses[1] = 0;
        rowAddresses[2] = 2;
        rowAddresses[3] = 0;

        // Column 0은 row 0, column 2는 row 2에 누산한다.
        // Invalid column 1/3의 contribution과 address는 무시되어야 한다.
        dut.commit(
            valids,
            rowAddresses,
            vector4(1, 99, -5, 99),
            True
        );
        state <= 3;
    endrule

    rule readAccumulatedRow0 (state == 3);
        observedRow0 <= dut.readRow(0);
        state <= 4;
    endrule

    rule checkAccumulation (state == 4);
        Vector#(4, Int#(32)) row2 = dut.readRow(2);

        Bool passed = observedRow0 == vector4(11, 20, 30, 40)
            && row2 == vector4(100, 200, 295, 400);

        if (!passed) begin
            $display(
                "ACCUMULATOR: FAIL masked row0=(%0d,%0d,%0d,%0d) row2=(%0d,%0d,%0d,%0d)",
                observedRow0[0], observedRow0[1],
                observedRow0[2], observedRow0[3],
                row2[0], row2[1], row2[2], row2[3]
            );
            $finish(1);
        end
        state <= 5;
    endrule

    rule replaceRow1 (state == 5);
        dut.commit(
            replicate(True),
            replicate(1),
            vector4(7, 8, 9, 10),
            False
        );
        state <= 6;
    endrule

    rule checkReplacement (state == 6);
        Vector#(4, Int#(32)) row1 = dut.readRow(1);

        if (row1 != vector4(7, 8, 9, 10)) begin
            $display(
                "ACCUMULATOR: FAIL replace row=(%0d,%0d,%0d,%0d)",
                row1[0], row1[1], row1[2], row1[3]
            );
            $finish(1);
        end
        else begin
            $display("ACCUMULATOR: PASS");
            $finish(0);
        end
    endrule
endmodule

endpackage
