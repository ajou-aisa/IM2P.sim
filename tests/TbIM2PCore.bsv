package TbIM2PCore;

import Vector::*;

import TestVectorUtils::*;

import Types::*;
import ExecuteCmd::*;
import IM2PCore::*;


function Vector#(2, Int#(8)) scaleRowFor(
    ScaleContext contextId,
    ScaleBlockIndex block
);
    Vector#(2, Int#(8)) row = case (block)
        0: vector2(2, 3);
        1: vector2(4, 5);
        default: vector2(6, 7);
    endcase;

    return contextId == 12 ? vector2(8, 9) : row;
endfunction

function Vector#(2, Int#(64)) expectedFor(UInt#(3) executionIndex);
    case (executionIndex)
        0: return vector2(5, 6);
        1: return vector2(10, 18);
        2: return vector2(10, 18);
        3: return vector2(20, 30);
        4: return vector2(30, 42);
        5: return vector2(40, 54);
        default: return vector2(2147483648, -2147483649);
    endcase
endfunction

function UInt#(32) kStartFor(UInt#(3) executionIndex);
    case (executionIndex)
        3: return 2;
        4: return 4;
        5: return 4;
        default: return 0;
    endcase
endfunction

typedef enum {
    TbBeginWeights,
    TbLoadWeight0,
    TbLoadWeight1,
    TbConfigure,
    TbStart,
    TbFeed,
    TbWait,
    TbCheck
} TbState deriving (Bits, Eq, FShow);

module mkTbIM2PCore(Empty);
    IM2PCoreIfc#(
        2,
        1,
        1,
        8,
        Int#(8),
        Int#(8),
        Int#(16),
        Int#(64),
        Int#(8)
    ) core <- mkIM2PCore;

    Reg#(TbState) state <- mkReg(TbBeginWeights);
    Reg#(UInt#(3)) executionIndex <- mkReg(0);
    Reg#(Bool) responsePending <- mkReg(False);
    Reg#(UInt#(2)) responseDelay <- mkReg(0);
    Reg#(ScaleRowRequest) capturedRequest <- mkRegU;
    Reg#(UInt#(10)) watchdog <- mkReg(0);

    rule watch;
        watchdog <= watchdog + 1;
        if (watchdog == 900) begin
            $display("IM2P CORE: FAIL (timeout)");
            $finish(1);
        end
    endrule

    rule captureScaleRequest (core.scaleRequestValid && !responsePending);
        ScaleRowRequest request = core.scaleRequest;
        Bool expected =
            (request.contextId == 11
                && request.block == 0
                && request.kind == ScaleDemand)
            || (request.contextId == 11
                && request.block == 1
                && request.kind == ScalePrefetch)
            || (request.contextId == 11
                && request.block == 2
                && request.kind == ScalePrefetch)
            || (request.contextId == 12
                && request.block == 2
                && request.kind == ScaleDemand);

        if (!expected) begin
            $display(
                "IM2P CORE: FAIL unexpected scale request context=%0d block=%0d kind=%0d",
                request.contextId, request.block, request.kind
            );
            $finish(1);
        end

        capturedRequest <= request;
        responsePending <= True;
        responseDelay <= 2;
    endrule

    rule delayScaleResponse (responsePending && responseDelay != 0);
        responseDelay <= responseDelay - 1;
    endrule

    rule returnScaleResponse (responsePending && responseDelay == 0);
        core.putScaleRow(
            capturedRequest.contextId,
            capturedRequest.block,
            scaleRowFor(capturedRequest.contextId, capturedRequest.block)
        );
        responsePending <= False;
    endrule

    rule beginWeights (state == TbBeginWeights);
        core.beginWeightLoad;
        state <= TbLoadWeight0;
    endrule

    rule loadWeight0 (state == TbLoadWeight0);
        core.loadWeightRow(0, vector2(1, 0));
        state <= TbLoadWeight1;
    endrule

    rule loadWeight1 (state == TbLoadWeight1);
        core.loadWeightRow(1, vector2(0, 1));
        state <= TbConfigure;
    endrule

    rule configureOrAdvance (state == TbConfigure && core.idle);
        if (executionIndex == 1 || executionIndex == 4) begin
            core.configureScaling(2, 6, 11);
        end
        else if (executionIndex == 5) begin
            core.configureScaling(2, 6, 12);
        end
        else if (executionIndex == 6) begin
            core.writeAccumulatorRow(
                3,
                vector2(2147483647, -2147483648)
            );
        end
        state <= TbStart;
    endrule

    rule startExecution (state == TbStart && core.weightsReady && core.idle);
        core.startExecution(ExecuteCmd {
            accumulatorBaseRow: 3,
            rowCount: 1,
            accumulate: executionIndex == 6,
            vectorOp: executionIndex == 0 || executionIndex == 6
                ? VectorBypass
                : VectorMultiply
        }, kStartFor(executionIndex), 2);
        state <= TbFeed;
    endrule

    rule feedRow (state == TbFeed && core.activationReady);
        core.putActivationRow(
            executionIndex == 6 ? vector2(1, -1) : vector2(5, 6)
        );
        state <= TbWait;
    endrule

    rule waitExecution (state == TbWait && core.executionDone);
        state <= TbCheck;
    endrule

    rule checkExecution (state == TbCheck);
        Vector#(2, Int#(64)) observed = core.readAccumulatorRow(3);

        if (observed != expectedFor(executionIndex)) begin
            $display(
                "IM2P CORE: FAIL execution=%0d row=(%0d,%0d)",
                executionIndex, observed[0], observed[1]
            );
            $finish(1);
        end
        else if (executionIndex == 6) begin
            $display("IM2P CORE: PASS boundaries=(2147483648,-2147483649)");
            $finish(0);
        end
        else if (executionIndex == 5) begin
            Bool countersPassed = core.scaleDemandRequests == 2
                && core.scalePrefetchRequests == 2
                && core.scaleCurrentHits == 1
                && core.scaleNextHits == 2
                && core.scaleDemandMisses == 2
                && core.scaleRowsReceived == 4
                && core.scaleWaitCycles > 0;

            if (!countersPassed || core.scaleRequestValid) begin
                $display(
                    "IM2P CORE: FAIL counters demand=%0d prefetch=%0d current=%0d next=%0d misses=%0d rows=%0d waits=%0d",
                    core.scaleDemandRequests,
                    core.scalePrefetchRequests,
                    core.scaleCurrentHits,
                    core.scaleNextHits,
                    core.scaleDemandMisses,
                    core.scaleRowsReceived,
                    core.scaleWaitCycles
                );
                $finish(1);
            end
            else begin
                core.acknowledgeExecution;
                executionIndex <= executionIndex + 1;
                state <= TbConfigure;
            end
        end
        else begin
            core.acknowledgeExecution;
            executionIndex <= executionIndex + 1;
            state <= TbConfigure;
        end
    endrule
endmodule

endpackage
