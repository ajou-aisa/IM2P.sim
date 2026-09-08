package WorkTypes;

import Types::*;
import HostMemoryTypes::*;

function Bool hostMatrixSpanFits(
    HostAddress base, MatrixExtent rows, HostStride rowStride,
    MatrixExtent columns, ElementBytes elementBytes
);
    UInt#(97) rowOffset = zeroExtend(rows - 1) * zeroExtend(rowStride);
    UInt#(97) rowBytes = zeroExtend(columns) * zeroExtend(elementBytes);
    UInt#(97) lastByte = zeroExtend(base) + rowOffset + rowBytes - 1;
    return rows > 0 && columns > 0 && elementBytes > 0
        && lastByte <= 97'hffffffffffffffff;
endfunction

function Bool hostBlockMatrixSpanFits(
    HostAddress base, MatrixExtent block, HostStride blockStride,
    MatrixExtent rows, HostStride rowStride,
    MatrixExtent columns, ElementBytes elementBytes
);
    UInt#(98) blockOffset = zeroExtend(block) * zeroExtend(blockStride);
    UInt#(98) rowOffset = zeroExtend(rows - 1) * zeroExtend(rowStride);
    UInt#(98) rowBytes = zeroExtend(columns) * zeroExtend(elementBytes);
    UInt#(98) lastByte = zeroExtend(base) + blockOffset + rowOffset + rowBytes - 1;
    return rows > 0 && columns > 0 && elementBytes > 0
        && lastByte <= 98'hffffffffffffffff;
endfunction

typedef enum {
    FullMatrix,
    AsyncStripes
} MatmulMode deriving (Bits, Eq, FShow);

typedef struct {
    UInt#(32) stripeId;
    MatrixExtent rowBegin;
    MatrixExtent rowCount;
    HostAddress activationBase;
    HostStride activationRowStride;
    UInt#(64) stripeContext;
    UInt#(64) publishCycle;
} ActivationStripe deriving (Bits, Eq, FShow);

typedef struct {
    UInt#(32) stripeId;
    MatrixExtent rowBegin;
    MatrixExtent rowCount;
    UInt#(64) stripeContext;
    UInt#(64) publishCycle;
    UInt#(64) completionCycle;
} StripeCompletion deriving (Bits, Eq, FShow);

typedef struct {
    MatmulJobId jobId;
    MatmulMode mode;

    HostAddress activationBase;
    HostAddress weightBase;
    HostAddress scaleBase;
    HostAddress outputBase;

    HostStride activationRowStride;
    HostStride weightRowStride;
    HostStride scaleRowStride;
    HostStride outputRowStride;

    MatrixExtent rowCount;
    MatrixExtent columnCount;
    MatrixExtent reductionCount;
    MatrixExtent tileIRows;
    MatrixExtent tileJColumns;
    MatrixExtent blockSize;

    ElementBytes activationElementBytes;
    ElementBytes weightElementBytes;
    ElementBytes scaleElementBytes;
    ElementBytes outputElementBytes;

    VectorOp vectorOp;
    UInt#(64) workContext;
} MatmulDescriptor deriving (Bits, Eq, FShow);

typedef struct {
    MatmulJobId jobId;
    UInt#(32) stripeId;
    UInt#(64) stripeContext;

    MatrixExtent iStart;
    MatrixExtent jStart;
    MatrixExtent iCount;
    MatrixExtent jCount;

    HostAddress activationBase;
    HostAddress weightBase;
    HostAddress scaleBase;
    HostAddress outputBase;

    HostStride activationRowStride;
    HostStride weightRowStride;
    HostStride scaleRowStride;
    HostStride outputRowStride;

    MatrixExtent reductionCount;
    MatrixExtent blockSize;
    VectorOp vectorOp;
    UInt#(64) workContext;
} MatmulWork#(numeric type arrayDim) deriving (Bits, Eq, FShow);

endpackage
