#include "im2p_cpu_functional.hpp"
#include "im2p_cpu_functional_internal.hpp"
#include "quants/common/hp1_scu.hpp"
#include <algorithm>
#include <limits>

namespace im2p::cpu_functional {
namespace hp1 = ggml::gemmini::quants::hp1;
namespace {
thread_local TimingObserver observer;
struct TimingScope {
  const char *stage;
  explicit TimingScope(const char *value) : stage(value) {
    if (observer.callback)
      observer.callback(observer.context, stage, true);
  }
  ~TimingScope() {
    if (observer.callback)
      observer.callback(observer.context, stage, false);
  }
};
bool extent(size_t rows, size_t columns, size_t stride, size_t bytes = 1) {
  return rows && columns && stride >= columns &&
         columns <= size_t(PTRDIFF_MAX) / bytes &&
         rows - 1 <= (size_t(PTRDIFF_MAX) / bytes - columns) / stride;
}
bool values_valid(const int8_t *values, size_t count, uint32_t bits) {
  return bits == 8 || std::all_of(values, values + count,
                                  [](int8_t x) { return x >= -8 && x <= 7; });
}
} // namespace

TimingObserver set_timing_observer(TimingObserver next) noexcept {
  const auto previous = observer;
  observer = next;
  return previous;
}

bool identity(uint32_t abi, uint32_t a, uint32_t a_bytes, uint32_t w,
              uint32_t w_bytes, uint32_t dim) noexcept {
  return abi == IM2P_ABI_VERSION && a == IM2P_ACTIVATION_BITS &&
         w == IM2P_WEIGHT_BITS && a_bytes == 1 && w_bytes == 1 &&
         dim == IM2P_DIM;
}

bool geometry(const im2p_production_geometry_v1_t &g,
              const im2p_matmul_desc_t &d, uint32_t scope) noexcept {
  if (g.version != IM2P_PRODUCTION_GEOMETRY_VERSION ||
      g.struct_size != sizeof(g) || g.activation_bits != IM2P_ACTIVATION_BITS ||
      g.weight_bits != IM2P_WEIGHT_BITS || g.dim != IM2P_DIM ||
      g.scope != scope || g.m != d.m || g.n != d.n || g.k != d.k || !g.m ||
      !g.n || !g.k || !g.stripe_rows || g.stripe_rows > UINT32_MAX ||
      !g.tile_i_count || !g.tile_j_count || !g.tile_k_count ||
      g.tile_i_count > UINT16_MAX / IM2P_DIM ||
      g.tile_j_count > UINT16_MAX / IM2P_DIM ||
      g.tile_k_count > UINT32_MAX / IM2P_DIM)
    return false;
  if (scope != IM2P_GEOMETRY_STRIPE) {
    if (g.row_begin || g.row_count != g.m || g.stripe_id)
      return false;
  } else if (g.row_begin >= g.m || !g.row_count ||
             g.row_count > g.m - g.row_begin || g.row_count > g.stripe_rows ||
             g.stripe_id > UINT32_MAX) {
    return false;
  }
  const uint64_t rows = scope == IM2P_GEOMETRY_STREAM
                            ? std::min(g.m, g.stripe_rows)
                            : g.row_count;
  const uint64_t i =
      (std::min(rows, g.tile_i_count * IM2P_DIM) + IM2P_DIM - 1) / IM2P_DIM;
  const uint64_t j =
      (std::min(g.n, g.tile_j_count * IM2P_DIM) + IM2P_DIM - 1) / IM2P_DIM;
  const uint64_t k = (std::min<uint64_t>(g.k, 32) + IM2P_DIM - 1) / IM2P_DIM;
  return i * j <= 64 &&
         (i + j) * k * IM2P_DIM <=
             IM2P_GEMMINI_BANK_COUNT * IM2P_GEMMINI_BANK_ROWS / 2 &&
         i * j * IM2P_DIM <= IM2P_ACCUMULATOR_ROWS / 2 &&
         (g.k - 1) / std::min<size_t>(IM2P_DIM, 32) <= UINT16_MAX && j <= 128;
}

int prepare(Operands &out, const im2p_matmul_desc_t &d) {
  TimingScope materialize("functional.materialize");
  if (!identity(d.abi_version, d.activation_bits, d.activation_storage_bytes,
                d.weight_bits, d.weight_storage_bytes, d.dim))
    return IM2P_CONFIGURATION_MISMATCH;
  const bool provider = d.provider.read_weight_i8 ||
                        d.provider.read_weight_i16 || d.provider.read_scale ||
                        d.provider.write_output;
  if (d.vector_op != IM2P_VECTOR_LEFT_SHIFT ||
      d.output_domain != IM2P_OUTPUT_SCU_FINAL || d.block_size != 32 ||
      d.m > UINT32_MAX || d.n > UINT32_MAX || d.k > UINT32_MAX ||
      !extent(d.k, d.n, d.weight_row_stride_bytes) ||
      !extent(d.m, d.n, d.output_row_stride, sizeof(int32_t)) ||
      !extent(d.k, d.n, d.n) ||
      (provider && (!d.provider.read_weight_i8 || d.provider.read_weight_i16 ||
                    !d.provider.read_scale || !d.provider.write_output)) ||
      (!provider && (!d.weights || !d.output || !d.scales)))
    return IM2P_INVALID_LAYOUT;
  const size_t blocks = (d.k + 31) / 32;
  if (!provider &&
      (d.scale_total_k < d.k || d.scale_valid_columns < d.n ||
       d.scale_column_offset > d.scale_row_stride ||
       d.n > d.scale_row_stride - d.scale_column_offset ||
       !extent(blocks, d.scale_column_offset + d.n, d.scale_row_stride,
               sizeof(uint32_t)) ||
       (blocks - 1) * d.scale_row_stride + d.scale_column_offset + d.n >
           d.scale_values_len))
    return IM2P_INVALID_LAYOUT;
  out.descriptor = d;
  out.weights.resize(d.k * d.n);
  out.scales.resize(blocks * d.n);
  for (size_t k = 0; k < d.k; ++k) {
    auto *row = out.weights.data() + k * d.n;
    if (provider) {
      for (size_t column = 0; column < d.n; column += IM2P_DIM)
        if (d.provider.read_weight_i8(d.provider.context, k, column,
                                      std::min<size_t>(IM2P_DIM, d.n - column),
                                      row + column) != IM2P_OK)
          return IM2P_ERROR;
    } else {
      std::copy_n(static_cast<const int8_t *>(d.weights) +
                      k * d.weight_row_stride_bytes,
                  d.n, row);
    }
    if (!values_valid(row, d.n, d.weight_bits))
      return IM2P_INVALID_LAYOUT;
  }
  for (size_t block = 0; block < blocks; ++block) {
    auto *row = out.scales.data() + block * d.n;
    if (provider) {
      for (size_t column = 0; column < d.n; column += IM2P_DIM)
        if (d.provider.read_scale(d.provider.context, block, column,
                                  std::min<size_t>(IM2P_DIM, d.n - column),
                                  row + column) != IM2P_OK)
          return IM2P_ERROR;
    } else {
      std::copy_n(d.scales + block * d.scale_row_stride + d.scale_column_offset,
                  d.n, row);
    }
    if (!std::all_of(row, row + d.n, hp1::valid_carrier))
      return IM2P_INVALID_LAYOUT;
  }
  return IM2P_OK;
}

int execute(const Operands &operands, const void *activations, size_t stride,
            size_t first_row, size_t rows) {
  const auto &d = operands.descriptor;
  if (!activations || !extent(rows, d.k, stride) || first_row >= d.m ||
      rows > d.m - first_row)
    return IM2P_INVALID_LAYOUT;
  const auto *a = static_cast<const int8_t *>(activations);
  for (size_t row = 0; row < rows; ++row)
    if (!values_valid(a + row * stride, d.k, d.activation_bits))
      return IM2P_INVALID_LAYOUT;
  std::vector<int32_t> output(rows * d.n, 0), partial(d.n);
  {
    TimingScope matmul("functional.matmul");
    for (size_t row = 0; row < rows; ++row) {
      auto *acc = output.data() + row * d.n;
      for (size_t begin = 0; begin < d.k;
           begin += std::min<size_t>(IM2P_DIM, 32)) {
        std::fill(partial.begin(), partial.end(), 0);
        const size_t end =
            std::min(d.k, begin + std::min<size_t>(IM2P_DIM, 32));
        for (size_t k = begin; k < end; ++k) {
          const int32_t activation = a[row * stride + k];
          const auto *weight = operands.weights.data() + k * d.n;
          for (size_t column = 0; column < d.n; ++column)
            partial[column] += activation * weight[column];
        }
        const auto *scale = operands.scales.data() + (begin / 32) * d.n;
        for (size_t column = 0; column < d.n; ++column)
          acc[column] =
              hp1::accumulate(acc[column], hp1::apply_validated(partial[column],
                                                                scale[column]));
      }
    }
  }
  TimingScope reconstruction("functional.output");
  std::vector<int64_t> lanes(std::min<size_t>(d.n, IM2P_DIM));
  for (size_t row = 0; row < rows; ++row) {
    if (!d.provider.write_output) {
      std::copy_n(output.data() + row * d.n, d.n,
                  d.output + (first_row + row) * d.output_row_stride);
      continue;
    }
    for (size_t column = 0; column < d.n; column += IM2P_DIM) {
      const size_t count = std::min<size_t>(IM2P_DIM, d.n - column);
      std::copy_n(output.data() + row * d.n + column, count, lanes.data());
      if (d.provider.write_output(d.provider.context, 0, first_row + row,
                                  column, count, lanes.data(),
                                  d.output_domain) != IM2P_OK)
        return IM2P_ERROR;
    }
  }
  return IM2P_OK;
}

int prepare_runs(Operands &out, const im2p_matmul_desc_t &d,
                 const std::vector<im2p_compact_run_t> &runs,
                 uint32_t original_k) {
  TimingScope materialize("functional.materialize");
  if (!identity(d.abi_version, d.activation_bits, d.activation_storage_bytes,
                d.weight_bits, d.weight_storage_bytes, d.dim))
    return IM2P_CONFIGURATION_MISMATCH;
  if (d.vector_op != IM2P_VECTOR_LEFT_SHIFT ||
      d.output_domain != IM2P_OUTPUT_SCU_FINAL || d.block_size != 32 ||
      d.m > UINT32_MAX || d.n > UINT32_MAX || d.k > UINT32_MAX ||
      !extent(d.m, d.k, d.activation_row_stride_bytes) ||
      !extent(d.k, d.n, d.weight_row_stride_bytes) ||
      !extent(d.m, d.n, d.output_row_stride, sizeof(int32_t)) ||
      !extent(d.k, d.n, d.n) || !extent(runs.size(), d.n, d.n) ||
      !d.activations || (!d.weights && !d.provider.read_weight_i8) ||
      (!d.scales && !d.provider.read_scale) ||
      (!d.output && !d.provider.write_output) ||
      d.provider.read_weight_i16)
    return IM2P_INVALID_LAYOUT;
  if (!d.provider.read_scale &&
      (d.scale_valid_columns < d.n ||
       d.scale_column_offset > d.scale_row_stride ||
       d.n > d.scale_row_stride - d.scale_column_offset ||
       !extent(runs.size(), d.scale_column_offset + d.n,
               d.scale_row_stride, sizeof(uint32_t)) ||
       (runs.size() - 1) * d.scale_row_stride + d.scale_column_offset + d.n >
           d.scale_values_len))
    return IM2P_INVALID_LAYOUT;
  uint64_t end = 0;
  uint32_t previous = 0;
  for (size_t index = 0; index < runs.size(); ++index) {
    const auto &run = runs[index];
    if (!run.compact_k_count || run.compact_k_count > 32 ||
        run.compact_k_begin != end ||
        (index && run.original_block_id <= previous) ||
        run.original_block_id > UINT16_MAX / std::max(1u, 32u / IM2P_DIM) ||
        static_cast<uint32_t>(__builtin_popcount(run.original_k_mask)) !=
            run.compact_k_count)
      return IM2P_INVALID_LAYOUT;
    for (uint32_t bit = 0; bit < 32; ++bit)
      if ((run.original_k_mask & (1u << bit)) &&
          uint64_t(run.original_block_id) * 32 + bit >=
              uint64_t(original_k))
        return IM2P_INVALID_LAYOUT;
    end += run.compact_k_count;
    if (end > d.k) return IM2P_INVALID_LAYOUT;
    previous = run.original_block_id;
  }
  if (end != d.k) return IM2P_INVALID_LAYOUT;
  out.descriptor = d;
  out.weights.resize(d.k * d.n);
  out.scales.resize(runs.size() * d.n);
  for (size_t k = 0; k < d.k; ++k) {
    auto *row = out.weights.data() + k * d.n;
    if (d.provider.read_weight_i8) {
      for (size_t column = 0; column < d.n; column += IM2P_DIM)
        if (d.provider.read_weight_i8(
                d.provider.context, k, column,
                std::min<size_t>(IM2P_DIM, d.n - column), row + column) !=
            IM2P_OK)
          return IM2P_ERROR;
    } else {
      std::copy_n(static_cast<const int8_t *>(d.weights) +
                      k * d.weight_row_stride_bytes,
                  d.n, row);
    }
    if (!values_valid(row, d.n, d.weight_bits)) return IM2P_INVALID_LAYOUT;
  }
  for (size_t index = 0; index < runs.size(); ++index) {
    auto *row = out.scales.data() + index * d.n;
    if (d.provider.read_scale) {
      for (size_t column = 0; column < d.n; column += IM2P_DIM)
        if (d.provider.read_scale(
                d.provider.context, index, column,
                std::min<size_t>(IM2P_DIM, d.n - column), row + column) !=
            IM2P_OK)
          return IM2P_ERROR;
    } else {
      std::copy_n(d.scales + index * d.scale_row_stride + d.scale_column_offset,
                  d.n, row);
    }
    if (!std::all_of(row, row + d.n, hp1::valid_carrier))
      return IM2P_INVALID_LAYOUT;
  }
  return IM2P_OK;
}

int execute_runs(const Operands &operands,
                 const std::vector<im2p_compact_run_t> &runs) {
  const auto &d = operands.descriptor;
  const auto *a = static_cast<const int8_t *>(d.activations);
  for (size_t row = 0; row < d.m; ++row)
    if (!values_valid(a + row * d.activation_row_stride_bytes, d.k,
                      d.activation_bits))
      return IM2P_INVALID_LAYOUT;
  std::vector<int32_t> output(d.m * d.n, 0), partial(d.n);
  {
    TimingScope matmul("functional.matmul");
    for (size_t row = 0; row < d.m; ++row) {
      auto *acc = output.data() + row * d.n;
      for (size_t index = 0; index < runs.size(); ++index) {
        const auto &run = runs[index];
        for (size_t local = 0; local < run.compact_k_count;
             local += IM2P_DIM) {
          std::fill(partial.begin(), partial.end(), 0);
          const size_t end = std::min<size_t>(run.compact_k_count,
                                              local + IM2P_DIM);
          for (size_t pos = local; pos < end; ++pos) {
            const size_t k = run.compact_k_begin + pos;
            const int32_t activation = a[row * d.activation_row_stride_bytes + k];
            const auto *weight = operands.weights.data() + k * d.n;
            for (size_t column = 0; column < d.n; ++column)
              partial[column] += activation * weight[column];
          }
          const auto *scale = operands.scales.data() + index * d.n;
          for (size_t column = 0; column < d.n; ++column)
            acc[column] = hp1::accumulate(
                acc[column], hp1::apply_validated(partial[column], scale[column]));
        }
      }
    }
  }
  TimingScope reconstruction("functional.output");
  if (d.provider.write_output) {
    std::vector<int64_t> values(output.begin(), output.end());
    return d.provider.write_output(d.provider.context, 0, 0, 0,
                                   values.size(), values.data(),
                                   d.output_domain) == IM2P_OK
               ? IM2P_OK
               : IM2P_ERROR;
  }
  for (size_t row = 0; row < d.m; ++row)
    std::copy_n(output.data() + row * d.n, d.n,
                d.output + row * d.output_row_stride);
  return IM2P_OK;
}

im2p_matmul_desc_t descriptor(const im2p_stripe_work_desc_t &d) noexcept {
  im2p_matmul_desc_t result{};
#define COPY(field) result.field = d.field
  COPY(abi_version);
  COPY(activation_bits);
  COPY(activation_storage_bytes);
  COPY(weight_bits);
  COPY(weight_storage_bytes);
  COPY(dim);
  COPY(weights);
  COPY(scales);
  COPY(output);
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
  COPY(scale_column_offset);
  COPY(scale_valid_columns);
  COPY(scale_values_len);
  COPY(vector_op);
  COPY(output_domain);
  COPY(work_context);
  COPY(provider);
#undef COPY
  return result;
}
} // namespace im2p::cpu_functional
