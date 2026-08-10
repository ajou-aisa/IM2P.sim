package SynthInt8;

import Config::*;
import IM2PCore::*;

// INT8/INT32 representative synthesis top. Bypass/Multiply/Shift는 재합성 없이
// startExecution의 runtime VectorOp으로 선택한다.
module mkSynthInt8(IM2PCoreIfc#(
    DefaultArrayDim,
    DefaultPeLatency,
    DefaultVectorLanes,
    DefaultAccumulatorRows,
    Int#(DefaultInputWidth),
    Int#(DefaultWeightWidth),
    Int#(DefaultProductWidth),
    Int#(DefaultAccumulatorWidth),
    Int#(DefaultScaleWidth)
));
    let core <- mkIM2PCore;
    return core;
endmodule

endpackage
