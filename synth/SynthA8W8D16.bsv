package SynthA8W8D16;

import Config::*;
import IM2PCore::*;

// INT8, Systolic array DIM 16
module mkSynthA8W8D16(IM2PCoreIfc#(
    16, // Array DIM
    1, // PE latency
    16, // Vector Lane
    IntegerAccumulatorRows#(16, A8AccumulatorWidth), // Accumulator Rows
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
