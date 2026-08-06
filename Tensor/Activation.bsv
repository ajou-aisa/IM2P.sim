package Activation;

import Vector::*;

// Activation은 raw 8-bit lane의 묶음이다.
// bitWidth=8이면 lane 1개, bitWidth=32이면 lane 4개가 생성된다.
// lanes[0]이 least-significant 8-bit lane이다.
typedef struct {
    Vector#(TDiv#(bitWidth, 8), Bit#(8)) lanes;
} Activation#(numeric type bitWidth) deriving (Bits, Eq, FShow);

// Vector 위치가 원본 activation의 M, K index를 나타낸다.
typedef Vector#(m, Vector#(k, Activation#(bitWidth)))
    ActivationTensor#(
        numeric type m,
        numeric type k,
        numeric type bitWidth
    );

// BSV typedef에는 기본 타입 인자가 없으므로 8-bit 기본 타입을 alias로 제공한다.
typedef Activation#(8) Activation8;
typedef Activation#(32) Activation32;
typedef ActivationTensor#(32, 64, 32) FixedActivationTensor;

endpackage
