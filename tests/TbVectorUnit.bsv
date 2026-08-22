package TbVectorUnit;

import Vector::*;

import TestVectorUtils::*;

import Types::*;
import VectorUnit::*;


function VectorOp operationFor(UInt#(3) index);
    case (index)
        0: return VectorBypass;
        1: return VectorMultiply;
        2: return VectorShift;
        3: return VectorExternal;
        default: return VectorBypass;
    endcase
endfunction

function Vector#(4, Bool) validsFor(UInt#(3) index);
    case (index)
        // Bypass는 네 column을 모두 처리한다.
        0: return replicate(True);

        // Multiply는 두 vector group에 각각 하나의 Valid column만 둔다.
        1: return vector4(True, False, True, False);

        // Shift는 첫 group 전체를 Invalid로 두어 empty-group 처리도 검증한다.
        2: return vector4(False, False, True, False);

        // External은 scale sideband가 있어도 partial을 그대로 통과시킨다.
        default: return replicate(True);
    endcase
endfunction

function Vector#(4, Int#(8)) scaleFor(UInt#(3) index);
    case (index)
        // Bypass에서도 non-zero scale을 넣어 실제로 무시되는지 확인한다.
        0: return vector4(7, 7, 7, 7);
        1: return vector4(2, -3, 4, -5);
        2: return vector4(1, -1, 2, -2);
        default: return vector4(99, -99, 7, -7);
    endcase
endfunction

function Vector#(4, Int#(64)) inputFor(UInt#(3) index);
    return index == 4
        ? vector4(2147483648, -2147483649, 2147483648, -2147483649)
        : vector4(3, -4, 5, -6);
endfunction

function Vector#(4, Int#(64)) expectedFor(UInt#(3) index);
    case (index)
        0: return vector4(3, -4, 5, -6);
        1: return vector4(6, 12, 20, 30);
        2: return vector4(6, -2, 20, -2);
        3: return vector4(3, -4, 5, -6);
        default: return vector4(
            2147483648, -2147483649, 2147483648, -2147483649
        );
    endcase
endfunction

// 같은 INT VectorUnit에서 Bypass -> Multiply -> Shift를 실행한다.
// arrayDim=4, vectorLanes=2이므로 각 request는 두 physical-lane group으로
// 처리된다. Sparse Valid mask와 Valid column이 하나도 없는 group도 포함한다.
module mkTbVectorUnit(Empty);
    VectorUnitIfc#(
        Int#(8),
        4,
        2,
        Int#(64),
        Int#(8)
    ) dut <- mkVectorUnit;

    Reg#(UInt#(3)) executionIndex <- mkReg(0);
    Reg#(UInt#(2)) groupIndex <- mkReg(0);
    Reg#(Bool) inFlight <- mkReg(False);

    rule issue (!inFlight && executionIndex < 5 && dut.ready);
        dut.put(
            validsFor(executionIndex),
            inputFor(executionIndex),
            scaleFor(executionIndex),
            operationFor(executionIndex)
        );
        groupIndex <= 0;
        inFlight <= True;
    endrule

    rule checkGroup (inFlight && dut.resultValid);
        VectorResult#(4, Int#(64)) transformed = dut.result;
        Vector#(4, Bool) inputValids = validsFor(executionIndex);
        Vector#(4, Int#(64)) expected = expectedFor(executionIndex);
        Bool passed = True;

        for (Integer column = 0; column < 4; column = column + 1) begin
            Bool belongsToCurrentGroup =
                groupIndex == fromInteger(column / 2);
            Bool shouldBeValid =
                belongsToCurrentGroup && inputValids[column];

            passed = passed
                && transformed.valids[column] == shouldBeValid;

            if (shouldBeValid) begin
                passed = passed
                    && transformed.contributions[column] == expected[column];
            end
            else begin
                // 현재 group 밖의 column과 Invalid input column은 deterministic zero다.
                passed = passed
                    && transformed.contributions[column] == 0;
            end
        end

        if (!passed) begin
            $display(
                "VECTOR UNIT: FAIL execution=%0d group=%0d",
                executionIndex,
                groupIndex
            );
            $finish(1);
        end

        dut.consume;
        if (groupIndex == 1) begin
            inFlight <= False;
            executionIndex <= executionIndex + 1;
        end
        else begin
            groupIndex <= groupIndex + 1;
        end
    endrule

    rule finish (!inFlight && executionIndex == 5 && dut.ready);
        if (pack(VectorExternal) != 2'b11 || !vectorOpUsesScale(VectorExternal)) begin
            $display("VECTOR UNIT: FAIL external encoding/scale policy");
            $finish(1);
        end
        $display("VECTOR UNIT: PASS boundaries=(2147483648,-2147483649)");
        $finish(0);
    endrule
endmodule

endpackage
