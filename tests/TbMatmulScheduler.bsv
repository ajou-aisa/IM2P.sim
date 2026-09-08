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
    TbPublishFirst,
    TbWaitFirstWork,
    TbPublishThird,
    TbPublishFinal,
    TbWaitFirstCompletion,
    TbWaitSecondWork,
    TbCompleteSecondWork,
    TbWaitThirdWork,
    TbCompleteThirdWork,
    TbHoldFullCompletionFifo,
    TbCheckSecondCompletion,
    TbCheckThirdCompletion,
    TbWaitFinalWork,
    TbCompleteFinalWork,
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
        activationBase: 64'h100001000,
        weightBase: 64'h200002000,
        scaleBase: 64'h300003000,
        outputBase: 64'h400004000,
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
    Reg#(UInt#(3)) completionHoldCycles <- mkReg(0);
    Reg#(UInt#(8)) watchdog <- mkReg(0);

    rule watch;
        watchdog <= watchdog + 1;
        if (watchdog == 200) begin
            $display("MATMUL SCHEDULER: FAIL timeout state=", fshow(state));
            $finish(1);
        end
    endrule

    rule startFull (state == TbStartFull);
        if (!hostMatrixSpanFits(64'hfffffffffffffffc, 1, 0, 1, 4)
                || hostMatrixSpanFits(64'hfffffffffffffffd, 1, 0, 1, 4)
                || hostMatrixSpanFits(1, 32'hffffffff, 64'hffffffffffffffff, 1, 1)
                || hostMatrixSpanFits(0, 0, 0, 1, 1)
                || hostMatrixSpanFits(0, 1, 0, 0, 1)
                || !hostBlockMatrixSpanFits(64'hffffffffffffff00, 3, 64,
                    2, 32, 8, 4)
                || hostBlockMatrixSpanFits(64'hffffffffffffff01, 3, 64,
                    2, 32, 8, 4)) begin
            $display("MATMUL SCHEDULER: FAIL widened address bounds");
            $finish(1);
        end
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
                || work.jCount != expectedJCount
                || work.activationBase != 64'h100001000 + zeroExtend(expectedI) * 16
                || work.weightBase != 64'h200002000 + zeroExtend(expectedJ)
                || work.scaleBase != 64'h300003000 + zeroExtend(expectedJ)
                || work.outputBase != 64'h400004000 + zeroExtend(expectedI) * 32
                    + zeroExtend(expectedJ) * 4) begin
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

    rule inspectFullLookahead (
        state == TbWaitFullWork && fullWorkCount < 2 && dut.lookaheadValid
    );
        MatmulWork#(4) lookahead = dut.lookaheadWork;
        if (lookahead.iStart != 4
                || lookahead.activationBase != 64'h100001040
                || lookahead.outputBase != 64'h400004080
                || lookahead.weightBase != 64'h200002000
                || lookahead.scaleBase != 64'h300003000) begin
            $display("MATMUL SCHEDULER: FAIL full lookahead address sequence");
            $finish(1);
        end
    endrule

    rule completeFullWork (state == TbCompleteFullWork);
        dut.completeWork(900 + zeroExtend(fullWorkCount));
        fullWorkCount <= fullWorkCount + 1;
        state <= fullWorkCount == 3 ? TbFinishFull : TbWaitFullWork;
    endrule

    rule finishFull (state == TbFinishFull && dut.done);
        dut.acknowledge;
        state <= TbStartAsync;
    endrule

    rule startAsync (state == TbStartAsync);
        dut.start(descriptorFor(AsyncStripes, 4, 1));
        state <= TbCheckUnpublished;
    endrule

    rule checkUnpublished (state == TbCheckUnpublished);
        if (dut.workValid) begin
            $display("MATMUL SCHEDULER: FAIL unpublished work visible");
            $finish(1);
        end

        if (asyncWaitCycles == 2) begin
            state <= TbPublishFirst;
        end
        else begin
            asyncWaitCycles <= asyncWaitCycles + 1;
        end
    endrule

    rule publishFirst (state == TbPublishFirst);
        dut.publishStripe(ActivationStripe {
            stripeId: 3,
            rowBegin: 0,
            rowCount: 1,
            activationBase: 64'h5000,
            activationRowStride: 16,
            stripeContext: 91,
            publishCycle: 101
        });
        state <= TbWaitFirstWork;
    endrule

    rule inspectFirstWork (state == TbWaitFirstWork && dut.workValid);
        MatmulWork#(4) work = dut.work;
        if (work.stripeId != 3 || work.iStart != 0
                || work.iCount != 1 || work.jCount != 1
                || work.activationBase != 64'h5000) begin
            $display("MATMUL SCHEDULER: FAIL current stripe work");
            $finish(1);
        end
        dut.publishStripe(ActivationStripe {
            stripeId: 4, rowBegin: 1, rowCount: 1,
            activationBase: 64'h5100, activationRowStride: 16,
            stripeContext: 92, publishCycle: 202
        });
        dut.acceptWork;
        state <= TbPublishThird;
    endrule

    rule publishThird (state == TbPublishThird);
        dut.publishStripe(ActivationStripe {
            stripeId: 5, rowBegin: 2, rowCount: 1,
            activationBase: 64'h5200, activationRowStride: 16,
            stripeContext: 93, publishCycle: 303
        });
        state <= TbPublishFinal;
    endrule

    rule publishFinal (state == TbPublishFinal);
        dut.publishStripe(ActivationStripe {
            stripeId: 6, rowBegin: 3, rowCount: 1,
            activationBase: 64'h5300, activationRowStride: 16,
            stripeContext: 94, publishCycle: 404
        });
        dut.completeWork(1001);
        state <= TbWaitFirstCompletion;
    endrule

    rule checkFirstCompletion (
        state == TbWaitFirstCompletion && dut.completionValid
    );
        StripeCompletion completion = dut.completion;
        if (completion.stripeId != 3 || completion.rowBegin != 0
                || completion.rowCount != 1 || completion.stripeContext != 91
                || completion.publishCycle != 101
                || completion.completionCycle != 1001) begin
            $display(
                "MATMUL SCHEDULER: FAIL current endpoint propagation publish=%0d completion=%0d",
                completion.publishCycle, completion.completionCycle
            );
            $finish(1);
        end
        $display(
            "MATMUL SCHEDULER: current endpoints publish=%0d completion=%0d",
            completion.publishCycle, completion.completionCycle
        );
        state <= TbWaitSecondWork;
    endrule

    rule inspectSecondWork (state == TbWaitSecondWork && dut.workValid);
        if (dut.work.stripeId != 4 || dut.work.iStart != 1
                || dut.work.activationBase != 64'h5100
                || dut.work.outputBase != 64'h400004020) begin
            $display("MATMUL SCHEDULER: FAIL lookahead promotion");
            $finish(1);
        end
        dut.acceptWork;
        state <= TbCompleteSecondWork;
    endrule

    rule completeSecondWork (state == TbCompleteSecondWork);
        dut.completeWork(1002);
        state <= TbWaitThirdWork;
    endrule

    rule inspectThirdWork (state == TbWaitThirdWork && dut.workValid);
        if (dut.work.stripeId != 5 || dut.work.iStart != 2
                || dut.work.activationBase != 64'h5200
                || dut.work.outputBase != 64'h400004040) begin
            $display("MATMUL SCHEDULER: FAIL second lookahead promotion");
            $finish(1);
        end
        dut.acceptWork;
        state <= TbCompleteThirdWork;
    endrule

    rule completeThirdWork (state == TbCompleteThirdWork);
        dut.completeWork(1003);
        state <= TbHoldFullCompletionFifo;
    endrule

    rule holdFullCompletionFifo (state == TbHoldFullCompletionFifo);
        StripeCompletion completion = dut.completion;
        if (!dut.completionValid || completion.stripeId != 3
                || completion.publishCycle != 101
                || completion.completionCycle != 1001) begin
            $display("MATMUL SCHEDULER: FAIL completion FIFO head changed");
            $finish(1);
        end
        if (dut.workValid) begin
            $display("MATMUL SCHEDULER: FAIL advanced through full completion FIFO");
            $finish(1);
        end
        if (completionHoldCycles == 4) begin
            dut.acknowledgeCompletion;
            state <= TbCheckSecondCompletion;
        end
        else begin
            completionHoldCycles <= completionHoldCycles + 1;
        end
    endrule

    rule checkSecondCompletion (
        state == TbCheckSecondCompletion && dut.completionValid
    );
        StripeCompletion completion = dut.completion;
        if (completion.stripeId != 4 || completion.publishCycle != 202
                || completion.completionCycle != 1002) begin
            $display(
                "MATMUL SCHEDULER: FAIL promoted endpoint propagation publish=%0d completion=%0d",
                completion.publishCycle, completion.completionCycle
            );
            $finish(1);
        end
        $display(
            "MATMUL SCHEDULER: promoted endpoints publish=%0d completion=%0d",
            completion.publishCycle, completion.completionCycle
        );
        dut.acknowledgeCompletion;
        state <= TbCheckThirdCompletion;
    endrule

    rule checkThirdCompletion (
        state == TbCheckThirdCompletion && dut.completionValid
    );
        StripeCompletion completion = dut.completion;
        if (completion.stripeId != 5 || completion.publishCycle != 303
                || completion.completionCycle != 1003) begin
            $display(
                "MATMUL SCHEDULER: FAIL blocked endpoint changed publish=%0d completion=%0d",
                completion.publishCycle, completion.completionCycle
            );
            $finish(1);
        end
        $display(
            "MATMUL SCHEDULER: FIFO-blocked endpoints publish=%0d completion=%0d",
            completion.publishCycle, completion.completionCycle
        );
        dut.acknowledgeCompletion;
        state <= TbWaitFinalWork;
    endrule

    rule inspectFinalWork (state == TbWaitFinalWork && dut.workValid);
        if (dut.work.stripeId != 6 || dut.work.iStart != 3
                || dut.work.activationBase != 64'h5300
                || dut.work.outputBase != 64'h400004060) begin
            $display("MATMUL SCHEDULER: FAIL final stripe promotion");
            $finish(1);
        end
        dut.acceptWork;
        state <= TbCompleteFinalWork;
    endrule

    rule completeFinalWork (state == TbCompleteFinalWork);
        dut.completeWork(1004);
        state <= TbFinishAsync;
    endrule

    rule finishAsync (
        state == TbFinishAsync && dut.done && dut.completionValid
    );
        StripeCompletion completion = dut.completion;
        if (completion.stripeId != 6 || completion.publishCycle != 404
                || completion.completionCycle != 1004) begin
            $display(
                "MATMUL SCHEDULER: FAIL final endpoint propagation publish=%0d completion=%0d",
                completion.publishCycle, completion.completionCycle
            );
            $finish(1);
        end
        $display(
            "MATMUL SCHEDULER: final endpoints publish=%0d completion=%0d",
            completion.publishCycle, completion.completionCycle
        );
        $display("MATMUL SCHEDULER: PASS exact stripe RTL endpoints");
        $finish(0);
    endrule
endmodule

endpackage
