package TbSystolicArrayWeightBanks;

import Vector::*;

import SystolicArray::*;

module mkTbSystolicArrayWeightBanks(Empty);
    SystolicArrayIfc#(
        1,
        1,
        Int#(8),
        Int#(8),
        Int#(16),
        Int#(32)
    ) dut <- mkSystolicArray;

    Reg#(UInt#(4)) state <- mkReg(0);
    Reg#(UInt#(8)) watchdog <- mkReg(0);

    rule watch;
        watchdog <= watchdog + 1;
        if (watchdog == 100) begin
            $display("SYSTOLIC ARRAY BANKS: FAIL timeout");
            $finish(1);
        end
    endrule

    rule beginActiveLoad (state == 0);
        dut.beginWeightLoad;
        state <= 1;
    endrule

    rule loadActiveBank (state == 1);
        dut.loadWeightRow(0, replicate(2));
        state <= 2;
    endrule

    rule beginInactiveLoad (state == 2 && dut.weightsReady);
        dut.beginWeightLoadBank(True);
        state <= 3;
    endrule

    rule preloadWhileComputing (state == 3);
        dut.loadWeightRowBank(True, 0, replicate(5));
        dut.step(replicate(tagged Valid 3), replicate(tagged Valid 0));
        state <= 4;
    endrule

    rule checkActiveStability (state == 4);
        Maybe#(Int#(32)) partial = dut.partialSums[0];
        Bool passed = !dut.activeWeightBank
            && dut.weightsReadyBank(True)
            && isValid(partial)
            && fromMaybe(0, partial) == 6;

        if (!passed) begin
            $display(
                "SYSTOLIC ARRAY BANKS: FAIL active=%0d ready=%0d C=%0d",
                dut.activeWeightBank,
                dut.weightsReadyBank(True),
                fromMaybe(0, partial)
            );
            $finish(1);
        end

        dut.clearPipeline;
        dut.activateWeightBank(True);
        state <= 5;
    endrule

    rule computePreloadedBank (state == 5);
        dut.step(replicate(tagged Valid 3), replicate(tagged Valid 0));
        state <= 6;
    endrule

    rule checkBankSwitch (state == 6);
        Maybe#(Int#(32)) partial = dut.partialSums[0];
        Bool passed = dut.activeWeightBank
            && isValid(partial)
            && fromMaybe(0, partial) == 15;

        if (!passed) begin
            $display(
                "SYSTOLIC ARRAY BANKS: FAIL switched=%0d C=%0d",
                dut.activeWeightBank,
                fromMaybe(0, partial)
            );
            $finish(1);
        end
        else begin
            $display("SYSTOLIC ARRAY BANKS: PASS");
            $finish(0);
        end
    endrule
endmodule

endpackage
