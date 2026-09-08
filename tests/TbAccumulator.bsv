package TbAccumulator;

import Assert::*;
import Clocks::*;
import StmtFSM::*;
import Vector::*;

import TestVectorUtils::*;
import Accumulator::*;

// Exercise the same BRAMCore1 backend used by generated Verilog, including the
// old INT64 arithmetic boundary and the new INT32 wrap and 1024-row address span.
module mkTbAccumulator(Empty);
    Clock clock <- exposeCurrentClock;
    MakeResetIfc resetControl <- mkReset(2, True, clock);
    AccumulatorIfc#(1024, 4, Int#(32)) dut <- mkAccumulator(
        reset_by resetControl.new_rst
    );
    AccumulatorIfc#(8, 4, Int#(64)) wide <- mkAccumulator;
    Reg#(UInt#(16)) cycles <- mkReg(0);

    rule watchdog;
        cycles <= cycles + 1;
        if (cycles == 400) begin
            $display("ACCUMULATOR: FAIL transaction timeout");
            $finish(1);
        end
    endrule

    Stmt test = seq
        wide.writeRow(0, vector4(2147483647, 20, 30, 40));
        wide.writeRow(2, vector4(100, 200, -2147483648, 400));
        wide.commit(vector4(True, False, True, False), vector4(0, 0, 2, 0),
            vector4(1, 99, -1, 99), True);
        action
            dynamicAssert(!wide.completionValid, "update acceptance is not completion");
        endaction
        action
            dynamicAssert(wide.completedValids == vector4(True, False, True, False),
                "INT64 completion mask changed");
            wide.consumeCompletion;
        endaction
        wide.requestReadRow(0);
        action
            dynamicAssert(wide.readResponse == vector4(2147483648, 20, 30, 40),
                "INT64 positive accumulation narrowed");
            wide.consumeReadResponse;
        endaction
        wide.requestReadRow(2);
        action
            dynamicAssert(wide.readResponse == vector4(100, 200, -2147483649, 400),
                "INT64 negative accumulation narrowed");
            wide.consumeReadResponse;
        endaction

        dut.writeRow(0, vector4(2147483647, 20, 30, 40));
        dut.writeRow(1023, vector4(100, 200, -2147483648, 400));
        dut.commit(vector4(True, False, True, False), vector4(0, 1023, 1023, 0),
            vector4(1, 99, -1, 99), True);
        action
            dynamicAssert(!dut.idle && !dut.completionValid,
                "pending RMW must block the next transaction");
        endaction
        action
            dynamicAssert(dut.completedValids == vector4(True, False, True, False),
                "sparse completion mask changed");
        endaction
        delay(4);
        action
            dynamicAssert(!dut.idle && dut.completionValid,
                "unconsumed completion was lost");
            dynamicAssert(dut.completedValids == vector4(True, False, True, False),
                "stalled completion metadata changed");
            dut.consumeCompletion;
        endaction
        action
            dynamicAssert(!dut.completionValid, "duplicate update completion");
            dut.requestReadRow(0);
        endaction
        action
            dynamicAssert(!dut.readResponseValid && !dut.idle,
                "BRAM read cannot produce a zero-latency response");
        endaction
        action
            dynamicAssert(dut.readResponse == vector4(-2147483648, 20, 30, 40),
                "INT32 masked wrap or invalid column corrupted");
        endaction
        delay(5);
        action
            dynamicAssert(!dut.idle && dut.readResponseValid,
                "held read response must apply backpressure");
            dynamicAssert(dut.readResponse == vector4(-2147483648, 20, 30, 40),
                "read payload changed under backpressure");
            dut.consumeReadResponse;
        endaction
        dut.requestReadRow(1023);
        action
            dynamicAssert(dut.readResponse == vector4(100, 200, 2147483647, 400),
                "last-row staggered column update failed");
            dut.consumeReadResponse;
        endaction

        // Replace requires no initialized old value; the next two requests must
        // accumulate from the latest completed write in the same bank and row.
        dut.commit(replicate(True), replicate(256), vector4(7, 8, 9, 10), False);
        dut.consumeCompletion;
        dut.commit(replicate(True), replicate(256), vector4(1, 2, 3, 4), True);
        dut.consumeCompletion;
        dut.commit(replicate(True), replicate(256), vector4(10, 20, 30, 40), True);
        dut.consumeCompletion;
        dut.requestReadRow(256);
        action
            dynamicAssert(dut.readResponse == vector4(18, 30, 42, 54),
                "same-row RMW read stale data or duplicated update");
            dut.consumeReadResponse;
        endaction
        dut.writeRow(255, vector4(-1, -2, -3, -4));
        dut.requestReadRow(255);
        action
            dynamicAssert(dut.readResponse == vector4(-1, -2, -3, -4),
                "row 255 aliases row 256");
            dut.consumeReadResponse;
        endaction
        dut.requestReadRow(256);
        action
            dynamicAssert(dut.readResponse == vector4(18, 30, 42, 54),
                "row 256 changed after adjacent preload");
            dut.consumeReadResponse;
        endaction

        dut.commit(replicate(False), replicate(1023), replicate(99), True);
        action
            dynamicAssert(dut.completedValids == replicate(False),
                "empty valid mask fabricated a committed column");
            dut.consumeCompletion;
        endaction
        dut.requestReadRow(1023);
        action
            dynamicAssert(dut.readResponse == vector4(100, 200, 2147483647, 400),
                "invalid columns wrote BRAM");
            dut.consumeReadResponse;
        endaction

        // An operation accepted before reset may have written before reset
        // arrives. Its completion/read payload must never enter the next job.
        dut.commit(replicate(True), replicate(1023), replicate(123), True);
        resetControl.assertReset;
        delay(6);
        action
            dynamicAssert(dut.idle && !dut.completionValid && !dut.readResponseValid,
                "pending update survived reset as a new completion");
        endaction
        dut.writeRow(1023, vector4(-7, 8, -9, 10));
        dut.requestReadRow(1023);
        resetControl.assertReset;
        delay(6);
        action
            dynamicAssert(dut.idle && !dut.completionValid && !dut.readResponseValid,
                "pending read survived reset as a new response");
        endaction
        dut.requestReadRow(1023);
        action
            dynamicAssert(dut.readResponse == vector4(-7, 8, -9, 10),
                "reset changed defined BRAM contents");
            dut.consumeReadResponse;
        endaction
        action
            dynamicAssert(dut.idle && !dut.completionValid && !dut.readResponseValid,
                "accumulator did not return to idle");
            $display("ACCUMULATOR: PASS INT32/INT64, stagger, hazards, backpressure, rows 255/256/1023, reset");
            $finish(0);
        endaction
    endseq;
    mkAutoFSM(test);
endmodule

endpackage
