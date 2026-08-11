package TbSystolicEngineWeightBanks;

import Vector::*;

import SystolicEngine::*;

module mkTbSystolicEngineWeightBanks(Empty);
    SystolicEngineIfc#(
        1,
        1,
        Int#(8),
        Int#(8),
        Int#(16),
        Int#(32)
    ) dut <- mkSystolicEngine;

    Reg#(UInt#(4)) state <- mkReg(0);
    Reg#(UInt#(8)) watchdog <- mkReg(0);

    rule watch;
        watchdog <= watchdog + 1;
        if (watchdog == 80) begin
            $display(
                "SYSTOLIC ENGINE BANKS: FAIL timeout active=%0d state=%0d",
                dut.active,
                state
            );
            $finish(1);
        end
    endrule

    rule beginActiveLoad (state == 0);
        dut.beginWeightLoad;
        state <= 1;
    endrule

    rule loadActive (state == 1);
        dut.loadWeightRow(0, replicate(2));
        state <= 2;
    endrule

    rule startExecution (state == 2 && dut.weightsReady);
        dut.start(1);
        state <= 3;
    endrule

    rule beginInactiveDuringExecution (state == 3 && dut.active);
        dut.beginWeightLoadBank(True);
        state <= 4;
    endrule

    rule loadInactiveDuringExecution (state == 4 && dut.active);
        dut.loadWeightRowBank(True, 0, replicate(5));
        state <= 5;
    endrule

    rule checkOverlap (state == 5);
        Bool passed = dut.active
            && !dut.activeWeightBank
            && dut.weightsReadyBank(True);

        if (!passed) begin
            $display(
                "SYSTOLIC ENGINE BANKS: FAIL active=%0d bank=%0d ready=%0d",
                dut.active,
                dut.activeWeightBank,
                dut.weightsReadyBank(True)
            );
            $finish(1);
        end
        else begin
            $display("SYSTOLIC ENGINE BANKS: PASS");
            $finish(0);
        end
    endrule
endmodule

endpackage
