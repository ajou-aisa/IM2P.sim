#ifndef IM2P_RUN_AWARE_NUMERIC_FIXTURE_HPP
#define IM2P_RUN_AWARE_NUMERIC_FIXTURE_HPP

#include <algorithm>
#include <charconv>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <limits>
#include <sstream>
#include <string>
#include <vector>

namespace {
template <typename T>
std::vector<T> read_numbers(std::istream &input, const char *label,
                            std::size_t count) {
  check(count <= 1'000'000, "production fixture count exceeds limit");
  std::string line, token;
  check(static_cast<bool>(std::getline(input, line)),
        "production fixture line is missing");
  std::istringstream fields(line);
  if (*label) {
    check(static_cast<bool>(fields >> token) && token == label,
          "production fixture label mismatch");
  }
  std::vector<T> values;
  values.reserve(count);
  for (std::size_t i = 0; i < count; ++i) {
    check(static_cast<bool>(fields >> token),
          "production fixture value is missing");
    T value{};
    const auto parsed = std::from_chars(token.data(), token.data() + token.size(), value);
    check(parsed.ec == std::errc{} && parsed.ptr == token.data() + token.size(),
          "production fixture value is invalid");
    values.push_back(value);
  }
  check(!(fields >> token), "production fixture has extra values");
  return values;
}

std::size_t fixture_size(std::uint64_t left, std::uint64_t right) {
  check(left && right && left <= 1'000'000 / right,
        "production fixture shape exceeds limit");
  return static_cast<std::size_t>(left * right);
}

struct ProductionCase {
  RmdRunWork work;
  std::vector<std::int32_t> expected;
  std::uint64_t activation_stride_bytes = 0, weight_stride_bytes = 0;
  std::uint64_t output_stride_bytes = 0, scale_stride_elements = 0;
  std::uint64_t work_context = 0;
};

ProductionCase load_production_case(const char *path) {
  std::ifstream input(path);
  check(input.is_open(), "production fixture could not be opened");
  std::string version;
  check(static_cast<bool>(std::getline(input, version)) &&
            version == "RMD_RUN_WORK_V1",
        "production fixture version mismatch");
  const auto d = read_numbers<std::uint64_t>(input, "descriptor", 23);
  const auto g = read_numbers<std::uint64_t>(input, "geometry", 16);
  const auto v = read_numbers<std::uint64_t>(input, "runs", 4);
  check(d[0] == IM2P_ABI_VERSION && d[1] == IM2P_ACTIVATION_BITS &&
            d[2] == 1 && d[3] == IM2P_OPERAND_BITS && d[4] == 1 &&
            d[5] == dim && d[6] && d[7] && d[8] &&
            d[6] <= UINT32_MAX && d[7] <= UINT32_MAX && d[8] <= UINT32_MAX &&
            d[9] >= d[8] && d[10] >= d[7] && d[11] >= d[7] &&
            d[12] && d[13] && d[14] == 32 && d[15] <= UINT32_MAX &&
            d[16] == d[7] && d[17] == 0 && d[18] == d[7] &&
            d[20] == IM2P_VECTOR_LEFT_SHIFT &&
            d[21] == IM2P_OUTPUT_SCU_FINAL &&
            g[0] == IM2P_PRODUCTION_GEOMETRY_VERSION &&
            g[1] == sizeof(im2p_production_geometry_v1_t) &&
            g[2] == d[1] && g[3] == d[3] && g[4] == d[5] &&
            g[5] == IM2P_GEOMETRY_FULL && g[6] == d[6] &&
            g[7] == d[7] && g[8] == d[8] &&
            g[9] && g[10] && g[11] && g[12] == d[6] &&
            g[13] == 0 && g[14] == d[6] && g[15] == 0 &&
            v[0] == IM2P_COMPACT_RUNS_VERSION &&
            v[1] == sizeof(im2p_compact_runs_t) && v[2] == d[15] &&
            v[3] && v[3] <= d[8],
        "production fixture metadata mismatch");
  const auto a_count = fixture_size(d[6], d[8]);
  const auto b_count = fixture_size(d[8], d[7]);
  const auto carrier_count = fixture_size(v[3], d[7]);
  const auto output_count = fixture_size(d[6], d[7]);
  check(d[19] == carrier_count, "production fixture carrier count mismatch");
  ProductionCase result;
  check(d[11] <= UINT64_MAX / 4, "production fixture output stride overflow");
  result.activation_stride_bytes = d[9];
  result.weight_stride_bytes = d[10];
  result.output_stride_bytes = d[11] * 4;
  result.scale_stride_elements = d[16];
  result.work_context = d[22];
  auto &work = result.work;
  work.work_id = 0;
  work.host_slot = 0;
  work.plan = {d[6], d[7], d[8], g[9], g[10], g[11], d[6],
               Mode::full, WorkKind::dense_hp1_final};
  work.geometry = {static_cast<std::uint32_t>(g[0]),
                   static_cast<std::uint32_t>(g[1]),
                   static_cast<std::uint32_t>(g[2]),
                   static_cast<std::uint32_t>(g[3]),
                   static_cast<std::uint32_t>(g[4]),
                   static_cast<std::uint32_t>(g[5]),
                   g[6], g[7], g[8], g[9], g[10], g[11],
                   g[12], g[13], g[14], g[15]};
  work.original_k = static_cast<std::uint32_t>(v[2]);
  for (std::size_t i = 0; i < v[3]; ++i) {
    const auto r = read_numbers<std::uint64_t>(input, "run", 4);
    check(std::all_of(r.begin(), r.end(),
                      [](auto value) { return value <= UINT32_MAX; }),
          "production fixture run value exceeds uint32");
    work.runs.push_back({static_cast<std::uint32_t>(r[0]),
                         static_cast<std::uint32_t>(r[1]),
                         static_cast<std::uint32_t>(r[2]),
                         static_cast<std::uint32_t>(r[3])});
  }
  check(read_numbers<std::uint64_t>(input, "ROW_MAP", 1).front() == d[6],
        "production fixture row map length mismatch");
  for (std::size_t i = 0; i < d[6]; ++i)
    (void)read_numbers<std::uint64_t>(input, "", 2);
  const auto read_signed = [&](const char *label, std::size_t count) {
    check(read_numbers<std::uint64_t>(input, label, 1).front() == count,
          "production fixture tensor length mismatch");
    return read_numbers<std::int64_t>(input, "", count);
  };
  const auto a = read_signed("A", a_count);
  const auto b = read_signed("B", b_count);
  const auto min_code = -(std::int64_t{1} << (IM2P_ACTIVATION_BITS - 1));
  for (const auto value : a) {
    check(value >= min_code && value < -min_code,
          "production fixture activation code out of range");
    work.activations.push_back(static_cast<std::int8_t>(value));
  }
  for (const auto value : b) {
    check(value >= min_code && value < -min_code,
          "production fixture weight code out of range");
    work.weights.push_back(static_cast<std::int8_t>(value));
  }
  check(read_numbers<std::uint64_t>(input, "CARRIERS", 1).front() == carrier_count,
        "production fixture carrier length mismatch");
  for (const auto value : read_numbers<std::uint64_t>(input, "", carrier_count)) {
    check(value <= UINT32_MAX, "production fixture carrier exceeds uint32");
    work.carriers.push_back(static_cast<std::uint32_t>(value));
  }
  for (const auto value : read_signed("OUTPUT", output_count)) {
    check(value >= INT32_MIN && value <= INT32_MAX,
          "production fixture output exceeds int32");
    result.expected.push_back(static_cast<std::int32_t>(value));
  }
  std::string extra;
  check(!std::getline(input, extra), "production fixture has trailing lines");
  return result;
}
}

#endif
