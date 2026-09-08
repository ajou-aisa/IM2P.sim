package TbCoreBramBoundary;

import Assert::*;
import StmtFSM::*;
import Vector::*;
import Types::*;
import ExecuteCmd::*;
import HostMemoryTypes::*;
import WorkTypes::*;
import IM2PCore::*;
import TestVectorUtils::*;

module mkTbCoreBramBoundary(Empty);
    IM2PCoreIfc#(2, 1, 1, 1024,
        Int#(8), Int#(8), Int#(16), Int#(32), Int#(8)) core <- mkIM2PCore;
    Reg#(UInt#(16)) cycles <- mkReg(0);
    Reg#(Bool) holdOutput <- mkReg(False);
    Reg#(Bool) holdRead <- mkReg(False);
    Reg#(HostRequestTag) heldTag <- mkRegU;

    rule watchdog;
        cycles <= cycles + 1;
        if (cycles == 2000) begin
            $display("CORE BRAM BOUNDARY: FAIL timeout core=%0d", core.matrixCoreState);
            $finish(1);
        end
    endrule

    rule serveActivation (core.activationReadRequestValid);
        dynamicAssert(core.activationReadRequestAddress == 64'h1000
            && core.activationReadRequestElementCount == 2, "unexpected A request");
        core.putActivationReadResponse(core.activationReadRequestTag, vector2(5, -6));
    endrule

    rule serveWeight (core.weightReadRequestValid);
        HostAddress address = core.weightReadRequestAddress;
        dynamicAssert((address == 64'h2000 || address == 64'h2002)
            && core.weightReadRequestElementCount == 2, "unexpected W request");
        core.putWeightReadResponse(core.weightReadRequestTag,
            address == 64'h2000 ? vector2(1, 0) : vector2(0, 1));
    endrule

    rule noScale (core.scaleReadRequestValid);
        dynamicAssert(False, "VectorBypass requested a scale row");
    endrule

    rule retainedOutputValid (holdOutput);
        dynamicAssert(core.outputWriteRequestValid, "stalled output lost valid");
        dynamicAssert(!core.matmulDone && !core.stripeCompletionValid,
            "job or stripe completed before output acknowledgement");
    endrule

    rule retainedOutputPayload (holdOutput && core.outputWriteRequestValid);
        dynamicAssert(core.outputWriteRequestValues == vector2(5, -6)
            && core.outputWriteRequestAddress == 64'h4000
            && core.outputWriteRequestTag == heldTag
            && core.outputWriteRequestElementCount == 2,
            "stalled output payload, address, tag or count changed");
    endrule

    // This request must remain unaccepted until the held low-level read is
    // consumed, otherwise changing MatrixIdle strands that response forever.
    rule rejectMatmulDuringRead (holdRead);
        core.startMatmul(92, FullMatrix,
            64'h1000, 64'h2000, 64'h3000, 64'h4000,
            2, 2, 2, 8, 1, 2, 2, 1, 2, 0, 2, 2, 0, False, VectorBypass);
        dynamicAssert(False, "matmul accepted while accumulator read response held");
    endrule

    Stmt test = seq
        core.writeAccumulatorRow(255, vector2(-255, 255));
        core.writeAccumulatorRow(256, vector2(-256, 256));
        core.writeAccumulatorRow(1023, vector2(2147483647, -2147483648));
        core.requestReadAccumulatorRow(255);
        action
            dynamicAssert(!core.accumulatorReadResponseValid && !core.idle,
                "core read acceptance exposed a zero-latency response");
        endaction
        action
            dynamicAssert(core.readAccumulatorRowResponse == vector2(-255, 255),
                "row 255 read failed");
            holdRead <= True;
        endaction
        delay(5);
        action
            dynamicAssert(core.accumulatorReadResponseValid && !core.idle,
                "held core read failed to apply backpressure");
            dynamicAssert(core.readAccumulatorRowResponse == vector2(-255, 255),
                "held core read changed");
            core.consumeAccumulatorReadResponse;
            holdRead <= False;
        endaction
        core.requestReadAccumulatorRow(256);
        action
            dynamicAssert(core.readAccumulatorRowResponse == vector2(-256, 256),
                "row 255/256 address truncation");
            core.consumeAccumulatorReadResponse;
        endaction
        core.requestReadAccumulatorRow(1023);
        action
            dynamicAssert(core.readAccumulatorRowResponse == vector2(2147483647, -2147483648),
                "last row preload failed");
            core.consumeAccumulatorReadResponse;
        endaction

        core.beginWeightLoad;
        core.loadWeightRow(0, vector2(1, 0));
        core.loadWeightRow(1, vector2(0, 1));
        await(core.weightsReady && core.idle);
        core.startExecution(ExecuteCmd {
            accumulatorBaseRow: 1023, rowCount: 1,
            accumulate: True, vectorOp: VectorBypass
        }, 0, 2);
        action
            dynamicAssert(!core.executionDone, "command accepted as already complete");
            core.putActivationRow(vector2(1, -1));
        endaction
        action
            dynamicAssert(!core.executionDone && core.debugFirstColumnCommitted == 0,
                "activation acceptance counted as BRAM commit");
        endaction
        await(core.executionDone);
        action
            dynamicAssert(core.debugFirstColumnCommitted == 1,
                "execution done before writeback completion");
            core.requestReadAccumulatorRow(1023);
        endaction
        action
            dynamicAssert(core.readAccumulatorRowResponse == vector2(-2147483648, 2147483647),
                "INT32 boundary wrap or last-row execution failed");
            core.consumeAccumulatorReadResponse;
        endaction
        core.acknowledgeExecution;
        core.startExecution(ExecuteCmd {
            accumulatorBaseRow: 1023, rowCount: 1,
            accumulate: True, vectorOp: VectorBypass
        }, 0, 2);
        core.putActivationRow(vector2(-1, 1));
        await(core.executionDone);
        core.requestReadAccumulatorRow(1023);
        action
            dynamicAssert(core.readAccumulatorRowResponse == vector2(2147483647, -2147483648),
                "next execution read stale accumulator data");
            core.consumeAccumulatorReadResponse;
        endaction
        core.acknowledgeExecution;

        // Count=2 must remain representable, and base=rows-count must be legal.
        core.startExecution(ExecuteCmd {
            accumulatorBaseRow: 1022, rowCount: 2,
            accumulate: False, vectorOp: VectorBypass
        }, 0, 2);
        core.putActivationRow(vector2(7, 8));
        core.putActivationRow(vector2(9, 10));
        await(core.executionDone);
        core.requestReadAccumulatorRow(1022);
        action
            dynamicAssert(core.readAccumulatorRowResponse == vector2(7, 8),
                "last full row-count first row failed");
            core.consumeAccumulatorReadResponse;
        endaction
        core.requestReadAccumulatorRow(1023);
        action
            dynamicAssert(core.readAccumulatorRowResponse == vector2(9, 10),
                "last full row-count second row failed");
            core.consumeAccumulatorReadResponse;
        endaction
        core.acknowledgeExecution;

        core.startMatmul(91, FullMatrix,
            64'h1000, 64'h2000, 64'h3000, 64'h4000,
            2, 2, 2, 8, 1, 2, 2, 1, 2, 0, 2, 2, 0, False, VectorBypass);
        await(core.outputWriteRequestValid);
        action
            dynamicAssert(core.outputWriteRequestValues == vector2(5, -6),
                "output exposed values before BRAM response");
            heldTag <= core.outputWriteRequestTag;
            holdOutput <= True;
        endaction
        delay(7);
        action
            dynamicAssert(core.outputWriteResponses == 0 && !core.matmulDone,
                "output accepted itself during host stall");
            core.putOutputWriteResponse(heldTag);
            holdOutput <= False;
        endaction
        await(core.matmulDone);
        action
            dynamicAssert(core.outputWriteRequests == 1 && core.outputWriteResponses == 1,
                "output write dropped or duplicated");
            dynamicAssert(core.lastCompletedWorkCycles
                == core.workCompletionCycle - core.workStartCycle,
                "BRAM latency broke work cycle endpoints");
            dynamicAssert(core.outputWaitCycles >= 7,
                "output backpressure missing from RTL cycle telemetry");
            $display("CORE BRAM BOUNDARY: PASS INT32 wrap, full addresses/count, repeat execution, held read/output, RTL cycles");
            $finish(0);
        endaction
    endseq;
    mkAutoFSM(test);
endmodule

endpackage
