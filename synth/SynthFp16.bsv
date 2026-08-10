package SynthFp16;

import FloatingPoint::*;

import Config::*;
import IM2PCore::*;

// 같은 IM2PCore source를 FP16 format으로 elaboration한다. FLOAT VectorTransform은
// VectorBypass만 지원하므로 scale multiplier/shifter가 생성되지 않는다.
module mkSynthFp16(IM2PCoreIfc#(
    DefaultArrayDim,
    DefaultPeLatency,
    DefaultVectorLanes,
    DefaultAccumulatorRows,
    Half,
    Half,
    Half,
    Half,
    Bit#(1)
));
    let core <- mkIM2PCore;
    return core;
endmodule

endpackage
