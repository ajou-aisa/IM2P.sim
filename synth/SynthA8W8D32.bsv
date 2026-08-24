package SynthA8W8D32;

import Config::*;
import IM2PCore::*;

// INT8, Systolic array DIM 32
module mkSynthA8W8D32(IM2PCoreIfc#(
    32, // Array DIM
    1, // PE latency
    32, // Vector Lane
    DefaultAccumulatorRows, // Accumulator Rows
    Int#(8), // input width
    Int#(8), // weight width
    Int#(16), // product width
    Int#(DefaultAccumulatorWidth), // accumulator/output-request width
    Int#(8) // scale width
));
    let core <- mkIM2PCore;
    return core;
endmodule

endpackage
