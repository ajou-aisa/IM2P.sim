package SynthInt8x32;

import Config::*;
import IM2PCore::*;

// INT8, Systolic array DIM 32
module mkSynthInt8x32(IM2PCoreIfc#(
    32, // Array DIM
    1, // PE latency
    32, // Vector Lane
    DefaultAccumulatorRows, // Accumulator Rows
    Int#(8), // input width
    Int#(8), // weight width
    Int#(16), // product width
    Int#(32), // output width
    Int#(8) // scale width
));
    let core <- mkIM2PCore;
    return core;
endmodule

endpackage
