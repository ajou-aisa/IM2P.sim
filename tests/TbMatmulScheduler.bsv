package TbMatmulScheduler;

import Types::*;
import WorkTypes::*;
import MatmulScheduler::*;

typedef enum {
    TbStartFull,
    TbWaitFullWork,
    TbCompleteFullWork,
    TbFinishFull,
    TbStartAsync,
    TbCheckUnpublished,
    TbPublishAsync,
    TbWaitAsyncWork,
    TbCompleteAsyncWork,
    TbFinishAsync
} TbState deriving (Bits, Eq, FShow);

function MatmulDescriptor descriptorFor(
    MatmulMode mode,
    MatrixExtent rows,
    MatrixExtent columns
);
    return MatmulDescriptor {
        jobId: 9,
        mode: mode,
        activationBase: 64'h1000,
        weightBase: 64'h2000,
        scaleBase: 64'h3000,
        outputBase: 64'h4000,
        activationRowStride: 16,
        weightRowStride: 16,
        scaleRowStride: 16,
        outputRowStride: 32,
        rowCount: rows,
        columnCount: columns,
        reductionCount: 9,
        tileIRows: 4,
        tileJColumns: 4,
        blockSize: 8,
        activationElementBytes: 1,
        weightElementBytes: 1,
        scaleElementBytes: 1,
        outputElementBytes: 4,
        vectorOp: VectorMultiply,
        workContext: 77
    };
endfunction

module mkTbMatmulScheduler(Empty);
    MatmulSchedulerIfc#(4) dut <- mkMatmulScheduler;
    Reg#(TbState) state <- mkReg(TbStartFull);
    Reg#(UInt#(3)) fullWorkCount <- mkReg(0);
    Reg#(UInt#(3)) asyncWaitCycles <- mkReg(0);
    Reg#(UInt#(8)) watchdog <- mkReg(0);

    rule watch;
        watchdog <= watchdog + 1;
        if (watchdog == 200) begin
            $display("MATMUL SCHEDULER: FAIL timeout state=", fshow(state));
            $finish(1);
        end
    endrule

    rule startFull (state == TbStartFull);
        dut.start(descriptorFor(FullMatrix, 6, 5));
        state <= TbWaitFullWork;
    endrule

    rule inspectFullWork (state == TbWaitFullWork && dut.workValid);
        MatmulWork#(4) work = dut.work;
        MatrixExtent expectedI =
            fullWorkCount < 2 ? 0 : 4;
        MatrixExtent expectedJ =
            fullWorkCount == 0 || fullWorkCount == 2 ? 0 : 4;
        MatrixExtent expectedICount =
            fullWorkCount < 2 ? 4 : 2;
        MatrixExtent expectedJCount =
            fullWorkCount == 0 || fullWorkCount == 2 ? 4 : 1;

        if (work.iStart != expectedI
                || work.jStart != expectedJ
                || work.iCount != expectedICount
                || work.jCount != expectedJCount) begin
            $display(
                "MATMUL SCHEDULER: FAIL work=%0d i=%0d/%0d j=%0d/%0d",
                fullWorkCount,
                work.iStart,
                work.iCount,
                work.jStart,
                work.jCount
            );
            $finish(1);
        end

        dut.acceptWork;
        state <= TbCompleteFullWork;
    endrule

    rule completeFullWork (state == TbCompleteFullWork);
        dut.completeWork;
        fullWorkCount <= fullWorkCount + 1;
        state <= fullWorkCount == 3 ? TbFinishFull : TbWaitFullWork;
    endrule

    rule finishFull (state == TbFinishFull && dut.done);
        dut.acknowledge;
        state <= TbStartAsync;
    endrule

    rule startAsync (state == TbStartAsync);
        dut.start(descriptorFor(AsyncStripes, 2, 3));
        state <= TbCheckUnpublished;
    endrule

    rule checkUnpublished (state == TbCheckUnpublished);
        if (dut.workValid) begin
            $display("MATMUL SCHEDULER: FAIL unpublished work visible");
            $finish(1);
        end

        if (asyncWaitCycles == 2) begin
            state <= TbPublishAsync;
        end
        else begin
            asyncWaitCycles <= asyncWaitCycles + 1;
        end
    endrule

    rule publishAsync (state == TbPublishAsync);
        dut.publishStripe(ActivationStripe {
            stripeId: 3,
            rowBegin: 0,
            rowCount: 2,
            activationBase: 64'h5000,
            stripeContext: 91
        });
        state <= TbWaitAsyncWork;
    endrule

    rule inspectAsyncWork (state == TbWaitAsyncWork && dut.workValid);
        MatmulWork#(4) work = dut.work;

        if (work.stripeId != 3
                || work.iStart != 0
                || work.jStart != 0
                || work.iCount != 2
                || work.jCount != 3
                || work.activationBase != 64'h5000) begin
            $display("MATMUL SCHEDULER: FAIL async work");
            $finish(1);
        end

        dut.acceptWork;
        state <= TbCompleteAsyncWork;
    endrule

    rule completeAsyncWork (state == TbCompleteAsyncWork);
        dut.completeWork;
        state <= TbFinishAsync;
    endrule

    rule finishAsync (state == TbFinishAsync && dut.done);
        $display("MATMUL SCHEDULER: PASS");
        $finish(0);
    endrule
endmodule

endpackage
