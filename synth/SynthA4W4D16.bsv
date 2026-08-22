package SynthA4W4D16;

import Config::*;
import IM2PCore::*;

// Signed INT4 activations and INT4 weights, systolic array DIM 16.
module mkSynthA4W4D16(IM2PCoreIfc#(
    16, // Array DIM
    1, // PE latency
    16, // Vector Lane
    DefaultAccumulatorRows, // Accumulator Rows
    Int#(4), // input width
    Int#(4), // weight width
    Int#(8), // product width
    Int#(DefaultAccumulatorWidth), // accumulator/output-request width
    Int#(8) // scale width
));
    let core <- mkIM2PCore;
    return core;
endmodule

endpackage
