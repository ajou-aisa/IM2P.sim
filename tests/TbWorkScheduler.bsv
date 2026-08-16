package TbWorkScheduler;

import Types::*;
import WorkScheduler::*;

typedef enum {
    TbCheckFunction,
    TbStart,
    TbInspect,
    TbWait,
    TbDone
} TbState deriving (Bits, Eq, FShow);

module mkTbWorkScheduler(Empty);
    WorkSchedulerIfc#(2) dut <- mkWorkScheduler;
    Reg#(TbState) state <- mkReg(TbCheckFunction);
    Reg#(UInt#(2)) fragment <- mkReg(0);
    Reg#(UInt#(8)) watchdog <- mkReg(0);

    rule watch;
        watchdog <= watchdog + 1;
        if (watchdog == 100) begin
            $display("WORK SCHEDULER: FAIL timeout");
            $finish(1);
        end
    endrule

    rule checkFragmentBoundaries (state == TbCheckFunction);
        MatrixExtent dim32Block8A = nextKFragmentCount(32, 0, 39, 8, True);
        MatrixExtent dim32Block8B = nextKFragmentCount(32, 8, 39, 8, True);
        MatrixExtent dim32Block8Tail = nextKFragmentCount(32, 32, 39, 8, True);
        MatrixExtent dim16Block32A = nextKFragmentCount(16, 0, 71, 32, True);
        MatrixExtent dim16Block32B = nextKFragmentCount(16, 16, 71, 32, True);
        MatrixExtent dim16Block32C = nextKFragmentCount(16, 32, 71, 32, True);
        MatrixExtent bypassCount = nextKFragmentCount(32, 0, 39, 0, False);

        Bool passed = dim32Block8A == 8
            && dim32Block8B == 8
            && dim32Block8Tail == 7
            && dim16Block32A == 16
            && dim16Block32B == 16
            && dim16Block32C == 16
            && bypassCount == 32;

        if (!passed) begin
            $display("WORK SCHEDULER: FAIL fragment function");
            $finish(1);
        end
        state <= TbStart;
    endrule

    rule start (state == TbStart);
        // DIM=2, K=6, block=4 gives (0,2), (2,2), (4,2).
        // Block-output mode replaces at both block starts and accumulates only
        // the second fragment within block zero.
        dut.start(0, 6, 4, True, True, True);
        state <= TbInspect;
    endrule

    rule inspect (state == TbInspect && dut.fragmentValid);
        MatrixExtent expectedStart = fragment == 0 ? 0
            : (fragment == 1 ? 2 : 4);
        Bool expectedAccumulate = fragment == 1;
        Bool expectedEnd = fragment != 0;
        ScaleBlockIndex expectedBlock = fragment < 2 ? 0 : 1;

        if (dut.fragmentKStart != expectedStart
                || dut.fragmentKCount != 2
                || dut.fragmentAccumulate != expectedAccumulate
                || dut.fragmentEndsBlock != expectedEnd
                || dut.fragmentBlockIndex != expectedBlock) begin
            $display(
                "WORK SCHEDULER: FAIL fragment=%0d start=%0d count=%0d acc=%0d end=%0d block=%0d",
                fragment, dut.fragmentKStart, dut.fragmentKCount,
                dut.fragmentAccumulate, dut.fragmentEndsBlock,
                dut.fragmentBlockIndex
            );
            $finish(1);
        end

        dut.acceptFragment;
        state <= TbWait;
    endrule

    rule complete (state == TbWait);
        dut.completeFragment;
        fragment <= fragment + 1;
        state <= fragment == 2 ? TbDone : TbInspect;
    endrule

    rule finish (state == TbDone && dut.done);
        $display("WORK SCHEDULER: PASS");
        $finish(0);
    endrule
endmodule

endpackage
