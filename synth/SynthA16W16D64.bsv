package SynthA16W16D64;

import Config::*;
import IM2PCore::*;
import SystolicArrayA16W16D64::*;

// Signed INT16 activations and INT16 weights, systolic array DIM 64.
module mkSynthA16W16D64(IM2PCoreIfc#(
    64, // Array DIM
    1, // PE latency
    64, // Vector Lane
    DefaultAccumulatorRows, // Accumulator Rows
    Int#(16), // input width
    Int#(16), // weight width
    Int#(32), // product width
    Int#(DefaultAccumulatorWidth), // accumulator/output-request width
    Int#(8) // scale width
));
    let array <- mkSystolicArrayA16W16D64;
    let core <- mkIM2PCoreWithArray(array);
    return core;
endmodule

endpackage
