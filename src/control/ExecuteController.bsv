package ExecuteController;

import Assert::*;
import Vector::*;

import Types::*;

// Generic module 내부에는 typedef를 둘 수 없으므로 package scope에 선언한다.
typedef enum {
    ControllerIdle,
    ControllerRunning,
    ControllerDone
} ControllerState deriving (Bits, Eq, FShow);

// 한 systolic execution에서 output column별 발행/commit row 수를 추적한다.
interface ExecuteControllerIfc#(numeric type arrayDim);
    // 이번 execution에서 각 output column이 생성할 logical output row 수를
    // 저장하고 column별 counter를 초기화한다.
    method Action start(BoundedCount#(arrayDim) rowCount);

    // 각 output column에서 다음에 발행될 result의 logical row offset이다.
    // Array result를 FIFO에 넣는 action에서 이 값을 함께 저장한 뒤,
    // 같은 action에서 noteArrayOutputs를 호출해야 한다.
    method Vector#(
        arrayDim,
        BoundedCount#(arrayDim)
    ) currentRowOffsets;

    // 이번 cycle에 SystolicArray가 complete partial sum을 발행한 column의
    // issuedRows를 증가시킨다.
    method Action noteArrayOutputs(Vector#(arrayDim, Bool) valids);

    // 이번 cycle에 Accumulator가 실제로 writeback한 column의 committedRows를
    // 증가시킨다. 모든 column이 rowCount에 도달하면 Done으로 전환한다.
    method Action noteCommitted(Vector#(arrayDim, Bool) valids);

    method Bool idle;
    method Bool active;
    method Bool done;

    // 완료 상태를 외부가 확인한 뒤 다음 execution을 받을 수 있도록 Idle로
    // 복귀한다.
    method Action acknowledge;
endinterface

module mkExecuteController(ExecuteControllerIfc#(arrayDim)) provisos (
    Add#(1, arrayDimMinusOne, arrayDim)
);
    Reg#(ControllerState) stateReg <- mkReg(ControllerIdle);

    // 각 output column이 이번 execution에서 최종적으로 생성해야 하는 row 수다.
    Reg#(BoundedCount#(arrayDim)) rowCountReg <- mkReg(0);

    // 각 output column에서 array 밖으로 이미 발행된 result row 수다.
    // 이 값은 동시에 그 column에서 다음에 발행될 logical row offset이다.
    Vector#(
        arrayDim,
        Reg#(BoundedCount#(arrayDim))
    ) issuedRows <- replicateM(mkReg(0));

    // 각 output column에서 Accumulator까지 실제 writeback된 result row 수다.
    // Array output과 commit 사이에 FIFO/VectorUnit 지연이 있으므로 issuedRows와
    // 별도로 추적한다.
    Vector#(
        arrayDim,
        Reg#(BoundedCount#(arrayDim))
    ) committedRows <- replicateM(mkReg(0));

    method Action start(BoundedCount#(arrayDim) rowCount)
            if (stateReg == ControllerIdle);
        dynamicAssert(rowCount != 0, "rowCount must be non-zero");
        dynamicAssert(
            rowCount <= fromInteger(valueOf(arrayDim)),
            "rowCount exceeds arrayDim"
        );

        stateReg <= ControllerRunning;
        rowCountReg <= rowCount;

        for (Integer column = 0;
                column < valueOf(arrayDim);
                column = column + 1) begin
            issuedRows[column] <= 0;
            committedRows[column] <= 0;
        end
    endmethod

    method Vector#(
        arrayDim,
        BoundedCount#(arrayDim)
    ) currentRowOffsets;
        Vector#(
            arrayDim,
            BoundedCount#(arrayDim)
        ) offsets = newVector;

        // 이미 발행한 row의 수가 다음에 발행할 logical row index와 같다.
        for (Integer column = 0;
                column < valueOf(arrayDim);
                column = column + 1) begin
            offsets[column] = issuedRows[column];
        end

        return offsets;
    endmethod

    method Action noteArrayOutputs(Vector#(arrayDim, Bool) valids)
            if (stateReg == ControllerRunning);
        for (Integer column = 0;
                column < valueOf(arrayDim);
                column = column + 1) begin
            if (valids[column]) begin
                dynamicAssert(
                    issuedRows[column] < rowCountReg,
                    "array produced more rows than rowCount"
                );

                issuedRows[column] <= issuedRows[column] + 1;
            end
        end
    endmethod

    method Action noteCommitted(Vector#(arrayDim, Bool) valids)
            if (stateReg == ControllerRunning);
        // Reg write는 action 끝에서 반영되므로 이번 writeback을 반영한 값을
        // nextCommitted로 계산해 완료 여부를 같은 cycle에 판단한다.
        Bool allCommittedAfterWriteback = True;

        for (Integer column = 0;
                column < valueOf(arrayDim);
                column = column + 1) begin
            BoundedCount#(arrayDim) nextCommitted =
                committedRows[column];

            if (valids[column]) begin
                dynamicAssert(
                    committedRows[column] < issuedRows[column],
                    "accumulator committed an output that was not issued"
                );
                dynamicAssert(
                    committedRows[column] < rowCountReg,
                    "accumulator committed more rows than rowCount"
                );

                nextCommitted = committedRows[column] + 1;
                committedRows[column] <= nextCommitted;
            end

            allCommittedAfterWriteback =
                allCommittedAfterWriteback
                && nextCommitted == rowCountReg;
        end

        if (allCommittedAfterWriteback) begin
            stateReg <= ControllerDone;
        end
    endmethod

    method Bool idle = stateReg == ControllerIdle;
    method Bool active = stateReg == ControllerRunning;
    method Bool done = stateReg == ControllerDone;

    method Action acknowledge if (stateReg == ControllerDone);
        stateReg <= ControllerIdle;
    endmethod

endmodule

endpackage
