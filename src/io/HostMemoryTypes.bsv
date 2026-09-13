package HostMemoryTypes;

import Vector::*;

import Types::*;

typedef UInt#(64) HostAddress;
typedef UInt#(64) HostStride;
typedef UInt#(64) HostRequestTag;
typedef UInt#(32) MatmulJobId;
typedef UInt#(8) ElementBytes;

// Only the SCU provider build selects this specialization. Its public start
// methods construct every row stride from a checked shift or a fixed power of
// two. Generic simulator/profile builds retain arbitrary byte strides.
function HostAddress hostRowOffset(MatrixExtent row, HostStride stride);
`ifdef IM2P_POWER_OF_TWO_STRIDES
    UInt#(6) shift = 0;
    for (Integer bitIndex = 1; bitIndex < 64; bitIndex = bitIndex + 1)
        if (pack(stride)[bitIndex] == 1) shift = fromInteger(bitIndex);
    return zeroExtend(row) << shift;
`else
    return zeroExtend(row) * stride;
`endif
endfunction

typedef struct {
    HostRequestTag tag;
    HostAddress address;
    BoundedCount#(arrayDim) elementCount;
} HostReadRequest#(numeric type arrayDim) deriving (Bits, Eq, FShow);

typedef struct {
    HostRequestTag tag;
    HostAddress address;
    BoundedCount#(arrayDim) elementCount;
    Vector#(arrayDim, element_t) values;
} HostWriteRequest#(
    numeric type arrayDim,
    type element_t
) deriving (Bits, Eq, FShow);

endpackage
