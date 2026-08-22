package TbPE;

import PE::*;

// -----------------------------------------------------------------------------
// Registered WS PE 단위 검증
// -----------------------------------------------------------------------------
//
// B=-5를 stationary weight로 적재한 뒤 A=3, D=20을 입력한다.
// peLatency=1이므로 다음 cycle에 Aout=3, Cout=20+3*(-5)=5가 나와야 한다.
module mkTbPE(Empty);
    PEIfc#(1, Int#(8), Int#(8), Int#(16), Int#(64)) dut <- mkPE;
    Reg#(UInt#(4)) state <- mkReg(0);

    rule preloadWeight (state == 0);
        dut.loadWeight(-5);
        state <= 1;
    endrule

    rule clearPipeline (state == 1);
        dut.clearPipeline;
        state <= 2;
    endrule

    rule driveOperands (state == 2);
        dut.loadWeightBank(True, 7);
        dut.step(tagged Valid 3, tagged Valid 20);
        state <= 3;
    endrule

    rule checkResult (state == 3);
        Maybe#(Int#(8)) activation = dut.activationOut;
        Maybe#(Int#(64)) partial = dut.partialOut;
        Bool passed = dut.weightLoaded
            && dut.weightBankLoaded(True)
            && !dut.activeWeightBank
            && isValid(activation)
            && isValid(partial)
            && fromMaybe(0, activation) == 3
            && fromMaybe(0, partial) == 5;

        if (!passed) begin
            $display(
                "PE: FAIL Avalid=%0d Cvalid=%0d A=%0d C=%0d",
                isValid(activation),
                isValid(partial),
                fromMaybe(0, activation),
                fromMaybe(0, partial)
            );
            $finish(1);
        end
        else begin
            dut.clearPipeline;
            state <= 4;
        end
    endrule

    rule activatePreloadedBank (state == 4);
        dut.activateWeightBank(True);
        state <= 5;
    endrule

    rule drivePreloadedWeight (state == 5);
        dut.step(tagged Valid 3, tagged Valid 20);
        state <= 6;
    endrule

    rule checkPreloadedResult (state == 6);
        Maybe#(Int#(64)) partial = dut.partialOut;
        Bool passed = dut.activeWeightBank
            && isValid(partial)
            && fromMaybe(0, partial) == 41;

        if (!passed) begin
            $display(
                "PE BANK: FAIL active=%0d Cvalid=%0d C=%0d",
                dut.activeWeightBank,
                isValid(partial),
                fromMaybe(0, partial)
            );
            $finish(1);
        end
        else begin
            dut.clearPipeline;
            state <= 7;
        end
    endrule

    rule drivePositiveBoundary (state == 7);
        dut.step(tagged Valid 1, tagged Valid 2147483641);
        state <= 8;
    endrule

    rule checkPositiveBoundary (state == 8);
        Maybe#(Int#(64)) partial = dut.partialOut;
        if (!isValid(partial) || fromMaybe(0, partial) != 2147483648) begin
            $display("PE: FAIL positive boundary C=%0d", fromMaybe(0, partial));
            $finish(1);
        end
        dut.clearPipeline;
        state <= 9;
    endrule

    rule driveNegativeBoundary (state == 9);
        dut.step(tagged Valid (-1), tagged Valid (-2147483642));
        state <= 10;
    endrule

    rule checkNegativeBoundary (state == 10);
        Maybe#(Int#(64)) partial = dut.partialOut;
        if (!isValid(partial) || fromMaybe(0, partial) != -2147483649) begin
            $display("PE: FAIL negative boundary C=%0d", fromMaybe(0, partial));
            $finish(1);
        end
        else begin
            $display("PE: PASS boundaries=(2147483648,-2147483649)");
            $finish(0);
        end
    endrule
endmodule

endpackage
