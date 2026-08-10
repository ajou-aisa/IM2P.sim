package TbIM2PCore;

import Vector::*;

import TestVectorUtils::*;

import Types::*;
import ExecuteCmd::*;
import IM2PCore::*;


function VectorOp operationFor(UInt#(2) executionIndex);
    case (executionIndex)
        0: return VectorBypass;
        1: return VectorMultiply;
        default: return VectorShift;
    endcase
endfunction

// Scale은 execution 단위 column vector이며 같은 execution의 모든 output row가
// 공유한다.
function Vector#(2, Int#(8)) scaleFor(UInt#(2) executionIndex);
    return executionIndex == 1
        ? vector2(2, 3)
        : vector2(1, -1);
endfunction

function Vector#(2, Int#(32)) expectedRow(
    UInt#(2) executionIndex,
    UInt#(1) row
);
    case (executionIndex)
        0: return row == 0
            ? vector2(5, 6)
            : vector2(7, 8);
        1: return row == 0
            ? vector2(15, 24)
            : vector2(21, 32);
        default: return row == 0
            ? vector2(25, 27)
            : vector2(35, 36);
    endcase
endfunction

// 하나의 IM2PCore instance에서 Bypass/Multiply/Shift를 연속 실행한다.
// vectorLanes=1로 두어 하나의 2-column array result가 두 group으로 처리될 때
// destination row metadata가 유지되는지도 함께 검증한다.
typedef enum {
    TbBeginWeights,
    TbLoadWeight0,
    TbLoadWeight1,
    TbConfigure,
    TbLoadScale,
    TbStart,
    TbFeedRow0,
    TbFeedRow1,
    TbWait,
    TbReadRow0
} TbState deriving (Bits, Eq, FShow);

module mkTbIM2PCore(Empty);
    IM2PCoreIfc#(
        2,
        1,
        1,
        8,
        2,
        Int#(8),
        Int#(8),
        Int#(16),
        Int#(32),
        Int#(8)
    ) core <- mkIM2PCore;

    Reg#(TbState) state <- mkReg(TbBeginWeights);
    Reg#(UInt#(2)) executionIndex <- mkReg(0);
    Reg#(Vector#(2, Int#(32))) observedRow0 <- mkRegU;
    Reg#(UInt#(10)) watchdog <- mkReg(0);

    rule watch;
        watchdog <= watchdog + 1;
        if (watchdog == 700) begin
            $display("IM2P CORE: FAIL (timeout)");
            $finish(1);
        end
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

    // Bypass execution은 scaling configuration 없이 시작한다.
    rule skipConfigure (state == TbConfigure && executionIndex == 0);
        state <= TbStart;
    endrule

    rule configureScaling (
        state == TbConfigure
        && executionIndex != 0
        && core.idle
    );
        core.configureScaling(2, 2, 1);
        state <= TbLoadScale;
    endrule

    rule loadScale (state == TbLoadScale && !core.scaleLoadReady);
        core.loadScaleBlock(scaleFor(executionIndex));
        state <= TbStart;
    endrule

    rule startExecution (state == TbStart && core.weightsReady && core.idle);
        core.startExecution(ExecuteCmd {
            accumulatorBaseRow: 3,
            rowCount: 2,
            accumulate: executionIndex != 0,
            vectorOp: operationFor(executionIndex)
        }, 0, 2);
        state <= TbFeedRow0;
    endrule

    rule feedRow0 (state == TbFeedRow0 && core.activationReady);
        core.putActivationRow(vector2(5, 6));
        state <= TbFeedRow1;
    endrule

    rule feedRow1 (state == TbFeedRow1 && core.activationReady);
        core.putActivationRow(vector2(7, 8));
        state <= TbWait;
    endrule

    rule waitExecution (state == TbWait && core.executionDone);
        // Accumulator는 한 cycle에 한 logical row를 읽는 host/debug port를 제공한다.
        // 서로 다른 두 row를 같은 rule에서 읽지 않고 cycle을 나누어 확인한다.
        observedRow0 <= core.readAccumulatorRow(3);
        state <= TbReadRow0;
    endrule

    rule checkExecution (state == TbReadRow0);
        Vector#(2, Int#(32)) row1 = core.readAccumulatorRow(4);
        Bool passed = observedRow0 == expectedRow(executionIndex, 0)
            && row1 == expectedRow(executionIndex, 1);

        if (!passed) begin
            $display(
                "IM2P CORE: FAIL execution=%0d row0=(%0d,%0d) row1=(%0d,%0d)",
                executionIndex,
                observedRow0[0], observedRow0[1], row1[0], row1[1]
            );
            $finish(1);
        end
        else if (executionIndex == 2) begin
            $display("IM2P CORE: PASS");
            $finish(0);
        end
        else begin
            core.acknowledgeExecution;
            executionIndex <= executionIndex + 1;
            state <= TbConfigure;
        end
    endrule
endmodule

endpackage
