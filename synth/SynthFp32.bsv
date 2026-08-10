package SynthFp32;

import FloatingPoint::*;

import Config::*;
import IM2PCore::*;

typedef FloatingPoint#(8, 23) Single;

// 같은 IM2PCore source를 FP32 format으로 elaboration한다.
module mkSynthFp32(IM2PCoreIfc#(
    DefaultArrayDim,
    DefaultPeLatency,
    DefaultVectorLanes,
    DefaultAccumulatorRows,
    Single,
    Single,
    Single,
    Single,
    Bit#(1)
));
    let core <- mkIM2PCore;
    return core;
endmodule

endpackage
