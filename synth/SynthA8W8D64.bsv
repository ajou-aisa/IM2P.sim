package SynthA8W8D64;

import Config::*;
import IM2PCore::*;
import SystolicArrayInt8x64::*;

// INT8, Systolic array DIM 64
module mkSynthA8W8D64(IM2PCoreIfc#(
    64, // Array DIM
    1, // PE latency
    64, // Vector Lane
    IntegerAccumulatorRows#(64, A8AccumulatorWidth), // Accumulator Rows
    Int#(A8InputWidth), // input width
    Int#(A8WeightWidth), // weight width
    Int#(A8ProductWidth), // product width
    Int#(A8AccumulatorWidth), // accumulator/output-request width
    Int#(8) // scale width
));
    let array <- mkSystolicArrayInt8x64;
    let core <- mkIM2PCoreWithArray(array);
    return core;
endmodule

endpackage
