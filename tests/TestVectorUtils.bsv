package TestVectorUtils;

import Vector::*;

// BSV Vector package에는 vec(...) literal constructor가 없다.
// 반복되는 2/3/4-element test vector를 명시적으로 만드는 testbench helper다.
function Vector#(2, element_t) vector2(
    element_t element0,
    element_t element1
);
    Vector#(2, element_t) values = newVector;
    values[0] = element0;
    values[1] = element1;
    return values;
endfunction

function Vector#(3, element_t) vector3(
    element_t element0,
    element_t element1,
    element_t element2
);
    Vector#(3, element_t) values = newVector;
    values[0] = element0;
    values[1] = element1;
    values[2] = element2;
    return values;
endfunction

function Vector#(4, element_t) vector4(
    element_t element0,
    element_t element1,
    element_t element2,
    element_t element3
);
    Vector#(4, element_t) values = newVector;
    values[0] = element0;
    values[1] = element1;
    values[2] = element2;
    values[3] = element3;
    return values;
endfunction

endpackage
