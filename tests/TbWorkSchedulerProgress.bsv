package TbWorkSchedulerProgress;

import Types::*;
import WorkScheduler::*;

typedef enum { Start, Inspect, Wait, Acknowledge, Promote } TestState
    deriving (Bits, Eq, FShow);

function MatrixExtent origin(UInt#(4) scenario);
    case (scenario)
        0: return 0;
        1: return 19;
        2: return 7;
        3: return 11;
        4: return 32'hfffffff0;
        5: return 32'h80000001;
        6: return 13;
        default: return 17;
    endcase
endfunction

function MatrixExtent blockSize(UInt#(4) scenario);
    case (scenario)
        0: return 3;
        1: return 7;
        2: return 1;
        3: return 11;
        4: return 32'hffffffff;
        5: return 32'h80000000;
        6: return 0;
        default: return 5;
    endcase
endfunction

function MatrixExtent reduction(UInt#(4) scenario);
    return scenario == 4 ? 15 : 23;
endfunction

module mkTbWorkSchedulerProgress(Empty);
    WorkSchedulerIfc#(4) dut <- mkWorkScheduler;
    Reg#(TestState) state <- mkReg(Start);
    Reg#(UInt#(4)) scenario <- mkReg(0);
    Reg#(MatrixExtent) expectedStart <- mkReg(0);
    Reg#(Bool) first <- mkReg(True);
    Reg#(Bool) prepared <- mkReg(False);
    Reg#(UInt#(16)) watchdog <- mkReg(0);

    rule watch;
        watchdog <= watchdog + 1;
        if (watchdog == 2000) begin
            $display("WORK PROGRESS: FAIL timeout scenario=%0d", scenario);
            $finish(1);
        end
    endrule

    rule start (state == Start);
        dut.start(origin(scenario), reduction(scenario), blockSize(scenario),
            scenario != 6, scenario != 7, scenario != 3);
        expectedStart <= origin(scenario);
        first <= True;
        state <= Inspect;
    endrule

    rule prepare (scenario == 0 && state == Wait && !prepared);
        dut.prepareLookahead(origin(1), reduction(1), blockSize(1),
            True, True, True);
        prepared <= True;
    endrule

    rule inspect (state == Inspect && dut.fragmentValid);
        MatrixExtent total = origin(scenario) + reduction(scenario);
        MatrixExtent size = blockSize(scenario);
        Bool scaled = scenario != 6;
        MatrixExtent safeSize = scaled ? size : 1;
        MatrixExtent count = nextKFragmentCount(4, expectedStart, total,
            size, scaled);
        MatrixExtent nextStart = expectedStart + count;
        Bool startsBlock = scaled && expectedStart % safeSize == 0;
        Bool accumulate = scenario != 3 && startsBlock
            ? False : !first || scenario != 7;
        Bool endsBlock = scaled
            && (nextStart == total || nextStart % safeSize == 0);
        MatrixExtent block = scaled ? expectedStart / safeSize : 0;
        if (dut.fragmentKStart != expectedStart
                || zeroExtend(dut.fragmentKCount) != count
                || dut.fragmentBlockIndex != block
                || dut.fragmentAccumulate != accumulate
                || dut.fragmentEndsBlock != endsBlock
                || dut.hasNextFragment != (nextStart < total)
                || dut.nextFragmentKStart != nextStart
                || zeroExtend(dut.nextFragmentKCount)
                    != nextKFragmentCount(4, nextStart, total, size, scaled)) begin
            $display("WORK PROGRESS: FAIL scenario=%0d start=%0d count=%0d block=%0d",
                scenario, dut.fragmentKStart, dut.fragmentKCount,
                dut.fragmentBlockIndex);
            $finish(1);
        end
        dut.acceptFragment;
        state <= Wait;
    endrule

    rule complete (state == Wait && (scenario != 0 || prepared));
        MatrixExtent total = origin(scenario) + reduction(scenario);
        MatrixExtent count = nextKFragmentCount(4, expectedStart, total,
            blockSize(scenario), scenario != 6);
        dut.completeFragment;
        expectedStart <= expectedStart + count;
        first <= False;
        state <= expectedStart + count == total ? Acknowledge : Inspect;
    endrule

    rule acknowledge (state == Acknowledge && dut.done);
        dut.acknowledge;
        if (scenario == 7) begin
            $display("WORK PROGRESS: PASS arbitrary blocks, origins, lookahead, wrap edge");
            $finish(0);
        end
        scenario <= scenario + 1;
        state <= scenario == 0 ? Promote : Start;
    endrule

    rule promote (state == Promote && dut.lookaheadValid);
        if (dut.lookaheadKStart != origin(1)
                || dut.lookaheadKCount != 2) begin
            $display("WORK PROGRESS: FAIL lookahead changed during current work");
            $finish(1);
        end
        dut.startPrepared;
        expectedStart <= origin(1);
        first <= True;
        state <= Inspect;
    endrule
endmodule

endpackage
