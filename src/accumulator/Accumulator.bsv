package Accumulator;

import RegFile::*;
import Vector::*;

import Types::*;
import Arithmetic::*;

// VectorUnit contribution을 지정된 logical row에 저장하거나 기존 값과 누산한다.
interface AccumulatorIfc#(
    numeric type rows,
    numeric type columns,
    type acc_t
);
    // Vector index는 SystolicArray output column 및 내부 bank index와 정적으로
    // 대응한다. rowAddresses[column]은 해당 bank 안의 destination row다.
    method Action commit(
        Vector#(columns, Bool) valids,
        Vector#(columns, RowAddress#(rows)) rowAddresses,
        Vector#(columns, acc_t) contributions,
        Bool accumulate
    );

    // DMA가 없는 testbench/상위 SoC model에서 accumulator state를 초기화하고
    // 결과를 읽기 위한 합성 가능한 boundary다.
    method Action writeRow(
        RowAddress#(rows) row,
        Vector#(columns, acc_t) values
    );
    method Vector#(columns, acc_t) readRow(RowAddress#(rows) row);
endinterface

module mkAccumulator(AccumulatorIfc#(rows, columns, acc_t)) provisos (
    Add#(1, rowsMinusOne, rows),
    Add#(1, columnsMinusOne, columns),
    Bits#(acc_t, accBits),
    AccumulatorArithmetic#(acc_t)
);
    // bank index = SystolicArray output column index.
    // 각 bank는 모든 logical output row의 해당 column 값을 저장한다.
    Vector#(
        columns,
        RegFile#(RowAddress#(rows), acc_t)
    ) banks <- replicateM(mkRegFileFull);

    method Action commit(
        Vector#(columns, Bool) valids,
        Vector#(columns, RowAddress#(rows)) rowAddresses,
        Vector#(columns, acc_t) contributions,
        Bool accumulate
    );
        for (Integer column = 0;
                column < valueOf(columns);
                column = column + 1) begin
            if (valids[column]) begin
                acc_t nextValue = contributions[column];

                if (accumulate) begin
                    nextValue = accumulatorAdd(
                        banks[column].sub(rowAddresses[column]),
                        contributions[column]
                    );
                end

                banks[column].upd(rowAddresses[column], nextValue);
            end
        end
    endmethod

    method Action writeRow(
        RowAddress#(rows) row,
        Vector#(columns, acc_t) values
    );
        for (Integer column = 0;
                column < valueOf(columns);
                column = column + 1) begin
            banks[column].upd(row, values[column]);
        end
    endmethod

    method Vector#(columns, acc_t) readRow(RowAddress#(rows) row);
        Vector#(columns, acc_t) values = newVector;

        for (Integer column = 0;
                column < valueOf(columns);
                column = column + 1) begin
            values[column] = banks[column].sub(row);
        end

        return values;
    endmethod

endmodule

endpackage
