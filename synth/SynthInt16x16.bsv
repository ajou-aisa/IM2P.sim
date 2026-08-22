package SynthInt16x16;

import Config::*;
import IM2PCore::*;

// Signed INT16 activations and INT8 weights, systolic array DIM 16.
module mkSynthInt16x16(IM2PCoreIfc#(
    16, // Array DIM
    1, // PE latency
    16, // Vector Lane
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
