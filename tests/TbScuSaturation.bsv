package TbScuSaturation;

import Assert::*;
import StmtFSM::*;
import Vector::*;
import TestVectorUtils::*;
import Types::*;
import VectorUnit::*;
import Accumulator::*;

// Literal oracles use arbitrary integer multiplication then clamp; legacy op1
// remains modulo 2^width. Values exercise both sides of each signed boundary.
function VectorOp operation(UInt#(4) index);
    return index < 3 ? VectorUnsignedMultiply : index == 11 ? VectorMultiply : VectorLeftShift;
endfunction

function Vector#(4, UInt#(32)) metadata(UInt#(4) index);
    case (index)
        0: return vector4(2, 2, 256, 256);
        1: return vector4(256, 256, 65790, 65790);
        2: return vector4(1, 1, 0, 0);
        3: return vector4(31, 31, 31, 31);
        4: return vector4(32, 32, 32, 32);
        5: return vector4(63, 63, 63, 63);
        6: return vector4(64, 64, 64, 64);
        7: return vector4(32767, 32767, 32767, 32767);
        8: return vector4(2147483648, 2147483648, 32767, 0);
        9: return vector4(1, 1, 1, 1);
        10: return vector4(1, 1, 1, 1);
        default: return vector4(2, 2, 2, 2);
    endcase
endfunction

function Vector#(4, Int#(32)) partials32(UInt#(4) index);
    case (index)
        0: return vector4(2147483647, -2147483648, 8388607, 8388608);
        1: return vector4(-8388608, -8388609, -1, 1);
        2: return vector4(2147483647, -2147483648, 999, -999);
        3: return vector4(1, -1, 2, -2);
        4: return vector4(1, -1, 2, -2);
        5: return vector4(1, -1, 2, -2);
        6: return vector4(1, -1, 2, -2);
        7: return vector4(0, 1, -1, 0);
        8: return vector4(2147483647, -2147483648, 0, -11);
        9: return vector4(2147483647, -2147483648, 1073741823, -1073741824);
        10: return vector4(1073741824, -1073741825, 0, -1);
        default: return vector4(2147483647, -2147483648, 2147483647, -2147483648);
    endcase
endfunction

function Vector#(4, Int#(32)) expected32(UInt#(4) index);
    case (index)
        0: return vector4(2147483647, -2147483648, 2147483392, 2147483647);
        1: return vector4(-2147483648, -2147483648, -65790, 65790);
        2: return vector4(2147483647, -2147483648, 0, 0);
        3: return vector4(2147483647, -2147483648, 2147483647, -2147483648);
        4: return vector4(2147483647, -2147483648, 2147483647, -2147483648);
        5: return vector4(2147483647, -2147483648, 2147483647, -2147483648);
        6: return vector4(2147483647, -2147483648, 2147483647, -2147483648);
        7: return vector4(0, 2147483647, -2147483648, 0);
        8: return vector4(0, 0, 0, -11);
        9: return vector4(2147483647, -2147483648, 2147483646, -2147483648);
        10: return vector4(2147483647, -2147483648, 0, -2);
        default: return vector4(-2, 0, -2, 0);
    endcase
endfunction

function Vector#(4, Int#(32)) contribution32(UInt#(4) index);
    case (index)
        0: return vector4(2147483647, -2147483648, 0, 5);
        1: return vector4(1, -1, 0, 7);
        2: return vector4(-1, 1, 0, -20);
        3: return vector4(1, -1, 0, 4);
        4: return vector4(1, -1, 0, 99);
        5: return vector4(0, 0, 0, 0);
        6: return vector4(2147483647, -2147483648, 2147483647, -2147483648);
        7: return vector4(-2147483648, 2147483647, 1, -1);
        default: return vector4(0, 0, -1, 1);
    endcase
endfunction

function Vector#(4, Int#(32)) committed32(UInt#(4) index);
    case (index)
        0: return vector4(2147483647, -2147483648, 0, 5);
        1: return vector4(2147483647, -2147483648, 0, 12);
        2: return vector4(2147483646, -2147483647, 0, -8);
        3: return vector4(2147483647, -2147483648, 0, -4);
        4: return vector4(-2147483648, 2147483647, 0, 95);
        5: return vector4(0, 0, 0, 0);
        6: return vector4(2147483647, -2147483648, 2147483647, -2147483648);
        7: return vector4(-1, -1, 2147483647, -2147483648);
        default: return vector4(-1, -1, 2147483646, -2147483647);
    endcase
endfunction

function Vector#(4, Int#(64)) partials64(UInt#(4) index);
    case (index)
        0: return vector4(9223372036854775807, -9223372036854775808, 36028797018963967, 36028797018963968);
        1: return vector4(-36028797018963968, -36028797018963969, -1, 1);
        2: return vector4(9223372036854775807, -9223372036854775808, 999, -999);
        3: return vector4(1, -1, 2, -2);
        4: return vector4(1, -1, 2, -2);
        5: return vector4(1, -1, 2, -2);
        6: return vector4(1, -1, 2, -2);
        7: return vector4(0, 1, -1, 0);
        8: return vector4(9223372036854775807, -9223372036854775808, 0, -11);
        9: return vector4(9223372036854775807, -9223372036854775808, 4611686018427387903, -4611686018427387904);
        10: return vector4(4611686018427387904, -4611686018427387905, 0, -1);
        default: return vector4(9223372036854775807, -9223372036854775808, 9223372036854775807, -9223372036854775808);
    endcase
endfunction

function Vector#(4, Int#(64)) expected64(UInt#(4) index);
    case (index)
        0: return vector4(9223372036854775807, -9223372036854775808, 9223372036854775552, 9223372036854775807);
        1: return vector4(-9223372036854775808, -9223372036854775808, -65790, 65790);
        2: return vector4(9223372036854775807, -9223372036854775808, 0, 0);
        3: return vector4(2147483648, -2147483648, 4294967296, -4294967296);
        4: return vector4(4294967296, -4294967296, 8589934592, -8589934592);
        5: return vector4(9223372036854775807, -9223372036854775808, 9223372036854775807, -9223372036854775808);
        6: return vector4(9223372036854775807, -9223372036854775808, 9223372036854775807, -9223372036854775808);
        7: return vector4(0, 9223372036854775807, -9223372036854775808, 0);
        8: return vector4(0, 0, 0, -11);
        9: return vector4(9223372036854775807, -9223372036854775808, 9223372036854775806, -9223372036854775808);
        10: return vector4(9223372036854775807, -9223372036854775808, 0, -2);
        default: return vector4(-2, 0, -2, 0);
    endcase
endfunction

function Vector#(4, Int#(64)) contribution64(UInt#(4) index);
    case (index)
        0: return vector4(9223372036854775807, -9223372036854775808, 0, 5);
        1: return vector4(1, -1, 0, 7);
        2: return vector4(-1, 1, 0, -20);
        3: return vector4(1, -1, 0, 4);
        4: return vector4(1, -1, 0, 99);
        5: return vector4(0, 0, 0, 0);
        6: return vector4(9223372036854775807, -9223372036854775808, 9223372036854775807, -9223372036854775808);
        7: return vector4(-9223372036854775808, 9223372036854775807, 1, -1);
        default: return vector4(0, 0, -1, 1);
    endcase
endfunction

function Vector#(4, Int#(64)) committed64(UInt#(4) index);
    case (index)
        0: return vector4(9223372036854775807, -9223372036854775808, 0, 5);
        1: return vector4(9223372036854775807, -9223372036854775808, 0, 12);
        2: return vector4(9223372036854775806, -9223372036854775807, 0, -8);
        3: return vector4(9223372036854775807, -9223372036854775808, 0, -4);
        4: return vector4(-9223372036854775808, 9223372036854775807, 0, 95);
        5: return vector4(0, 0, 0, 0);
        6: return vector4(9223372036854775807, -9223372036854775808, 9223372036854775807, -9223372036854775808);
        7: return vector4(-1, -1, 9223372036854775807, -9223372036854775808);
        default: return vector4(-1, -1, 9223372036854775806, -9223372036854775807);
    endcase
endfunction

module mkTbScuSaturation(Empty);
    VectorUnitIfc#(Int#(8), 4, 4, Int#(32), UInt#(32)) vector32 <- mkVectorUnit;
    VectorUnitIfc#(Int#(16), 4, 4, Int#(64), UInt#(32)) vector64 <- mkVectorUnit;
    AccumulatorIfc#(4, 4, Int#(32)) acc32 <- mkAccumulator;
    AccumulatorIfc#(4, 4, Int#(64)) acc64 <- mkAccumulator;
    Reg#(UInt#(4)) index <- mkReg(0);
    Reg#(Bool) mode <- mkReg(True);
    Reg#(UInt#(16)) cycles <- mkReg(0);

    rule watchdog;
        cycles <= cycles + 1;
        if (cycles == 1000) begin
            $display("SCU SATURATION: FAIL timeout");
            $finish(1);
        end
    endrule

    Stmt test = seq
        while (index < 12) seq
            action
                vector32.put(replicate(True), partials32(index), metadata(index), operation(index));
                vector64.put(replicate(True), partials64(index), metadata(index), operation(index));
            endaction
            action
                dynamicAssert(vector32.result.valids == replicate(True), "INT32 SCU valid mask");
                dynamicAssert(vector64.result.valids == replicate(True), "INT64 SCU valid mask");
                dynamicAssert(vector32.result.contributions == expected32(index), "INT32 SCU boundary");
                dynamicAssert(vector64.result.contributions == expected64(index), "INT64 SCU boundary");
                vector32.consume;
                vector64.consume;
            endaction
            index <= index + 1;
        endseq
        index <= 0;
        while (index < 9) seq
            mode <= index != 4;
            action
                Bool accumulate = index != 0 && index != 5 && index != 6;
                acc32.commit(replicate(True), replicate(0), contribution32(index), accumulate, mode);
                acc64.commit(replicate(True), replicate(0), contribution64(index), accumulate, mode);
                mode <= !mode; // Accepted request must retain its own arithmetic policy.
            endaction
            delay(3);
            action
                dynamicAssert(acc32.completedValids == replicate(True), "INT32 completion mask");
                dynamicAssert(acc64.completedValids == replicate(True), "INT64 completion mask");
                acc32.consumeCompletion;
                acc64.consumeCompletion;
            endaction
            action
                acc32.requestReadRow(0);
                acc64.requestReadRow(0);
            endaction
            action
                dynamicAssert(acc32.readResponse == committed32(index), "INT32 ordered saturated accumulation");
                dynamicAssert(acc64.readResponse == committed64(index), "INT64 ordered saturated accumulation");
                acc32.consumeReadResponse;
                acc64.consumeReadResponse;
            endaction
            index <= index + 1;
        endseq
        action
            $display("SCU SATURATION: PASS vector_jobs=12 vector_lanes=96 commits=18 reads=18 widths=32,64 legacy_wrap=preserved zero_replace=PASS immutable_mode=PASS");
            $finish(0);
        endaction
    endseq;
    mkAutoFSM(test);
endmodule

endpackage
