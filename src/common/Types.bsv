package Types;

import Vector::*;

// -----------------------------------------------------------------------------
// 여러 subsystem이 공유하는 최소 공통 타입
// -----------------------------------------------------------------------------

// 0부터 limit까지의 개수를 모두 표현한다.
// rowCount처럼 상한값 limit 자체도 유효한 값인 counter에 사용한다.
typedef UInt#(TLog#(TAdd#(limit, 1))) BoundedCount#(numeric type limit);

// limit개의 항목 중 하나를 가리키는 index다. 유효 범위는 0부터 limit-1이다.
// BoundedCount를 실제 Vector/array index로 사용할 때 의미를 구분하기 위해 둔다.
typedef UInt#(TLog#(limit)) BoundedIndex#(numeric type limit);

// Accumulator의 logical row 주소다.
// SystolicArray output column은 동일 index의 Accumulator bank에 정적으로
// 대응하므로 주소에는 bank 번호를 포함하지 않고 row만 표현한다.
typedef UInt#(TLog#(rows)) RowAddress#(numeric type rows);

// 하나의 합성된 INT VectorUnit이 execution마다 선택하는 runtime 연산이다.
//
// VectorBypass
//     complete partial sum을 변경하지 않고 contribution으로 사용한다.
//
// VectorMultiply
//     complete partial sum에 signed scale coefficient를 곱한다.
//
// VectorShift
//     scale을 signed exponent로 해석해 좌/우 shift한다.
typedef enum {
    VectorBypass,
    VectorMultiply,
    VectorShift
} VectorOp deriving (Bits, Eq, FShow);

// 선택한 연산이 scale sideband를 실제로 사용하는지 반환한다.
function Bool vectorOpUsesScale(VectorOp op);
    return op != VectorBypass;
endfunction

// Bool Vector의 OR reduction이다.
// 호출한 계층에서 하나 이상의 element가 유효한지 검사할 때 사용한다.
function Bool anyTrue(Vector#(elementCount, Bool) values);
    Bool result = False;

    for (Integer element = 0;
            element < valueOf(elementCount);
            element = element + 1) begin
        result = result || values[element];
    end

    return result;
endfunction

// Bool Vector의 AND reduction이다.
// 호출한 계층에서 모든 element가 조건을 만족하는지 검사할 때 사용한다.
function Bool allTrue(Vector#(elementCount, Bool) values);
    Bool result = True;

    for (Integer element = 0;
            element < valueOf(elementCount);
            element = element + 1) begin
        result = result && values[element];
    end

    return result;
endfunction

endpackage
