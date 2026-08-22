package SynthInt16x64;

import Config::*;
import IM2PCore::*;
import SystolicArrayInt16x64::*;

// Signed INT16 activations and INT8 weights, systolic array DIM 64.
module mkSynthInt16x64(IM2PCoreIfc#(
    64, // Array DIM
    1, // PE latency
    64, // Vector Lane
    DefaultAccumulatorRows, // Accumulator Rows
    Int#(16), // input width
    Int#(8), // weight width
    Int#(24), // product width
    Int#(DefaultAccumulatorWidth), // accumulator/output-request width
    Int#(8) // scale width
));
    let array <- mkSystolicArrayInt16x64;
    let core <- mkIM2PCoreWithArray(array);
    return core;
endmodule

endpackage
