package TbMatmulLookahead;
import Types::*;
import WorkTypes::*;
import MatmulScheduler::*;
function MatmulDescriptor desc();
 return MatmulDescriptor { jobId: 44, mode: AsyncStripes,
  activationBase: 'h1000, weightBase: 'h2000, scaleBase: 'h3000,
  outputBase: 'h4000, activationRowStride: 8, weightRowStride: 8,
  scaleRowStride: 8, outputRowStride: 16, rowCount: 8, columnCount: 2,
  reductionCount: 2, tileIRows: 2, tileJColumns: 2, blockSize: 2,
  activationElementBytes: 1, weightElementBytes: 1, scaleElementBytes: 1,
  outputElementBytes: 4, vectorOp: VectorMultiply, workContext: 70 };
endfunction
function ActivationStripe stripe(UInt#(32) id, MatrixExtent row);
 return ActivationStripe { stripeId: id, rowBegin: row, rowCount: 2,
  activationBase: 'h1000 + zeroExtend(row) * 8,
  activationRowStride: 8 + zeroExtend(id),
  stripeContext: 100 + zeroExtend(id) };
endfunction
typedef enum { Start, Pub0, Accept0, Pub1, See1, Pub2, Pub3,
 Done0, Promote1, Done1, Promote2, Done2, Promote3, Pass }
 State deriving (Bits, Eq, FShow);
module mkTbMatmulLookahead(Empty);
 MatmulSchedulerIfc#(2) dut <- mkMatmulScheduler;
 Reg#(State) state <- mkReg(Start);
 Reg#(UInt#(8)) cycle <- mkReg(0);
 rule tick;
  cycle <= cycle + 1;
  if (cycle == 150) begin
   $display("MATMUL LOOKAHEAD: FAIL timeout"); $finish(1);
  end
 endrule
 rule r0 (state == Start); dut.start(desc); state <= Pub0; endrule
 rule r1 (state == Pub0); dut.publishStripe(stripe(0, 0)); state <= Accept0; endrule
 rule r2 (state == Accept0 && dut.workValid);
  if (dut.work.stripeId != 0) begin $display("MATMUL LOOKAHEAD: FAIL current"); $finish(1); end
  dut.acceptWork; state <= Pub1;
 endrule
 rule r3 (state == Pub1);
  dut.publishStripe(stripe(1, 2)); state <= See1;
 endrule
 rule r4 (state == See1 && dut.lookaheadValid);
  if (dut.lookaheadWork.stripeId != 1 || dut.lookaheadWork.iStart != 2
      || dut.lookaheadWork.activationRowStride != 9 || !dut.active) begin
   $display("MATMUL LOOKAHEAD: FAIL descriptor"); $finish(1);
  end
  $display("LOOKAHEAD s1 visible time=%0t", $time); state <= Pub2;
 endrule
 rule r5 (state == Pub2); dut.publishStripe(stripe(2, 4)); state <= Pub3; endrule
 rule r6 (state == Pub3); dut.publishStripe(stripe(3, 6)); state <= Done0; endrule
 rule r7 (state == Done0);
  if (!dut.lookaheadValid || dut.lookaheadWork.stripeId != 1) begin
   $display("MATMUL LOOKAHEAD: FAIL immediate lookahead replaced"); $finish(1);
  end
  dut.completeWork; state <= Promote1;
 endrule
 rule r8 (state == Promote1 && dut.workValid);
  if (dut.work.stripeId != 1) begin $display("MATMUL LOOKAHEAD: FAIL promote s1"); $finish(1); end
  dut.acceptWork; state <= Done1;
 endrule
 rule r9 (state == Done1 && dut.lookaheadValid);
  if (dut.lookaheadWork.stripeId != 2) begin $display("MATMUL LOOKAHEAD: FAIL expose s2"); $finish(1); end
  if (dut.completionValid) dut.acknowledgeCompletion;
  dut.completeWork; state <= Promote2;
 endrule
 rule r10 (state == Promote2 && dut.workValid);
  if (dut.work.stripeId != 2) begin $display("MATMUL LOOKAHEAD: FAIL promote s2"); $finish(1); end
  dut.acceptWork; state <= Done2;
 endrule
 rule r11 (state == Done2 && dut.lookaheadValid);
  if (dut.lookaheadWork.stripeId != 3) begin $display("MATMUL LOOKAHEAD: FAIL expose s3"); $finish(1); end
  if (dut.completionValid) dut.acknowledgeCompletion;
  dut.completeWork; state <= Promote3;
 endrule
 rule r12 (state == Promote3 && dut.workValid);
  if (dut.work.stripeId != 3 || dut.work.activationRowStride != 11) begin
   $display("MATMUL LOOKAHEAD: FAIL promote s3"); $finish(1);
  end
  $display("LOOKAHEAD progression s1/s2/s3 time=%0t", $time); state <= Pass;
 endrule
 rule r13 (state == Pass); $display("MATMUL LOOKAHEAD: PASS"); $finish(0); endrule
endmodule
endpackage
