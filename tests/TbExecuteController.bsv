package TbExecuteController;

import Vector::*;

import TestVectorUtils::*;

import ExecuteController::*;


// Staggered column output과 Accumulator commit을 독립적으로 추적하는지 검증한다.
module mkTbExecuteController(Empty);
    ExecuteControllerIfc#(3) dut <- mkExecuteController;
    Reg#(UInt#(4)) state <- mkReg(0);
    Reg#(UInt#(8)) watchdog <- mkReg(0);

    rule watch;
        watchdog <= watchdog + 1;
        if (watchdog == 100) begin
            $display("EXECUTE CONTROLLER: FAIL (timeout)");
            $finish(1);
        end
    endrule

    rule startExecution (state == 0 && dut.idle);
        dut.start(2);
        state <= 1;
    endrule

    rule issueFirstWave (state == 1 && dut.active);
        let offsets = dut.currentRowOffsets;
        Bool passed = offsets[0] == 0
            && offsets[1] == 0
            && offsets[2] == 0;

        if (!passed) begin
            $display("EXECUTE CONTROLLER: FAIL initial offsets");
            $finish(1);
        end

        dut.noteArrayOutputs(vector3(True, False, True));
        state <= 2;
    endrule

    rule issueSecondWave (state == 2);
        let offsets = dut.currentRowOffsets;
        Bool passed = offsets[0] == 1
            && offsets[1] == 0
            && offsets[2] == 1;

        if (!passed) begin
            $display("EXECUTE CONTROLLER: FAIL second offsets");
            $finish(1);
        end

        dut.noteArrayOutputs(vector3(True, True, False));
        state <= 3;
    endrule

    rule issueThirdWave (state == 3);
        let offsets = dut.currentRowOffsets;
        Bool passed = offsets[0] == 2
            && offsets[1] == 1
            && offsets[2] == 1;

        if (!passed) begin
            $display("EXECUTE CONTROLLER: FAIL third offsets");
            $finish(1);
        end

        dut.noteArrayOutputs(vector3(False, True, True));
        state <= 4;
    endrule

    rule commitFirstWave (state == 4);
        dut.noteCommitted(vector3(True, False, True));
        state <= 5;
    endrule

    rule commitSecondWave (state == 5);
        if (dut.done) begin
            $display("EXECUTE CONTROLLER: FAIL early done");
            $finish(1);
        end

        dut.noteCommitted(vector3(True, True, False));
        state <= 6;
    endrule

    rule commitThirdWave (state == 6);
        if (dut.done) begin
            $display("EXECUTE CONTROLLER: FAIL early done");
            $finish(1);
        end

        dut.noteCommitted(vector3(False, True, True));
        state <= 7;
    endrule

    rule acknowledgeDone (state == 7 && dut.done);
        dut.acknowledge;
        state <= 8;
    endrule

    rule finish (state == 8 && dut.idle);
        $display("EXECUTE CONTROLLER: PASS");
        $finish(0);
    endrule
endmodule

endpackage
