package SynthInt16x32;

import Config::*;
import IM2PCore::*;

// Signed INT16 activations and INT8 weights, systolic array DIM 32.
module mkSynthInt16x32(IM2PCoreIfc#(
    32, // Array DIM
    1, // PE latency
    32, // Vector Lane
    DefaultAccumulatorRows, // Accumulator Rows
    Int#(16), // input width
    Int#(8), // weight width
    Int#(24), // product width
    Int#(DefaultAccumulatorWidth), // accumulator/output-request width
    Int#(8) // scale width
));
    let core <- mkIM2PCore;
    return core;
endmodule

endpackage
