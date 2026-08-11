package TbWorkScheduler;

import Types::*;
import WorkScheduler::*;

module mkTbWorkScheduler(Empty);
    rule checkFragmentBoundaries;
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
            $display(
                "WORK SCHEDULER: FAIL B8=(%0d,%0d,%0d) B32=(%0d,%0d,%0d) bypass=%0d",
                dim32Block8A,
                dim32Block8B,
                dim32Block8Tail,
                dim16Block32A,
                dim16Block32B,
                dim16Block32C,
                bypassCount
            );
            $finish(1);
        end
        else begin
            $display("WORK SCHEDULER: PASS");
            $finish(0);
        end
    endrule
endmodule

endpackage
