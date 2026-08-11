package TbIM2PLookahead;
import Vector::*;
import Types::*;
import HostMemoryTypes::*;
import WorkTypes::*;
import IM2PCore::*;
typedef enum { Start, Pub0, Run0, RunBoth, Done } State deriving(Bits,Eq,FShow);
module mkTbIM2PLookahead(Empty);
 IM2PCoreIfc#(2,1,1,4,Int#(8),Int#(8),Int#(16),Int#(32),Int#(8)) c <- mkIM2PCore;
 Reg#(State) st <- mkReg(Start); Reg#(UInt#(16)) wd <- mkReg(0);
 Reg#(Bool) ap <- mkReg(False); Reg#(HostRequestTag) at <- mkRegU;
 Reg#(Bool) wp <- mkReg(False); Reg#(HostRequestTag) wt <- mkRegU;
 Reg#(Bool) sp <- mkReg(False); Reg#(HostRequestTag) stg <- mkRegU;
 Reg#(Bool) op <- mkReg(False); Reg#(HostRequestTag) ot <- mkRegU;
 rule watch; wd<=wd+1; if(wd==4000) begin $display("IM2P LOOKAHEAD: FAIL timeout state=",fshow(st)," core=%0d ms=%0d ws=%0d a=%0d w=%0d s=%0d active=%0d",c.matrixCoreState,c.matmulSchedulerState,c.workSchedulerState,c.activationReadRequests,c.weightReadRequests,c.scaleReadRequests,c.executionActive); $display("engine accepted=%0d configured=%0d issued=%0d committed=%0d result=%0d vector=%0d preload=%0d",c.debugAcceptedRows,c.debugConfiguredRows,c.debugFirstColumnIssued,c.debugFirstColumnCommitted,c.debugEngineResultValid,c.debugVectorBusy,c.lookaheadWeightPreloadCycle);$finish(1);end endrule
 rule start(st==Start && c.idle);
  c.startMatmul(55,AsyncStripes,'h1000,'h2000,'h3000,'h4000,
   8,8,8,16,4,2,2,2,2,0,2,2,10,False,VectorMultiply);
  st<=Pub0;
 endrule
 rule pub0(st==Pub0); c.publishActivationStripe(0,2,8); st<=Run0; endrule
 rule pub1(st==Run0 && c.executionActive);
  c.publishActivationStripe(2,2,12); st<=RunBoth;
 endrule
 rule ca(c.activationReadRequestValid && !ap); at<=c.activationReadRequestTag;ap<=True; endrule
 rule ra(ap); c.putActivationReadResponse(at,replicate(1));ap<=False; endrule
 rule cw(c.weightReadRequestValid && !wp); wt<=c.weightReadRequestTag;wp<=True; endrule
 rule rw(wp);
  UInt#(32) rowTag = truncate(wt);
  Int#(8) value = ((rowTag & 1) == 0) ? 1 : 2;
  c.putWeightReadResponse(wt,replicate(value));wp<=False;
 endrule
 rule cs(c.scaleReadRequestValid && !sp); stg<=c.scaleReadRequestTag;sp<=True; endrule
 rule rs(sp); c.putScaleReadResponse(stg,replicate(1));sp<=False; endrule
 rule co(c.outputWriteRequestValid && !op);
  Vector#(2, Int#(32)) values = c.outputWriteRequestValues;
  if (values[0] != 3 || values[1] != 3) begin
   $display("IM2P LOOKAHEAD: FAIL current/result changed values=(%0d,%0d)",
            values[0], values[1]); $finish(1);
  end
  ot<=c.outputWriteRequestTag;op<=True;
 endrule
 rule ro(op); c.putOutputWriteResponse(ot);op<=False; endrule
 rule finish(st==RunBoth && c.matmulDone);
  if(c.lookaheadFirstActivationCycle==0
    || c.lookaheadFirstWeightCycle!=0 || c.lookaheadWeightPreloadCycle!=0
    || c.lookaheadWeightRequests != 0 || c.lookaheadWeightReuseHits != 1
    || c.currentStripeCompletionCycle==0
    || c.lookaheadStartCycle==0 || c.lookaheadScaleCycle==0
    || c.lookaheadScaleRequests != 0 || c.lookaheadScaleReuses != 1
    || c.lookaheadFirstActivationCycle>=c.currentStripeCompletionCycle
    || c.lookaheadReadyCycle==0
    || c.lookaheadReadyCycle>c.currentStripeCompletionCycle
    || c.crossStripeOverlapCycles==0
    || c.lookaheadStartCycle<=c.currentStripeCompletionCycle) begin
   $display("IM2P LOOKAHEAD: FAIL cycles pub=%0d a=%0d w=%0d p=%0d whost=%0d whits=%0d s=%0d sreq=%0d sreuse=%0d complete=%0d start=%0d",
    c.lookaheadPublishCycle,c.lookaheadFirstActivationCycle,c.lookaheadFirstWeightCycle,
    c.lookaheadWeightPreloadCycle,c.lookaheadWeightRequests,
    c.lookaheadWeightReuseHits,c.lookaheadScaleCycle,
    c.lookaheadScaleRequests,c.lookaheadScaleReuses,
    c.currentStripeCompletionCycle,c.lookaheadStartCycle);$finish(1);
  end
  $display("LOOKAHEAD CORE pub=%0d a=%0d w=%0d preload=%0d whost=%0d whits=%0d scale=%0d srequests=%0d sreuse=%0d complete=%0d start=%0d",
    c.lookaheadPublishCycle,c.lookaheadFirstActivationCycle,c.lookaheadFirstWeightCycle,
    c.lookaheadWeightPreloadCycle,c.lookaheadWeightRequests,
    c.lookaheadWeightReuseHits,c.lookaheadScaleCycle,
    c.lookaheadScaleRequests,c.lookaheadScaleReuses,
    c.currentStripeCompletionCycle,c.lookaheadStartCycle);
  c.acknowledgeMatmul; st<=Done;
 endrule
 rule done(st==Done && c.idle); $display("IM2P LOOKAHEAD: PASS");$finish(0);endrule
endmodule
endpackage
