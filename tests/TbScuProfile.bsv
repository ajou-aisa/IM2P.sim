package TbScuProfile;

import Assert::*;
import Vector::*;
import Config::*;
import Types::*;
import HostMemoryTypes::*;
import WorkTypes::*;
import IM2PCore::*;

`ifdef SCU_A8_D16
  typedef 16 TestDim;
  typedef 8 TestBits;
  typedef 16 TestProduct;
  typedef 32 TestAcc;
  typedef 20 TestPartial;
  Integer minimumInput = -128;
  Integer maximumInput = 127;
`elsif SCU_A8_D32
  typedef 32 TestDim;
  typedef 8 TestBits;
  typedef 16 TestProduct;
  typedef 32 TestAcc;
  typedef 21 TestPartial;
  Integer minimumInput = -128;
  Integer maximumInput = 127;
`elsif SCU_A8_D64
  typedef 64 TestDim;
  typedef 8 TestBits;
  typedef 16 TestProduct;
  typedef 32 TestAcc;
  typedef 22 TestPartial;
  Integer minimumInput = -128;
  Integer maximumInput = 127;
`elsif SCU_A4_D16
  typedef 16 TestDim;
  typedef 4 TestBits;
  typedef 8 TestProduct;
  typedef 32 TestAcc;
  typedef 12 TestPartial;
  Integer minimumInput = -8;
  Integer maximumInput = 7;
`elsif SCU_A16_D16
  typedef 16 TestDim;
  typedef 16 TestBits;
  typedef 32 TestProduct;
  typedef 64 TestAcc;
  typedef 36 TestPartial;
  Integer minimumInput = -32768;
  Integer maximumInput = 32767;
`else
  // Default focused regression is A4/D16; larger arrays are selected explicitly.
  typedef 16 TestDim;
  typedef 4 TestBits;
  typedef 8 TestProduct;
  typedef 32 TestAcc;
  typedef 12 TestPartial;
  Integer minimumInput = -8;
  Integer maximumInput = 7;
`endif

typedef TDiv#(TestDim, 2) TestVectorLanes;
typedef IntegerAccumulatorRows#(TestDim, TestAcc) TestRows;
HostAddress aBase = 64'h100000;
HostAddress wBase = 64'h200000;
HostAddress sBase = 64'h300000;
HostAddress cBase = 64'h400000;
Integer elementBytes = valueOf(TestBits) == 16 ? 2 : 1;

function MatrixExtent rows(UInt#(2) job);
    return job == 1 ? fromInteger(valueOf(TestDim) + 1) : 2;
endfunction
function MatrixExtent columns(UInt#(2) job);
    return job == 0 ? 4 : fromInteger(valueOf(TestDim) + 1);
endfunction
function MatrixExtent reduction(UInt#(2) job);
    return job == 0 ? fromInteger(valueOf(TestDim)) : 64;
endfunction
function HostStride aStride(UInt#(2) job);
    return zeroExtend(reduction(job)) * fromInteger(elementBytes) + 16;
endfunction
function HostStride wStride(UInt#(2) job);
    return (zeroExtend(columns(job)) + 4) * fromInteger(elementBytes);
endfunction
function HostStride sStride(UInt#(2) job);
    return (zeroExtend(columns(job)) + 2) * 4;
endfunction
function HostStride cStride(UInt#(2) job);
    // Canonical provider addresses retain the int32 compatibility span even
    // when the output callback transports an exact signed64 value.
    return (zeroExtend(columns(job)) + 3) * 4;
endfunction
function Int#(TestBits) activation(UInt#(2) job, HostAddress row);
    if (job == 0) return fromInteger(minimumInput);
    else if (row == fromInteger(valueOf(TestDim))) return 2;
    else return row % 2 == 0 ? 1 : -1;
endfunction
function Int#(TestBits) weight(UInt#(2) job, HostAddress k, HostAddress column);
    UInt#(2) lane = truncate(column);
    if (job == 0) begin
        case (lane)
            0: return fromInteger(minimumInput);
            1: return fromInteger(maximumInput);
            2: return 0;
            default: return 1;
        endcase
    end
    else begin
        Int#(TestBits) sign = lane == 1 ? -1 : (lane == 2 && job == 1 ? 0 : 1);
        return k < 32 ? sign : -sign;
    end
endfunction
function UInt#(32) metadata(UInt#(2) job, ScaleBlockIndex block, HostAddress column);
    UInt#(2) lane = truncate(column);
    if (job == 0) return 1;
    else if (job == 1) begin
        case (lane)
            0: return block == 0 ? 65790 : 256;
            1: return block == 0 ? 1 : 65790;
            2: return 65536;
            default: return block == 0 ? 0 : 65790;
        endcase
    end
    else begin
        case (lane)
            0: return 32767;
            1: return block == 0 ? 32'h80000000 : 0;
            2: return block == 0 ? 0 : 32'h80000000;
            default: return block == 0 ? 0 : 32767;
        endcase
    end
endfunction

// Independent literal/algebraic oracle: no Scale/Arithmetic DUT helper.
// Job1 has opposite K32 dots but unequal block metadata; premerging produces
// zero incorrectly. Job2 also distinguishes per-fragment saturation from
// whole-block/whole-K saturation when DIM16 splits each K32 block in two.
function Int#(TestAcc) expected(UInt#(2) job, HostAddress row, HostAddress column);
    UInt#(2) lane = truncate(column);
    Int#(TestAcc) minimum = minBound;
    Int#(TestAcc) maximum = maxBound;
    if (job == 0) begin
        case (lane)
            0: return fromInteger(valueOf(TestDim) * minimumInput * minimumInput);
            1: return fromInteger(valueOf(TestDim) * minimumInput * maximumInput);
            2: return 0;
            default: return fromInteger(valueOf(TestDim) * minimumInput);
        endcase
    end
    else if (job == 1) begin
        Int#(TestAcc) factor = row == fromInteger(valueOf(TestDim)) ? 2 : row % 2 == 0 ? 1 : -1;
        case (lane)
            0: return factor * 2097088; // 32 * (65790 - 256)
            1: return factor * 2105248; // -32 * (1 - 65790)
            2: return 0;
            default: return factor * (-2105280); // 32 * (0 - 65790)
        endcase
    end
    else begin
        case (lane)
            0: return valueOf(TestDim) == 16 ? (row == 0 ? minimum : maximum - 1) : -1;
            1, 2: return row == 0 ? 32 : -32;
            default: return valueOf(TestDim) == 16
                ? (row == 0 ? minimum : maximum)
                : (row == 0 ? minimum + 32 : maximum - 32);
        endcase
    end
endfunction

module mkTbScuProfile(Empty);
    staticAssert(valueOf(IntegerPartialWidth#(TestDim, TestProduct)) == valueOf(TestPartial), "exact profile partial width");
    IM2PCoreIfc#(TestDim, 1, TestVectorLanes, TestRows,
        Int#(TestBits), Int#(TestBits), Int#(TestProduct), Int#(TestAcc), UInt#(32)) core <- mkIM2PCore;
    Reg#(UInt#(2)) job <- mkReg(0);
    Reg#(Bool) active <- mkReg(False);
    Reg#(UInt#(32)) watchdog <- mkReg(0);
    Reg#(UInt#(32)) outputCount <- mkReg(0);
    Reg#(UInt#(32)) ackCount <- mkReg(0);
    Reg#(UInt#(32)) scalarCount <- mkReg(0);
    Reg#(UInt#(32)) scaleCount <- mkReg(0);
    Reg#(Bit#(4)) blockColumns <- mkReg(0);
    Reg#(Bool) aPending <- mkReg(False);
    Reg#(Bool) wPending <- mkReg(False);
    Reg#(Bool) sPending <- mkReg(False);
    Reg#(Bool) cPending <- mkReg(False);
    Reg#(UInt#(3)) aDelay <- mkReg(0);
    Reg#(UInt#(3)) wDelay <- mkReg(0);
    Reg#(UInt#(3)) sDelay <- mkReg(0);
    Reg#(UInt#(3)) cDelay <- mkReg(0);
    Reg#(HostRequestTag) aTag <- mkRegU;
    Reg#(HostRequestTag) wTag <- mkRegU;
    Reg#(HostRequestTag) sTag <- mkRegU;
    Reg#(HostRequestTag) cTag <- mkRegU;
    Reg#(HostAddress) aRow <- mkRegU;
    Reg#(HostAddress) wRow <- mkRegU;
    Reg#(HostAddress) wColumn <- mkRegU;
    Reg#(HostAddress) sColumn <- mkRegU;
    Reg#(ScaleBlockIndex) sBlock <- mkRegU;

    rule watch;
        watchdog <= watchdog + 1;
        if (watchdog == 200000) begin
            $display("SCU PROFILE: FAIL timeout bits=%0d dim=%0d job=%0d state=%0d fragments=%0d", valueOf(TestBits), valueOf(TestDim), job, core.matrixCoreState, core.matmulFragmentsCompleted);
            $finish(1);
        end
    endrule
    rule start (!active && job < 3 && core.idle);
        core.startMatmul(100 + zeroExtend(job), FullMatrix, aBase, wBase, sBase, cBase,
            aStride(job), wStride(job), sStride(job), cStride(job),
            rows(job), columns(job), reduction(job),
            fromInteger(valueOf(TestDim)), fromInteger(valueOf(TestDim)),
            0, reduction(job), job == 0 ? fromInteger(valueOf(TestDim)) : 32,
            1000 + zeroExtend(job), False, job == 2 ? VectorLeftShift : VectorUnsignedMultiply);
        active <= True;
        outputCount <= 0; ackCount <= 0; scalarCount <= 0; scaleCount <= 0; blockColumns <= 0;
    endrule
    rule captureA (core.activationReadRequestValid && !aPending);
        HostAddress offset = core.activationReadRequestAddress - aBase;
        HostAddress row = offset / aStride(job);
        HostAddress k = (offset % aStride(job)) / fromInteger(elementBytes);
        dynamicAssert(row < zeroExtend(rows(job)) && k < zeroExtend(reduction(job)), "activation address range");
        dynamicAssert(k + zeroExtend(core.activationReadRequestElementCount) <= zeroExtend(reduction(job)), "activation tail");
        HostAddress fragment = job == 0 ? fromInteger(valueOf(TestDim)) : fromInteger(valueOf(TestDim) < 32 ? valueOf(TestDim) : 32);
        dynamicAssert(k % fragment == 0 && zeroExtend(core.activationReadRequestElementCount) == fragment, "physical fragment and K32 boundary");
        aTag <= core.activationReadRequestTag; aRow <= row; aPending <= True; aDelay <= 1;
    endrule
    rule waitA (aPending && aDelay != 0); aDelay <= aDelay - 1; endrule
    rule replyA (aPending && aDelay == 0);
        core.putActivationReadResponse(aTag, replicate(activation(job, aRow))); aPending <= False;
    endrule
    rule captureW (core.weightReadRequestValid && !wPending);
        HostAddress offset = core.weightReadRequestAddress - wBase;
        HostAddress row = offset / wStride(job);
        HostAddress column = (offset % wStride(job)) / fromInteger(elementBytes);
        dynamicAssert(row < zeroExtend(reduction(job)) && column < zeroExtend(columns(job)), "weight address range");
        wTag <= core.weightReadRequestTag; wRow <= row; wColumn <= column; wPending <= True; wDelay <= 1;
    endrule
    rule waitW (wPending && wDelay != 0); wDelay <= wDelay - 1; endrule
    rule replyW (wPending && wDelay == 0);
        Vector#(TestDim, Int#(TestBits)) values = newVector;
        for (Integer lane = 0; lane < valueOf(TestDim); lane = lane + 1)
            values[lane] = weight(job, wRow, wColumn + fromInteger(lane));
        core.putWeightReadResponse(wTag, values); wPending <= False;
    endrule
    rule captureS (core.scaleReadRequestValid && !sPending);
        ScaleBlockIndex block = core.scaleRequestBlock;
        HostAddress offset = core.scaleReadRequestAddress - sBase - zeroExtend(block) * sStride(job);
        HostAddress column = offset / 4;
        dynamicAssert(offset % 4 == 0 && column < zeroExtend(columns(job)), "typed scale address");
        dynamicAssert(core.scaleRequestContext == 1000 + zeroExtend(job) + column, "immutable scale context");
        dynamicAssert(block < (job == 0 ? 1 : 2), "scale block range");
        Bit#(2) bitIndex = (column == 0 ? 0 : 2) + truncate(pack(block));
        blockColumns <= blockColumns | (1 << bitIndex);
        sTag <= core.scaleReadRequestTag; sBlock <= block; sColumn <= column; sPending <= True; sDelay <= 3;
        scaleCount <= scaleCount + 1;
    endrule
    rule waitS (sPending && sDelay != 0); sDelay <= sDelay - 1; endrule
    rule replyS (sPending && sDelay == 0);
        Vector#(TestDim, UInt#(32)) values = newVector;
        for (Integer lane = 0; lane < valueOf(TestDim); lane = lane + 1)
            values[lane] = metadata(job, sBlock, sColumn + fromInteger(lane));
        core.putScaleReadResponse(sTag, values); sPending <= False;
    endrule
    rule captureC (core.outputWriteRequestValid && !cPending);
        HostAddress offset = core.outputWriteRequestAddress - cBase;
        HostAddress row = offset / cStride(job);
        HostAddress column = (offset % cStride(job)) / 4;
        let count = core.outputWriteRequestElementCount;
        let values = core.outputWriteRequestValues;
        HostAddress expectedRow = zeroExtend(outputCount);
        HostAddress expectedColumn = 0;
        if (job == 1) begin
            expectedRow = outputCount < fromInteger(2 * valueOf(TestDim))
                ? zeroExtend(outputCount) % fromInteger(valueOf(TestDim)) : fromInteger(valueOf(TestDim));
            expectedColumn = (outputCount >= fromInteger(valueOf(TestDim)) && outputCount < fromInteger(2 * valueOf(TestDim)))
                || outputCount == fromInteger(2 * valueOf(TestDim) + 1) ? fromInteger(valueOf(TestDim)) : 0;
        end
        else if (job == 2) begin
            expectedRow = zeroExtend(outputCount % 2);
            expectedColumn = outputCount < 2 ? 0 : fromInteger(valueOf(TestDim));
        end
        dynamicAssert(row == expectedRow && column == expectedColumn, "output work/row order and duplicate rejection");
        dynamicAssert(offset % 4 == 0 && row < zeroExtend(rows(job)) && column < zeroExtend(columns(job)), "final output address");
        dynamicAssert(column + zeroExtend(count) <= zeroExtend(columns(job)), "output tail range");
        dynamicAssert((core.outputWriteRequestTag >> 32) == 100 + zeroExtend(job), "output job identity");
        for (Integer lane = 0; lane < valueOf(TestDim); lane = lane + 1) begin
            if (fromInteger(lane) < count) begin
                Int#(TestAcc) wanted = expected(job, row, column + fromInteger(lane));
                if (values[lane] != wanted) begin
                    $display("SCU PROFILE: FAIL bits=%0d dim=%0d job=%0d row=%0d col=%0d got=%0d expected=%0d", valueOf(TestBits), valueOf(TestDim), job, row, column + fromInteger(lane), values[lane], wanted);
                    $finish(1);
                end
            end
        end
        cTag <= core.outputWriteRequestTag; cPending <= True; cDelay <= 3;
        outputCount <= outputCount + 1; scalarCount <= scalarCount + zeroExtend(count);
    endrule
    rule waitC (cPending && cDelay != 0); cDelay <= cDelay - 1; endrule
    rule replyC (cPending && cDelay == 0);
        core.putOutputWriteResponse(cTag); cPending <= False; ackCount <= ackCount + 1;
    endrule
    rule finish (active && core.matmulDone && !cPending);
        UInt#(64) works = job == 0 ? 1 : job == 1 ? 4 : 2;
        UInt#(64) fragments = job == 0 ? 1 : works * fromInteger(valueOf(TestDim) == 16 ? 4 : 2);
        UInt#(32) writes = rows(job) * (job == 0 ? 1 : 2);
        dynamicAssert(core.matmulWorksCompleted == works && core.matmulFragmentsCompleted == fragments, "scheduler work/fragment count");
        dynamicAssert(outputCount == writes && ackCount == writes && scalarCount == rows(job) * columns(job), "whole-K output/count/ACK");
        dynamicAssert(core.outputWriteRequests == zeroExtend(outputCount) && core.outputWriteResponses == zeroExtend(ackCount), "device output counters");
        dynamicAssert(core.scaleReadRequests == zeroExtend(scaleCount) && blockColumns == (job == 0 ? 1 : 15), "observed scale boundary coverage");
        $display("SCU PROFILE JOB PASS bits=%0d dim=%0d partial=%0d acc=%0d job=%0d M=%0d N=%0d K=%0d block=%0d works=%0d fragments=%0d scalars=%0d writes=%0d ACK=%0d scale_requests=%0d A_requests=%0d W_requests=%0d cycles=%0d", valueOf(TestBits), valueOf(TestDim), valueOf(TestPartial), valueOf(TestAcc), job, rows(job), columns(job), reduction(job), job == 0 ? valueOf(TestDim) : 32, works, fragments, scalarCount, outputCount, ackCount, scaleCount, core.activationReadRequests, core.weightReadRequests, core.workCycles);
        core.acknowledgeMatmul; active <= False; job <= job + 1;
    endrule
    rule pass (job == 3 && !active);
        $display("SCU PROFILE: PASS jobs=3 bits=%0d dim=%0d vector_lanes=%0d partial=%0d acc=%0d hardware=Bluesim-only", valueOf(TestBits), valueOf(TestDim), valueOf(TestVectorLanes), valueOf(TestPartial), valueOf(TestAcc));
        $finish(0);
    endrule
endmodule
endpackage
