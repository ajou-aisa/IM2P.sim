package PE;

import NumericFormat::*;

// format은 INT 또는 FLOAT이고 precision은 한 element의 전체 비트 수다.
// activation, weight, psum이 모두 같은 NumericElement 타입을 사용하므로
// PE 안에서 입력 element와 accumulator element의 폭은 항상 같다.
interface PE#(type format, numeric type precision);
    method Action preloadWeight(NumericElement#(format, precision) weight);

    method Action step(
        Bool inValid,
        NumericElement#(format, precision) x,
        NumericElement#(format, precision) psum
    );

    // step 호출 한 cycle 뒤 outValid가 True일 때만 두 출력이 유효하다.
    method Bool outValid;
    method NumericElement#(format, precision) xOut;
    method NumericElement#(format, precision) psumOut;
endinterface

module mkPE(PE#(format, precision))
provisos (NumericFormat#(format, precision));
    Reg#(NumericElement#(format, precision)) weightReg <- mkRegU;
    Reg#(Bool) weightLoaded <- mkReg(False);

    Reg#(NumericElement#(format, precision)) xPipe <- mkRegU;
    Reg#(NumericElement#(format, precision)) psumPipe <- mkRegU;
    Reg#(Bool) validReg <- mkReg(False);

    method Action preloadWeight(NumericElement#(format, precision) weight);
        weightReg <= weight;
        weightLoaded <= True;
    endmethod

    method Action step(
        Bool inValid,
        NumericElement#(format, precision) x,
        NumericElement#(format, precision) psum
    );
        xPipe <= x;
        validReg <= inValid && weightLoaded;

        if (inValid && weightLoaded) begin
            psumPipe <= numericMac(?, x, weightReg, psum);
        end
    endmethod

    method Bool outValid = validReg;
    method NumericElement#(format, precision) xOut = xPipe;
    method NumericElement#(format, precision) psumOut = psumPipe;

endmodule

endpackage
