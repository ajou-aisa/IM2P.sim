package Weight;

import Vector::*;

typedef Int#(8) Weight;

// Vector 위치가 원본 weight의 K, N index를 나타낸다.
typedef Vector#(k, Vector#(n, Weight))
    WeightTensor#(numeric type k, numeric type n);

typedef WeightTensor#(64, 32) FixedWeightTensor;

endpackage