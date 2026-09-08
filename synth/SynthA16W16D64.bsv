package SynthA16W16D64;

import Config::*;
import IM2PCore::*;
import SystolicArrayA16W16D64::*;

// Signed INT16 activations and INT16 weights, systolic array DIM 64.
module mkSynthA16W16D64(IM2PCoreIfc#(
    64, // Array DIM
    1, // PE latency
    64, // Vector Lane
    IntegerAccumulatorRows#(64, A16AccumulatorWidth), // Accumulator Rows
    Int#(A16InputWidth), // input width
    Int#(A16WeightWidth), // weight width
    Int#(A16ProductWidth), // product width
    Int#(A16AccumulatorWidth), // accumulator/output-request width
    Int#(8) // scale width
));
    let array <- mkSystolicArrayA16W16D64;
    let core <- mkIM2PCoreWithArray(array);
    return core;
endmodule

endpackage
