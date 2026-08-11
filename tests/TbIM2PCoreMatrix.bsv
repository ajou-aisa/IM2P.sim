package TbIM2PCoreMatrix;

import Vector::*;

import TestVectorUtils::*;

import Types::*;
import HostMemoryTypes::*;
import WorkTypes::*;
import ExecuteCmd::*;
import IM2PCore::*;

// -----------------------------------------------------------------------------
// Address-driven scheduler integration test for the single IM2PCore
// -----------------------------------------------------------------------------
//
// 이 testbench는 host memory를 모사하는 provider 역할을 하며, Core가 스스로
// 생성한 A/W/S/C 주소만으로 전체 matmul을 완주하는지 검증한다. Testbench는
// I/J/K loop을 돌리지 않고, legacy beginWeightLoad/startExecution/
// putActivationRow도 사용하지 않는다. 검증 대상은 Core가 발행하는 주소, tag,
// element count, 채널 독립성, 완료 순서다.
//
// Geometry: arrayDim = 2, M = N = K = 3.
// 따라서 I/J/K 세 축에 동시에 tail(1)이 존재한다.
//
//     I tiles : (0,2), (2,1)
//     J tiles : (0,2), (2,1)
//     K frags : (0,2), (2,1)
//
// Byte 규약: A/W/S element = 1 byte, C element = 4 bytes.
//
//     A row address = A + (iStart + row) * aStride + localK * 1
//     W row address = W + (localK + wRow) * wStride + jStart * 1
//     S row address = S + (block - kOrigin/blockSize) * sStride + jStart * 1
//     C row address = C + (iStart + row) * cStride + jStart * 4
//
// 검증 방식은 정확한 발행 순서를 강제하지 않는다. 네 채널은 서로 독립이므로
// 순서를 고정하면 구현을 과하게 제약한다. 대신 다음을 강제한다.
//
//   1. 모든 요청 주소/개수는 위 공식에서 유도된 합법 집합에 정확히 속한다.
//   2. 각 채널의 총 요청 수가 정확하다(A=12, W=12, C=6).
//   3. 각 C 주소는 정확히 한 번만 기록된다.
//   4. Bypass job은 S 요청을 전혀 발행하지 않는다.
//   5. matmulDone은 모든 C write 응답 이후에만 assert된다.
//   6. Async mode는 publish 전에 A 요청을 발행하지 않는다.
//   7. Active weight bank는 execution drain 이후에만 전환된다.
//
// 응답은 sleep이 아니라 상태 기계로 지연시킨다. 각 채널은 요청을 포착한 뒤
// 정해진 cycle 수만큼 pending 상태에 머무르고, 그동안 다른 채널은 계속
// 진행할 수 있어야 한다.

typedef UInt#(64) Addr;

// Testbench가 모사하는 host memory geometry다.
Addr activationBaseAddr = 64'h1000;
Addr weightBaseAddr = 64'h2000;
Addr scaleBaseAddr = 64'h3000;
Addr outputBaseAddr = 64'h4000;

HostStride activationStride = 8;
HostStride weightStride = 8;
HostStride scaleStride = 8;
HostStride outputStride = 16;

typedef enum {
    TbFullStart,
    TbFullRun,
    TbFullCheck,
    TbAsyncStart,
    TbAsyncObserveGate,
    TbAsyncPublish,
    TbAsyncRun,
    TbAsyncCheck,
    TbBankStart,
    TbBankObserve,
    TbBankCheck,
    TbPass
} TbPhase deriving (Bits, Eq, FShow);

// A[i][k] = i * 3 + k + 1, K = 3.
function Int#(8) activationElement(MatrixExtent row, MatrixExtent k);
    UInt#(32) value = row * 3 + k + 1;
    return unpack(truncate(pack(value)));
endfunction

// W[k][j] = identity, K = N = 3.
function Int#(8) weightElement(MatrixExtent k, MatrixExtent column);
    return k == column ? 1 : 0;
endfunction

// Identity weight 이므로 C = A 의 좌상단 3x3이다.
function Int#(32) expectedOutput(MatrixExtent row, MatrixExtent column);
    UInt#(32) value = row * 3 + column + 1;
    return unpack(pack(value));
endfunction

// A 요청 주소를 (row, localK)로 역산한다. 합법이 아니면 Invalid다.
function Maybe#(Tuple2#(MatrixExtent, MatrixExtent)) decodeActivation(
    Addr address,
    BoundedCount#(2) elementCount
);
    Addr offset = address - activationBaseAddr;
    Addr row = offset / activationStride;
    Addr localK = offset % activationStride;
    BoundedCount#(2) expectedCount = localK == 2 ? 1 : 2;

    Bool legal = address >= activationBaseAddr
        && row < 3
        && (localK == 0 || localK == 2)
        && elementCount == expectedCount;

    return legal
        ? tagged Valid tuple2(truncate(row), truncate(localK))
        : tagged Invalid;
endfunction

// W 요청 주소를 (globalRow, jStart)로 역산한다.
function Maybe#(Tuple2#(MatrixExtent, MatrixExtent)) decodeWeight(
    Addr address,
    BoundedCount#(2) elementCount
);
    Addr offset = address - weightBaseAddr;
    Addr row = offset / weightStride;
    Addr jStart = offset % weightStride;
    BoundedCount#(2) expectedCount = jStart == 2 ? 1 : 2;

    Bool legal = address >= weightBaseAddr
        && row < 3
        && (jStart == 0 || jStart == 2)
        && elementCount == expectedCount;

    return legal
        ? tagged Valid tuple2(truncate(row), truncate(jStart))
        : tagged Invalid;
endfunction

// C 요청 주소를 (row, jStart)로 역산한다.
function Maybe#(Tuple2#(MatrixExtent, MatrixExtent)) decodeOutput(
    Addr address,
    BoundedCount#(2) elementCount
);
    Addr offset = address - outputBaseAddr;
    Addr row = offset / outputStride;
    Addr columnBytes = offset % outputStride;
    Addr jStart = columnBytes / 4;
    BoundedCount#(2) expectedCount = jStart == 2 ? 1 : 2;

    Bool legal = address >= outputBaseAddr
        && row < 3
        && (columnBytes % 4) == 0
        && (jStart == 0 || jStart == 2)
        && elementCount == expectedCount;

    return legal
        ? tagged Valid tuple2(truncate(row), truncate(jStart))
        : tagged Invalid;
endfunction

// C 주소를 6개 tile row slot 중 하나의 index로 사상한다.
function UInt#(3) outputSlot(MatrixExtent row, MatrixExtent jStart);
    return truncate(row * 2 + (jStart == 0 ? 0 : 1));
endfunction

module mkTbIM2PCoreMatrix(Empty);
    IM2PCoreIfc#(
        2,
        1,
        1,
        8,
        Int#(8),
        Int#(8),
        Int#(16),
        Int#(32),
        Int#(8)
    ) core <- mkIM2PCore;

    Reg#(TbPhase) phase <- mkReg(TbFullStart);
    Reg#(UInt#(16)) watchdog <- mkReg(0);

    // 채널별 pending 응답 상태다. 요청을 포착한 cycle에 즉시 응답하지 않고
    // 서로 다른 지연을 준다. 이렇게 해야 Core가 특정 채널의 즉답에 의존하지
    // 않는다는 점이 드러난다.
    Reg#(Bool) activationPending <- mkReg(False);
    Reg#(UInt#(3)) activationDelay <- mkReg(0);
    Reg#(HostRequestTag) activationTag <- mkRegU;
    Reg#(MatrixExtent) activationRow <- mkRegU;
    Reg#(MatrixExtent) activationK <- mkRegU;
    Reg#(BoundedCount#(2)) activationCount <- mkRegU;

    Reg#(Bool) weightPending <- mkReg(False);
    Reg#(UInt#(3)) weightDelay <- mkReg(0);
    Reg#(HostRequestTag) weightTag <- mkRegU;
    Reg#(MatrixExtent) weightRow <- mkRegU;
    Reg#(MatrixExtent) weightColumn <- mkRegU;
    Reg#(BoundedCount#(2)) weightCount <- mkRegU;

    Reg#(Bool) outputPending <- mkReg(False);
    Reg#(UInt#(3)) outputDelay <- mkReg(0);
    Reg#(HostRequestTag) outputTag <- mkRegU;

    // 관측 counter다.
    Reg#(UInt#(6)) activationRequests <- mkReg(0);
    Reg#(UInt#(6)) weightRequests <- mkReg(0);
    Reg#(UInt#(6)) scaleRequests <- mkReg(0);
    Reg#(UInt#(6)) outputRequests <- mkReg(0);
    Reg#(UInt#(6)) outputResponses <- mkReg(0);
    Reg#(Vector#(6, Bool)) outputWritten <- mkReg(replicate(False));

    // Async gate 관측용이다. publish 이전에 A 요청이 보이면 즉시 실패다.
    Reg#(Bool) publishDone <- mkReg(False);
    Reg#(UInt#(4)) asyncGateCycles <- mkReg(0);

    // Weight bank drain 관측용이다.
    Reg#(Bool) bankObserved <- mkReg(False);
    Reg#(Bool) bankViolation <- mkReg(False);
    Reg#(Bool) previousActiveBank <- mkReg(False);
    Reg#(Bool) bankSwitchSeen <- mkReg(False);
    Reg#(Bool) preloadDuringExecutionSeen <- mkReg(False);

    rule watch;
        watchdog <= watchdog + 1;

        if (watchdog == 20000) begin
            $display(
                "IM2P CORE MATRIX: FAIL timeout phase=",
                fshow(phase),
                " a=%0d w=%0d s=%0d c=%0d/%0d",
                activationRequests,
                weightRequests,
                scaleRequests,
                outputRequests,
                outputResponses
            );
            $finish(1);
        end
    endrule

    // -------------------------------------------------------------------------
    // Activation read channel
    // -------------------------------------------------------------------------

    rule captureActivationRequest (
        core.activationReadRequestValid && !activationPending
    );
        Addr address = core.activationReadRequestAddress;
        BoundedCount#(2) count = core.activationReadRequestElementCount;
        Maybe#(Tuple2#(MatrixExtent, MatrixExtent)) decoded =
            decodeActivation(address, count);

        // Async mode는 stripe publish 이전에 activation을 읽으면 안 된다.
        if (phase == TbAsyncStart
                || phase == TbAsyncObserveGate
                || (phase == TbAsyncPublish && !publishDone)) begin
            $display(
                "IM2P CORE MATRIX: FAIL activation request before stripe publish address=%0h",
                address
            );
            $finish(1);
        end

        if (!isValid(decoded)) begin
            $display(
                "IM2P CORE MATRIX: FAIL illegal activation address=%0h count=%0d",
                address, count
            );
            $finish(1);
        end

        // Tag는 상위 32bit에 jobId를 담는다.
        HostRequestTag tag = core.activationReadRequestTag;

        if ((tag >> 32) != 21) begin
            $display(
                "IM2P CORE MATRIX: FAIL activation tag jobId=%0d tag=%0h",
                tag >> 32, tag
            );
            $finish(1);
        end

        activationTag <= tag;
        activationRow <= tpl_1(fromMaybe(tuple2(0, 0), decoded));
        activationK <= tpl_2(fromMaybe(tuple2(0, 0), decoded));
        activationCount <= count;
        activationRequests <= activationRequests + 1;
        activationPending <= True;
        activationDelay <= 3;
    endrule

    rule advanceActivationDelay (activationPending && activationDelay != 0);
        activationDelay <= activationDelay - 1;
    endrule

    rule returnActivationResponse (activationPending && activationDelay == 0);
        Vector#(2, Int#(8)) values = replicate(0);

        for (Integer lane = 0; lane < 2; lane = lane + 1) begin
            if (fromInteger(lane) < activationCount) begin
                values[lane] = activationElement(
                    activationRow,
                    activationK + fromInteger(lane)
                );
            end
        end

        core.putActivationReadResponse(activationTag, values);
        activationPending <= False;
    endrule

    // -------------------------------------------------------------------------
    // Weight read channel
    // -------------------------------------------------------------------------

    rule captureWeightRequest (
        core.weightReadRequestValid && !weightPending
    );
        Addr address = core.weightReadRequestAddress;
        BoundedCount#(2) count = core.weightReadRequestElementCount;
        Maybe#(Tuple2#(MatrixExtent, MatrixExtent)) decoded =
            decodeWeight(address, count);

        if (!isValid(decoded)) begin
            $display(
                "IM2P CORE MATRIX: FAIL illegal weight address=%0h count=%0d",
                address, count
            );
            $finish(1);
        end

        weightTag <= core.weightReadRequestTag;
        weightRow <= tpl_1(fromMaybe(tuple2(0, 0), decoded));
        weightColumn <= tpl_2(fromMaybe(tuple2(0, 0), decoded));
        weightCount <= count;
        weightRequests <= weightRequests + 1;
        weightPending <= True;
        weightDelay <= 1;
    endrule

    rule advanceWeightDelay (weightPending && weightDelay != 0);
        weightDelay <= weightDelay - 1;
    endrule

    rule returnWeightResponse (weightPending && weightDelay == 0);
        Vector#(2, Int#(8)) values = replicate(0);

        for (Integer lane = 0; lane < 2; lane = lane + 1) begin
            if (fromInteger(lane) < weightCount) begin
                values[lane] = weightElement(
                    weightRow,
                    weightColumn + fromInteger(lane)
                );
            end
        end

        core.putWeightReadResponse(weightTag, values);
        weightPending <= False;
    endrule

    // -------------------------------------------------------------------------
    // Scale read channel
    //
    // 세 phase 모두 VectorBypass를 사용하므로 S 요청은 하나도 나와서는 안 된다.
    // -------------------------------------------------------------------------

    rule rejectScaleRequest (core.scaleReadRequestValid);
        scaleRequests <= scaleRequests + 1;
        $display(
            "IM2P CORE MATRIX: FAIL bypass job issued scale request address=%0h",
            core.scaleReadRequestAddress
        );
        $finish(1);
    endrule

    // -------------------------------------------------------------------------
    // Output write channel
    // -------------------------------------------------------------------------

    rule captureOutputRequest (
        core.outputWriteRequestValid && !outputPending
    );
        Addr address = core.outputWriteRequestAddress;
        BoundedCount#(2) count = core.outputWriteRequestElementCount;
        Vector#(2, Int#(32)) values = core.outputWriteRequestValues;
        Maybe#(Tuple2#(MatrixExtent, MatrixExtent)) decoded =
            decodeOutput(address, count);

        if (!isValid(decoded)) begin
            $display(
                "IM2P CORE MATRIX: FAIL illegal output address=%0h count=%0d",
                address, count
            );
            $finish(1);
        end

        MatrixExtent row = tpl_1(fromMaybe(tuple2(0, 0), decoded));
        MatrixExtent jStart = tpl_2(fromMaybe(tuple2(0, 0), decoded));
        UInt#(3) slot = outputSlot(row, jStart);

        // 같은 C row/tile은 정확히 한 번만 기록되어야 한다.
        if (outputWritten[slot]) begin
            $display(
                "IM2P CORE MATRIX: FAIL duplicate output write address=%0h",
                address
            );
            $finish(1);
        end

        Bool dataOk = True;

        for (Integer lane = 0; lane < 2; lane = lane + 1) begin
            if (fromInteger(lane) < count) begin
                Int#(32) expected = expectedOutput(
                    row,
                    jStart + fromInteger(lane)
                );

                if (values[lane] != expected) begin
                    dataOk = False;
                end
            end
        end

        if (!dataOk) begin
            $display(
                "IM2P CORE MATRIX: FAIL output data address=%0h v=(%0d,%0d)",
                address, values[0], values[1]
            );
            $finish(1);
        end

        Vector#(6, Bool) written = outputWritten;
        written[slot] = True;
        outputWritten <= written;

        outputTag <= core.outputWriteRequestTag;
        outputRequests <= outputRequests + 1;
        outputPending <= True;
        outputDelay <= 2;
    endrule

    rule advanceOutputDelay (outputPending && outputDelay != 0);
        outputDelay <= outputDelay - 1;
    endrule

    rule returnOutputResponse (outputPending && outputDelay == 0);
        core.putOutputWriteResponse(outputTag);
        outputResponses <= outputResponses + 1;
        outputPending <= False;
    endrule

    // 모든 C write가 acknowledge되기 전에 matmulDone이 뜨면 실패다.
    rule checkCompletionOrdering (core.matmulDone);
        if (phase == TbFullRun && outputResponses != 6) begin
            $display(
                "IM2P CORE MATRIX: FAIL matmulDone before writes complete acks=%0d",
                outputResponses
            );
            $finish(1);
        end
    endrule

    // -------------------------------------------------------------------------
    // Weight bank drain 관측
    //
    // Active bank는 execution이 완전히 drain된 뒤에만 바뀌어야 하고, 실행
    // 중에는 inactive bank로만 preload가 진행되어야 한다.
    // -------------------------------------------------------------------------

    rule observeWeightBank;
        Bool activeBank = core.activeWeightBank;

        if (activeBank != previousActiveBank) begin
            bankSwitchSeen <= True;

            if (core.executionActive) begin
                bankViolation <= True;
                $display(
                    "IM2P CORE MATRIX: FAIL active weight bank switched during execution"
                );
                $finish(1);
            end
        end

        if (core.executionActive && core.inactiveWeightBankLoading) begin
            preloadDuringExecutionSeen <= True;
        end

        previousActiveBank <= activeBank;
    endrule

    // -------------------------------------------------------------------------
    // Phase 1: full-matrix job. M = N = K = 3, DIM = 2, VectorBypass.
    // -------------------------------------------------------------------------

    rule startFullMatrix (phase == TbFullStart && core.idle);
        core.startMatmul(
            21,                 // jobId
            FullMatrix,         // mode
            activationBaseAddr,
            weightBaseAddr,
            scaleBaseAddr,
            outputBaseAddr,
            activationStride,
            weightStride,
            scaleStride,
            outputStride,
            3,                  // rowCount M
            3,                  // columnCount N
            3,                  // reductionCount K
            2,                  // tileIRows
            2,                  // tileJColumns
            0,                  // kOrigin
            3,                  // scaleTotalK
            1,                  // scaleBlockSize
            0,                  // scaleContext
            False,              // accumulateFirstFragment
            VectorBypass
        );
        phase <= TbFullRun;
    endrule

    rule finishFullMatrix (phase == TbFullRun && core.matmulDone);
        Bool countsOk = activationRequests == 12
            && weightRequests == 12
            && scaleRequests == 0
            && outputRequests == 6
            && outputResponses == 6
            && allTrue(outputWritten);

        if (!countsOk) begin
            $display(
                "IM2P CORE MATRIX: FAIL full counts a=%0d w=%0d s=%0d c=%0d acks=%0d",
                activationRequests,
                weightRequests,
                scaleRequests,
                outputRequests,
                outputResponses
            );
            $finish(1);
        end

        // Full mode must prepare the next I tile/J0 while the current I
        // tile traverses J, then promote it without changing numerical output.
        if (core.lookaheadFirstActivationCycle == 0
                || core.lookaheadFirstWeightCycle == 0
                || core.lookaheadWeightPreloadCycle == 0
                || core.currentStripeCompletionCycle == 0
                || core.lookaheadStartCycle
                    <= core.currentStripeCompletionCycle) begin
            $display(
                "IM2P CORE MATRIX: FAIL full lookahead a=%0d w=%0d preload=%0d complete=%0d start=%0d",
                core.lookaheadFirstActivationCycle,
                core.lookaheadFirstWeightCycle,
                core.lookaheadWeightPreloadCycle,
                core.currentStripeCompletionCycle,
                core.lookaheadStartCycle
            );
            $finish(1);
        end

        $display(
            "FULL LOOKAHEAD a=%0d w=%0d preload=%0d complete=%0d start=%0d",
            core.lookaheadFirstActivationCycle,
            core.lookaheadFirstWeightCycle,
            core.lookaheadWeightPreloadCycle,
            core.currentStripeCompletionCycle,
            core.lookaheadStartCycle
        );

        // 여러 K fragment와 여러 I/J tile을 실제로 순회했는지 확인한다.
        if (core.matmulFragmentsCompleted <= 1
                || core.matmulWorksCompleted <= 1) begin
            $display(
                "IM2P CORE MATRIX: FAIL scheduling not exercised fragments=%0d works=%0d",
                core.matmulFragmentsCompleted,
                core.matmulWorksCompleted
            );
            $finish(1);
        end

        core.acknowledgeMatmul;
        phase <= TbFullCheck;
    endrule

    rule resetForAsync (phase == TbFullCheck && core.idle);
        activationRequests <= 0;
        weightRequests <= 0;
        outputRequests <= 0;
        outputResponses <= 0;
        outputWritten <= replicate(False);
        phase <= TbAsyncStart;
    endrule

    // -------------------------------------------------------------------------
    // Phase 2: async stripe job. publish 이전에는 A 요청이 없어야 한다.
    // -------------------------------------------------------------------------

    rule startAsyncMatrix (phase == TbAsyncStart && core.idle);
        core.startMatmul(
            21,
            AsyncStripes,
            activationBaseAddr,
            weightBaseAddr,
            scaleBaseAddr,
            outputBaseAddr,
            activationStride,
            weightStride,
            scaleStride,
            outputStride,
            3,
            3,
            3,
            2,
            2,
            0,
            3,
            1,
            0,
            False,
            VectorBypass
        );
        phase <= TbAsyncObserveGate;
    endrule

    // Publish 없이 충분한 cycle 동안 A 요청이 나오지 않아야 한다.
    // captureActivationRequest가 이 phase에서 곧바로 실패시킨다.
    rule observeAsyncGate (phase == TbAsyncObserveGate);
        if (activationRequests != 0) begin
            $display(
                "IM2P CORE MATRIX: FAIL async issued %0d activation reads before publish",
                activationRequests
            );
            $finish(1);
        end

        if (asyncGateCycles == 12) begin
            phase <= TbAsyncPublish;
        end
        else begin
            asyncGateCycles <= asyncGateCycles + 1;
        end
    endrule

    rule publishStripes (phase == TbAsyncPublish && !publishDone);
        core.publishActivationStripe(0, 3, activationStride);
        publishDone <= True;
        phase <= TbAsyncRun;
    endrule

    rule finishAsyncMatrix (phase == TbAsyncRun && core.matmulDone);
        Bool countsOk = activationRequests == 12
            && outputRequests == 6
            && outputResponses == 6
            && allTrue(outputWritten);

        if (!countsOk) begin
            $display(
                "IM2P CORE MATRIX: FAIL async counts a=%0d c=%0d acks=%0d",
                activationRequests,
                outputRequests,
                outputResponses
            );
            $finish(1);
        end

        core.acknowledgeMatmul;
        phase <= TbAsyncCheck;
    endrule

    rule checkAsyncStats (phase == TbAsyncCheck && core.idle);
        if (core.stripesPublished != 1 || core.stripeRowsPublished != 3) begin
            $display(
                "IM2P CORE MATRIX: FAIL stripe stats stripes=%0d rows=%0d",
                core.stripesPublished,
                core.stripeRowsPublished
            );
            $finish(1);
        end

        phase <= TbBankStart;
    endrule

    // -------------------------------------------------------------------------
    // Phase 3: weight bank 전환은 drain 이후에만 일어나야 한다.
    //
    // 두 개 이상의 J tile을 가진 job을 돌리면 weight bank를 반드시 갈아끼워야
    // 하며, 그 전환은 execution이 끝난 뒤에만 관측되어야 한다.
    // -------------------------------------------------------------------------

    rule startBankJob (phase == TbBankStart && core.idle);
        activationRequests <= 0;
        weightRequests <= 0;
        outputRequests <= 0;
        outputResponses <= 0;
        outputWritten <= replicate(False);
        bankSwitchSeen <= False;
        preloadDuringExecutionSeen <= False;

        core.startMatmul(
            21,
            FullMatrix,
            activationBaseAddr,
            weightBaseAddr,
            scaleBaseAddr,
            outputBaseAddr,
            activationStride,
            weightStride,
            scaleStride,
            outputStride,
            3,
            3,
            3,
            2,
            2,
            0,
            3,
            1,
            0,
            False,
            VectorBypass
        );
        phase <= TbBankObserve;
    endrule

    rule finishBankJob (phase == TbBankObserve && core.matmulDone);
        core.acknowledgeMatmul;
        bankObserved <= True;
        phase <= TbBankCheck;
    endrule

    rule checkBankJob (phase == TbBankCheck && bankObserved && core.idle);
        if (bankViolation) begin
            $display(
                "IM2P CORE MATRIX: FAIL weight bank switched without drain"
            );
            $finish(1);
        end

        if (!bankSwitchSeen) begin
            $display(
                "IM2P CORE MATRIX: FAIL weight bank never alternated"
            );
            $finish(1);
        end

        if (!preloadDuringExecutionSeen) begin
            $display(
                "IM2P CORE MATRIX: FAIL no inactive bank preload overlapped execution"
            );
            $finish(1);
        end

        if (core.weightBankActivations < 2) begin
            $display(
                "IM2P CORE MATRIX: FAIL bank activations=%0d",
                core.weightBankActivations
            );
            $finish(1);
        end

        phase <= TbPass;
    endrule

    rule reportPass (phase == TbPass);
        $display("IM2P CORE MATRIX: PASS");
        $finish(0);
    endrule
endmodule

endpackage
