package HostMemoryTypes;

import Vector::*;

import Types::*;

typedef UInt#(64) HostAddress;
typedef UInt#(64) HostStride;
typedef UInt#(64) HostRequestTag;
typedef UInt#(32) MatmulJobId;
typedef UInt#(8) ElementBytes;

// Generic simulator/reference builds accept arbitrary byte strides.
function HostAddress hostRowOffset(MatrixExtent row, HostStride stride);
    return zeroExtend(row) * stride;
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
