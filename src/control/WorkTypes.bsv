package WorkTypes;

import Types::*;
import HostMemoryTypes::*;

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
} ActivationStripe deriving (Bits, Eq, FShow);

typedef struct {
    UInt#(32) stripeId;
    MatrixExtent rowBegin;
    MatrixExtent rowCount;
    UInt#(64) stripeContext;
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
