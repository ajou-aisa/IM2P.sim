package ExecuteCmd;

import Types::*;

// -----------------------------------------------------------------------------
// DMA 없는 하나의 systolic execution command
// -----------------------------------------------------------------------------
//
// execution은 quantization block을 의미하지 않는다. 현재 array에 preload된
// stationary weight와 전달된 activation row들을 이용해 현재 array-width K tile의
// column 결과를 만들고, runtime VectorOp으로 선택한 후단 연산을 적용한 뒤
// accumulator에 반영하는 일반 실행 단위다. 전체 K가 arrayDim보다 크면 상위
// software/controller가 여러 execution을 수행하고 accumulate=True로 결합한다.
//
// Core는 block_size와 k_start에서 scale[b,:]를 RTL에서 선택해 동일 execution의
// 모든 output row에 적용한다. Scale이 필요 없는 실행은 VectorBypass를 사용하며
// scale table 없이도 동작한다.

typedef struct {
    RowAddress#(accRows) accumulatorBaseRow;
    BoundedCount#(arrayDim) rowCount;
    Bool accumulate;
    VectorOp vectorOp;
} ExecuteCmd#(
    numeric type arrayDim,
    numeric type accRows
) deriving (Bits, Eq, FShow);

endpackage
