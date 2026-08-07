package Weight;

import Vector::*;
import NumericFormat::*;

// Vector 위치는 원본 weight의 K, N index다.
// activation과 같은 format/precision을 전달하면 element 타입도 완전히 같다.
typedef Vector#(
    k,
    Vector#(n, NumericElement#(format, precision))
) WeightTensor#(
    numeric type k,
    numeric type n,
    type format,
    numeric type precision
);

// 고정된 것은 tensor shape뿐이며 수치 형식과 precision은 호출자가 정한다.
typedef WeightTensor#(64, 32, format, precision)
    FixedWeightTensor#(type format, numeric type precision);

endpackage
