package SynthA16W16D32;

import Config::*;
import IM2PCore::*;

// Signed INT16 activations and INT16 weights, systolic array DIM 32.
module mkSynthA16W16D32(IM2PCoreIfc#(
    32, // Array DIM
    1, // PE latency
    32, // Vector Lane
    IntegerAccumulatorRows#(32, A16AccumulatorWidth), // Accumulator Rows
    Int#(A16InputWidth), // input width
    Int#(A16WeightWidth), // weight width
    Int#(A16ProductWidth), // product width
    Int#(A16AccumulatorWidth), // accumulator/output-request width
    UInt#(32) // ABI5 typed scale metadata
));
    let core <- mkIM2PCore;
    return core;
endmodule

endpackage
