package SynthInt8;

import Config::*;
import KQuantIM2PCore::*;

// INT8/INT32 representative synthesis top. Bypass/Multiply/Shift는 재합성 없이
// startExecution의 runtime VectorOp으로 선택한다.
module mkSynthInt8(KQuantIM2PCoreIfc#(
    DefaultArrayDim,
    DefaultPeLatency,
    DefaultVectorLanes,
    DefaultAccumulatorRows,
    DefaultScaleBlocks,
    Int#(DefaultInputWidth),
    Int#(DefaultWeightWidth),
    Int#(DefaultProductWidth),
    Int#(DefaultAccumulatorWidth),
    Int#(DefaultScaleWidth)
));
    let core <- mkKQuantIM2PCore;
    return core;
endmodule

endpackage
