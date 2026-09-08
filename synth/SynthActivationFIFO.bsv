package SynthActivationFIFO;

import FIFOF::*;
import Vector::*;

// Match A8/W8 DIM16 engine.activationRows, including its guarded methods.
module mkSynthActivationFIFO(FIFOF#(Vector#(16, Int#(8))));
    let fifo <- mkGFIFOF(False, True);
    return fifo;
endmodule

endpackage
