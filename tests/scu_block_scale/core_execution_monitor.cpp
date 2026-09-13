// Read-only observations of an unmodified generated production model.
// The runner supplies the frozen production FFI source through this macro.
#ifndef IM2P_MONITOR_FFI
#error "build through run_core_execution_monitor.py"
#endif
#include IM2P_MONITOR_FFI
#include "VmkSynthA8W8D16___024root.h"

#include <array>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>

namespace {
constexpr uint64_t ABase = 0x1000, WBase = 0x2000, SBase = 0x3000, CBase = 0x4000;
constexpr unsigned RowMask = (1U << IM2P_ROW_ADDRESS_BITS) - 1;

void require(bool condition, const char *message) {
    if (!condition) throw std::runtime_error(message);
}
int32_t signed32(uint32_t bits) {
    int32_t result;
    std::memcpy(&result, &bits, sizeof(result));
    return result;
}

struct Case {
    const char *name;
    uint8_t op;
    std::array<int8_t,4> a, w;
    std::array<uint32_t,2> metadata;
    // Independently specified scalar trace, never used to drive the DUT.
    std::array<int32_t,4> partial, contribution, accumulated;
};

const std::array<Case,6> Cases{{
    {"h1_base",4,{1,1,1,1},{1,1,1,1},{1,256},
        {16,16,16,16},{16,16,4096,4096},{16,32,4128,8224}},
    {"h1_changed_beta",4,{1,1,1,1},{1,1,1,1},{1,257},
        {16,16,16,16},{16,16,4112,4112},{16,32,4144,8256}},
    {"h1_bit16",4,{1,1,1,1},{1,1,1,1},{65536,65790},
        {16,16,16,16},{1048576,1048576,1052640,1052640},{1048576,2097152,3149792,4202432}},
    {"h1_clamp_then_cancel",4,{-128,-128,1,1},{-128,-128,-1,-1},{65536,1},
        {262144,262144,-16,-16},{INT32_MAX,INT32_MAX,-16,-16},
        {INT32_MAX,INT32_MAX,INT32_MAX-16,INT32_MAX-32}},
    {"hp1_zero_then_large",5,{-1,0,1,1},{1,1,1,1},{0x80000000U,32767},
        {-16,0,16,16},{0,0,INT32_MAX,INT32_MAX},{0,0,INT32_MAX,INT32_MAX}},
    {"hp1_negative_large_then_zero",5,{-1,-1,1,1},{1,1,1,1},{32767,0x80000000U},
        {-16,-16,16,16},{INT32_MIN,INT32_MIN,0,0},{INT32_MIN,INT32_MIN,INT32_MIN,INT32_MIN}},
}};

// These members are generated from existing registers/wires. No bind logic,
// tracing register, public accessor, or numerical datapath is added to RTL.
#define SIGNAL(name) root.mkSynthA8W8D16__DOT__##name

struct Monitor {
    const Case &test;
    size_t commits = 0, writes = 0, rule_fires = 0, padded_column_commits = 0;
    bool pending_commit = false, pending_write = false;
    unsigned padded_mask = 0;
    uint64_t cycle = 0;

    void before(Simulator &simulator) {
        const auto &root = *simulator.top->rootp;
        require(simulator.top->CLK == 0, "monitor must sample before the rising edge");
        cycle = simulator.debug_positive_edges;
        const bool rule_fired = SIGNAL(WILL_FIRE_RL_core_core_commitVectorResult);
        const unsigned valid_mask = SIGNAL(core_core_vectorUnit_validReg);
        padded_mask = 0;
        if (rule_fired) {
            ++rule_fires;
            require(valid_mask != 0 && (valid_mask & (valid_mask-1)) == 0,
                    "M1 wavefront must select one physical column");
            require(SIGNAL(core_core_vectorUnit_groupIndexReg) == 0, "unexpected vector group");
            // N1 still computes the other physical columns using padded W=0.
            // They are valid engine results but are not logical C outputs.
            padded_mask = valid_mask & ~1U;
            for (unsigned lane=1; lane<16; ++lane) if (padded_mask & (1U << lane)) {
                require(SIGNAL(core_core_vectorUnit_partialReg)[lane] == 0 &&
                        SIGNAL(core_core_executionScaleRowReg)[lane] == 0 &&
                        SIGNAL(core_core_vectorUnit_scaleReg)[lane] == 0,
                        "padded physical column unexpectedly carries data");
                ++padded_column_commits;
            }
        }
        pending_commit = rule_fired && (valid_mask & 1U) != 0;
        pending_write = SIGNAL(core_core_accumulator_banks_0__024EN) &&
                        SIGNAL(core_core_accumulator_banks_0__024WE);
        if (pending_commit) {
            require(commits < 4, "extra VectorUnit contribution");
            const uint32_t metadata = test.metadata[commits/2];
            const unsigned command = SIGNAL(core_core_commandReg);
            require(SIGNAL(core_core_vectorUnit_validReg) == 1, "unexpected valid lane mask");
            require(SIGNAL(core_core_vectorUnit_operationReg) == test.op && (command & 7) == test.op,
                    "SCU operation was not consumed by the VectorUnit");
            require(signed32(SIGNAL(core_core_vectorUnit_partialReg)[0]) == test.partial[commits],
                    "PE partial changed before SCU");
            require(SIGNAL(core_core_executionScaleRowReg)[0] == metadata &&
                    SIGNAL(core_core_vectorUnit_scaleReg)[0] == metadata, "SCU metadata snapshot mismatch");
            require(SIGNAL(core_core_matrixFragmentKStartReg) == commits*16 &&
                    SIGNAL(core_core_matrixFragmentBlockIndexReg) == commits/2,
                    "fragment/block boundary mismatch");
            require(((command >> 3) & 1) == (commits != 0) &&
                    SIGNAL(core_core_matrixFragmentAccumulateReg) == (commits != 0),
                    "replace/accumulate reset at the wrong boundary");
            require((SIGNAL(core_core_destinationRowAddressesReg)[0] & RowMask) == 0,
                    "wrong accumulator destination");
            std::cout << "SCU_CORE_CONSUME case=" << test.name << " cycle=" << cycle
                      << " fragment=" << commits << " k_start=" << commits*16
                      << " block=" << commits/2 << " lane=0 valid_mask=1 op=" << unsigned(test.op)
                      << " pe_partial=" << signed32(SIGNAL(core_core_vectorUnit_partialReg)[0])
                      << " execution_metadata=" << SIGNAL(core_core_executionScaleRowReg)[0]
                      << " vector_metadata=" << SIGNAL(core_core_vectorUnit_scaleReg)[0]
                      << " accumulate=" << ((command >> 3) & 1) << " destination=0\n";
        }
        if (pending_write) {
            require(writes < 4 && writes < commits, "accumulator write lacks a captured SCU result");
            require(SIGNAL(core_core_accumulator_banks_0__024ADDR) == 0 &&
                    signed32(SIGNAL(core_core_accumulator_banks_0__024DI)) == test.accumulated[writes],
                    "architectural saturated write mismatch");
            require(SIGNAL(core_core_accumulator_pendingAccumulate) == (writes != 0) &&
                    SIGNAL(core_core_accumulator_pendingSaturating), "accumulator clamp control mismatch");
            std::cout << "SCU_CORE_ACC_WRITE case=" << test.name << " cycle=" << cycle
                      << " fragment=" << writes << " lane=0 destination=0 contribution="
                      << signed32(SIGNAL(core_core_accumulator_pendingContributions)[0])
                      << " accumulate=" << unsigned(SIGNAL(core_core_accumulator_pendingAccumulate))
                      << " saturating=" << unsigned(SIGNAL(core_core_accumulator_pendingSaturating))
                      << " old_valid=" << (writes != 0) << " old_bits="
                      << SIGNAL(core_core_accumulator_banks_0__DOT__DO_R)
                      << " final_write=" << signed32(SIGNAL(core_core_accumulator_banks_0__024DI)) << '\n';
        }
    }

    void after(Simulator &simulator) {
        const auto &root = *simulator.top->rootp;
        if (padded_mask) {
            require(SIGNAL(core_core_accumulator_pendingValids) == padded_mask,
                    "padded column validity changed at SCU capture");
            for (unsigned lane=1; lane<16; ++lane) if (padded_mask & (1U << lane))
                require(SIGNAL(core_core_accumulator_pendingContributions)[lane] == 0,
                        "padded column SCU contribution is not zero");
        }
        if (pending_commit) {
            require(SIGNAL(core_core_accumulator_pendingValids) == 1 &&
                    (SIGNAL(core_core_accumulator_pendingRows)[0] & RowMask) == 0 &&
                    signed32(SIGNAL(core_core_accumulator_pendingContributions)[0]) == test.contribution[commits],
                    "SCU contribution was not captured by architectural accumulator");
            require(SIGNAL(core_core_accumulator_pendingAccumulate) == (commits != 0) &&
                    SIGNAL(core_core_accumulator_pendingSaturating), "SCU commit mode mismatch");
            std::cout << "SCU_CORE_CAPTURE case=" << test.name << " cycle=" << cycle+1
                      << " fragment=" << commits << " actual_contribution="
                      << signed32(SIGNAL(core_core_accumulator_pendingContributions)[0])
                      << " pending_valid=1 destination=0\n";
            ++commits;
        }
        if (pending_write) {
            require(signed32(SIGNAL(core_core_accumulator_banks_0__DOT__RAM)[0]) == test.accumulated[writes],
                    "accumulator RAM did not retain actual write");
            ++writes;
        }
    }
};
#undef SIGNAL

void run(const Case &test) {
    const auto destroy = [](void *handle) { im2p_destroy(handle); };
    std::unique_ptr<void,decltype(destroy)> handle(im2p_create(),destroy);
    require(bool(handle), "simulator allocation");
    auto &simulator = *static_cast<Simulator *>(handle.get());
    im2p_matmul_descriptor_t descriptor{};
    descriptor.job_id = 7;
    descriptor.activation_base = ABase; descriptor.weight_base = WBase;
    descriptor.scale_base = SBase; descriptor.output_base = CBase;
    descriptor.activation_row_stride = 64; descriptor.weight_row_stride = 1;
    descriptor.scale_row_stride = 4; descriptor.output_row_stride = 4;
    descriptor.row_count = descriptor.column_count = 1;
    descriptor.reduction_count = 64;
    descriptor.tile_i_rows = descriptor.tile_j_columns = 1;
    descriptor.scale_total_k = 64; descriptor.scale_block_size = 32;
    descriptor.scale_context = 7; descriptor.vector_op = test.op;
    require(im2p_start_matmul(handle.get(), &descriptor) == 1, "matrix start");
    Monitor monitor{test};
    size_t outputs = 0, scale_requests = 0;
    bool done = false;
    for (size_t step = 0; step < 100000; ++step) {
        im2p_read_request_t request{};
        std::array<int8_t,16> values{};
        int status = im2p_activation_read_request(handle.get(), &request);
        require(status >= 0, "activation request");
        if (status == 1) {
            require(request.address >= ABase && request.address-ABase+request.element_count <= 64,
                    "activation address");
            for (size_t lane=0; lane<request.element_count; ++lane)
                values[lane] = test.a[(request.address-ABase+lane)/16];
            require(im2p_stage_activation_read_response(handle.get(),request.tag,values.data(),request.element_count)==1,
                    "activation response");
        }
        status = im2p_weight_read_request(handle.get(),&request);
        require(status >= 0,"weight request");
        if (status == 1) {
            require(request.address>=WBase && request.address-WBase<64 && request.element_count==1,"weight address");
            values[0]=test.w[(request.address-WBase)/16];
            require(im2p_stage_weight_read_response(handle.get(),request.tag,values.data(),1)==1,"weight response");
        }
        status = im2p_scale_read_request(handle.get(),&request);
        require(status >= 0,"scale request");
        if (status == 1) {
            require(request.address>=SBase && request.address-SBase<8 &&
                    (request.address-SBase)%4==0 && request.element_count==1,"scale carrier address");
            const uint32_t metadata=test.metadata[(request.address-SBase)/4];
            require(im2p_stage_scale_read_response(handle.get(),request.tag,&metadata,1)==1,"scale response");
            ++scale_requests;
        }
        im2p_write_request_t output{};
        std::array<int64_t,16> actual{};
        status=im2p_output_write_request_i64(handle.get(),&output,actual.data());
        require(status>=0,"output request");
        if (status == 1) {
            require(outputs==0 && output.address==CBase && output.element_count==1 &&
                    actual[0]==test.accumulated.back(),"actual final output mismatch");
            require(im2p_stage_output_write_response(handle.get(),output.tag)==1,"output ACK");
            ++outputs;
        }
        monitor.before(simulator);
        im2p_tick_staged(handle.get());
        monitor.after(simulator);
        if (im2p_matmul_done(handle.get())) { done=true; break; }
    }
    im2p_matrix_counters_t counters{};
    im2p_matrix_counters(handle.get(),&counters);
    require(done && outputs==1 && monitor.commits==4 && monitor.writes==4 &&
            monitor.rule_fires==64 && monitor.padded_column_commits==60 && scale_requests>=2 &&
            counters.fragments_completed==4 && counters.works_completed==1 &&
            counters.output_write_requests==1 && counters.output_write_responses==1,"completion/count mismatch");
    require(im2p_acknowledge_matmul(handle.get())==1,"matrix retirement");
    std::cout << "SCU_CORE_CASE_PASS case=" << test.name << " raw=" << test.accumulated.back()
              << " actual_vector_commits=" << monitor.commits << " actual_accumulator_writes=" << monitor.writes
              << " vector_rule_fires=" << monitor.rule_fires
              << " padded_column_commits=" << monitor.padded_column_commits
              << " scale_requests=" << scale_requests << " outputs=" << outputs
              << " fragments=" << counters.fragments_completed << " works=" << counters.works_completed << '\n';
}
} // namespace

int main() {
    try {
        require(kDim==16 && kActivationBits==8 && kWeightBits==8 && kAccumulatorBits==32,
                "monitor requires A8/W8/D16 signed32");
        require(std::strcmp(im2p_compiled_numerical_semantics_revision(),"signed-scu-sat-v2")==0,
                "monitor requires frozen S2 numerical revision");
        for (const auto &test : Cases) run(test);
        std::cout << "SCU_CORE_MONITOR_PASS cases=6 actual_vector_commits=24 actual_accumulator_writes=24"
                  << " final_raw_comparisons=6 production_rtl=1 monitor_mutations=0 physical_jobs=0\n";
        return 0;
    } catch (const std::exception &error) {
        std::cerr << "SCU_CORE_MONITOR_FAIL " << error.what() << '\n';
        return 1;
    }
}
