package SynthA4W4D64;

import Config::*;
import IM2PCore::*;
import SystolicArrayA4W4D64::*;

// Signed INT4 activations and INT4 weights, systolic array DIM 64.
module mkSynthA4W4D64(IM2PCoreIfc#(
    64, // Array DIM
    1, // PE latency
    64, // Vector Lane
    IntegerAccumulatorRows#(64, A4AccumulatorWidth), // Accumulator Rows
    Int#(A4InputWidth), // input width
    Int#(A4WeightWidth), // weight width
    Int#(A4ProductWidth), // product width
    Int#(A4AccumulatorWidth), // accumulator/output-request width
    Int#(8) // scale width
));
    let array <- mkSystolicArrayA4W4D64;
    let core <- mkIM2PCoreWithArray(array);
    return core;
endmodule

endpackage
