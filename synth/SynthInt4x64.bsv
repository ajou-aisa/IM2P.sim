package SynthInt4x64;

import Config::*;
import IM2PCore::*;
import SystolicArrayInt4x64::*;

// Signed INT4 activations and INT8 weights, systolic array DIM 64.
module mkSynthInt4x64(IM2PCoreIfc#(
    64, // Array DIM
    1, // PE latency
    64, // Vector Lane
    DefaultAccumulatorRows, // Accumulator Rows
    Int#(4), // input width
    Int#(8), // weight width
    Int#(12), // product width
    Int#(DefaultAccumulatorWidth), // accumulator/output-request width
    Int#(8) // scale width
));
    let array <- mkSystolicArrayInt4x64;
    let core <- mkIM2PCoreWithArray(array);
    return core;
endmodule

endpackage
