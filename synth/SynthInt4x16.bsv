package SynthInt4x16;

import Config::*;
import IM2PCore::*;

// Signed INT4 activations and INT8 weights, systolic array DIM 16.
module mkSynthInt4x16(IM2PCoreIfc#(
    16, // Array DIM
    1, // PE latency
    16, // Vector Lane
    DefaultAccumulatorRows, // Accumulator Rows
    Int#(4), // input width
    Int#(8), // weight width
    Int#(12), // product width
    Int#(32), // output width
    Int#(8) // scale width
));
    let core <- mkIM2PCore;
    return core;
endmodule

endpackage
