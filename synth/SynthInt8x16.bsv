package SynthInt8x16;

import Config::*;
import IM2PCore::*;

// INT8, Systolic array DIM 16
module mkSynthInt8x16(IM2PCoreIfc#(
    16, // Array DIM
    1, // PE latency
    16, // Vector Lane
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
