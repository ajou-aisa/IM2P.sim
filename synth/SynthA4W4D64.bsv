package SynthA4W4D64;

import Config::*;
import IM2PCore::*;
import SystolicArrayA4W4D64::*;

// Signed INT4 activations and INT4 weights, systolic array DIM 64.
module mkSynthA4W4D64(IM2PCoreIfc#(
    64, // Array DIM
    1, // PE latency
    64, // Vector Lane
    DefaultAccumulatorRows, // Accumulator Rows
    Int#(4), // input width
    Int#(4), // weight width
    Int#(8), // product width
    Int#(DefaultAccumulatorWidth), // accumulator/output-request width
    Int#(8) // scale width
));
    let array <- mkSystolicArrayA4W4D64;
    let core <- mkIM2PCoreWithArray(array);
    return core;
endmodule

endpackage
