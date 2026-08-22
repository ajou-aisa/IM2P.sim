package SynthInt8x64;

import Config::*;
import IM2PCore::*;
import SystolicArrayInt8x64::*;

// INT8, Systolic array DIM 64
module mkSynthInt8x64(IM2PCoreIfc#(
    64, // Array DIM
    1, // PE latency
    64, // Vector Lane
    DefaultAccumulatorRows, // Accumulator Rows
    Int#(8), // input width
    Int#(8), // weight width
    Int#(16), // product width
    Int#(DefaultAccumulatorWidth), // accumulator/output-request width
    Int#(8) // scale width
));
    let array <- mkSystolicArrayInt8x64;
    let core <- mkIM2PCoreWithArray(array);
    return core;
endmodule

endpackage
