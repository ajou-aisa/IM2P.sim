package Accumulator;

import RegFile::*;
import Vector::*;

interface Accumulator#(numeric type accRows, numeric type dim);
    // DIM x DIM output tile이 차지할 첫 accumulator row를 설정한다.
    // accumulate=False이면 첫 K tile 결과를 쓰고, True이면 기존 값에 더한다.
    method Action startTile(
        UInt#(TLog#(accRows)) baseRow,
        Bool accumulate
    );

    // Systolic array의 staggered column 출력을 나오는 cycle에 바로 저장한다.
    method Action capture(
        Vector#(dim, Bool) valids,
        Vector#(dim, Int#(32)) values
    );

    // 모든 column에서 DIM개 row를 저장한 다음 cycle에 True가 된다.
    method Bool tileDone;

    // 논리적인 accumulator row 하나를 모든 column bank에서 읽는다.
    method Vector#(dim, Int#(32)) readRow(
        UInt#(TLog#(accRows)) row
    );
endinterface

module mkAccumulator(Accumulator#(accRows, dim))
    provisos (
        Add#(1, dimMinusOne, dim),
        Add#(dim, freeRows, accRows),
        Add#(
            TLog#(TAdd#(dim, 1)),
            counterAddressPadding,
            TLog#(accRows)
        )
    );

    // 논리적으로 storage[row][col]이지만, 각 column을 독립 bank로 구성해
    // 서로 다른 row로 도착하는 DIM개 결과를 같은 cycle에 저장할 수 있다.
    Vector#(dim, RegFile#(UInt#(TLog#(accRows)), Int#(32))) banks
        <- replicateM(mkRegFileFull);

    Vector#(dim, Reg#(UInt#(TLog#(TAdd#(dim, 1))))) rowCounters
        <- replicateM(mkReg(0));

    Reg#(UInt#(TLog#(accRows))) baseRowReg <- mkReg(0);
    Reg#(Bool) accumulateReg <- mkReg(False);
    Reg#(Bool) tileActive <- mkReg(False);

    method Action startTile(
        UInt#(TLog#(accRows)) baseRow,
        Bool accumulate
    );
        baseRowReg <= baseRow;
        accumulateReg <= accumulate;
        tileActive <= True;

        for (Integer col = 0; col < valueOf(dim); col = col + 1) begin
            rowCounters[col] <= 0;
        end
    endmethod

    method Action capture(
        Vector#(dim, Bool) valids,
        Vector#(dim, Int#(32)) values
    );
        for (Integer col = 0; col < valueOf(dim); col = col + 1) begin
            if (tileActive && valids[col]
                    && rowCounters[col] < fromInteger(valueOf(dim))) begin
                UInt#(TLog#(accRows)) row =
                    baseRowReg + zeroExtend(rowCounters[col]);
                Int#(32) nextValue = values[col];

                if (accumulateReg) begin
                    nextValue = banks[col].sub(row) + values[col];
                end

                banks[col].upd(row, nextValue);
                rowCounters[col] <= rowCounters[col] + 1;
            end
        end
    endmethod

    method Bool tileDone;
        Bool done = tileActive;

        for (Integer col = 0; col < valueOf(dim); col = col + 1) begin
            done = done
                && rowCounters[col] == fromInteger(valueOf(dim));
        end

        return done;
    endmethod

    method Vector#(dim, Int#(32)) readRow(
        UInt#(TLog#(accRows)) row
    );
        Vector#(dim, Int#(32)) values = newVector;

        for (Integer col = 0; col < valueOf(dim); col = col + 1) begin
            values[col] = banks[col].sub(row);
        end

        return values;
    endmethod

endmodule

endpackage
