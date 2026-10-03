// Additive public API ownership tests using the real Rust/integrated runtime.
// Per-stripe varying factors below are a transport-capability fixture, not an
// assertion that generic IM2P_SIM calls the separate facade's stripe retiler.
#define main im2p_existing_authority_fixture_main
#include "schedule_authority_probe.cpp"
#undef main
#include "accepted_work_observer.h"
#include <cstring>
#include <memory>

namespace {
std::vector<im2p_accepted_work_observation_t> records;
bool record_failed = false;
void record(void *, const im2p_accepted_work_observation_t *r) noexcept {
  try {
    records.push_back(*r);
  } catch (...) {
    record_failed = true;
  }
}
struct Memory {
  size_t m, n, k;
  std::vector<int64_t> output;
  std::vector<uint8_t> seen;
  explicit Memory(size_t rows, size_t columns, size_t reduction)
      : m(rows), n(columns), k(reduction), output(m * n, -77), seen(m * n) {}
  void reset() {
    std::fill(output.begin(), output.end(), -77);
    std::fill(seen.begin(), seen.end(), 0);
  }
  bool exact() const {
    return std::all_of(output.begin(), output.end(),
                       [this](auto v) { return v == int64_t(k); }) &&
           std::all_of(seen.begin(), seen.end(), [](auto v) { return v == 1; });
  }
  static int weight(void *p, size_t row, size_t col, size_t count,
                    int8_t *out) {
    const auto &s = *static_cast<Memory *>(p);
    if (!out || row >= s.k || col > s.n || count > s.n - col)
      return IM2P_ERROR;
    std::fill(out, out + count, 1);
    return IM2P_OK;
  }
  static int scale(void *p, size_t block, size_t col, size_t count,
                   uint32_t *out) {
    const auto &s = *static_cast<Memory *>(p);
    if (!out || block >= (s.k + 31) / 32 || col > s.n || count > s.n - col)
      return IM2P_ERROR;
    std::fill(out, out + count, 0);
    return IM2P_OK;
  }
  static int write(void *p, size_t block, size_t row, size_t col, size_t count,
                   const int64_t *values, uint32_t domain) {
    auto &s = *static_cast<Memory *>(p);
    if (!values || block || domain != IM2P_OUTPUT_SCU_FINAL || row >= s.m ||
        col > s.n || count > s.n - col)
      return IM2P_ERROR;
    for (size_t j = 0; j < count; ++j) {
      const auto offset = row * s.n + col + j;
      if (s.seen[offset]++)
        return IM2P_ERROR;
      s.output[offset] = values[j];
    }
    return IM2P_OK;
  }
  im2p_provider_t provider() { return {this, weight, nullptr, scale, write}; }
};
im2p_matmul_desc_t full_desc(Fixture &f, Memory &mem) {
  im2p_matmul_desc_t d{};
  d.abi_version = IM2P_ABI_VERSION;
  d.activation_bits = GGML_GEMMINI_ACTIVATION_BITS;
  d.weight_bits = GGML_GEMMINI_WEIGHT_BITS;
  d.activation_storage_bytes = d.weight_storage_bytes = 1;
  d.dim = DIM;
  d.activations = f.args.A.raw_data();
  d.m = mem.m;
  d.n = mem.n;
  d.k = mem.k;
  d.activation_row_stride_bytes = f.args.A.row_stride_bytes;
  d.weight_row_stride_bytes = mem.n;
  d.output_row_stride = mem.n;
  d.tile_i_rows = std::min(mem.m, size_t(DIM));
  d.tile_j_columns = std::min(mem.n, size_t(DIM));
  d.block_size = 32;
  d.scale_total_k = mem.k;
  d.scale_row_stride = mem.n;
  d.scale_valid_columns = mem.n;
  d.vector_op = IM2P_VECTOR_LEFT_SHIFT;
  d.output_domain = IM2P_OUTPUT_SCU_FINAL;
  d.provider = mem.provider();
  return d;
}
using Simulator = std::unique_ptr<im2p_sim_t, decltype(&im2p_sim_destroy)>;
using Stream = std::unique_ptr<im2p_stream_t, decltype(&im2p_destroy_stream)>;
size_t accepted_count(bool explicit_plan, const std::array<uint64_t, 3> &tile) {
  size_t count = 0;
  for (const auto &x : records)
    if (x.event == IM2P_OBSERVE_ACCEPTED) {
      require(x.explicit_geometry == uint64_t(explicit_plan),
              "geometry lifetime leaked across calls");
      require(std::array<uint64_t, 3>{x.lowerer_tile_i, x.lowerer_tile_j,
                                      x.lowerer_tile_k} == tile,
              "request-specific factors not consumed verbatim");
      ++count;
    }
  require(count && !record_failed, "missing accepted work observations");
  return count;
}
void full_isolation_and_passivity() {
  Fixture f(129, 129, 96);
  Memory mem(129, 129, 96);
  std::fill(f.args.A.bytes->begin(), f.args.A.bytes->end(), uint8_t{1});
  const auto d = full_desc(f, mem);
  const auto g =
      lower::capture_production_geometry(f.args, IM2P_GEOMETRY_FULL, 0, 129, 0);
  Simulator sim(im2p_sim_create(), im2p_sim_destroy);
  require(bool(sim), "create real simulator");
  im2p_work_stats_extended_t observed{}, quiet{}, legacy{};
  records.clear();
  im2p_test_set_work_observer(record, nullptr);
  require(im2p_execute_matmul_planned(sim.get(), &d, &g, &observed) ==
                  IM2P_OK &&
              mem.exact(),
          "planned FULL provider output");
  const auto expected =
      verify_lowering({129, 129, 96, g.tile_i_count, g.tile_j_count,
                       g.tile_k_count, g.stripe_rows, hp::Mode::full,
                       hp::WorkKind::dense_hp1_final})
          .first;
  require(accepted_count(true, {g.tile_i_count, g.tile_j_count,
                                g.tile_k_count}) == expected,
          "planned FULL lowered work count");
  im2p_test_set_work_observer(nullptr, nullptr);
  Simulator second(im2p_sim_create(), im2p_sim_destroy);
  mem.reset();
  require(im2p_execute_matmul_planned(second.get(), &d, &g, &quiet) ==
                  IM2P_OK &&
              mem.exact(),
          "observer-disabled FULL output");
  require(
      observed.base.work_total_cycles == quiet.base.work_total_cycles &&
          observed.base.activation_read_requests ==
              quiet.base.activation_read_requests &&
          observed.base.weight_read_requests ==
              quiet.base.weight_read_requests &&
          observed.base.scale_read_requests == quiet.base.scale_read_requests &&
          observed.base.output_write_requests ==
              quiet.base.output_write_requests &&
          observed.base.output_write_responses ==
              quiet.base.output_write_responses &&
          observed.base.completed_fragments == quiet.base.completed_fragments &&
          observed.base.completed_output_tiles ==
              quiet.base.completed_output_tiles,
      "passive observer changed logical cycles or counters");
  // Same handle after an explicit plan; no persistent override of legacy 1/1/1.
  records.clear();
  mem.reset();
  im2p_test_set_work_observer(record, nullptr);
  require(im2p_execute_matmul_extended(sim.get(), &d, &legacy) == IM2P_OK &&
              mem.exact(),
          "legacy call after explicit plan failed");
  const auto legacy_expected =
      verify_lowering({129, 129, 96, 1, 1, 1, DIM, hp::Mode::full,
                       hp::WorkKind::dense_hp1_final})
          .first;
  require(accepted_count(false, {1, 1, 1}) == legacy_expected,
          "legacy call inherited explicit factors");
  im2p_test_set_work_observer(nullptr, nullptr);
  std::cout << "{\"test\":\"full_isolation_and_observer_passivity\",\"status\":"
               "\"PASS\","
               "\"explicit_loops\":"
            << expected << ",\"legacy_loops\":" << legacy_expected
            << ",\"observer_cycles\":" << observed.base.work_total_cycles
            << ",\"quiet_cycles\":" << quiet.base.work_total_cycles << "}\n";
}

void stripe_copy_and_admission() {
  Fixture f(129, 129, 96);
  Memory mem(129, 129, 96);
  const auto &a = f.args;
  std::fill(f.args.A.bytes->begin(), f.args.A.bytes->end(), uint8_t{1});
  const auto whole =
      lower::capture_production_geometry(a, IM2P_GEOMETRY_STREAM, 0, a.I, 0);
  im2p_stripe_work_desc_t d{};
  const auto full = full_desc(f, mem);
  d.abi_version = full.abi_version;
  d.activation_bits = full.activation_bits;
  d.activation_storage_bytes = 1;
  d.weight_bits = full.weight_bits;
  d.weight_storage_bytes = 1;
  d.dim = DIM;
  d.m = a.I;
  d.n = a.J;
  d.k = a.K;
  d.weight_row_stride_bytes = a.J;
  d.output_row_stride = a.J;
  d.tile_i_rows = std::min(a.I, size_t(DIM));
  d.tile_j_columns = std::min(a.J, size_t(DIM));
  d.block_size = 32;
  d.scale_total_k = a.K;
  d.scale_row_stride = a.J;
  d.scale_valid_columns = a.J;
  d.stripe_count = (a.I + whole.stripe_rows - 1) / whole.stripe_rows;
  d.vector_op = IM2P_VECTOR_LEFT_SHIFT;
  d.output_domain = IM2P_OUTPUT_SCU_FINAL;
  d.provider = mem.provider();
  Simulator sim(im2p_sim_create(), im2p_sim_destroy);
  im2p_stream_t *raw = nullptr;
  records.clear();
  im2p_test_set_work_observer(record, nullptr);
  require(im2p_begin_striped_matmul_planned(sim.get(), &d, &whole, &raw) ==
                  IM2P_OK &&
              raw,
          "planned stream admission");
  Stream stream(raw, im2p_destroy_stream);
  std::vector<im2p_production_geometry_v1_t> expected;
  std::vector<im2p_stripe_completion_extended_t> completions;
  size_t negatives = 0;
  bool changed = false;
  auto progress = [&] {
    require(im2p_progress_stream(stream.get(), 256) == IM2P_OK,
            "planned stream progress");
    im2p_stripe_completion_extended_t c{};
    int status = 0;
    while ((status = im2p_poll_completed_extended(stream.get(), &c)) == 1)
      completions.push_back(c);
    require(status == 0, "planned stream completion poll");
  };
  for (size_t id = 0, begin = 0; begin < a.I;
       begin += whole.stripe_rows, ++id) {
    const size_t rows = std::min<size_t>(whole.stripe_rows, a.I - begin);
    auto dispatch = a;
    // The real shared selector operates on stripe shape; emulate only the
    // separate facade's documented restore-I rule in this capability test.
    dispatch.I = rows;
    dispatch.tile_I = dispatch.tile_J = dispatch.tile_K = 0;
    ggml::gemmini::gemmini_set_tile_ws(&dispatch);
    dispatch.tile_I = a.tile_I;
    dispatch.I = a.I;
    auto g = lower::capture_production_geometry(dispatch, IM2P_GEOMETRY_STRIPE,
                                                begin, rows, id);
    changed |= g.tile_j_count != whole.tile_j_count ||
               g.tile_k_count != whole.tile_k_count;
    im2p_activation_stripe_t s{};
    s.abi_version = IM2P_ABI_VERSION;
    s.activation_bits = GGML_GEMMINI_ACTIVATION_BITS;
    s.activation_storage_bytes = 1;
    s.weight_bits = GGML_GEMMINI_WEIGHT_BITS;
    s.weight_storage_bytes = 1;
    s.dim = DIM;
    s.stripe_id = id;
    s.i_start = begin;
    s.rows = rows;
    s.activations = static_cast<const uint8_t *>(a.A.raw_data()) +
                    begin * a.A.row_stride_bytes;
    s.activation_row_stride_bytes = a.A.row_stride_bytes;
    s.context = id + 9;
    auto reject = [&](const im2p_production_geometry_v1_t *bad) {
      const auto before = records.size();
      require(im2p_publish_stripe_planned(stream.get(), &s, bad) ==
                  IM2P_INVALID_LAYOUT,
              "invalid per-stripe companion admitted");
      require(records.size() == before, "rejected stripe advanced runtime");
      ++negatives;
    };
    reject(nullptr);
    auto bad = g;
    bad.tile_k_count = 0;
    reject(&bad);
    bad = g;
    bad.stripe_id++;
    reject(&bad);
    bad = g;
    bad.row_begin++;
    reject(&bad);
    bad = g;
    bad.scope = IM2P_GEOMETRY_FULL;
    reject(&bad);
    bad = g;
    bad.stripe_rows++;
    reject(&bad);
    require(im2p_publish_stripe(stream.get(), &s) == IM2P_INVALID_LAYOUT,
            "planned stream admitted a legacy publication");
    ++negatives;
    int status = 0;
    for (size_t wait = 0; wait < 100000; ++wait) {
      status = im2p_publish_stripe_planned(stream.get(), &s, &g);
      if (status == IM2P_OK)
        break;
      require(status == IM2P_BACKPRESSURE,
              "unexpected valid publication rejection");
      progress();
    }
    require(status == IM2P_OK, "valid publication never accepted");
    expected.push_back(g);
    // Caller record may immediately expire or be repurposed after acceptance.
    std::memset(&g, 0xa5, sizeof(g));
  }
  for (size_t wait = 0; completions.size() < d.stripe_count && wait < 100000;
       ++wait)
    progress();
  require(completions.size() == d.stripe_count,
          "incomplete published stripe coverage");
  im2p_work_stats_extended_t stats{};
  require(im2p_finish_stream_extended(stream.get(), &stats) == IM2P_OK &&
              mem.exact(),
          "planned varying-stripe output");
  im2p_test_set_work_observer(nullptr, nullptr);
  std::vector<size_t> accepted(expected.size());
  for (const auto &x : records)
    if (x.event == IM2P_OBSERVE_ACCEPTED) {
      const auto &g = expected.at(x.stripe_id);
      require(std::memcmp(&g, &x.geometry, sizeof(g)) == 0,
              "queued stripe retained caller pointer");
      require(x.lowerer_tile_i == g.tile_i_count &&
                  x.lowerer_tile_j == g.tile_j_count &&
                  x.lowerer_tile_k == g.tile_k_count,
              "stripe reused the initial operation tile");
      ++accepted.at(x.stripe_id);
    }
  for (size_t id = 0; id < expected.size(); ++id) {
    const auto &g = expected[id];
    size_t count = 0;
    lower::ScheduleConfig config{
        {a.I, a.J, a.K},
        {DIM, GGML_GEMMINI_ACTIVATION_BITS},
        {g.tile_i_count, g.tile_j_count, g.tile_k_count, g.stripe_rows},
        {a.K, a.J, a.J * 4, a.J * 4, 0},
        true,
        true,
        false};
    lower::LoopCursor cursor{g.row_begin};
    while (cursor.i < g.row_begin + g.row_count) {
      const auto loop =
          lower::plan_loop(config, g.row_begin + g.row_count, cursor);
      lower::advance_loop(config, loop, cursor);
      ++count;
    }
    require(count == accepted[id],
            "per-stripe expected/accepted work count mismatch");
    require(completions[id].base.stripe_id == id &&
                completions[id].base.i_start == g.row_begin &&
                completions[id].base.rows == g.row_count &&
                completions[id].base.context == id + 9,
            "publication completion identity or order changed");
  }
  if (DIM == 32)
    require(changed,
            "DIM32 varying-factor fixture did not exercise distinct geometry");
  require(!record_failed, "observer allocation failure");
  std::cout << "{\"test\":\"stripe_snapshot_admission\",\"status\":\"PASS\","
               "\"stripes\":"
            << expected.size() << ",\"negative_checks\":" << negatives
            << ",\"varied_factors\":" << (changed ? "true" : "false")
            << ",\"aggregate_timing\":\"NOT_IMPLEMENTED\"}\n";
}

void large_k_scale_cache_admission() {
  Fixture f(128, 768, 3072);
  Memory mem(128, 768, 3072);
  const auto &a = f.args;
#if GGML_GEMMINI_ACTIVATION_BITS == 8 && GGML_GEMMINI_WEIGHT_BITS == 8 &&      \
    DIM == 16
  require(a.tile_I == 5 && a.tile_J == 5 && a.tile_K == 51,
          "GPT-2 down-projection production tile changed");
#endif
  const auto whole =
      lower::capture_production_geometry(a, IM2P_GEOMETRY_STREAM, 0, a.I, 0);

  const auto full = full_desc(f, mem);
  im2p_stripe_work_desc_t d{};
  d.abi_version = full.abi_version;
  d.activation_bits = full.activation_bits;
  d.activation_storage_bytes = 1;
  d.weight_bits = full.weight_bits;
  d.weight_storage_bytes = 1;
  d.dim = DIM;
  d.m = a.I;
  d.n = a.J;
  d.k = a.K;
  d.weight_row_stride_bytes = a.J;
  d.output_row_stride = a.J;
  d.tile_i_rows = std::min(a.I, size_t(DIM));
  d.tile_j_columns = std::min(a.J, size_t(DIM));
  d.block_size = 32;
  d.scale_total_k = a.K;
  d.scale_row_stride = a.J;
  d.scale_valid_columns = a.J;
  d.stripe_count = (a.I + whole.stripe_rows - 1) / whole.stripe_rows;
  d.vector_op = IM2P_VECTOR_LEFT_SHIFT;
  d.output_domain = IM2P_OUTPUT_SCU_FINAL;
  d.provider = mem.provider();

  Simulator sim(im2p_sim_create(), im2p_sim_destroy);
  require(bool(sim), "create large-K simulator");
  im2p_stream_t *raw = nullptr;
  require(im2p_begin_striped_matmul_planned(sim.get(), &d, &whole, &raw) ==
                  IM2P_OK &&
              raw,
          "large-K planned stream admission");
  Stream stream(raw, im2p_destroy_stream);

  const size_t rows = std::min<size_t>(whole.stripe_rows, a.I);
  const auto stripe_geometry =
      lower::capture_production_geometry(a, IM2P_GEOMETRY_STRIPE, 0, rows, 0);
  require(stripe_geometry.tile_i_count == a.tile_I &&
              stripe_geometry.tile_j_count == a.tile_J &&
              stripe_geometry.tile_k_count == a.tile_K,
          "large-K stripe lost production tile");

  im2p_activation_stripe_t stripe{};
  stripe.abi_version = IM2P_ABI_VERSION;
  stripe.activation_bits = GGML_GEMMINI_ACTIVATION_BITS;
  stripe.activation_storage_bytes = 1;
  stripe.weight_bits = GGML_GEMMINI_WEIGHT_BITS;
  stripe.weight_storage_bytes = 1;
  stripe.dim = DIM;
  stripe.stripe_id = 0;
  stripe.i_start = 0;
  stripe.rows = rows;
  stripe.activations = a.A.raw_data();
  stripe.activation_row_stride_bytes = a.A.row_stride_bytes;
  stripe.context = 1;

  require(im2p_publish_stripe_planned(stream.get(), &stripe,
                                      &stripe_geometry) == IM2P_OK,
          "large-K production stripe 0 rejected by scale-cache admission");

  const size_t second_begin = rows;
  const size_t second_rows = std::min<size_t>(whole.stripe_rows, a.I - second_begin);
  auto second_geometry = lower::capture_production_geometry(
      a, IM2P_GEOMETRY_STRIPE, second_begin, second_rows, 1);
  stripe.stripe_id = 1;
  stripe.i_start = second_begin;
  stripe.rows = second_rows;
  stripe.activations =
      static_cast<const uint8_t *>(a.A.raw_data()) +
      second_begin * a.A.row_stride_bytes;
  const int second_status =
      im2p_publish_stripe_planned(stream.get(), &stripe, &second_geometry);
  require(second_status == IM2P_OK,
          "large-K production stripe 1 rejected by scale-cache admission");

  std::cout
      << "{\"test\":\"large_k_scale_cache_admission\",\"status\":\"PASS\","
         "\"m\":128,\"n\":768,\"k\":3072,\"tile\":["
      << a.tile_I << ',' << a.tile_J << ',' << a.tile_K
      << "],\"stripes_published\":2}\n";
}
} // namespace
int main() {
  try {
    full_isolation_and_passivity();
    stripe_copy_and_admission();
    large_k_scale_cache_admission();
  } catch (const std::exception &e) {
    im2p_test_set_work_observer(nullptr, nullptr);
    std::cerr << "PRODUCTION_GEOMETRY_API_TEST_FAIL " << e.what() << '\n';
    return 1;
  }
}
