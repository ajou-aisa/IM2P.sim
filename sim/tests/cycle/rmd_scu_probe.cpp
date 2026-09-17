// Real packet -> compact descriptor -> public ABI -> integrated RTL
// certificate.
#define main im2p_prepare_fixture_main
#include "schedule_authority_probe.cpp"
#undef main
#include "accepted_work_observer.h"
#include "quants/common/hp1_scu.hpp"
#include "quants/common/weight_reader.hpp"
#include "residual/rmd/rmd-builder.hpp"
#include "residual/rmd/rmd-compose.hpp"
#include "residual/rmd/rmd-executor.hpp"
#include "residual/rmd/rmd-im2p-executor.hpp"
#include "residual/rmd/rmd-reference.hpp"
#include "rmd_scu_observer.h"
#include <cstring>
#include <filesystem>
#include <fstream>
#include <memory>

namespace {
namespace rmd = ggml::gemmini::rmd;
namespace wr = ggml::gemmini::quants::wreader;
namespace route = ggml::gemmini::quants::wroute;
namespace numerical = ggml::gemmini::quants::hp1;
namespace fs = std::filesystem;
const std::vector<int64_t> &correction_values(const rmd::Correction &value) {
  return std::get<rmd::BlockScaledInt64Correction>(value).values;
}
using Work = im2p_accepted_work_observation_t;
using Event = im2p_rmd_scu_observation_t;
using Simulator = std::unique_ptr<im2p_sim_t, decltype(&im2p_sim_destroy)>;

struct Trace {
  std::vector<Work> work;
  std::vector<Event> events;
  bool failed = false;
  static void on_work(void *p, const Work *r) noexcept {
    auto &s = *static_cast<Trace *>(p);
    try {
      s.work.push_back(*r);
    } catch (...) {
      s.failed = true;
    }
  }
  static void on_event(void *p, const Event *r) noexcept {
    auto &s = *static_cast<Trace *>(p);
    try {
      s.events.push_back(*r);
    } catch (...) {
      s.failed = true;
    }
  }
  void start() {
    im2p_test_set_work_observer(on_work, this);
    im2p_test_set_rmd_scu_observer(on_event, this);
  }
  static void stop() {
    im2p_test_set_work_observer(nullptr, nullptr);
    im2p_test_set_rmd_scu_observer(nullptr, nullptr);
  }
};

struct Input {
  im2p_matmul_desc_t descriptor{};
  im2p_production_geometry_v1_t geometry{};
  std::vector<int8_t> a, w;
  std::vector<uint32_t> carriers;
  std::vector<int64_t> output;
  std::vector<unsigned> seen;
  im2p_provider_t upstream{};
  bool relay = false;
  static int weight(void *p, size_t k, size_t j, size_t count, int8_t *out) {
    auto &x = *static_cast<Input *>(p);
    if (!out || k >= x.descriptor.k || j > x.descriptor.n ||
        count > x.descriptor.n - j)
      return IM2P_INVALID_LAYOUT;
    std::copy_n(x.w.data() + k * x.descriptor.weight_row_stride_bytes + j,
                count, out);
    return IM2P_OK;
  }
  static int scale(void *p, size_t block, size_t j, size_t count,
                   uint32_t *out) {
    auto &x = *static_cast<Input *>(p);
    if (!out || block || j > x.carriers.size() || count > x.carriers.size() - j)
      return IM2P_INVALID_LAYOUT;
    std::copy_n(x.carriers.data() + j, count, out);
    return IM2P_OK;
  }
  static int write(void *p, size_t block, size_t i, size_t j, size_t count,
                   const int64_t *values, uint32_t domain) {
    auto &x = *static_cast<Input *>(p);
    if (!values || block || domain != IM2P_OUTPUT_SCU_FINAL ||
        i >= x.descriptor.m || j > x.descriptor.n || count > x.descriptor.n - j)
      return IM2P_INVALID_LAYOUT;
    for (size_t c = 0; c < count; ++c) {
      const size_t at = i * x.descriptor.n + j + c;
      if (x.seen.at(at)++ || values[c] < INT32_MIN || values[c] > INT32_MAX)
        return IM2P_ERROR;
      x.output.at(at) = values[c];
    }
    return x.relay ? x.upstream.write_output(x.upstream.context, block, i, j,
                                             count, values, domain)
                   : IM2P_OK;
  }
  static Input capture(const im2p_matmul_desc_t &d,
                       const im2p_production_geometry_v1_t &g) {
    require(d.vector_op == IM2P_VECTOR_LEFT_SHIFT &&
                d.output_domain == IM2P_OUTPUT_SCU_FINAL &&
                d.block_size == 32 && d.k <= 32 && d.provider.read_scale &&
                d.provider.read_weight_i8,
            "production residual did not construct normal HP1 work");
    Input x;
    x.descriptor = d;
    x.geometry = g;
    x.upstream = d.provider;
    x.relay = true;
    x.a.resize(d.m * d.activation_row_stride_bytes);
    x.w.resize(d.k * d.weight_row_stride_bytes);
    x.carriers.resize(d.n);
    x.output.resize(d.m * d.n);
    x.seen.resize(d.m * d.n);
    for (size_t i = 0; i < d.m; ++i)
      std::copy_n(static_cast<const int8_t *>(d.activations) +
                      i * d.activation_row_stride_bytes,
                  d.k, x.a.data() + i * d.activation_row_stride_bytes);
    for (size_t k = 0; k < d.k; ++k)
      require(d.provider.read_weight_i8(
                  d.provider.context, k, 0, d.n,
                  x.w.data() + k * d.weight_row_stride_bytes) == IM2P_OK,
              "capture selected weights");
    require(d.provider.read_scale(d.provider.context, 0, 0, d.n,
                                  x.carriers.data()) == IM2P_OK,
            "capture original HP1 carriers");
    return x;
  }
  int run(Trace &trace, im2p_work_stats_extended_t &stats,
          bool dense_construction = false) {
    Simulator sim(im2p_sim_create(), im2p_sim_destroy);
    require(bool(sim), "create real Gemmini simulator");
    std::fill(output.begin(), output.end(), INT64_MIN);
    std::fill(seen.begin(), seen.end(), 0);
    auto d = descriptor;
    if (dense_construction) {
      // Independent normal dense public-API construction; no residual executor.
      // Matrix/storage facts and selected geometry are identical by design.
      d = {};
      d.abi_version = IM2P_ABI_VERSION;
      d.activation_bits = d.weight_bits = GGML_GEMMINI_WEIGHT_BITS;
      d.activation_storage_bytes = d.weight_storage_bytes = 1;
      d.dim = DIM;
      d.m = geometry.m;
      d.n = geometry.n;
      d.k = geometry.k;
      d.activation_row_stride_bytes = descriptor.activation_row_stride_bytes;
      d.weight_row_stride_bytes = descriptor.weight_row_stride_bytes;
      d.output_row_stride = descriptor.output_row_stride;
      d.tile_i_rows = std::min<size_t>(d.m, DIM);
      d.tile_j_columns = std::min<size_t>(d.n, DIM);
      d.block_size = 32;
      d.scale_total_k = d.k;
      d.scale_row_stride = d.n;
      d.scale_valid_columns = d.n;
      d.vector_op = IM2P_VECTOR_LEFT_SHIFT;
      d.output_domain = IM2P_OUTPUT_SCU_FINAL;
      d.work_context = descriptor.work_context;
    }
    d.activations = a.data();
    d.provider = {this, weight, nullptr, scale, write};
    trace.start();
    const auto result =
        im2p_execute_matmul_planned(sim.get(), &d, &geometry, &stats);
    Trace::stop();
    require(!trace.failed, "passive observer allocation");
    return result;
  }
};

template <class T> void array_json(std::ostream &out, const std::vector<T> &v) {
  out << '[';
  for (size_t i = 0; i < v.size(); ++i) {
    if (i)
      out << ',';
    out << +v[i];
  }
  out << ']';
}
void geometry_json(std::ostream &out, const im2p_production_geometry_v1_t &g) {
  out << '{';
#define G(k) out << "\"" #k "\":" << g.k << ','
  G(version);
  G(struct_size);
  G(activation_bits);
  G(weight_bits);
  G(dim);
  G(scope);
  G(m);
  G(n);
  G(k);
  G(tile_i_count);
  G(tile_j_count);
  G(tile_k_count);
  G(stripe_rows);
  G(row_begin);
  G(row_count);
#undef G
  out << "\"stripe_id\":" << g.stripe_id << '}';
}
void work_json(std::ostream &out, const Work &x) {
  out << "{\"geometry\":";
  geometry_json(out, x.geometry);
  out << ',';
#define F(k) out << "\"" #k "\":" << x.k << ','
  F(event);
  F(cycle);
  F(explicit_geometry);
  F(source_m);
  F(source_n);
  F(source_k);
  F(activation_host_stride);
  F(weight_host_stride);
  F(scale_host_stride);
  F(output_host_stride);
  F(lowerer_tile_i);
  F(lowerer_tile_j);
  F(lowerer_tile_k);
  F(stripe_id);
  F(stripe_row_begin);
  F(stripe_row_count);
  F(host_slot);
  F(i);
  F(j);
  F(k);
  F(rows);
  F(columns);
  F(reduction);
  F(order);
  F(fragment_count);
  F(max_i);
  F(max_j);
  F(max_k);
  F(pad_i);
  F(pad_j);
  F(pad_k);
  F(a_address);
  F(b_address);
  F(c_address);
  F(scale_address);
  F(a_stride);
  F(b_stride);
  F(c_stride);
  F(scale_base);
  F(scale_generation);
  F(fragment_base);
  F(work_base);
  F(accumulate);
  F(final_fragment);
  F(first_loop);
  F(final_loop);
  F(logical_work_id);
  F(rmd_raw);
  F(start_cycle);
  F(done_cycle);
  F(elapsed_cycles);
  F(load_requests);
  F(load_responses);
  F(store_requests);
  F(store_responses);
  F(scale_requests);
#undef F
  out << "\"scale_responses\":" << x.scale_responses << '}';
}
std::string accepted_signature(const Trace &trace) {
  std::ostringstream out;
  for (const auto &w : trace.work)
    if (w.event == IM2P_OBSERVE_ACCEPTED)
      work_json(out, w);
  return out.str();
}
size_t verify_lowerer(const Input &x, const Trace &trace) {
  const auto &g = x.geometry;
  const auto &d = x.descriptor;
  lower::ScheduleConfig cfg{
      {d.m, d.n, d.k},
      {DIM, GGML_GEMMINI_ACTIVATION_BITS},
      {g.tile_i_count, g.tile_j_count, g.tile_k_count, g.stripe_rows},
      {d.activation_row_stride_bytes, d.weight_row_stride_bytes,
       d.scale_row_stride * 4, d.output_row_stride * 4, 0},
      true,
      true,
      false};
  lower::LoopCursor cursor{};
  size_t count = 0;
  for (const auto &w : trace.work)
    if (w.event == IM2P_OBSERVE_ACCEPTED) {
      const auto p = lower::plan_loop(cfg, d.m, cursor);
      require(w.rmd_raw == 0 && w.explicit_geometry == 1,
              "residual selected raw/default work");
      require(w.lowerer_tile_i == g.tile_i_count &&
                  w.lowerer_tile_j == g.tile_j_count &&
                  w.lowerer_tile_k == g.tile_k_count &&
                  std::memcmp(&g, &w.geometry, sizeof(g)) == 0,
              "selected compact geometry changed");
      require(std::tie(w.i, w.j, w.k, w.rows, w.columns, w.reduction,
                       w.fragment_count) ==
                  std::tie(p.i, p.j, p.k, p.is, p.js, p.ks, p.fragment_count),
              "lowerer/accepted compact work differs");
      require(w.max_i == p.ip / DIM && w.max_j == p.jp / DIM &&
                  w.max_k == p.kp / DIM && w.pad_i == p.ip - p.is &&
                  w.pad_j == p.jp - p.js && w.pad_k == p.kp - p.ks &&
                  w.fragment_base == p.fragment_base &&
                  w.accumulate == p.accumulate &&
                  w.final_fragment == p.final_contribution &&
                  w.first_loop == p.first && w.final_loop == p.last,
              "accepted HP1 fragment semantics changed");
      lower::advance_loop(cfg, p, cursor);
      ++count;
    }
  require(count && cursor.i == d.m, "missing compact accepted work");
  return count;
}

struct Certificate {
  fs::path root;
  std::ofstream index;
  size_t pairs = 0, numerical_cases = 0, bound_cases = 0;
  std::string active_case;
  explicit Certificate(fs::path p)
      : root(std::move(p)), index(root / "invocations.jsonl") {
    require(bool(index), "create invocation evidence");
  }
  static int planned(void *opaque, const im2p_matmul_desc_t *d,
                     const im2p_production_geometry_v1_t *g,
                     im2p_work_stats_extended_t *stats) {
    auto &self = *static_cast<Certificate *>(opaque);
    require(d && g && stats, "planned residual callback inputs");
    Input input = Input::capture(*d, *g);
    Trace residual, dense;
    im2p_work_stats_extended_t rs{}, ds{};
    require(input.run(residual, rs) == IM2P_OK,
            "real residual HP1 invocation failed");
    const auto residual_values = input.output;
    input.relay = false;
    require(input.run(dense, ds, true) == IM2P_OK,
            "paired dense HP1 invocation failed");
    require(input.output == residual_values,
            "main/residual integer output differs");
    require(accepted_signature(residual) == accepted_signature(dense),
            "paired actual accepted descriptors differ");
    require(rs.base.work_total_cycles == ds.base.work_total_cycles &&
                rs.base.scale_read_requests == ds.base.scale_read_requests &&
                rs.base.completed_fragments == ds.base.completed_fragments,
            "paired cycles/counters differ");
    require(residual.events.size() == dense.events.size(),
            "paired event count differs");
    for (size_t i = 0; i < residual.events.size(); ++i) {
      const auto &a = residual.events[i], &b = dense.events[i];
      require(a.cycle == b.cycle && a.event_mask == b.event_mask &&
                  std::equal(a.scu_data, a.scu_data + DIM, b.scu_data),
              "paired SCU/event stream differs");
    }
    const auto accepted = verify_lowerer(input, residual);
    const auto dir = self.root / ("work-" + std::to_string(self.pairs));
    fs::create_directory(dir);
    std::ofstream wfile(dir / "accepted.jsonl"), efile(dir / "events.csv"),
        jfile(dir / "input.json");
    for (const auto &w : residual.work)
      if (w.event == IM2P_OBSERVE_ACCEPTED || w.event == IM2P_OBSERVE_DONE) {
        work_json(wfile, w);
        wfile << '\n';
      }
    for (const auto &event : residual.events) {
      efile << event.cycle << ',' << event.event_mask;
      if (event.event_mask & (uint64_t{1} << 11))
        for (unsigned lane = 0; lane < DIM; ++lane)
          efile << ',' << event.scu_data[lane];
      efile << '\n';
    }
    jfile << "{\"geometry\":";
    geometry_json(jfile, input.geometry);
    jfile << ",\"original_block_id\":" << d->work_context
          << ",\"a_stride\":" << d->activation_row_stride_bytes
          << ",\"b_stride\":" << d->weight_row_stride_bytes
          << ",\"c_stride\":" << d->output_row_stride * 4 << ",\"a\":";
    array_json(jfile, input.a);
    jfile << ",\"w\":";
    array_json(jfile, input.w);
    jfile << ",\"carriers\":";
    array_json(jfile, input.carriers);
    jfile << ",\"output\":";
    array_json(jfile, input.output);
    jfile << "}\n";
    self.index << "{\"case\":\"" << self.active_case << "\",\"directory\":\""
               << dir.string()
               << "\",\"status\":\"PASS\",\"selected_k\":" << d->k
               << ",\"original_block_id\":" << d->work_context
               << ",\"accepted_work\":" << accepted
               << ",\"fragments\":" << rs.base.completed_fragments
               << ",\"provider_cycles\":" << rs.base.work_total_cycles
               << ",\"main_residual_descriptors_equal\":true,\"selected_events_"
                  "equal\":true,\"scu_data_equal\":true,\"rmd_raw\":false}\n";
    ++self.pairs;
    *stats = rs;
    return IM2P_OK;
  }
  static int bound(void *opaque, const hp::RmdScuWork &work,
                   std::vector<int32_t> &out, uint64_t &cycles) {
    auto &self = *static_cast<Certificate *>(opaque);
    require(work.plan.kind == hp::WorkKind::dense_hp1_final &&
                work.host_slot == 0,
            "bound residual selected alternate numerical datapath");
    Input input;
    auto &d = input.descriptor;
    d.abi_version = IM2P_ABI_VERSION;
    d.activation_bits = d.weight_bits = GGML_GEMMINI_WEIGHT_BITS;
    d.activation_storage_bytes = d.weight_storage_bytes = 1;
    d.dim = DIM;
    d.m = work.plan.m;
    d.n = work.plan.n;
    d.k = work.plan.k;
    d.activation_row_stride_bytes = d.k;
    d.weight_row_stride_bytes = d.n;
    d.output_row_stride = d.n;
    d.tile_i_rows = std::min<size_t>(d.m, DIM);
    d.tile_j_columns = std::min<size_t>(d.n, DIM);
    d.block_size = 32;
    d.scale_total_k = d.k;
    d.scale_row_stride = d.n;
    d.scale_valid_columns = d.n;
    d.vector_op = IM2P_VECTOR_LEFT_SHIFT;
    d.output_domain = IM2P_OUTPUT_SCU_FINAL;
    d.work_context = work.original_block_id;
    input.geometry = work.geometry;
    input.a = work.activations;
    input.w = work.weights;
    input.carriers = work.carriers;
    input.output.resize(d.m * d.n);
    input.seen.resize(d.m * d.n);
    Trace trace;
    im2p_work_stats_extended_t stats{};
    require(input.run(trace, stats) == IM2P_OK,
            "bound scaled work failed real RTL");
    verify_lowerer(input, trace);
    out.assign(input.output.begin(), input.output.end());
    cycles = stats.base.work_total_cycles;
    ++self.bound_cases;
    return IM2P_OK;
  }
};

void set_weight(Fixture &f, size_t block, size_t col, size_t k, int value) {
  auto &w = f.blocks.at(col * (f.args.K / 32) + block);
#if GGML_GEMMINI_WEIGHT_BITS == 4
  const auto shift = (k / 16) * 4;
  w.qs[k % 16] = uint8_t((w.qs[k % 16] & ~(15u << shift)) |
                         ((unsigned(value + 8) & 15u) << shift));
#else
  w.qs[k] = static_cast<int8_t>(value);
#endif
}
void packet_case(Certificate &cert, const std::string &name, size_t compact_k,
                 int pattern, int16_t exponent, bool multiple_blocks = false) {
  const size_t rows = pattern == 5 ? 2 : 1, n = 3, original_k = 64;
  Fixture f(rows, n, original_k);
  for (size_t j = 0; j < n; ++j)
    for (size_t b = 0; b < 2; ++b) {
      auto &weight = f.blocks[j * 2 + b];
      weight.m =
          j == 2 ? INT16_MIN
                 : static_cast<int16_t>(exponent == 1 && j == 1 ? 2 : exponent);
      weight.channel_scale = static_cast<float>(j + 1) * 0.25f;
      for (size_t k = 0; k < 32; ++k) {
        int code = j == 1 ? -1 : 1;
        if (pattern == 2 && k < 16)
          code = 0;
        if (pattern == 3 && k >= 16)
          code = 0;
        if (pattern == 4 && k >= 16)
          code = -code;
        if (pattern == 7)
          code = 0;
        set_weight(f, b, j, k, code);
      }
    }
  f.args.act_quant.storage()
      .emplace<ggml::gemmini::quants::act::tensor::Meta>()
      .scale = 0.5f;
  rmd::RmdStripeBuilder builder;
  builder.reset(0, 0, rows, original_k, n, GGML_GEMMINI_ACTIVATION_BITS);
  for (size_t b = multiple_blocks ? 0 : 1; b < 2; ++b)
    for (size_t row = 0; row < rows; ++row)
      for (size_t k = 0; k < compact_k; ++k) {
        int32_t value = pattern == 1 ? -1 : 1;
        if (pattern == 5) {
          const int32_t v[] = {INT32_MAX, INT32_MIN, 129,   -129,
                               8,         -8,        65537, -65537};
          value = v[(k + row) % 8];
        }
        require(builder.add_residual(row, b * 32 + k, value),
                "build full-int32 residual packet");
      }
  const auto packet = builder.finish();
  require(bool(packet), "finish residual packet");
  std::vector<int64_t> expected;
  require(rmd::reference_hp1_packet_correction(f.args, *packet, expected) ==
              rmd::RmdStatus::success,
          "independent fragment-SCU reference failed");
  cert.active_case = name;
  const auto before = cert.pairs;
  rmd::Correction correction;
  rmd::RmdExecutionMetrics metrics;
  const rmd::Im2pFullExecutor executor{&cert, nullptr, Certificate::planned};
  require(rmd::execute_rmd_stripe_im2p(nullptr, f.args, *packet, correction,
                                       &metrics,
                                       &executor) == rmd::RmdStatus::success,
          "production packet HP1 SCU execution failed");
  require(correction_values(correction) == expected,
          "packet SCU/radix reference mismatch");
  require(metrics.im2p_dot_calls == cert.pairs - before,
          "logical residual call count mismatch");
  if (pattern != 5 && !multiple_blocks)
    require(metrics.im2p_dot_calls == 1,
            "compact K split into host-visible calls");
  rmd::Correction checked;
  require(rmd::execute_rmd_stripe_reference(f.args, *packet, checked) ==
                  rmd::RmdStatus::success &&
              correction_values(checked) == expected,
          "checked software/reference disagreement");
  Simulator generic(im2p_sim_create(), im2p_sim_destroy);
  rmd::Correction direct;
  require(rmd::execute_rmd_stripe_im2p(generic.get(), f.args, *packet,
                                       direct) == rmd::RmdStatus::success &&
              correction_values(direct) == expected,
          "default generic path differs from intercepted production path");
  hp::RmdExecutorContext bound{
      ggml_gemmini_hp1_capability(), &cert, nullptr, 0, 0, Certificate::bound};
  rmd::Correction bound_output;
  require(hp::execute_rmd_packet(bound, f.args, *packet, bound_output) ==
                  rmd::RmdStatus::success &&
              correction_values(bound_output) == expected,
          "bound/generic packet numerical mismatch");
  std::vector<float> merged(rows * n, 1.0f);
  require(rmd::merge_rmd_correction_to(f.args, merged.data(), *packet,
                                       correction) == rmd::RmdStatus::success,
          "final floating reconstruction failed");
  for (size_t i = 0; i < rows * n; ++i)
    require(
        merged[i] == static_cast<float>(1.0 + double(expected[i]) * 0.5 *
                                                  double((i % n + 1) * 0.25)),
        "host reapplied an integer HP1 factor or changed final float factors");
  ++cert.numerical_cases;
  std::cout
      << "{\"case\":\"" << name
      << "\",\"status\":\"PASS\",\"compact_k\":" << compact_k
      << ",\"logical_calls\":" << metrics.im2p_dot_calls
      << ",\"host_integer_block_multiply\":false,\"reference_equal\":true}\n";
}
void carrier_and_failure_tests(Certificate &cert) {
  Fixture f(1, 3, 64);
  auto getplan = [&] {
    return route::resolve_weight_route_plan(
        f.args, route::WeightScaleInfoMode::ResidualHp1Scu);
  };
  for (int exponent : {INT16_MIN, 0, 1, 31, 63, 32767}) {
    f.blocks[1].m = static_cast<int16_t>(exponent);
    const auto plan = getplan();
    require(plan.valid, "valid original HP1 carrier rejected");
    const auto x = wr::read_hp1_carrier_validated(f.args, plan, 0, 1);
    require(x.ok() && x.carrier == (exponent == INT16_MIN ? 0x80000000u
                                                          : uint32_t(exponent)),
            "original HP1 exponent/sentinel incorrectly encoded");
  }
  f.blocks[1].m = -1;
  require(!getplan().valid, "invalid negative HP1 metadata accepted");
  f.blocks[1].m = 0;
  rmd::RmdStripeBuilder b;
  b.reset(0, 0, 1, 64, 3, GGML_GEMMINI_ACTIVATION_BITS);
  // Two original blocks are two logical calls. The statistics overflow fault
  // intentionally requires a second addition; one compact K is no longer split.
  require(b.add_residual(0, 0, 1) && b.add_residual(0, 32, 1),
          "failure packet");
  const auto packet = b.finish();
  require(bool(packet), "failure packet finish");
  const auto before = cert.pairs;
  const rmd::Im2pFullExecutor missing{&cert, nullptr, nullptr};
  rmd::Correction out = rmd::BlockScaledInt64Correction{{11, 12, 13}};
  require(rmd::execute_rmd_stripe_im2p(nullptr, f.args, *packet, out, nullptr,
                                       &missing) != rmd::RmdStatus::success &&
              correction_values(out) == std::vector<int64_t>{11, 12, 13} &&
              cert.pairs == before,
          "missing backend silently fell back or changed output");
  hp::RmdExecutorContext raw_only{ggml_gemmini_hp1_capability()};
  require(hp::execute_rmd_packet(raw_only, f.args, *packet, out) ==
              rmd::RmdStatus::unsupported_route,
          "bound production silently selected raw fallback");
  for (auto fault : {rmd::Im2pProviderTestFault::read_failure,
                     rmd::Im2pProviderTestFault::write_failure,
                     rmd::Im2pProviderTestFault::watchdog,
                     rmd::Im2pProviderTestFault::duplicate_output,
                     rmd::Im2pProviderTestFault::missing_output,
                     rmd::Im2pProviderTestFault::output_index,
                     rmd::Im2pProviderTestFault::stats_overflow}) {
    Simulator sim(im2p_sim_create(), im2p_sim_destroy);
    rmd::RmdExecutionMetrics metrics;
    metrics.packet_call_count = 37;
    const auto status = rmd::execute_rmd_stripe_im2p_for_test(
        sim.get(), f.args, *packet, out, &metrics, fault);
    if (status == rmd::RmdStatus::success ||
        correction_values(out) != std::vector<int64_t>{11, 12, 13} ||
        metrics.packet_call_count != 37)
      std::cerr << "fault=" << unsigned(fault) << " status=" << unsigned(status)
                << " committed_packets=" << metrics.packet_call_count << '\n';
    require(status != rmd::RmdStatus::success &&
                correction_values(out) == std::vector<int64_t>{11, 12, 13} &&
                metrics.packet_call_count == 37,
            "transactional residual fault was not atomic");
  }
  std::cout
      << "{\"test\":\"carrier-and-transactionality\",\"status\":\"PASS\"}\n";
}
} // namespace

int main(int argc, char **argv) {
  try {
    require(argc == 2, "external evidence directory required");
    fs::create_directories(argv[1]);
    Certificate cert(argv[1]);
    carrier_and_failure_tests(cert);
    packet_case(cert, "k31-carrier1", 31, 0, 1);
    packet_case(cert, "k32-carrier0", 32, 0, 0);
    packet_case(cert, "k25-positive-saturation", 25, 0, 30);
    packet_case(cert, "k25-negative-saturation", 25, 1, 30);
    packet_case(cert, "accumulator-only-saturation", 32, 0, 26);
    packet_case(cert, "first-zero-then-nonzero", 31, 2, 1);
    packet_case(cert, "first-nonzero-then-zero", 31, 3, 1);
    packet_case(cert, "fragment-saturation-cancellation", 32, 4, 30);
    packet_case(cert, "zero-raw", 25, 7, 32767);
    packet_case(cert, "architectural-exponent", 17, 0, 32767);
    packet_case(cert, "radix-extrema-multiple-blocks", 31, 5, 0, true);
    std::cout << "{\"status\":\"PASS\",\"profile\":\"a"
              << GGML_GEMMINI_WEIGHT_BITS << "w" << GGML_GEMMINI_WEIGHT_BITS
              << "-d" << DIM
              << "-hp1\",\"numerical_cases\":" << cert.numerical_cases
              << ",\"same_hardware_pairs\":" << cert.pairs
              << ",\"bound_invocations\":" << cert.bound_cases
              << ",\"raw_mode\":false,\"host_integer_block_multiply\":false}\n";
  } catch (const std::exception &e) {
    Trace::stop();
    std::cerr << "RMD_SCU_FAIL " << e.what() << '\n';
    return 1;
  }
}
