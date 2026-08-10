package Config;

// -----------------------------------------------------------------------------
// 대표 synthesis configuration
// -----------------------------------------------------------------------------
//
// 핵심 RTL은 numeric/type parameter를 직접 받는다. 아래 typedef는 synth/와
// testbench가 공유하는 대표값일 뿐이며, 특정 block size나 scaling 실행을
// architecture에 고정하지 않는다.
//
// synthesis-time
//   - numeric format과 precision
//   - array dimension과 PE hop latency
//   - vector lane 수와 accumulator depth
//
// runtime
//   - VectorBypass / VectorMultiply / VectorShift
//   - 기존 accumulator와 누산할지 여부
//   - block_size, global K progress, context-tagged scale row stream

typedef 16 DefaultArrayDim;
typedef 1  DefaultPeLatency;
typedef 16 DefaultVectorLanes;

typedef 8  DefaultInputWidth;
typedef 8  DefaultWeightWidth;
typedef 16 DefaultProductWidth;
typedef 32 DefaultAccumulatorWidth;
typedef 8  DefaultScaleWidth;

typedef 256 DefaultAccumulatorRows;

endpackage
