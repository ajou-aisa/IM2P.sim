package SynthA4W4D32;

import Config::*;
import IM2PCore::*;

// Signed INT4 activations and INT4 weights, systolic array DIM 32.
module mkSynthA4W4D32(IM2PCoreIfc#(
    32, // Array DIM
    1, // PE latency
    32, // Vector Lane
    IntegerAccumulatorRows#(32, A4AccumulatorWidth), // Accumulator Rows
    Int#(A4InputWidth), // input width
    Int#(A4WeightWidth), // weight width
    Int#(A4ProductWidth), // product width
    Int#(A4AccumulatorWidth), // accumulator/output-request width
    Int#(8) // scale width
));
    let core <- mkIM2PCore;
    return core;
endmodule

endpackage
