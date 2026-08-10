package SynthInt8x16;

import Config::*;
import KQuantIM2PCore::*;

// INT8, Systolic array DIM 16
module mkSynthInt8x16(KQuantIM2PCoreIfc#(
    16, // Array DIM
    1, // PE latency
    16, // Vector Lane
    DefaultAccumulatorRows, // Accumulator Rows
    DefaultScaleBlocks, // K-quant scale blocks
    Int#(8), // input width
    Int#(8), // weight width
    Int#(16), // product width
    Int#(32), // output width
    Int#(8) // scale width
));
    let core <- mkKQuantIM2PCore;
    return core;
endmodule

endpackage
