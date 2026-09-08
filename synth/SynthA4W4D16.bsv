package SynthA4W4D16;

import Config::*;
import IM2PCore::*;

// Signed INT4 activations and INT4 weights, systolic array DIM 16.
module mkSynthA4W4D16(IM2PCoreIfc#(
    16, // Array DIM
    1, // PE latency
    16, // Vector Lane
    IntegerAccumulatorRows#(16, A4AccumulatorWidth), // Accumulator Rows
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
