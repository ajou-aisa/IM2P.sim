// Test-only boundary capture. Execute the real shared tiler and HP1 host
// prepare callback, then intentionally reject before numerical submission.
// This is NOT a full GGML invocation or an RTL/cycle timing certificate.
// Match the production include discipline: the selected generated parameters
// must be loaded before gemmini.h's quoted, same-directory fallback header.
#include <gemmini_params.h>

#include "../../cycle/scheduled_work.hpp"
#include "gemmini.h"
#include "ggml-gemmini-args.h"
#include "ggml-gemmini-fpga.hpp"
#include "quants/act/meta.hpp"

#include <algorithm>
#include <array>
#include <iostream>
#include <optional>
#include <stdexcept>
#include <tuple>
#include <vector>

extern "C" void gemmini_log_debug_layer(const char *, const char *,
                                        ...) noexcept {}

namespace {
namespace hp = im2p::gemmini_hp1;
namespace lower = im2p::gemmini;

void require(bool condition, const char *message) {
  if (!condition)
    throw std::runtime_error(message);
}

struct Fixture {
  ggml_gemmini_args_t args{};
#if GGML_GEMMINI_WEIGHT_BITS == 4
  std::vector<block_q4_hp1> blocks;
#else
  std::vector<block_q8_hp1> blocks;
#endif
  std::vector<float> output;

  Fixture(size_t m, size_t n, size_t k)
      : blocks(n * (k / 32)), output(m * n, 91.0f) {
    require(k && k % 32 == 0, "fixture requires block-scaled K");
    args.I = m;
    args.J = n;
    args.K = k;
    args.block_size_k = 32;
    args.sA = k;
    args.sC = n;
    args.f_out = output.data();
    args.stride_f_out = n;
    args.col_stride_f_out = 1;
    args.residual_route = ggml::gemmini::residual::ResidualRoute::ws_packet;
    require(args.A.allocate(m, k, GGML_GEMMINI_ACTIVATION_BITS),
            "activation fixture allocation");
    args.A.zero_fill();
    args.act_quant.storage()
        .emplace<ggml::gemmini::quants::act::tensor::Meta>()
        .scale = 1.0f;
    for (auto &block : blocks) {
      block.channel_scale = 1.0f;
      block.m = 0;
#if GGML_GEMMINI_WEIGHT_BITS == 4
      std::fill(std::begin(block.qs), std::end(block.qs), uint8_t{0x88});
#endif
    }
#if GGML_GEMMINI_WEIGHT_BITS == 4
    args.weight_format = ggml_gemmini_args_t::im2p_weight_format_t::q4_hp1;
    args.q4_hp1_blocks = blocks.data();
    args.native_block_count = blocks.size();
    args.native_blocks_per_row = k / 32;
#else
    args.weight_format = ggml_gemmini_args_t::im2p_weight_format_t::q8_hp1;
    args.q8_hp1_blocks = blocks.data();
    args.q8_hp1_block_count = blocks.size();
    args.q8_hp1_blocks_per_row = k / 32;
#endif
    args.native_weight_bytes = blocks.size() * sizeof(blocks.front());
    // The only tile selection in this test is the actual external production
    // function. Neither Python nor the IM2P lowerer invents tile factors.
    ggml::gemmini::gemmini_set_tile_ws(&args);
    const auto geometry = args.activation_geometry();
    require(geometry.ok(), "shared production geometry rejected");
    args.activation_rows_per_stripe = geometry.geometry.stripe_rows;
  }
};

struct Capture {
  std::optional<hp::WorkPlanV1> plan;
  unsigned prepare_count = 0;
  static int prepare(void *opaque, const hp::WorkPlanV1 &value) {
    auto &self = *static_cast<Capture *>(opaque);
    ++self.prepare_count;
    self.plan = value;
    return IM2P_ERROR; // deliberate stop: no quantization, transport or RTL
  }
  static int full(void *, const im2p_matmul_desc_t *,
                  im2p_work_stats_extended_t *) {
    throw std::runtime_error("numerical submission must not be reached");
  }
  static int begin(void *, const im2p_stripe_work_desc_t *) {
    return IM2P_ERROR;
  }
  static int publish(void *, const im2p_activation_stripe_t *) {
    return IM2P_ERROR;
  }
  static int poll(void *, im2p_stripe_completion_extended_t *) {
    return IM2P_ERROR;
  }
  static int finish(void *, im2p_work_stats_extended_t *) { return IM2P_ERROR; }
  static int raw(void *, const hp::RmdRawWork &, std::vector<int32_t> &,
                 uint64_t &) {
    return IM2P_ERROR;
  }
  static int scu(void *, const hp::RmdScuWork &, std::vector<int32_t> &,
                 uint64_t &) {
    return IM2P_ERROR;
  }
};

bool same_plan(const hp::WorkPlanV1 &a, const hp::WorkPlanV1 &b) {
  return std::tie(a.m, a.n, a.k, a.tile_i, a.tile_j, a.tile_k,
                  a.activation_rows_per_stripe, a.mode, a.kind) ==
         std::tie(b.m, b.n, b.k, b.tile_i, b.tile_j, b.tile_k,
                  b.activation_rows_per_stripe, b.mode, b.kind);
}

std::pair<uint64_t, uint64_t> verify_lowering(const hp::WorkPlanV1 &p) {
  im2p_cycle_model_config_t c{};
  im2p_cycle_model_config_init(&c);
  c.hardware = {GGML_GEMMINI_ACTIVATION_BITS,
                GGML_GEMMINI_WEIGHT_BITS,
                DIM,
                32,
                32,
                BANK_NUM,
                BANK_ROWS,
                ACC_ROWS,
                DIM * GGML_GEMMINI_WEIGHT_BITS / 8,
                DIM * 4,
                4,
                2};
  im2p_cycle_request_t request{};
  im2p_cycle_request_init(&request);
  request.m = p.m;
  request.n = p.n;
  request.k = p.k;
  request.tile_i = p.tile_i;
  request.tile_j = p.tile_j;
  request.tile_k = p.tile_k;
  request.submission = IM2P_CYCLE_BLOCK_SUBMISSIONS;
  const auto expanded = im2p::cycle::expand_work(c, request);
  const lower::ScheduleConfig config{{p.m, p.n, p.k},
                                     {DIM, GGML_GEMMINI_ACTIVATION_BITS},
                                     {p.tile_i, p.tile_j, p.tile_k, p.m},
                                     {p.k, p.n, p.n * 4, p.n * 4, 0},
                                     true,
                                     true,
                                     false};
  require(expanded.planner.tile.tile_i == p.tile_i &&
              expanded.planner.tile.tile_j == p.tile_j &&
              expanded.planner.tile.tile_k == p.tile_k,
          "cycle expansion changed production factors");
  lower::LoopCursor cursor{};
  uint64_t loops = 0, fragments = 0;
  while (cursor.i < p.m) {
    const auto planned = lower::plan_loop(config, p.m, cursor);
    const auto &work = expanded.work.at(loops);
    const auto &actual = work.first_plan;
#define SAME(member)                                                           \
  require(planned.member == actual.member, "lowerer mismatch: " #member)
    SAME(i);
    SAME(j);
    SAME(k);
    SAME(is);
    SAME(js);
    SAME(ks);
    SAME(ip);
    SAME(jp);
    SAME(kp);
    SAME(order);
    SAME(fragment_base);
    SAME(first);
    SAME(last);
    SAME(accumulate);
    SAME(first_contribution);
    SAME(final_contribution);
    SAME(scale_first_block);
    SAME(scale_rows);
    SAME(scale_release_count);
    SAME(fragment_count);
    SAME(activation_packed_bytes);
    SAME(weight_packed_bytes);
    SAME(activation_host_bytes);
    SAME(weight_host_bytes);
    SAME(scale_packed_bytes);
    SAME(scale_host_bytes);
    SAME(final_output_bytes);
#undef SAME
    require(work.fragments.size() == planned.fragment_count,
            "fragment count changed");
    for (size_t index = 0; index < planned.fragment_count; ++index) {
      const auto a = lower::plan_fragment(config, planned, index);
      const auto &b = work.fragments.at(index).plan;
      require(std::tie(a.i, a.j, a.k, a.rows, a.columns, a.reduction,
                       a.fragment_index, a.output_index, a.scale_index,
                       a.first_contribution, a.accumulate,
                       a.final_contribution) ==
                  std::tie(b.i, b.j, b.k, b.rows, b.columns, b.reduction,
                           b.fragment_index, b.output_index, b.scale_index,
                           b.first_contribution, b.accumulate,
                           b.final_contribution),
              "cycle expansion changed a production-lowered fragment");
      ++fragments;
    }
    ++loops;
    lower::advance_loop(config, planned, cursor);
  }
  require(loops == expanded.work.size() && loops == expanded.planner_loops &&
              fragments == expanded.fragments,
          "lowering coverage incomplete");
  return {loops, fragments};
}

struct PublicProjection {
  std::array<size_t, 5> fields{};
  unsigned calls = 0;
  static int capture(void *opaque, const im2p_matmul_desc_t *d,
                     im2p_work_stats_extended_t *) {
    auto &self = *static_cast<PublicProjection *>(opaque);
    require(d, "missing public descriptor");
    self.fields = {d->m, d->n, d->k, d->tile_i_rows, d->tile_j_columns};
    ++self.calls;
    return IM2P_ERROR; // test observes descriptor construction, never values
  }
};

PublicProjection public_projection(ggml_gemmini_args_t &args) {
  PublicProjection observed;
  lower::Options options;
  options.numerical_contract = lower::NumericalContract::scu_final_integer;
  options.full_executor_context = &observed;
  options.full_executor = PublicProjection::capture;
  auto started = lower::execute(&args, lower::Mode::full, options);
  require(started.status.ok() && started.run,
          "public descriptor capture start");
  const auto result = lower::fence(*started.run);
  require(!result.status.ok() && observed.calls == 1,
          "expected intentional capture rejection");
  return observed;
}
} // namespace

int main() {
  try {
    unsigned case_number = 0;
    for (const auto shape : std::array<std::array<size_t, 3>, 4>{
             {{1, 1, 32}, {2, 3, 64}, {65, 67, 96}, {129, 129, 96}}}) {
      Fixture fixture(shape[0], shape[1], shape[2]);
      for (const bool pipeline : {false, true}) {
        auto &args = fixture.args;
        Capture captured;
        const ggml_gemmini_hp1_executor binding{
            ggml_gemmini_hp1_capability(),
            &captured,
            Capture::prepare,
            Capture::full,
            {&captured, Capture::begin, Capture::publish, Capture::poll,
             Capture::finish},
            Capture::raw,
            Capture::scu};
        require(ggml_gemmini_hp1_bind_executor(binding),
                "bind capture callbacks");
        bool quantized = false;
        const bool executed = ggml_gemmini_fpga_execute(
            args, pipeline, [&] { quantized = true; }, "schedule-authority",
            true);
        ggml_gemmini_hp1_unbind_executor();
        require(!executed && !quantized && captured.prepare_count == 1 &&
                    captured.plan,
                "prepare capture did not stop before execution");
        require(ggml_gemmini_fpga_last_error() ==
                    "GEMMINI_HP1 executor rejected the host work plan",
                "unexpected rejection before/after prepare");
        const hp::WorkPlanV1 expected{args.I,
                                      args.J,
                                      args.K,
                                      args.tile_I,
                                      args.tile_J,
                                      args.tile_K,
                                      args.activation_rows_per_stripe,
                                      pipeline ? hp::Mode::pipeline
                                               : hp::Mode::full,
                                      hp::WorkKind::dense_hp1_final};
        require(same_plan(*captured.plan, expected),
                "host changed selected geometry");
        const auto decoded =
            hp::decode_work_plan_v1(hp::encode_work_plan_v1(*captured.plan));
        require(same_plan(decoded, expected),
                "companion codec changed geometry");
        const auto [loops, fragments] = verify_lowering(decoded);
        std::cout << "{\"kind\":\"prepare_capture\",\"case\":" << ++case_number
                  << ",\"bits\":" << GGML_GEMMINI_ACTIVATION_BITS
                  << ",\"dim\":" << DIM << ",\"shape\":[" << args.I << ','
                  << args.J << ',' << args.K << "],\"tile\":[" << args.tile_I
                  << ',' << args.tile_J << ',' << args.tile_K
                  << "],\"stripe_rows\":" << args.activation_rows_per_stripe
                  << ",\"mode\":\"" << (pipeline ? "pipeline" : "full")
                  << "\",\"companion_exact\":true,\"lowering_exact\":true,"
                     "\"planner_loops\":"
                  << loops << ",\"fragments\":" << fragments
                  << ",\"numerical_submission\":\"NOT_RUN\"}\n";
      }
    }
    // A counterexample to treating the public scalar descriptor as complete
    // selected geometry. These are caller-supplied test alternatives, not a
    // tiler.
    Fixture generic(129, 129, 96);
    const auto first = public_projection(generic.args);
    require(generic.args.tile_I != 1 || generic.args.tile_J != 1 ||
                generic.args.tile_K != 1,
            "counterexample needs a nontrivial selected plan");
    generic.args.tile_I = 1;
    generic.args.tile_J = 1;
    generic.args.tile_K = 1;
    generic.args.activation_rows_per_stripe = DIM;
    const hp::WorkPlanV1 alternate{129,
                                   129,
                                   96,
                                   1,
                                   1,
                                   1,
                                   DIM,
                                   hp::Mode::full,
                                   hp::WorkKind::dense_hp1_final};
    (void)verify_lowering(alternate); // both plans fit the hardware contract
    const auto second = public_projection(generic.args);
    require(first.fields == second.fields && first.fields[3] == DIM &&
                first.fields[4] == DIM,
            "public companion contract changed: re-audit geometry propagation");
    std::cout
        << "{\"kind\":\"generic_projection\",\"bits\":"
        << GGML_GEMMINI_ACTIVATION_BITS << ",\"dim\":" << DIM
        << ",\"distinct_factors_same_descriptor\":true,"
           "\"production_certificate\":\"BLOCKED_MISSING_TILE_COMPANION\"}\n";
    return 0;
  } catch (const std::exception &error) {
    ggml_gemmini_hp1_unbind_executor();
    std::cerr << "SCHEDULE_AUTHORITY_PROBE_FAIL: " << error.what() << '\n';
    return 1;
  }
}
