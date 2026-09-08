package Accumulator;

import BRAMCore::*;
import RWire::*;
import Vector::*;

import Types::*;
import Arithmetic::*;

typedef enum {
    AccumulatorIdle,
    AccumulatorUpdating,
    AccumulatorCompleted,
    AccumulatorReading,
    AccumulatorReadReady
} AccumulatorState deriving (Bits, Eq, FShow);

typedef union tagged {
    struct {
        Vector#(columns, Bool) valids;
        Vector#(columns, RowAddress#(rows)) rowAddresses;
        Vector#(columns, acc_t) contributions;
        Bool accumulate;
    } Update;
    struct {
        RowAddress#(rows) row;
        Vector#(columns, acc_t) values;
    } Preload;
    RowAddress#(rows) ReadRow;
} AccumulatorRequest#(numeric type rows, numeric type columns, type acc_t)
    deriving (Bits);

interface AccumulatorIfc#(
    numeric type rows,
    numeric type columns,
    type acc_t
);
    // Acceptance only: completedValids becomes valid after the BRAM write edge.
    method Action commit(
        Vector#(columns, Bool) valids,
        Vector#(columns, RowAddress#(rows)) rowAddresses,
        Vector#(columns, acc_t) contributions,
        Bool accumulate
    );

    method Bool completionValid;
    method Vector#(columns, Bool) completedValids;
    method Action consumeCompletion;

    // Preload writes every bank on its acceptance edge, while no request is pending.
    method Action writeRow(
        RowAddress#(rows) row,
        Vector#(columns, acc_t) values
    );
    method Action requestReadRow(RowAddress#(rows) row);
    method Bool readResponseValid;
    method Vector#(columns, acc_t) readResponse;
    method Action consumeReadResponse;
    method Bool idle;
endinterface

module mkAccumulator(AccumulatorIfc#(rows, columns, acc_t)) provisos (
    Add#(1, rowsMinusOne, rows),
    Add#(1, columnsMinusOne, columns),
    Bits#(acc_t, accBits),
    AccumulatorArithmetic#(acc_t)
);
    // ponytail: one outstanding transaction serializes bank hazards; add forwarding
    // only if measured throughput requires more than one pending RMW.
    // Reset clears control only. BRAM contents and unread locations have no zero guarantee.
    Vector#(
        columns,
        BRAM_PORT#(RowAddress#(rows), acc_t)
    ) banks <- replicateM(mkBRAMCore1(valueOf(rows), False));
    Reg#(AccumulatorState) state <- mkReg(AccumulatorIdle);
    Reg#(Vector#(columns, Bool)) pendingValids <- mkRegU;
    Reg#(Vector#(columns, RowAddress#(rows))) pendingRows <- mkRegU;
    Reg#(Vector#(columns, acc_t)) pendingContributions <- mkRegU;
    Reg#(Bool) pendingAccumulate <- mkRegU;
    Reg#(Vector#(columns, acc_t)) response <- mkRegU;
    RWire#(AccumulatorRequest#(rows, columns, acc_t)) request <- mkRWire;
    PulseWire consumed <- mkPulseWire;

    // One state-qualified writer also handles external EN held through the clock
    // edge: post-edge state changes cannot overlap an old request with a new phase.
    // RWire adds no cycle; acceptance at E still writes/captures at E+1.
    rule advance;
        case (state)
            AccumulatorIdle: begin
                case (request.wget) matches
                    tagged Valid (tagged Update .update): begin
                        pendingValids <= update.valids;
                        pendingRows <= update.rowAddresses;
                        pendingContributions <= update.contributions;
                        pendingAccumulate <= update.accumulate;
                        for (Integer column = 0; column < valueOf(columns); column = column + 1) begin
                            if (update.valids[column] && update.accumulate) begin
                                banks[column].put(False, update.rowAddresses[column], ?);
                            end
                        end
                        state <= AccumulatorUpdating;
                    end
                    tagged Valid (tagged Preload .preload): begin
                        for (Integer column = 0; column < valueOf(columns); column = column + 1) begin
                            banks[column].put(True, preload.row, preload.values[column]);
                        end
                    end
                    tagged Valid (tagged ReadRow .row): begin
                        for (Integer column = 0; column < valueOf(columns); column = column + 1) begin
                            banks[column].put(False, row, ?);
                        end
                        state <= AccumulatorReading;
                    end
                    default: noAction;
                endcase
            end
            AccumulatorUpdating: begin
                for (Integer column = 0; column < valueOf(columns); column = column + 1) begin
                    if (pendingValids[column]) begin
                        acc_t nextValue = pendingContributions[column];
                        if (pendingAccumulate) begin
                            nextValue = accumulatorAdd(banks[column].read, nextValue);
                        end
                        banks[column].put(True, pendingRows[column], nextValue);
                    end
                end
                state <= AccumulatorCompleted;
            end
            AccumulatorReading: begin
                Vector#(columns, acc_t) values = newVector;
                for (Integer column = 0; column < valueOf(columns); column = column + 1) begin
                    values[column] = banks[column].read;
                end
                response <= values;
                state <= AccumulatorReadReady;
            end
            default: begin
                if (consumed) begin
                    state <= AccumulatorIdle;
                end
            end
        endcase
    endrule

    method Action commit(
        Vector#(columns, Bool) valids,
        Vector#(columns, RowAddress#(rows)) rowAddresses,
        Vector#(columns, acc_t) contributions,
        Bool accumulate
    ) if (state == AccumulatorIdle);
        request.wset(tagged Update {
            valids: valids, rowAddresses: rowAddresses,
            contributions: contributions, accumulate: accumulate
        });
    endmethod

    method Bool completionValid = state == AccumulatorCompleted;
    method Vector#(columns, Bool) completedValids if (state == AccumulatorCompleted);
        return pendingValids;
    endmethod
    method Action consumeCompletion if (state == AccumulatorCompleted);
        consumed.send;
    endmethod

    method Action writeRow(
        RowAddress#(rows) row,
        Vector#(columns, acc_t) values
    ) if (state == AccumulatorIdle);
        request.wset(tagged Preload { row: row, values: values });
    endmethod

    method Action requestReadRow(RowAddress#(rows) row) if (state == AccumulatorIdle);
        request.wset(tagged ReadRow row);
    endmethod

    method Bool readResponseValid = state == AccumulatorReadReady;
    method Vector#(columns, acc_t) readResponse if (state == AccumulatorReadReady);
        return response;
    endmethod
    method Action consumeReadResponse if (state == AccumulatorReadReady);
        consumed.send;
    endmethod
    method Bool idle = state == AccumulatorIdle;

endmodule

endpackage
