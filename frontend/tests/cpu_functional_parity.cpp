#include <gemmini_params.h>

#include "gemmini.h"
#include "im2p_sim.h"
#include <algorithm>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>
#if defined(IM2P_CPU_FUNCTIONAL_TEST)
#include "im2p_cpu_functional.hpp"
#endif

static void require(bool condition, const char *message) {
  if (!condition)
    throw std::runtime_error(message);
}

struct Memory {
  size_t m, n, k;
  std::vector<int8_t> a, w;
  std::vector<uint32_t> scales;
  std::vector<int64_t> output;
  size_t writes = 0;
  Memory(size_t rows, size_t columns, size_t reduction)
      : m(rows), n(columns), k(reduction), a(m * (k + 3)), w(k * n),
        scales(((k + 31) / 32) * n), output(m * n, -777) {
    for (size_t i = 0; i < m; ++i)
      for (size_t r = 0; r < k; ++r)
        a[i * (k + 3) + r] = int8_t((i * 3 + r * 7) % 15) - 7;
    for (size_t r = 0; r < k; ++r)
      for (size_t j = 0; j < n; ++j)
        w[r * n + j] = int8_t((r * 11 + j * 5) % 15) - 7;
    for (size_t b = 0; b < (k + 31) / 32; ++b)
      for (size_t j = 0; j < n; ++j) {
        constexpr uint32_t carriers[] = {0, 2, 31, 0x80000000u, 32767};
        scales[b * n + j] = carriers[(b + j) % 5];
      }
  }
  static int weight(void *p, size_t row, size_t col, size_t count,
                    int8_t *out) {
    auto &s = *static_cast<Memory *>(p);
    if (!out || count > DIM || row >= s.k || col > s.n || count > s.n - col)
      return IM2P_ERROR;
    std::copy_n(s.w.data() + row * s.n + col, count, out);
    return IM2P_OK;
  }
  static int scale(void *p, size_t row, size_t col, size_t count,
                   uint32_t *out) {
    auto &s = *static_cast<Memory *>(p);
    if (!out || count > DIM || row >= (s.k + 31) / 32 || col > s.n ||
        count > s.n - col)
      return IM2P_ERROR;
    std::copy_n(s.scales.data() + row * s.n + col, count, out);
    return IM2P_OK;
  }
  static int write(void *p, size_t block, size_t row, size_t col, size_t count,
                   const int64_t *values, uint32_t domain) {
    auto &s = *static_cast<Memory *>(p);
    if (!values || block || row >= s.m || count > DIM || col > s.n ||
        count > s.n - col || domain != IM2P_OUTPUT_SCU_FINAL)
      return IM2P_ERROR;
    std::copy_n(values, count, s.output.data() + row * s.n + col);
    ++s.writes;
    return IM2P_OK;
  }
};

static im2p_matmul_desc_t descriptor(Memory &mem) {
  im2p_matmul_desc_t d{};
  d.abi_version = IM2P_ABI_VERSION;
  d.activation_bits = d.weight_bits = GGML_GEMMINI_ACTIVATION_BITS;
  d.activation_storage_bytes = d.weight_storage_bytes = 1;
  d.dim = DIM;
  d.m = mem.m;
  d.n = mem.n;
  d.k = mem.k;
  d.activations = mem.a.data();
  d.activation_row_stride_bytes = mem.k + 3;
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
  d.work_context = 731;
  d.provider = {&mem, Memory::weight, nullptr, Memory::scale, Memory::write};
  return d;
}

static im2p_stripe_work_desc_t striped(const im2p_matmul_desc_t &d,
                                       size_t height) {
  im2p_stripe_work_desc_t s{};
#define COPY(field) s.field = d.field
  COPY(abi_version);
  COPY(activation_bits);
  COPY(activation_storage_bytes);
  COPY(weight_bits);
  COPY(weight_storage_bytes);
  COPY(dim);
  COPY(m);
  COPY(n);
  COPY(k);
  COPY(weight_row_stride_bytes);
  COPY(output_row_stride);
  COPY(tile_i_rows);
  COPY(tile_j_columns);
  COPY(block_size);
  COPY(scale_total_k);
  COPY(scale_row_stride);
  COPY(scale_valid_columns);
  COPY(vector_op);
  COPY(output_domain);
  COPY(provider);
  COPY(work_context);
#undef COPY
  s.stripe_count = (d.m - 1) / height + 1;
  return s;
}

int main(int argc, char **argv) {
  try {
    require(argc == 5, "M N K full|pipeline required");
    Memory mem(std::stoull(argv[1]), std::stoull(argv[2]),
               std::stoull(argv[3]));
    const bool pipeline = std::string(argv[4]) == "pipeline";
    ggml_gemmini_args_t args{};
    args.I = mem.m;
    args.J = mem.n;
    args.K = mem.k;
    args.block_size_k = 32;
    require(args.A.allocate(mem.m, mem.k, GGML_GEMMINI_ACTIVATION_BITS),
            "activation geometry");
    ggml::gemmini::gemmini_set_tile_ws(&args);
    const auto layout = args.activation_geometry();
    require(layout.ok(), "production tile geometry");
    im2p_production_geometry_v1_t g{1,
                                    sizeof(g),
                                    GGML_GEMMINI_ACTIVATION_BITS,
                                    GGML_GEMMINI_WEIGHT_BITS,
                                    DIM,
                                    pipeline ? IM2P_GEOMETRY_STREAM
                                             : IM2P_GEOMETRY_FULL,
                                    mem.m,
                                    mem.n,
                                    mem.k,
                                    args.tile_I,
                                    args.tile_J,
                                    args.tile_K,
                                    layout.geometry.stripe_rows,
                                    0,
                                    mem.m,
                                    0};
    const auto d = descriptor(mem);
    std::unique_ptr<im2p_sim_t, decltype(&im2p_sim_destroy)> sim(
        im2p_sim_create(), im2p_sim_destroy);
    require(bool(sim), "create engine");
    im2p_work_stats_extended_t stats{};
    size_t completions = 0;
    if (!pipeline) {
      require(im2p_execute_matmul_planned(sim.get(), &d, &g, &stats) == IM2P_OK,
              "FULL engine failed");
    } else {
      auto s = striped(d, g.stripe_rows);
      im2p_stream_t *raw = nullptr;
      require(im2p_begin_striped_matmul_planned(sim.get(), &s, &g, &raw) ==
                  IM2P_OK,
              "STREAM begin failed");
      std::unique_ptr<im2p_stream_t, decltype(&im2p_destroy_stream)> stream(
          raw, im2p_destroy_stream);
      sim.reset();
      s.provider = {};
      for (size_t begin = 0, id = 0; begin < mem.m;
           begin += g.stripe_rows, ++id) {
        const size_t rows = std::min<size_t>(g.stripe_rows, mem.m - begin);
        auto stripe_g = g;
        stripe_g.scope = IM2P_GEOMETRY_STRIPE;
        stripe_g.row_begin = begin;
        stripe_g.row_count = rows;
        stripe_g.stripe_id = id;
        im2p_activation_stripe_t stripe{IM2P_ABI_VERSION,
                                        GGML_GEMMINI_ACTIVATION_BITS,
                                        1,
                                        GGML_GEMMINI_WEIGHT_BITS,
                                        1,
                                        DIM,
                                        uint32_t(id),
                                        begin,
                                        rows,
                                        mem.a.data() + begin * (mem.k + 3),
                                        mem.k + 3,
                                        73 + id};
        require(im2p_publish_stripe_planned(stream.get(), &stripe, &stripe_g) ==
                    IM2P_OK,
                "stripe publish failed");
        im2p_stripe_completion_extended_t completion{};
        int polled = 0;
        for (size_t guard = 0; guard < 10000000 && !polled; ++guard) {
          require(im2p_progress_stream(stream.get(), 1) == IM2P_OK,
                  "stream progress failed");
          polled = im2p_poll_completed_extended(stream.get(), &completion);
        }
        require(polled == 1 && completion.base.i_start == begin &&
                    completion.base.rows == rows &&
                    completion.base.stripe_id == id &&
                    completion.base.context == stripe.context,
                "completion snapshot");
        ++completions;
      }
      require(im2p_finish_stream_extended(stream.get(), &stats) == IM2P_OK,
              "stream finish failed");
      require(stats.base.stripes_published == completions &&
                  stats.base.stripe_rows_published == mem.m,
              "publication counters");
    }
#if defined(IM2P_CPU_FUNCTIONAL_TEST)
    require(std::string(im2p_sim_implementation()) == "CPU_FUNCTIONAL" &&
                stats.base.work_total_cycles == 0,
            "CPU execution must not fabricate RTL acceptance or cycles");
#endif
    std::cout << "{\"profile\":\"a" << GGML_GEMMINI_ACTIVATION_BITS << "w"
              << GGML_GEMMINI_WEIGHT_BITS << "-d" << DIM << "-hp1\",\"mode\":\""
              << argv[4] << "\",\"shape\":[" << mem.m << ',' << mem.n << ','
              << mem.k << "],\"tile\":[" << g.tile_i_count << ','
              << g.tile_j_count << ',' << g.tile_k_count
              << "],\"stripe_rows\":" << g.stripe_rows
              << ",\"completions\":" << completions << ",\"output\":[";
    for (size_t i = 0; i < mem.output.size(); ++i)
      std::cout << (i ? "," : "") << mem.output[i];
    std::cout << "]}\n";
  } catch (const std::exception &e) {
    std::cerr << "CPU_FUNCTIONAL_PARITY_FAIL " << e.what() << '\n';
    return 1;
  }
}
