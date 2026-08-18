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
    uint64_t debug_positive_edges;
    uint8_t staged_response_mask;
    uint8_t observed_response_mask;
    uint8_t max_concurrent_responses;
    bool matrix_active;
    bool matrix_async;
    uint32_t matrix_rows;
    uint32_t matrix_reduction;
    uint32_t published_rows;
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

template <size_t Words>
void set_i8_lanes(VlWide<Words> &signal, const int8_t *values, size_t count) {
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

void evaluate(Simulator *simulator) {
    simulator->top->eval();
}

void clear_enables(Simulator *simulator);

void clock_staged(Simulator *simulator) {
    simulator->observed_response_mask |= simulator->staged_response_mask;
    simulator->max_concurrent_responses = std::max(
        simulator->max_concurrent_responses,
        static_cast<uint8_t>(__builtin_popcount(simulator->staged_response_mask))
    );
    simulator->top->CLK = 0;
    evaluate(simulator);
    simulator->top->CLK = 1;
    evaluate(simulator);
    clear_enables(simulator);
    simulator->top->CLK = 0;
    evaluate(simulator);
    ++simulator->debug_positive_edges;
    simulator->staged_response_mask = 0;
}

/*
 * Every EN_* input of the generated model. A freshly constructed Top does not
 * value-initialize its port members, so an indeterminate scheduler enable can
 * fire the instant RST_N is released and drag the core out of MatrixIdle.
 * Clearing all enables is also the correct post-condition after any staged
 * pulse, so both reset and pulse funnel through this helper.
 */
void clear_enables(Simulator *simulator) {
    auto *top = simulator->top;
    top->EN_beginWeightLoad = 0;
    top->EN_loadWeightRow = 0;
    top->EN_configureScaling = 0;
    top->EN_putScaleRow = 0;
    top->EN_startExecution = 0;
    top->EN_putActivationRow = 0;
    top->EN_acknowledgeExecution = 0;
    top->EN_writeAccumulatorRow = 0;
    top->EN_startMatmul = 0;
    top->EN_publishActivationStripe = 0;
    top->EN_putActivationReadResponse = 0;
    top->EN_putWeightReadResponse = 0;
    top->EN_putScaleReadResponse = 0;
    top->EN_putOutputWriteResponse = 0;
    top->EN_acknowledgeStripeCompletion = 0;
    top->EN_acknowledgeMatmul = 0;
}

void pulse(Simulator *simulator, CData &enable) {
    clear_enables(simulator);
    enable = 1;
    clock_staged(simulator);
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
        simulator = new Simulator{context, top, 0, 0, 0, 0, false, false, 0, 0, 0};
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
    clear_enables(simulator);
    evaluate(simulator);
    for (int cycle = 0; cycle < 2; ++cycle) {
        simulator->top->CLK = 1;
        evaluate(simulator);
        simulator->top->CLK = 0;
        evaluate(simulator);
    }
    clear_enables(simulator);
    simulator->top->RST_N = 1;
    evaluate(simulator);
    simulator->debug_positive_edges = 0;
    simulator->staged_response_mask = 0;
    simulator->observed_response_mask = 0;
    simulator->max_concurrent_responses = 0;
    simulator->matrix_active = false;
    simulator->matrix_async = false;
    simulator->matrix_rows = 0;
    simulator->matrix_reduction = 0;
    simulator->published_rows = 0;
}

extern "C" void im2p_tick(im2p_handle_t handle) {
    auto *simulator = static_cast<Simulator *>(handle);
    clear_enables(simulator);
    clock_staged(simulator);
}

extern "C" void im2p_tick_staged(im2p_handle_t handle) {
    clock_staged(static_cast<Simulator *>(handle));
}

extern "C" void im2p_eval(im2p_handle_t handle) {
    evaluate(static_cast<Simulator *>(handle));
}

extern "C" uint64_t im2p_cycle_count(im2p_handle_t handle) {
    auto *simulator = static_cast<Simulator *>(handle);
    evaluate(simulator);
    return simulator->top->rtlCycleCount;
}

extern "C" uint64_t im2p_positive_edge_count(im2p_handle_t handle) {
    return static_cast<Simulator *>(handle)->debug_positive_edges;
}

extern "C" int im2p_work_active(im2p_handle_t handle) {
    auto *simulator = static_cast<Simulator *>(handle);
    evaluate(simulator);
    return simulator->top->workActive ? 1 : 0;
}

extern "C" uint64_t im2p_work_cycle_count(im2p_handle_t handle) {
    auto *simulator = static_cast<Simulator *>(handle);
    evaluate(simulator);
    return simulator->top->workCycles;
}

extern "C" uint64_t im2p_last_completed_work_cycles(im2p_handle_t handle) {
    auto *simulator = static_cast<Simulator *>(handle);
    evaluate(simulator);
    return simulator->top->lastCompletedWorkCycles;
}

extern "C" uint64_t im2p_work_start_cycle(im2p_handle_t handle) {
    auto *simulator = static_cast<Simulator *>(handle);
    evaluate(simulator);
    return simulator->top->workStartCycle;
}

extern "C" uint64_t im2p_work_completion_cycle(im2p_handle_t handle) {
    auto *simulator = static_cast<Simulator *>(handle);
    evaluate(simulator);
    return simulator->top->workCompletionCycle;
}

extern "C" uint32_t im2p_observed_response_mask(im2p_handle_t handle) {
    return static_cast<Simulator *>(handle)->observed_response_mask;
}

extern "C" uint32_t im2p_max_concurrent_responses(im2p_handle_t handle) {
    return static_cast<Simulator *>(handle)->max_concurrent_responses;
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
            || vector_op > 3) {
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

extern "C" int im2p_start_matmul(
    im2p_handle_t handle,
    const im2p_matmul_descriptor_t *descriptor
) {
    if (handle == nullptr || descriptor == nullptr) {
        return IM2P_REQUEST_INVALID_ARGUMENT;
    }
    if (descriptor->mode > 1 || descriptor->vector_op > 3
        || descriptor->row_count == 0 || descriptor->column_count == 0
        || descriptor->reduction_count == 0
        || descriptor->tile_i_rows == 0 || descriptor->tile_i_rows > kDim
        || descriptor->tile_j_columns == 0 || descriptor->tile_j_columns > kDim
        || descriptor->activation_row_stride < descriptor->reduction_count
        || descriptor->weight_row_stride < descriptor->column_count
        || descriptor->output_row_stride
            < descriptor->column_count * sizeof(int32_t)) {
        return IM2P_REQUEST_INVALID_ARGUMENT;
    }
    auto *simulator = static_cast<Simulator *>(handle);
    evaluate(simulator);
    auto *top = simulator->top;
    if (!top->RDY_startMatmul) {
        return 0;
    }

    // Copied field by field; the descriptor is not retained past this call.
    top->startMatmul_jobId = descriptor->job_id;
    top->startMatmul_mode = descriptor->mode;
    top->startMatmul_activationBase = descriptor->activation_base;
    top->startMatmul_weightBase = descriptor->weight_base;
    top->startMatmul_scaleBase = descriptor->scale_base;
    top->startMatmul_outputBase = descriptor->output_base;
    top->startMatmul_activationRowStride = descriptor->activation_row_stride;
    top->startMatmul_weightRowStride = descriptor->weight_row_stride;
    top->startMatmul_scaleRowStride = descriptor->scale_row_stride;
    top->startMatmul_outputRowStride = descriptor->output_row_stride;
    top->startMatmul_rowCount = descriptor->row_count;
    top->startMatmul_columnCount = descriptor->column_count;
    top->startMatmul_reductionCount = descriptor->reduction_count;
    top->startMatmul_tileIRows = descriptor->tile_i_rows;
    top->startMatmul_tileJColumns = descriptor->tile_j_columns;
    top->startMatmul_kOrigin = descriptor->k_origin;
    top->startMatmul_scaleTotalK = descriptor->scale_total_k;
    top->startMatmul_scaleBlockSize = descriptor->scale_block_size;
    top->startMatmul_scaleContext = descriptor->scale_context;
    top->startMatmul_accumulateFirstFragment =
        descriptor->accumulate_first_fragment ? 1 : 0;
    top->startMatmul_vectorOp = descriptor->vector_op;
    pulse(simulator, top->EN_startMatmul);
    simulator->matrix_active = true;
    simulator->matrix_async = descriptor->mode == 1;
    simulator->matrix_rows = descriptor->row_count;
    simulator->matrix_reduction = descriptor->reduction_count;
    simulator->published_rows = descriptor->mode == 0 ? descriptor->row_count : 0;
    return 1;
}

extern "C" int im2p_publish_activation_stripe(
    im2p_handle_t handle,
    uint32_t row_begin,
    uint32_t row_count,
    uint64_t row_stride
) {
    if (handle == nullptr) {
        return IM2P_PUBLISH_INVALID;
    }
    auto *simulator = static_cast<Simulator *>(handle);
    if (!simulator->matrix_active || !simulator->matrix_async
        || simulator->published_rows == simulator->matrix_rows) {
        return IM2P_PUBLISH_LATE;
    }
    if (row_begin < simulator->published_rows) {
        return IM2P_PUBLISH_DUPLICATE;
    }
    if (row_count == 0 || row_stride < simulator->matrix_reduction
        || row_begin != simulator->published_rows
        || row_begin > simulator->matrix_rows
        || row_count > simulator->matrix_rows - row_begin) {
        return IM2P_PUBLISH_INVALID;
    }
    evaluate(simulator);
    auto *top = simulator->top;
    if (!top->RDY_publishActivationStripe) {
        return IM2P_PUBLISH_BACKPRESSURE;
    }
    top->publishActivationStripe_rowBegin = row_begin;
    top->publishActivationStripe_rowCount = row_count;
    top->publishActivationStripe_rowStride = row_stride;
    pulse(simulator, top->EN_publishActivationStripe);
    simulator->published_rows += row_count;
    return IM2P_PUBLISH_ACCEPTED;
}

extern "C" int im2p_activation_stripe_ready(im2p_handle_t handle) {
    if (handle == nullptr) {
        return IM2P_REQUEST_INVALID_ARGUMENT;
    }
    auto *simulator = static_cast<Simulator *>(handle);
    evaluate(simulator);
    return simulator->top->RDY_publishActivationStripe;
}

extern "C" int im2p_matmul_done(im2p_handle_t handle) {
    if (handle == nullptr) {
        return IM2P_REQUEST_INVALID_ARGUMENT;
    }
    auto *simulator = static_cast<Simulator *>(handle);
    evaluate(simulator);
    return simulator->top->matmulDone && simulator->top->RDY_matmulDone;
}

extern "C" int im2p_acknowledge_matmul(im2p_handle_t handle) {
    if (handle == nullptr) {
        return IM2P_REQUEST_INVALID_ARGUMENT;
    }
    auto *simulator = static_cast<Simulator *>(handle);
    evaluate(simulator);
    if (!simulator->top->RDY_acknowledgeMatmul) {
        return 0;
    }
    pulse(simulator, simulator->top->EN_acknowledgeMatmul);
    simulator->matrix_active = false;
    return 1;
}

extern "C" int im2p_activation_read_request(
    im2p_handle_t handle,
    im2p_read_request_t *request
) {
    if (handle == nullptr || request == nullptr) {
        return IM2P_REQUEST_INVALID_ARGUMENT;
    }
    auto *simulator = static_cast<Simulator *>(handle);
    evaluate(simulator);
    auto *top = simulator->top;
    // Tag/address/count carry meaning only while the request is valid; the
    // generated RDY signals mirror that guard.
    if (!top->activationReadRequestValid
        || !top->RDY_activationReadRequestTag
        || !top->RDY_activationReadRequestAddress
        || !top->RDY_activationReadRequestElementCount) {
        return IM2P_REQUEST_ABSENT;
    }
    request->tag = top->activationReadRequestTag;
    request->address = top->activationReadRequestAddress;
    request->element_count = top->activationReadRequestElementCount;
    return IM2P_REQUEST_PRESENT;
}

extern "C" int im2p_weight_read_request(
    im2p_handle_t handle,
    im2p_read_request_t *request
) {
    if (handle == nullptr || request == nullptr) {
        return IM2P_REQUEST_INVALID_ARGUMENT;
    }
    auto *simulator = static_cast<Simulator *>(handle);
    evaluate(simulator);
    auto *top = simulator->top;
    if (!top->weightReadRequestValid
        || !top->RDY_weightReadRequestTag
        || !top->RDY_weightReadRequestAddress
        || !top->RDY_weightReadRequestElementCount) {
        return IM2P_REQUEST_ABSENT;
    }
    request->tag = top->weightReadRequestTag;
    request->address = top->weightReadRequestAddress;
    request->element_count = top->weightReadRequestElementCount;
    return IM2P_REQUEST_PRESENT;
}

extern "C" int im2p_scale_read_request(
    im2p_handle_t handle,
    im2p_read_request_t *request
) {
    if (handle == nullptr || request == nullptr) {
        return IM2P_REQUEST_INVALID_ARGUMENT;
    }
    auto *simulator = static_cast<Simulator *>(handle);
    evaluate(simulator);
    auto *top = simulator->top;
    if (!top->scaleReadRequestValid
        || !top->RDY_scaleReadRequestTag
        || !top->RDY_scaleReadRequestAddress
        || !top->RDY_scaleReadRequestElementCount) {
        return IM2P_REQUEST_ABSENT;
    }
    request->tag = top->scaleReadRequestTag;
    request->address = top->scaleReadRequestAddress;
    request->element_count = top->scaleReadRequestElementCount;
    return IM2P_REQUEST_PRESENT;
}

extern "C" int im2p_stage_activation_read_response(
    im2p_handle_t handle,
    uint64_t tag,
    const int8_t *values,
    uint32_t count
) {
    if (handle == nullptr || values == nullptr || count > kDim) {
        return IM2P_REQUEST_INVALID_ARGUMENT;
    }
    auto *simulator = static_cast<Simulator *>(handle);
    evaluate(simulator);
    auto *top = simulator->top;
    if (!top->RDY_putActivationReadResponse) {
        return 0;
    }
    if (!top->activationReadRequestValid
        || !top->RDY_activationReadRequestTag
        || tag != top->activationReadRequestTag) {
        return IM2P_REQUEST_IDENTITY_MISMATCH;
    }
    top->putActivationReadResponse_tag = tag;
    set_i8_lanes(top->putActivationReadResponse_values, values, count);
    top->EN_putActivationReadResponse = 1;
    simulator->staged_response_mask |= 0x1;
    evaluate(simulator);
    return 1;
}

extern "C" int im2p_put_activation_read_response(
    im2p_handle_t handle,
    uint64_t tag,
    const int8_t *values,
    uint32_t count
) {
    if (handle == nullptr || values == nullptr || count > kDim) {
        return IM2P_REQUEST_INVALID_ARGUMENT;
    }
    auto *simulator = static_cast<Simulator *>(handle);
    evaluate(simulator);
    auto *top = simulator->top;
    if (!top->RDY_putActivationReadResponse) {
        return 0;
    }
    if (!top->activationReadRequestValid
        || !top->RDY_activationReadRequestTag
        || tag != top->activationReadRequestTag) {
        return IM2P_REQUEST_IDENTITY_MISMATCH;
    }
    top->putActivationReadResponse_tag = tag;
    set_i8_lanes(top->putActivationReadResponse_values, values, count);
    pulse(simulator, top->EN_putActivationReadResponse);
    return 1;
}

extern "C" int im2p_stage_weight_read_response(
    im2p_handle_t handle,
    uint64_t tag,
    const int8_t *values,
    uint32_t count
) {
    if (handle == nullptr || values == nullptr || count > kDim) {
        return IM2P_REQUEST_INVALID_ARGUMENT;
    }
    auto *simulator = static_cast<Simulator *>(handle);
    evaluate(simulator);
    auto *top = simulator->top;
    if (!top->RDY_putWeightReadResponse) return 0;
    if (!top->weightReadRequestValid || !top->RDY_weightReadRequestTag
        || tag != top->weightReadRequestTag) return IM2P_REQUEST_IDENTITY_MISMATCH;
    top->putWeightReadResponse_tag = tag;
    set_i8_lanes(top->putWeightReadResponse_values, values, count);
    top->EN_putWeightReadResponse = 1;
    simulator->staged_response_mask |= 0x2;
    evaluate(simulator);
    return 1;
}

extern "C" int im2p_put_weight_read_response(
    im2p_handle_t handle,
    uint64_t tag,
    const int8_t *values,
    uint32_t count
) {
    if (handle == nullptr || values == nullptr || count > kDim) {
        return IM2P_REQUEST_INVALID_ARGUMENT;
    }
    auto *simulator = static_cast<Simulator *>(handle);
    evaluate(simulator);
    auto *top = simulator->top;
    if (!top->RDY_putWeightReadResponse) {
        return 0;
    }
    if (!top->weightReadRequestValid
        || !top->RDY_weightReadRequestTag
        || tag != top->weightReadRequestTag) {
        return IM2P_REQUEST_IDENTITY_MISMATCH;
    }
    top->putWeightReadResponse_tag = tag;
    set_i8_lanes(top->putWeightReadResponse_values, values, count);
    pulse(simulator, top->EN_putWeightReadResponse);
    return 1;
}

extern "C" int im2p_stage_scale_read_response(
    im2p_handle_t handle,
    uint64_t tag,
    const int8_t *values,
    uint32_t count
) {
    if (handle == nullptr || values == nullptr || count > kDim) {
        return IM2P_REQUEST_INVALID_ARGUMENT;
    }
    auto *simulator = static_cast<Simulator *>(handle);
    evaluate(simulator);
    auto *top = simulator->top;
    if (!top->RDY_putScaleReadResponse) return 0;
    if (!top->scaleReadRequestValid || !top->RDY_scaleReadRequestTag
        || tag != top->scaleReadRequestTag) return IM2P_REQUEST_IDENTITY_MISMATCH;
    top->putScaleReadResponse_tag = tag;
    set_i8_lanes(top->putScaleReadResponse_values, values, count);
    top->EN_putScaleReadResponse = 1;
    simulator->staged_response_mask |= 0x4;
    evaluate(simulator);
    return 1;
}

extern "C" int im2p_put_scale_read_response(
    im2p_handle_t handle,
    uint64_t tag,
    const int8_t *values,
    uint32_t count
) {
    if (handle == nullptr || values == nullptr || count > kDim) {
        return IM2P_REQUEST_INVALID_ARGUMENT;
    }
    auto *simulator = static_cast<Simulator *>(handle);
    evaluate(simulator);
    auto *top = simulator->top;
    if (!top->RDY_putScaleReadResponse) {
        return 0;
    }
    if (!top->scaleReadRequestValid
        || !top->RDY_scaleReadRequestTag
        || tag != top->scaleReadRequestTag) {
        return IM2P_REQUEST_IDENTITY_MISMATCH;
    }
    top->putScaleReadResponse_tag = tag;
    set_i8_lanes(top->putScaleReadResponse_values, values, count);
    pulse(simulator, top->EN_putScaleReadResponse);
    return 1;
}

extern "C" int im2p_output_write_request(
    im2p_handle_t handle,
    im2p_write_request_t *request,
    int32_t *values
) {
    if (handle == nullptr || request == nullptr || values == nullptr) {
        return IM2P_REQUEST_INVALID_ARGUMENT;
    }
    auto *simulator = static_cast<Simulator *>(handle);
    evaluate(simulator);
    auto *top = simulator->top;
    if (!top->outputWriteRequestValid
        || !top->RDY_outputWriteRequestTag
        || !top->RDY_outputWriteRequestAddress
        || !top->RDY_outputWriteRequestElementCount
        || !top->RDY_outputWriteRequestValues) {
        return IM2P_REQUEST_ABSENT;
    }
    request->tag = top->outputWriteRequestTag;
    request->address = top->outputWriteRequestAddress;
    request->element_count = top->outputWriteRequestElementCount;
    copy_wide(top->outputWriteRequestValues, values, kDim);
    return IM2P_REQUEST_PRESENT;
}

extern "C" int im2p_stage_output_write_response(
    im2p_handle_t handle,
    uint64_t tag
) {
    if (handle == nullptr) return IM2P_REQUEST_INVALID_ARGUMENT;
    auto *simulator = static_cast<Simulator *>(handle);
    evaluate(simulator);
    auto *top = simulator->top;
    if (!top->RDY_putOutputWriteResponse) return 0;
    if (!top->outputWriteRequestValid || !top->RDY_outputWriteRequestTag
        || tag != top->outputWriteRequestTag) return IM2P_REQUEST_IDENTITY_MISMATCH;
    top->putOutputWriteResponse_tag = tag;
    top->EN_putOutputWriteResponse = 1;
    simulator->staged_response_mask |= 0x8;
    evaluate(simulator);
    return 1;
}

extern "C" int im2p_put_output_write_response(
    im2p_handle_t handle,
    uint64_t tag
) {
    if (handle == nullptr) {
        return IM2P_REQUEST_INVALID_ARGUMENT;
    }
    auto *simulator = static_cast<Simulator *>(handle);
    evaluate(simulator);
    auto *top = simulator->top;
    if (!top->RDY_putOutputWriteResponse) {
        return 0;
    }
    if (!top->outputWriteRequestValid
        || !top->RDY_outputWriteRequestTag
        || tag != top->outputWriteRequestTag) {
        return IM2P_REQUEST_IDENTITY_MISMATCH;
    }
    top->putOutputWriteResponse_tag = tag;
    pulse(simulator, top->EN_putOutputWriteResponse);
    return 1;
}

extern "C" int im2p_stripe_completion(
    im2p_handle_t handle,
    im2p_stripe_completion_t *completion
) {
    if (handle == nullptr || completion == nullptr) {
        return IM2P_REQUEST_INVALID_ARGUMENT;
    }
    auto *simulator = static_cast<Simulator *>(handle);
    evaluate(simulator);
    auto *top = simulator->top;
    if (!top->stripeCompletionValid
        || !top->RDY_stripeCompletionId
        || !top->RDY_stripeCompletionRowBegin
        || !top->RDY_stripeCompletionRowCount
        || !top->RDY_stripeCompletionContext) {
        return IM2P_REQUEST_ABSENT;
    }
    completion->stripe_id = top->stripeCompletionId;
    completion->row_begin = top->stripeCompletionRowBegin;
    completion->row_count = top->stripeCompletionRowCount;
    completion->stripe_context = top->stripeCompletionContext;
    return IM2P_REQUEST_PRESENT;
}

extern "C" int im2p_stage_acknowledge_stripe_completion(im2p_handle_t handle) {
    if (handle == nullptr) return IM2P_REQUEST_INVALID_ARGUMENT;
    auto *simulator = static_cast<Simulator *>(handle);
    evaluate(simulator);
    if (!simulator->top->RDY_acknowledgeStripeCompletion) return 0;
    simulator->top->EN_acknowledgeStripeCompletion = 1;
    evaluate(simulator);
    return 1;
}

extern "C" int im2p_acknowledge_stripe_completion(im2p_handle_t handle) {
    if (handle == nullptr) {
        return IM2P_REQUEST_INVALID_ARGUMENT;
    }
    auto *simulator = static_cast<Simulator *>(handle);
    evaluate(simulator);
    if (!simulator->top->RDY_acknowledgeStripeCompletion) {
        return 0;
    }
    pulse(simulator, simulator->top->EN_acknowledgeStripeCompletion);
    return 1;
}

extern "C" void im2p_matrix_counters(
    im2p_handle_t handle,
    im2p_matrix_counters_t *counters
) {
    if (handle == nullptr || counters == nullptr) {
        return;
    }
    auto *simulator = static_cast<Simulator *>(handle);
    evaluate(simulator);
    auto *top = simulator->top;
    counters->fragments_completed = top->matmulFragmentsCompleted;
    counters->works_completed = top->matmulWorksCompleted;
    counters->stripes_published = top->stripesPublished;
    counters->stripe_rows_published = top->stripeRowsPublished;
    counters->activation_read_requests = top->activationReadRequests;
    counters->weight_read_requests = top->weightReadRequests;
    counters->scale_read_requests = top->scaleReadRequests;
    counters->output_write_requests = top->outputWriteRequests;
    counters->output_write_responses = top->outputWriteResponses;
    counters->weight_bank_activations = top->weightBankActivations;
    counters->activation_wait_cycles = top->activationWaitCycles;
    counters->weight_wait_cycles = top->weightWaitCycles;
    counters->output_wait_cycles = top->outputWaitCycles;
    counters->stripe_host_wait_cycles = top->stripeHostWaitCycles;
    counters->compute_cycles = top->computeCycles;
    counters->drain_cycles = top->drainCycles;
    counters->weight_preload_cycles = top->weightPreloadCycles;
    counters->activation_overlap_cycles = top->activationOverlapCycles;
    counters->weight_overlap_cycles = top->weightOverlapCycles;
    counters->scale_overlap_cycles = top->scaleOverlapCycles;
    counters->overlap_cycles = top->overlapCycles;
    counters->cross_stripe_overlap_cycles = top->crossStripeOverlapCycles;
    counters->lookahead_prepared = top->lookaheadPrepared ? 1 : 0;
    counters->lookahead_publish_cycle = top->lookaheadPublishCycle;
    counters->lookahead_first_activation_cycle = top->lookaheadFirstActivationCycle;
    counters->lookahead_first_weight_cycle = top->lookaheadFirstWeightCycle;
    counters->lookahead_weight_preload_cycle = top->lookaheadWeightPreloadCycle;
    counters->lookahead_weight_requests = top->lookaheadWeightRequests;
    counters->lookahead_weight_reuse_hits = top->lookaheadWeightReuseHits;
    counters->lookahead_scale_cycle = top->lookaheadScaleCycle;
    counters->lookahead_scale_requests = top->lookaheadScaleRequests;
    counters->lookahead_scale_reuses = top->lookaheadScaleReuses;
    counters->current_stripe_completion_cycle = top->currentStripeCompletionCycle;
    counters->lookahead_ready_cycle = top->lookaheadReadyCycle;
    counters->lookahead_start_cycle = top->lookaheadStartCycle;
}

extern "C" void im2p_matrix_debug(
    im2p_handle_t handle,
    im2p_matrix_debug_t *debug
) {
    if (handle == nullptr || debug == nullptr) {
        return;
    }
    auto *simulator = static_cast<Simulator *>(handle);
    evaluate(simulator);
    auto *top = simulator->top;
    debug->matmul_scheduler_state = top->matmulSchedulerState;
    debug->work_scheduler_state = top->workSchedulerState;
    debug->matrix_core_state = top->matrixCoreState;
    debug->active_weight_bank = top->activeWeightBank ? 1 : 0;
    debug->inactive_weight_bank_loading = top->inactiveWeightBankLoading ? 1 : 0;
    debug->execution_active = top->executionActive ? 1 : 0;
    debug->accepted_rows = top->debugAcceptedRows;
    debug->configured_rows = top->debugConfiguredRows;
    debug->first_column_issued = top->debugFirstColumnIssued;
    debug->first_column_committed = top->debugFirstColumnCommitted;
    debug->engine_result_valid = top->debugEngineResultValid ? 1 : 0;
    debug->vector_busy = top->debugVectorBusy ? 1 : 0;
    debug->activation_request_valid = top->activationReadRequestValid ? 1 : 0;
    debug->weight_request_valid = top->weightReadRequestValid ? 1 : 0;
    debug->scale_request_valid = top->scaleReadRequestValid ? 1 : 0;
    debug->output_request_valid = top->outputWriteRequestValid ? 1 : 0;
    debug->stripe_host_waiting = top->matmulSchedulerState == 1 ? 1 : 0;
    debug->lookahead_prepared = top->lookaheadPrepared ? 1 : 0;
    debug->lookahead_stripe_id = top->lookaheadStripeId;
}
