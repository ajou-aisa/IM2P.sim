package SynthSchedulerDiagnostics;

import WorkScheduler::*;
import MatmulScheduler::*;

(* synthesize *)
module mkWorkSchedulerDiagnostic(WorkSchedulerIfc#(16));
    let scheduler <- mkWorkScheduler;
    return scheduler;
endmodule

(* synthesize *)
module mkMatmulSchedulerDiagnostic(MatmulSchedulerIfc#(16));
    let scheduler <- mkMatmulScheduler;
    return scheduler;
endmodule

endpackage
