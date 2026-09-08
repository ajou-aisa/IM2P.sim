package SynthA16W16D16;

import Config::*;
import IM2PCore::*;

// Signed INT16 activations and INT16 weights, systolic array DIM 16.
module mkSynthA16W16D16(IM2PCoreIfc#(
    16, // Array DIM
    1, // PE latency
    16, // Vector Lane
    IntegerAccumulatorRows#(16, A16AccumulatorWidth), // Accumulator Rows
    Int#(A16InputWidth), // input width
    Int#(A16WeightWidth), // weight width
    Int#(A16ProductWidth), // product width
    Int#(A16AccumulatorWidth), // accumulator/output-request width
    Int#(8) // scale width
));
    let core <- mkIM2PCore;
    return core;
endmodule

endpackage
