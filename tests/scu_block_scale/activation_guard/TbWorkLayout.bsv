package TbWorkLayout;

import Types::*;
import WorkTypes::*;

// Separate pack oracle; none of these rules are added to the numerical DUT.
function Action emitWork(Integer index, MatrixExtent rows, MatrixExtent columns);
    action
        MatmulWork#(16) work = MatmulWork {
            jobId: fromInteger('h89abcdef + index),
            stripeId: fromInteger('hd2345678 + index),
            stripeContext: fromInteger('hfedcba9876543210 + index),
            iStart: 'ha13579bd, jStart: 'hc2468ace,
            iCount: rows, jCount: columns,
            activationBase: 'h923456789abcdef1,
            weightBase: 'ha23456789abcdef2,
            scaleBase: 'hb23456789abcdef3,
            outputBase: 'hc23456789abcdef4,
            activationRowStride: 'hd23456789abcdef5,
            weightRowStride: 'he23456789abcdef6,
            scaleRowStride: 'hf23456789abcdef7,
            outputRowStride: 'h823456789abcdef8,
            reductionCount: 'h81234567, blockSize: 'h92345678,
            vectorOp: VectorUnsignedMultiply,
            workContext: 'hafedcba987654321
        };
        $display("WORK_LAYOUT_VECTOR %0d %0d %h %h %h %h %h %h %h %h %h",
            index, valueOf(SizeOf#(MatmulWork#(16))), pack(work),
            work.jobId, work.stripeId, work.stripeContext, work.iStart, work.jStart,
            work.iCount, work.jCount, pack(work.vectorOp));
    endaction
endfunction

(* synthesize *)
module mkTbWorkLayout(Empty);
    rule show;
        // Wide/nonphysical values exercise neighbouring fields only in this pack test.
        emitWork(0, 1, 'h80000013);
        emitWork(1, 9, 1);
        emitWork(2, 16, 'hfedcba98);
        $finish(0);
    endrule
endmodule
endpackage
