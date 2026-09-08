package SynthA8W8D32;

import Config::*;
import IM2PCore::*;

// INT8, Systolic array DIM 32
module mkSynthA8W8D32(IM2PCoreIfc#(
    32, // Array DIM
    1, // PE latency
    32, // Vector Lane
    IntegerAccumulatorRows#(32, A8AccumulatorWidth), // Accumulator Rows
    Int#(A8InputWidth), // input width
    Int#(A8WeightWidth), // weight width
    Int#(A8ProductWidth), // product width
    Int#(A8AccumulatorWidth), // accumulator/output-request width
    Int#(8) // scale width
));
    let core <- mkIM2PCore;
    return core;
endmodule

endpackage
