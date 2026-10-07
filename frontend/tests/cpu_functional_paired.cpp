// CPU-functional paired stream parity (P2.4), bit-exact:
// - paired Main == unpaired Main (planned stream, same stripes);
// - paired residual == im2p_execute_matmul_planned_runs fed the llama-style
//   gathered W (original row block * 32 + bit) and carriers (column, block).
// Shapes are the P2.1 cases for this DIM plus DIM-generic block/stripe cases.
#include "im2p_sim.h"

#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <memory>
#include <string>
#include <vector>

namespace {

void require(bool ok, const std::string &what) {
  if (ok) return;
  std::fprintf(stderr, "CPU_FUNCTIONAL_PAIRED_FAIL %s\n", what.c_str());
  std::exit(1);
}

using Sim = std::unique_ptr<im2p_sim_t, decltype(&im2p_sim_destroy)>;
using Stream = std::unique_ptr<im2p_stream_t, decltype(&im2p_destroy_stream)>;

struct Case {
  std::string name;
  size_t m, n, k, tile_i, tile_j, tile_k, stripe_rows;
  // Per stripe: residual rows and per-block surviving-K masks (0 = no run).
  std::vector<std::vector<size_t>> rows;
  std::vector<std::vector<uint32_t>> masks;
};

struct Operands {
  std::vector<int8_t> a, w;
  std::vector<uint32_t> scales;
};

struct Residual {
  size_t rows = 0, compact_k = 0;
  std::vector<im2p_compact_run_t> runs;
  im2p_compact_runs_t view{};
  std::vector<int8_t> a;
  std::vector<int32_t> output;
};

uint64_t state = 0x9e3779b97f4a7c15ULL;
uint32_t next() {
  state = state * 6364136223846793005ULL + 1442695040888963407ULL;
  return static_cast<uint32_t>(state >> 33);
}
// Valid for A4/W4 and A8/W8.
int8_t value() { return static_cast<int8_t>(static_cast<int>(next() % 16) - 8); }
uint32_t carrier() {
  const auto r = next() % 8;
  return r == 7 ? 0x80000000u : r;
}

im2p_production_geometry_v1_t geometry(uint32_t scope, const Case &c, size_t m, size_t n,
                                       size_t k, size_t row_begin, size_t row_count,
                                       size_t stripe_id) {
  return {IM2P_PRODUCTION_GEOMETRY_VERSION, sizeof(im2p_production_geometry_v1_t),
          IM2P_ACTIVATION_BITS, IM2P_WEIGHT_BITS, IM2P_DIM, scope, m, n, k,
          c.tile_i, c.tile_j, c.tile_k, c.stripe_rows, row_begin, row_count, stripe_id};
}

Residual make_residual(const Case &c, size_t rows, const std::vector<uint32_t> &masks) {
  Residual r;
  r.rows = rows;
  for (size_t block = 0; block < masks.size(); ++block) {
    if (!masks[block]) continue;
    const auto count = static_cast<uint32_t>(__builtin_popcount(masks[block]));
    r.runs.push_back({static_cast<uint32_t>(block), masks[block],
                      static_cast<uint32_t>(r.compact_k), count});
    r.compact_k += count;
  }
  r.view = {IM2P_COMPACT_RUNS_VERSION, sizeof(im2p_compact_runs_t),
            static_cast<uint32_t>(c.k), r.runs.size(), r.runs.data()};
  r.a.resize(rows * r.compact_k);
  for (auto &x : r.a) x = value();
  r.output.assign(rows * c.n, -99);
  return r;
}

im2p_paired_residual_v1_t companion(Residual &r, size_t n) {
  im2p_paired_residual_v1_t p{};
  p.version = IM2P_PAIRED_RESIDUAL_VERSION;
  p.struct_size = sizeof(p);
  p.rows = r.rows;
  p.compact_k = r.compact_k;
  p.activations = r.a.data();
  p.activation_row_stride_bytes = r.compact_k;
  p.runs = &r.view;
  p.output = r.output.data();
  p.output_row_stride = n;
  return p;
}

enum class Mode { planned, paired_null, paired_rows0, paired };

// One planned stream over all stripes; returns Main output.
std::vector<int32_t> run_stream(const Case &c, const Operands &ops, Mode mode,
                                std::vector<Residual> *residuals = nullptr) {
  std::vector<int32_t> output(c.m * c.n, -99);
  const size_t blocks = (c.k + 31) / 32;
  im2p_stripe_work_desc_t d{};
  d.abi_version = IM2P_ABI_VERSION;
  d.activation_bits = IM2P_ACTIVATION_BITS;
  d.weight_bits = IM2P_WEIGHT_BITS;
  d.activation_storage_bytes = d.weight_storage_bytes = 1;
  d.dim = IM2P_DIM;
  d.weights = ops.w.data();
  d.scales = ops.scales.data();
  d.output = output.data();
  d.m = c.m;
  d.n = c.n;
  d.k = c.k;
  d.weight_row_stride_bytes = c.n;
  d.output_row_stride = c.n;
  d.tile_i_rows = std::min<size_t>(c.m, IM2P_DIM);
  d.tile_j_columns = std::min<size_t>(c.n, IM2P_DIM);
  d.block_size = 32;
  d.scale_total_k = c.k;
  d.scale_row_stride = c.n;
  d.scale_valid_columns = c.n;
  d.scale_values_len = blocks * c.n;
  d.stripe_count = (c.m + c.stripe_rows - 1) / c.stripe_rows;
  d.vector_op = IM2P_VECTOR_LEFT_SHIFT;
  d.output_domain = IM2P_OUTPUT_SCU_FINAL;
  const auto whole = geometry(IM2P_GEOMETRY_STREAM, c, c.m, c.n, c.k, 0, c.m, 0);
  Sim sim(im2p_sim_create(), im2p_sim_destroy);
  im2p_stream_t *raw = nullptr;
  require(sim && im2p_begin_striped_matmul_planned(sim.get(), &d, &whole, &raw) == IM2P_OK,
          c.name + " begin");
  Stream stream(raw, im2p_destroy_stream);
  for (size_t id = 0, begin = 0; begin < c.m; ++id, begin += c.stripe_rows) {
    const size_t rows = std::min(c.stripe_rows, c.m - begin);
    im2p_activation_stripe_t s{};
    s.abi_version = IM2P_ABI_VERSION;
    s.activation_bits = IM2P_ACTIVATION_BITS;
    s.weight_bits = IM2P_WEIGHT_BITS;
    s.activation_storage_bytes = s.weight_storage_bytes = 1;
    s.dim = IM2P_DIM;
    s.stripe_id = static_cast<uint32_t>(id);
    s.i_start = begin;
    s.rows = rows;
    s.activations = ops.a.data() + begin * c.k;
    s.activation_row_stride_bytes = c.k;
    const auto part = geometry(IM2P_GEOMETRY_STRIPE, c, c.m, c.n, c.k, begin, rows, id);
    int status = IM2P_ERROR;
    if (mode == Mode::planned) {
      status = im2p_publish_stripe_planned(stream.get(), &s, &part);
    } else if (mode == Mode::paired_null) {
      status = im2p_publish_stripe_paired(stream.get(), &s, &part, nullptr);
    } else if (mode == Mode::paired_rows0) {
      im2p_paired_residual_v1_t empty{};
      empty.version = IM2P_PAIRED_RESIDUAL_VERSION;
      empty.struct_size = sizeof(empty);
      status = im2p_publish_stripe_paired(stream.get(), &s, &part, &empty);
    } else {
      auto &r = (*residuals)[id];
      auto p = companion(r, c.n);
      if (id == 0) {
        // Rejected companions publish nothing and write nothing.
        auto bad = p;
        bad.version = 2;
        require(im2p_publish_stripe_paired(stream.get(), &s, &part, &bad) ==
                    IM2P_INVALID_LAYOUT, c.name + " malformed companion");
        auto wrong_k = r.view;
        wrong_k.original_k = static_cast<uint32_t>(c.k + 32);
        bad = p;
        bad.runs = &wrong_k;
        require(im2p_publish_stripe_paired(stream.get(), &s, &part, &bad) ==
                    IM2P_INVALID_LAYOUT, c.name + " original_k mismatch");
        require(std::all_of(r.output.begin(), r.output.end(), [](int32_t v) { return v == -99; }) &&
                    std::all_of(output.begin(), output.end(), [](int32_t v) { return v == -99; }),
                c.name + " rejected companion wrote output");
      }
      status = im2p_publish_stripe_paired(stream.get(), &s, &part, &p);
    }
    require(status == IM2P_OK, c.name + " publish stripe " + std::to_string(id));
  }
  im2p_stripe_completion_extended_t completion{};
  size_t completed = 0;
  while (im2p_poll_completed_extended(stream.get(), &completion) == 1) ++completed;
  im2p_work_stats_extended_t stats{};
  require(completed == d.stripe_count &&
              im2p_finish_stream_extended(stream.get(), &stats) == IM2P_OK,
          c.name + " finish");
  return output;
}

std::vector<int32_t> residual_reference(const Case &c, const Operands &ops, const Residual &r) {
  // llama rmd-run-aware gather: compact W row from original row (block, local k),
  // carriers from (column, block), one carrier row per run.
  std::vector<int8_t> w(r.compact_k * c.n);
  std::vector<uint32_t> carriers(r.runs.size() * c.n);
  for (size_t index = 0; index < r.runs.size(); ++index) {
    const auto &run = r.runs[index];
    size_t row = run.compact_k_begin;
    for (unsigned bit = 0; bit < 32; ++bit)
      if (run.original_k_mask >> bit & 1U)
        std::copy_n(ops.w.data() + (size_t{run.original_block_id} * 32 + bit) * c.n, c.n,
                    w.data() + row++ * c.n);
    std::copy_n(ops.scales.data() + size_t{run.original_block_id} * c.n, c.n,
                carriers.data() + index * c.n);
  }
  std::vector<int32_t> out(r.rows * c.n, -77);
  im2p_matmul_desc_t d{};
  d.abi_version = IM2P_ABI_VERSION;
  d.activation_bits = IM2P_ACTIVATION_BITS;
  d.weight_bits = IM2P_WEIGHT_BITS;
  d.activation_storage_bytes = d.weight_storage_bytes = 1;
  d.dim = IM2P_DIM;
  d.activations = r.a.data();
  d.weights = w.data();
  d.scales = carriers.data();
  d.output = out.data();
  d.m = r.rows;
  d.n = c.n;
  d.k = r.compact_k;
  d.activation_row_stride_bytes = r.compact_k;
  d.weight_row_stride_bytes = c.n;
  d.output_row_stride = c.n;
  d.tile_i_rows = std::min<size_t>(r.rows, IM2P_DIM);
  d.tile_j_columns = std::min<size_t>(c.n, IM2P_DIM);
  d.block_size = 32;
  d.scale_total_k = c.k;
  d.scale_row_stride = c.n;
  d.scale_valid_columns = c.n;
  d.scale_values_len = carriers.size();
  d.vector_op = IM2P_VECTOR_LEFT_SHIFT;
  d.output_domain = IM2P_OUTPUT_SCU_FINAL;
  Case full = c;
  full.tile_i = full.tile_j = full.tile_k = 1;
  full.stripe_rows = r.rows;
  const auto g = geometry(IM2P_GEOMETRY_FULL, full, r.rows, c.n, r.compact_k, 0, r.rows, 0);
  Sim sim(im2p_sim_create(), im2p_sim_destroy);
  require(sim && im2p_execute_matmul_planned_runs(sim.get(), &d, &g, &r.view, nullptr) ==
                     IM2P_OK, c.name + " planned_runs reference");
  return out;
}

void run_case(const Case &c) {
  Operands ops;
  ops.a.resize(c.m * c.k);
  ops.w.resize(c.k * c.n);
  ops.scales.resize((c.k + 31) / 32 * c.n);
  for (auto &x : ops.a) x = value();
  for (auto &x : ops.w) x = value();
  for (auto &x : ops.scales) x = carrier();
  const auto unpaired = run_stream(c, ops, Mode::planned);
  require(run_stream(c, ops, Mode::paired_null) == unpaired, c.name + " NULL companion");
  require(run_stream(c, ops, Mode::paired_rows0) == unpaired, c.name + " rows == 0");
  std::printf("CPU_FUNCTIONAL_PAIRED %s", c.name.c_str());
  for (size_t variant = 0; variant < c.rows.front().size(); ++variant) {
    std::vector<Residual> residuals;
    for (size_t stripe = 0; stripe < c.rows.size(); ++stripe)
      residuals.push_back(make_residual(c, c.rows[stripe][variant], c.masks[stripe]));
    require(run_stream(c, ops, Mode::paired, &residuals) == unpaired, c.name + " paired Main");
    for (const auto &r : residuals)
      require(r.output == residual_reference(c, ops, r), c.name + " paired residual");
    std::printf(" MR%zu", c.rows.front()[variant]);
  }
  std::printf(" PASS\n");
}

} // namespace

int main() {
  constexpr size_t d = IM2P_DIM;
  const std::vector<uint32_t> k96 = {0, 0x00f0f000u, 0xffff0fffu};
  std::vector<uint32_t> k1024(32, 0), k128 = {0xffffffffu, 0, 0x00ff00ffu, 0}, k3072(96, 0);
  k1024[0] = 1;
  k1024[3] = 0xffffffffu;
  k1024[31] = 0x80000001u;
  k3072[0] = 1;
  k3072[25] = 0xffffffffu;
  k3072[95] = 0x80000001u;
  std::vector<Case> cases;
  if (d == 16) {
    cases.push_back({"A8D16 M80 N16 K96 tile(5,1,6)", 80, 16, 96, 5, 1, 6, 80,
                     {{1, 16, 17, 432, 433}}, {k96}});
    cases.push_back({"A8D16 M80 N80 K96 tile(5,5,6)", 80, 80, 96, 5, 5, 6, 80, {{1, 16, 17}}, {k96}});
    cases.push_back({"A8D16 M80 N96 K96 tile(5,5,6)", 80, 96, 96, 5, 5, 6, 80, {{1, 16, 17}}, {k96}});
    cases.push_back({"A8D16 M80 N80 K96 tile(2,5,6)", 80, 80, 96, 2, 5, 6, 80, {{1, 16, 432}}, {k96}});
    cases.push_back({"A8D16 M16 N16 K3072 tile(1,1,51)", 16, 16, 3072, 1, 1, 51, 16, {{1}}, {k3072}});
  } else if (d == 64) {
    cases.push_back({"A8D64 M64 N256 K1024 tile(1,1,16)", 64, 256, 1024, 1, 1, 16, 64,
                     {{1, 64, 65}}, {k1024}});
    cases.push_back({"A8D64 M64 N256 K128 tile(1,2,2)", 64, 256, 128, 1, 2, 2, 64, {{1, 64}}, {k128}});
    cases.push_back({"A8D64 M64 N64 K128 tile(1,1,2)", 64, 64, 128, 1, 1, 2, 64, {{1, 64}}, {k128}});
  }
  // One inactive block, one run with <= 16 active K, one with > 16.
  cases.push_back({"blocks M" + std::to_string(d) + " N" + std::to_string(d) + " K96", d, d, 96,
                   1, 1, d == 16 ? 6 : 2, d, {{1, d}}, {k96}});
  // Two stripes with different companions.
  cases.push_back({"stripes M" + std::to_string(3 * d) + " K96 rows 2D+D", 3 * d, d, 96, 2, 1,
                   d == 16 ? 6 : 2, 2 * d, {{5, 1}, {2, 3}},
                   {k96, {0xffffffffu, 0x00000001u, 0}}});
  for (const auto &c : cases) run_case(c);
  std::printf("CPU_FUNCTIONAL_PAIRED_PASS profile=a%dw%d-d%d cases=%zu\n", IM2P_ACTIVATION_BITS,
              IM2P_WEIGHT_BITS, IM2P_DIM, cases.size());
}
