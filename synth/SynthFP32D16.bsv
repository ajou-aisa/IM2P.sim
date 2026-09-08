package SynthFP32D16;

import FloatingPoint::*;

import Config::*;
import IM2PCore::*;

typedef FloatingPoint#(8, 23) Single;

// 같은 IM2PCore source를 FP32 format으로 elaboration한다.
module mkSynthFP32D16(IM2PCoreIfc#(
    DefaultArrayDim,
    DefaultPeLatency,
    DefaultVectorLanes,
    FloatingAccumulatorRows,
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
