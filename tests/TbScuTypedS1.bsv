package TbScuTypedS1;

import Vector::*;
import TestVectorUtils::*;
import Types::*;
import VectorUnit::*;

function VectorOp operation(UInt#(3) test);
    case (test)
        0, 1: return VectorUnsignedMultiply;
        2, 3: return VectorLeftShift;
        4: return VectorMultiply;
        5: return VectorShift;
        default: return VectorExternal;
    endcase
endfunction

function Vector#(4, UInt#(32)) metadata(UInt#(3) test);
    case (test)
        0: return vector4(1, 256, 257, 65790);
        1: return vector4(0, 65535, 65536, 65790);
        2: return vector4(0, 8, 9, 32'h80000000);
        3: return vector4(32'h80000000, 0, 32767, 31);
        4: return vector4(32'hffffffff, 32'hffffff80, 127, 0);
        5: return vector4(32'hffffffff, 1, 32'hfffffffe, 0);
        default: return vector4(65790, 65536, 256, 32'h80000000);
    endcase
endfunction

function Vector#(4, Int#(32)) partials(UInt#(3) test);
    case (test)
        1: return vector4(-19, 1, -1, 0);
        3: return vector4(3, -4, 0, 0);
        default: return vector4(3, -4, 5, -6);
    endcase
endfunction

function Vector#(4, Int#(32)) expected(UInt#(3) test);
    case (test)
        0: return vector4(3, -1024, 1285, -394740);
        1: return vector4(0, 65535, -65536, 0);
        2: return vector4(3, -1024, 2560, 0);
        3: return vector4(0, -4, 0, 0);
        4: return vector4(-3, 512, 635, 0);
        5: return vector4(1, -8, 1, -6);
        default: return vector4(3, -4, 5, -6);
    endcase
endfunction

// Overflow-free S1 proof only. The two physical groups retain distinct
// per-column metadata until consume; zero contributions retain their valid bit.
module mkTbScuTypedS1(Empty);
    VectorUnitIfc#(Int#(8), 4, 2, Int#(32), UInt#(32)) dut32 <- mkVectorUnit;
    VectorUnitIfc#(Int#(16), 4, 2, Int#(64), UInt#(32)) dut64 <- mkVectorUnit;
    Reg#(UInt#(3)) test <- mkReg(0);
    Reg#(UInt#(1)) group <- mkReg(0);
    Reg#(Bool) active <- mkReg(False);
    Reg#(UInt#(6)) validCount <- mkReg(0);

    rule issue (!active && test < 7 && dut32.ready && dut64.ready);
        Vector#(4, Bool) valids = replicate(True);
        if (test == 1) valids[0] = False;
        dut32.put(valids, partials(test), metadata(test), operation(test));
        dut64.put(valids, map(signExtend, partials(test)), metadata(test), operation(test));
        active <= True;
        group <= 0;
    endrule

    rule check (active && dut32.resultValid && dut64.resultValid);
        let got32 = dut32.result;
        let got64 = dut64.result;
        let wanted = expected(test);
        UInt#(6) count = validCount;
        for (Integer col = 0; col < 4; col = col + 1) begin
            Bool valid = group == fromInteger(col / 2) && !(test == 1 && col == 0);
            Int#(32) value = valid ? wanted[col] : 0;
            if (got32.valids[col] != valid || got64.valids[col] != valid ||
                got32.contributions[col] != value ||
                got64.contributions[col] != signExtend(value)) begin
                $display("SCU S1 VECTOR UNIT: FAIL test=%0d group=%0d col=%0d got32=%0d got64=%0d expected=%0d",
                         test, group, col, got32.contributions[col], got64.contributions[col], value);
                $finish(1);
            end
            if (valid) count = count + 1;
        end
        validCount <= count;
        dut32.consume;
        dut64.consume;
        if (group == 1) begin
            active <= False;
            test <= test + 1;
        end
        else group <= 1;
    endrule

    rule finish (!active && test == 7 && dut32.ready && dut64.ready);
        if (pack(VectorUnsignedMultiply) != 3'd4 || pack(VectorLeftShift) != 3'd5 ||
            !vectorOpUsesScale(VectorUnsignedMultiply) || !vectorOpUsesScale(VectorLeftShift) ||
            validCount != 27) begin
            $display("SCU S1 VECTOR UNIT: FAIL encoding/count=%0d", validCount);
            $finish(1);
        end
        $display("SCU S1 VECTOR UNIT: PASS jobs=7 groups=14 valid_lanes=27 widths=32,64 overflow_free=1");
        $finish(0);
    endrule
endmodule

endpackage
