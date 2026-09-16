// Exercise the production C ABI with the existing independent RTL numerical
// fixtures. This adapter services memory only: it contains no GEMM/SCU oracle.
#include <algorithm>
#include <array>
#include <cstdint>
#include <iostream>
#include <stdexcept>
#include <vector>

namespace rtl_ffi {
#include "im2p_verilator.h"
}
#include "frontend_rtl_fixture.hpp"
#include "rmd_rtl_fixture.hpp"

namespace hp = im2p::gemmini_hp1;
namespace f = rtl_ffi;

static void require(bool condition, const char *message) {
  if (!condition) throw std::runtime_error(message);
}

struct Session {
  f::im2p_handle_t handle = f::im2p_create();
  Session() { require(handle != nullptr, "runtime creation failed"); }
  ~Session() { f::im2p_destroy(handle); }
  Session(const Session &) = delete;
  Session &operator=(const Session &) = delete;

  int run(const hp::WorkPlanV1 &plan, const std::vector<std::int8_t> &a,
          const std::vector<std::int8_t> &b, const std::vector<std::uint32_t> &scales,
          bool raw, std::vector<std::int32_t> &output, hp::FrontendRtlTiming &timing) {
    constexpr std::uint64_t a_base = 0x1000000000000000ULL;
    constexpr std::uint64_t b_base = 0x2000000000000000ULL;
    constexpr std::uint64_t s_base = 0x3000000000000000ULL;
    constexpr std::uint64_t c_base = 0x4000000000000000ULL;
    require(a.size() == plan.m * plan.k && b.size() == plan.k * plan.n, "fixture extents");
    const f::im2p_ffi_work_plan_t tile{plan.tile_i, plan.tile_j, plan.tile_k,
                                    plan.activation_rows_per_stripe};
    require(f::im2p_configure_work_plan(handle, &tile) == 1, "configure work plan");
    f::im2p_matmul_descriptor_t descriptor{};
    descriptor.job_id = 7;
    descriptor.activation_base = a_base;
    descriptor.weight_base = b_base;
    descriptor.scale_base = s_base;
    descriptor.output_base = c_base;
    descriptor.activation_row_stride = plan.k;
    descriptor.weight_row_stride = plan.n;
    descriptor.scale_row_stride = plan.n * sizeof(std::uint32_t);
    descriptor.output_row_stride = plan.n * sizeof(std::int32_t);
    descriptor.row_count = plan.m;
    descriptor.column_count = plan.n;
    descriptor.reduction_count = plan.k;
    descriptor.tile_i_rows = IM2P_DIM;
    descriptor.tile_j_columns = IM2P_DIM;
    descriptor.scale_total_k = plan.k;
    descriptor.scale_block_size = raw ? 1 : 32;
    descriptor.vector_op = raw ? 0 : 5;
    require(f::im2p_start_matmul(handle, &descriptor) == 1, "start fixture work");
    output.assign(plan.m * plan.n, 0);
    std::vector<bool> seen(output.size(), false);
    for (std::uint64_t iteration = 0; iteration < 10000000; ++iteration) {
      f::im2p_read_request_t read{};
      auto status = f::im2p_activation_read_request(handle, &read);
      require(status >= 0, "activation request fault");
      if (status == 1) {
        const auto offset = read.address - a_base;
        require(offset <= a.size() && read.element_count <= a.size() - offset, "activation extent");
        require(f::im2p_stage_activation_read_response(handle, read.tag, a.data() + offset,
                    read.element_count) == 1, "activation response");
      }
      status = f::im2p_weight_read_request(handle, &read);
      require(status >= 0, "weight request fault");
      if (status == 1) {
        const auto offset = read.address - b_base;
        require(offset <= b.size() && read.element_count <= b.size() - offset, "weight extent");
        require(f::im2p_stage_weight_read_response(handle, read.tag, b.data() + offset,
                    read.element_count) == 1, "weight response");
      }
      status = f::im2p_scale_read_request(handle, &read);
      require(status >= 0, "scale request fault");
      if (status == 1) {
        require(read.address >= s_base && (read.address - s_base) % 4 == 0, "scale alignment");
        const auto offset = (read.address - s_base) / 4;
        require(offset <= scales.size() && read.element_count <= scales.size() - offset, "scale extent");
        require(f::im2p_stage_scale_read_response(handle, read.tag, scales.data() + offset,
                    read.element_count) == 1, "scale response");
      }
      f::im2p_write_request_t write{};
      std::array<std::int64_t, IM2P_DIM> values{};
      status = f::im2p_output_write_request_i64(handle, &write, values.data());
      require(status >= 0, "output request fault");
      if (status == 1) {
        require(write.address >= c_base && (write.address - c_base) % 4 == 0, "output alignment");
        const auto offset = (write.address - c_base) / 4;
        require(offset <= output.size() && write.element_count <= output.size() - offset,
                "output extent");
        for (std::size_t lane = 0; lane < write.element_count; ++lane) {
          require(!seen[offset + lane], "intermediate or duplicate output visible");
          require(values[lane] >= INT32_MIN && values[lane] <= INT32_MAX, "INT32 result domain");
          output[offset + lane] = static_cast<std::int32_t>(values[lane]);
          seen[offset + lane] = true;
        }
        require(f::im2p_stage_output_write_response(handle, write.tag) == 1, "output response");
      }
      if (f::im2p_matmul_done(handle)) {
        require(std::all_of(seen.begin(), seen.end(), [](bool value) { return value; }), "missing output");
        timing = {f::im2p_work_start_cycle(handle), f::im2p_work_completion_cycle(handle)};
        require(timing.done_cycle - timing.start_cycle == f::im2p_last_completed_work_cycles(handle),
                "logical cycle interval");
        std::uint64_t checksum = 14695981039346656037ULL;
        for (auto value : output) { checksum ^= static_cast<std::uint32_t>(value); checksum *= 1099511628211ULL; }
        f::im2p_matrix_counters_t counters{};
        f::im2p_matrix_counters(handle, &counters);
        std::cout << "RUNTIME_FIXTURE raw=" << raw << " m=" << plan.m << " n=" << plan.n
                  << " k=" << plan.k << " start=" << timing.start_cycle << " done=" << timing.done_cycle
                  << " cycles=" << timing.done_cycle - timing.start_cycle << " checksum=" << checksum
                  << " reads=" << counters.activation_read_requests << ',' << counters.weight_read_requests
                  << ',' << counters.scale_read_requests << " stores=" << counters.output_write_responses << '\n';
        require(f::im2p_acknowledge_matmul(handle) == 1, "acknowledge fixture");
        return IM2P_OK;
      }
      f::im2p_tick_staged(handle);
    }
    throw std::runtime_error("existing fixture exceeded bounded runtime progress limit");
  }
};

static int dense(void *context, const hp::FrontendRtlWork &work,
                 std::vector<std::int32_t> &output, hp::FrontendRtlTiming &timing) {
  try {
    return static_cast<Session *>(context)->run(work.plan, work.activations, work.weights,
                                               work.carriers, false, output, timing);
  } catch (const std::exception &error) {
    std::cerr << "RUNTIME_CALLBACK_FAIL m=" << work.plan.m << " n=" << work.plan.n
              << " k=" << work.plan.k << " reason=" << error.what() << '\n';
    return IM2P_ERROR;
  }
}
static int raw(void *context, const hp::RmdRawWork &work,
               std::vector<std::int32_t> &output, std::uint64_t &cycles) {
  hp::FrontendRtlTiming timing{};
  const int status = static_cast<Session *>(context)->run(work.plan, work.activations,
                                                         work.weights, {}, true, output, timing);
  cycles = timing.done_cycle - timing.start_cycle;
  return status;
}

int main() {
  try {
    Session session;
    const hp::Capability capability{"runtime-test", "runtime-test", "hp1-fragment-sat32-v1",
      IM2P_OPERAND_BITS, IM2P_OPERAND_BITS, IM2P_DIM, 32, 32,
      IM2P_OPERAND_BITS == 4 ? hp::Packing::signed_int4_low_nibble_first : hp::Packing::signed_int8,
      true, true, true, 4, IM2P_BANK_ROWS, IM2P_ACC_ROWS,
      IM2P_DIM * IM2P_OPERAND_BITS / 8, IM2P_DIM * 4};
    bool failed = false;
    try { hp::run_frontend_ws_rtl_fixture(capability, &session, dense); }
    catch (const std::exception &error) {
      std::cerr << "DENSE_FIXTURE_FAIL " << error.what() << '\n';
      failed = true;
    }
    // Independent fixture groups must still run after a recorded failure.
    Session raw_session;
    hp::run_rmd_ws_rtl_fixture(capability, &raw_session, raw);
    if (failed) return 1;
    std::cout << "INTEGRATED_RUNTIME_FIXTURES_PASS\n";
    return 0;
  } catch (const std::exception &error) {
    std::cerr << "INTEGRATED_RUNTIME_FIXTURES_FAIL " << error.what() << '\n';
    return 1;
  }
}
