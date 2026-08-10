#include "im2p_verilator.h"

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <cstring>

#include "verilated.h"

#if IM2P_DIM == 16
#include "VmkSynthInt8x16.h"
using Top = VmkSynthInt8x16;
constexpr uint32_t kDim = 16;
constexpr uint32_t kCommandWidth = 16;
constexpr uint32_t kAccumulatorWords = 16;
#elif IM2P_DIM == 32
#include "VmkSynthInt8x32.h"
using Top = VmkSynthInt8x32;
constexpr uint32_t kDim = 32;
constexpr uint32_t kCommandWidth = 17;
constexpr uint32_t kAccumulatorWords = 32;
#else
#error "IM2P_DIM must be 16 or 32"
#endif

struct Simulator {
    VerilatedContext *context;
    Top *top;
    uint64_t cycles;
};

double sc_time_stamp() {
    return 0.0;
}

template <size_t Words>
void set_bytes(VlWide<Words> &signal, const int8_t *values, size_t count) {
    for (size_t index = 0; index < Words; ++index) {
        signal[index] = 0U;
    }
    for (size_t index = 0; index < count; ++index) {
        const auto value = static_cast<uint8_t>(values[index]);
        const size_t word = index / 4;
        const size_t shift = (index % 4) * 8;
        signal[word] |= static_cast<uint32_t>(value) << shift;
    }
}

template <size_t Words>
void set_i32_signal(VlWide<Words> &signal, const int32_t *values, size_t count) {
    for (size_t index = 0; index < Words; ++index) {
        signal[index] = 0U;
    }
    for (size_t index = 0; index < count; ++index) {
        signal[index] = static_cast<uint32_t>(values[index]);
    }
}

template <size_t Words>
void copy_wide(const VlWide<Words> &signal, int32_t *values, size_t count) {
    for (size_t index = 0; index < count; ++index) {
        values[index] = static_cast<int32_t>(signal[index]);
    }
}

void evaluate(Simulator *simulator) {
    simulator->top->eval();
}

void pulse(Simulator *simulator, CData &enable) {
    enable = 1;
    simulator->top->CLK = 0;
    evaluate(simulator);
    simulator->top->CLK = 1;
    evaluate(simulator);
    enable = 0;
    simulator->top->CLK = 0;
    evaluate(simulator);
    ++simulator->cycles;
}

uint32_t command_bits(uint32_t base_row, uint32_t row_count, int accumulate, uint8_t op) {
    const uint32_t row_bits = IM2P_DIM == 16 ? 5U : 6U;
    return (base_row << (row_bits + 3U))
        | (row_count << 3U)
        | ((accumulate ? 1U : 0U) << 2U)
        | (static_cast<uint32_t>(op) & 0x3U);
}

extern "C" im2p_handle_t im2p_create(void) {
    VerilatedContext *context = nullptr;
    Top *top = nullptr;
    Simulator *simulator = nullptr;
    try {
        context = new VerilatedContext;
        top = new Top(context);
        simulator = new Simulator{context, top, 0};
        im2p_reset(simulator);
        return simulator;
    }
    catch (...) {
        delete simulator;
        delete top;
        delete context;
        return nullptr;
    }
}

extern "C" void im2p_destroy(im2p_handle_t handle) {
    auto *simulator = static_cast<Simulator *>(handle);
    if (simulator == nullptr) {
        return;
    }
    delete simulator->top;
    delete simulator->context;
    delete simulator;
}

extern "C" void im2p_reset(im2p_handle_t handle) {
    auto *simulator = static_cast<Simulator *>(handle);
    simulator->top->CLK = 0;
    simulator->top->RST_N = 0;
    evaluate(simulator);
    for (int cycle = 0; cycle < 2; ++cycle) {
        simulator->top->CLK = 1;
        evaluate(simulator);
        simulator->top->CLK = 0;
        evaluate(simulator);
        ++simulator->cycles;
    }
    simulator->top->RST_N = 1;
    evaluate(simulator);
    simulator->cycles = 0;
}

extern "C" void im2p_tick(im2p_handle_t handle) {
    auto *simulator = static_cast<Simulator *>(handle);
    simulator->top->CLK = 0;
    evaluate(simulator);
    simulator->top->CLK = 1;
    evaluate(simulator);
    simulator->top->CLK = 0;
    evaluate(simulator);
    ++simulator->cycles;
}

extern "C" uint64_t im2p_cycle_count(im2p_handle_t handle) {
    return static_cast<Simulator *>(handle)->cycles;
}

extern "C" int im2p_weights_ready(im2p_handle_t handle) {
    auto *top = static_cast<Simulator *>(handle)->top;
    evaluate(static_cast<Simulator *>(handle));
    return top->weightsReady && top->RDY_weightsReady;
}

extern "C" int im2p_load_weight_ready(im2p_handle_t handle) {
    auto *simulator = static_cast<Simulator *>(handle);
    evaluate(simulator);
    return simulator->top->RDY_loadWeightRow;
}

extern "C" int im2p_activation_ready(im2p_handle_t handle) {
    auto *top = static_cast<Simulator *>(handle)->top;
    evaluate(static_cast<Simulator *>(handle));
    return top->activationReady && top->RDY_activationReady;
}

extern "C" int im2p_execution_done(im2p_handle_t handle) {
    auto *top = static_cast<Simulator *>(handle)->top;
    evaluate(static_cast<Simulator *>(handle));
    return top->executionDone && top->RDY_executionDone;
}

extern "C" int im2p_idle(im2p_handle_t handle) {
    auto *top = static_cast<Simulator *>(handle)->top;
    evaluate(static_cast<Simulator *>(handle));
    return top->idle && top->RDY_idle;
}

extern "C" int im2p_begin_weight_load(im2p_handle_t handle) {
    auto *simulator = static_cast<Simulator *>(handle);
    evaluate(simulator);
    if (!simulator->top->RDY_beginWeightLoad) {
        return 0;
    }
    pulse(simulator, simulator->top->EN_beginWeightLoad);
    return 1;
}

extern "C" int im2p_load_weight_row(
    im2p_handle_t handle,
    uint32_t row,
    const int8_t *values
) {
    if (handle == nullptr || values == nullptr) {
        return 0;
    }
    auto *simulator = static_cast<Simulator *>(handle);
    evaluate(simulator);
    if (!simulator->top->RDY_loadWeightRow || row >= kDim) {
        return 0;
    }
    simulator->top->loadWeightRow_row = row;
    set_bytes(simulator->top->loadWeightRow_weights, values, kDim);
    pulse(simulator, simulator->top->EN_loadWeightRow);
    return 1;
}

extern "C" int im2p_configure_scaling(
    im2p_handle_t handle,
    uint32_t block_size,
    uint32_t total_k,
    uint64_t context
) {
    auto *simulator = static_cast<Simulator *>(handle);
    evaluate(simulator);
    if (!simulator->top->RDY_configureScaling
        || block_size == 0
        || total_k == 0) {
        return 0;
    }
    simulator->top->configureScaling_blockSize = block_size;
    simulator->top->configureScaling_totalK = total_k;
    simulator->top->configureScaling_contextId = context;
    pulse(simulator, simulator->top->EN_configureScaling);
    return 1;
}

extern "C" int im2p_service_scale_request(
    im2p_handle_t handle,
    const im2p_scale_matrix_view_t *view
) {
    if (handle == nullptr) {
        return IM2P_SCALE_INVALID_VIEW;
    }
    auto *simulator = static_cast<Simulator *>(handle);
    evaluate(simulator);
    auto *top = simulator->top;
    if (!top->scaleRequestValid) {
        return IM2P_SCALE_NO_REQUEST;
    }
    if (view == nullptr || view->values == nullptr) {
        return IM2P_SCALE_INVALID_VIEW;
    }
    if (!top->RDY_scaleRequestContext || !top->RDY_scaleRequestBlock
        || !top->RDY_scaleRequestKind || top->scaleRequestKind > 1) {
        return IM2P_SCALE_REQUEST_NOT_READY;
    }
    if (top->scaleRequestContext != view->context) {
        return IM2P_SCALE_CONTEXT_MISMATCH;
    }
    if (view->block_size == 0 || view->total_k == 0 || view->columns == 0
        || view->valid_columns == 0 || view->valid_columns > kDim
        || view->row_stride < view->columns
        || view->column_offset > view->columns
        || view->valid_columns > view->columns - view->column_offset) {
        return IM2P_SCALE_INVALID_LAYOUT;
    }
    const size_t block_count = 1 + (view->total_k - 1) / view->block_size;
    const size_t block = top->scaleRequestBlock;
    if (block >= block_count || block > (SIZE_MAX - view->column_offset) / view->row_stride) {
        return IM2P_SCALE_BLOCK_OUT_OF_RANGE;
    }
    const size_t row_start = block * view->row_stride + view->column_offset;
    if (row_start > view->values_len
        || view->valid_columns > view->values_len - row_start) {
        return IM2P_SCALE_BLOCK_OUT_OF_RANGE;
    }

    int8_t row[kDim] = {};
    std::copy_n(view->values + row_start, view->valid_columns, row);

    // This call consumes the borrowed pointer into a stack row. Neither the
    // bridge nor the Verilated model retains any Rust-owned address.
    if (!top->RDY_putScaleRow) {
        return IM2P_SCALE_RESPONSE_NOT_READY;
    }
    top->putScaleRow_contextId = view->context;
    top->putScaleRow_block = static_cast<uint32_t>(block);
    set_bytes(top->putScaleRow_columnScales, row, kDim);
    pulse(simulator, top->EN_putScaleRow);
    return IM2P_SCALE_ROW_ACCEPTED;
}

extern "C" void im2p_scale_counters(
    im2p_handle_t handle,
    im2p_scale_counters_t *counters
) {
    if (handle == nullptr || counters == nullptr) {
        return;
    }
    auto *simulator = static_cast<Simulator *>(handle);
    evaluate(simulator);
    auto *top = simulator->top;
    counters->demand_requests = top->scaleDemandRequests;
    counters->prefetch_requests = top->scalePrefetchRequests;
    counters->current_hits = top->scaleCurrentHits;
    counters->next_hits = top->scaleNextHits;
    counters->demand_misses = top->scaleDemandMisses;
    counters->rows_received = top->scaleRowsReceived;
    counters->wait_cycles = top->scaleWaitCycles;
}

extern "C" int im2p_start_execution(
    im2p_handle_t handle,
    uint32_t accumulator_base_row,
    uint32_t row_count,
    int accumulate,
    uint8_t vector_op,
    uint32_t k_start,
    uint32_t k_count
) {
    auto *simulator = static_cast<Simulator *>(handle);
    evaluate(simulator);
    if (!simulator->top->RDY_startExecution
        || accumulator_base_row >= 256
        || row_count > kDim
        || row_count == 0
        || k_count > kDim
        || k_count == 0
        || vector_op > 2) {
        return 0;
    }
    simulator->top->startExecution_command =
        command_bits(accumulator_base_row, row_count, accumulate, vector_op);
    simulator->top->startExecution_kStart = k_start;
    simulator->top->startExecution_kCount = k_count;
    pulse(simulator, simulator->top->EN_startExecution);
    return 1;
}

extern "C" int im2p_put_activation_row(
    im2p_handle_t handle,
    const int8_t *values
) {
    if (handle == nullptr || values == nullptr) {
        return 0;
    }
    auto *simulator = static_cast<Simulator *>(handle);
    evaluate(simulator);
    if (!simulator->top->RDY_putActivationRow) {
        return 0;
    }
    set_bytes(simulator->top->putActivationRow_activations, values, kDim);
    pulse(simulator, simulator->top->EN_putActivationRow);
    return 1;
}

extern "C" int im2p_acknowledge_execution(im2p_handle_t handle) {
    auto *simulator = static_cast<Simulator *>(handle);
    evaluate(simulator);
    if (!simulator->top->RDY_acknowledgeExecution) {
        return 0;
    }
    pulse(simulator, simulator->top->EN_acknowledgeExecution);
    return 1;
}

extern "C" int im2p_write_accumulator_row(
    im2p_handle_t handle,
    uint32_t row,
    const int32_t *values
) {
    if (handle == nullptr || values == nullptr) {
        return 0;
    }
    auto *simulator = static_cast<Simulator *>(handle);
    evaluate(simulator);
    if (!simulator->top->RDY_writeAccumulatorRow || row >= 256) {
        return 0;
    }
    simulator->top->writeAccumulatorRow_row = row;
    set_i32_signal(simulator->top->writeAccumulatorRow_values, values, kDim);
    pulse(simulator, simulator->top->EN_writeAccumulatorRow);
    return 1;
}

extern "C" int im2p_read_accumulator_row(
    im2p_handle_t handle,
    uint32_t row,
    int32_t *values
) {
    if (handle == nullptr || values == nullptr) {
        return 0;
    }
    auto *simulator = static_cast<Simulator *>(handle);
    evaluate(simulator);
    if (!simulator->top->RDY_readAccumulatorRow || row >= 256) {
        return 0;
    }
    simulator->top->readAccumulatorRow_row = row;
    evaluate(simulator);
    copy_wide(simulator->top->readAccumulatorRow, values, kDim);
    return 1;
}
