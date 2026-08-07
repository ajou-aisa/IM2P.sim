package Activation;

import Vector::*;
import NumericFormat::*;

// Vector 위치는 원본 activation의 M, K index다.
// format은 INT/FLOAT, precision은 각 element의 전체 비트 수다.
// 구체적인 4/8-bit alias를 만들지 않고 사용하는 쪽에서 둘을 직접 지정한다.
typedef Vector#(
    m,
    Vector#(k, NumericElement#(format, precision))
) ActivationTensor#(
    numeric type m,
    numeric type k,
    type format,
    numeric type precision
);

// 고정된 것은 tensor shape뿐이며 수치 형식과 precision은 호출자가 정한다.
typedef ActivationTensor#(32, 64, format, precision)
    FixedActivationTensor#(type format, numeric type precision);

endpackage
