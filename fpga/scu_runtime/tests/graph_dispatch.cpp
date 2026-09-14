// Test-only scheduler integration. Link only standard ggml/base, not the adapter
// or simulator archive. The shipped backend module supplies both implementations.
#define GGML_GEMMINI_MATMUL_IMPLEMENTATION 1
#include "ggml-gemmini-fpga.hpp"
#include "ggml-gemmini-args.h"
#include "ggml-gemmini.h"
#include "ggml-backend.h"
#include "ggml-alloc.h"
#include "ggml-quants.h"
#include "im2p_sim.h"
#include <gemmini.h>
#include <algorithm>
#include <array>
#include <atomic>
#include <bit>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <dlfcn.h>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <vector>

using namespace ggml::gemmini;
static std::atomic<unsigned> simulator_creates{0}, simulator_executes{0}, simulator_streams{0};
// Executable symbol interposition, checked below and with the module's dynamic
// relocations by the runner. A simulator call aborts; no success stub exists.
extern "C" __attribute__((visibility("default"))) im2p_sim_t * im2p_sim_create() {
    ++simulator_creates; std::fputs("FORBIDDEN simulator create\n", stderr); std::abort();
}
extern "C" __attribute__((visibility("default"))) int im2p_execute_matmul_extended(
    im2p_sim_t *, const im2p_matmul_desc_t *, im2p_work_stats_extended_t *) {
    ++simulator_executes; std::fputs("FORBIDDEN simulator execute\n", stderr); std::abort();
}
extern "C" __attribute__((visibility("default"))) int im2p_begin_striped_matmul(
    im2p_sim_t *, const im2p_stripe_work_desc_t *, im2p_stream_t **) {
    ++simulator_streams; std::fputs("FORBIDDEN simulator stream\n", stderr); std::abort();
}
static void require(bool ok, const char * message) {
    if (!ok) throw std::runtime_error(message);
}

struct Observation {
    std::vector<float> expected;
    std::vector<int32_t> raw;
    size_t calls = 0, scalars = 0, reference_fragments = 0;
    size_t m = 0, n = 0, blocks = 0;
    std::vector<int64_t> block_raw;
    std::vector<uint8_t> coverage;
    const std::vector<uint8_t> *original_q8_0 = nullptr;
    bool external = false;
    bool edges = false;
    size_t seed = 0;
    std::string mode;
    std::filesystem::path capture_directory;
    size_t intermediate_scalars = 0;
    std::string failure;
    void begin(size_t rows, size_t columns, size_t k) {
        m = rows; n = columns; blocks = k / 32;
        block_raw.assign(external ? m * n * blocks : 0, 0); coverage.assign(block_raw.size(), 0);
        intermediate_scalars = 0;
        failure.clear();
    }
    static void block(void *opaque, size_t b, size_t row, size_t col, size_t count, const int64_t *values) noexcept {
        auto &self = *static_cast<Observation *>(opaque);
        self.intermediate_scalars += count;
        if (!self.external) {
            self.failure = "SCU emitted an intermediate dense callback"; return;
        }
        if (!values || b >= self.blocks || row >= self.m || col >= self.n || count > self.n - col) {
            self.failure = "External callback extent"; return;
        }
        for (size_t lane = 0; lane < count; ++lane) {
            const size_t index = (b * self.m + row) * self.n + col + lane;
            if (self.coverage[index]++) self.failure = "External duplicate contribution";
            self.block_raw[index] = values[lane];
        }
    }
    static void external_complete(const ggml_gemmini_args_t &args, void *opaque) {
        auto &self = *static_cast<Observation *>(opaque);
        require(self.failure.empty(), self.failure.c_str());
        require(!simulator_creates && !simulator_executes && !simulator_streams, "simulator fallback");
        require(args.I == self.m && args.J == self.n && args.K / 32 == self.blocks &&
                std::all_of(self.coverage.begin(), self.coverage.end(), [](uint8_t value) {return value == 1;}),
                "External incomplete contribution coverage");
        const bool hp1 = args.weight_format == ggml_gemmini_args_t::im2p_weight_format_t::q8_hp1;
        require(hp1 ? args.has_q8_hp1_im2p_contract() : args.has_q8_h1_im2p_contract(), "native H1/HP1 contract");
        if (self.original_q8_0) {
            require(!hp1 && self.original_q8_0->size() == args.J * self.blocks * sizeof(block_q8_0),
                    "Q8_0 source provenance extent");
            std::vector<block_q8_0> row(self.blocks);
            for (size_t col = 0; col < args.J; ++col) {
                std::memcpy(row.data(), self.original_q8_0->data() + col * row.size() * sizeof(block_q8_0),
                            row.size() * sizeof(block_q8_0));
                float low = INFINITY, high = 0;
                for (const auto &source : row) {
                    const float scale = ggml_fp16_to_fp32(source.d);
                    require(std::isfinite(scale) && scale > 0, "Q8_0 positive-scale fixture");
                    low = std::min(low, scale); high = std::max(high, scale);
                }
                // Independent reference from original fp16 scales, after RTL;
                // no production row helper or returned H1 metadata as input.
                // Match the ordinary host's existing -ffast-math reciprocal.
                const float scale = high == low ? low : (high - low) * (1.0f / 255.0f);
                const uint16_t offset = high == low ? 1 : uint16_t(std::clamp(std::round(double(low) / scale), 0.0, 65535.0));
                for (size_t b = 0; b < self.blocks; ++b) {
                    const auto &actual = args.q8_h1_blocks[col * self.blocks + b];
                    const uint8_t code = high == low ? 0 : uint8_t(std::clamp(
                        std::round(double(ggml_fp16_to_fp32(row[b].d)) / scale) - offset, 0.0, 255.0));
                    if (std::memcmp(actual.qs, row[b].qs, 32) != 0 || actual.c_b != code ||
                        actual.R != offset || std::bit_cast<uint32_t>(actual.s_rf) != std::bit_cast<uint32_t>(scale)) {
                        std::fprintf(stderr, "Q8_0 provenance col=%zu block=%zu scale=%a/%a R=%u/%u code=%u/%u\n",
                                     col, b, actual.s_rf, scale, actual.R, offset, actual.c_b, code);
                        throw std::runtime_error("Q8_0 original codes/independent scale provenance mismatch");
                    }
                }
            }
            std::cout << "Q8_0_PROVENANCE PASS blocks=" << args.J * self.blocks
                      << " codes=original scale_reference=original_fp16 independent=1 endpoint=after_RTL\n";
        }
        const auto &meta = std::get<quants::act::exsia::Meta>(args.act_quant.storage());
        const auto geometry = args.activation_geometry(); require(geometry.ok(), "External activation geometry");
        self.expected.assign(args.I * args.J, 0);
        for (size_t row = 0; row < args.I; ++row) {
            const auto *a = reinterpret_cast<const int8_t *>(args.A.raw_data()) + row * args.A.row_stride_bytes;
            const size_t stripe = row / geometry.geometry.stripe_rows;
            require(stripe < meta.theta.size() && meta.theta[stripe] != INT16_MIN, "External theta identity");
            const double activation_scale = std::ldexp(1.0, meta.theta[stripe]);
            for (size_t col = 0; col < args.J; ++col) {
                double sum = 0;
                for (size_t b = 0; b < self.blocks; ++b) {
                    const size_t index = col * self.blocks + b;
                    const int8_t *codes = hp1 ? args.q8_hp1_blocks[index].qs : args.q8_h1_blocks[index].qs;
                    int64_t dot = 0;
                    for (size_t lane = 0; lane < 32; ++lane) dot += int64_t(a[b * 32 + lane]) * codes[lane];
                    require(self.block_raw[(b * args.I + row) * args.J + col] == dot, "External raw int64 mismatch");
                    double factor;
                    if (hp1) {
                        const auto &weight = args.q8_hp1_blocks[index];
                        factor = weight.m == INT16_MIN ? 0.0 : double(gemmini_ldexp_fast_pos(weight.channel_scale, weight.m));
                    } else {
                        const auto &weight = args.q8_h1_blocks[index];
                        factor = double(weight.s_rf) * double(uint32_t(weight.c_b) + weight.R);
                    }
                    // R0 main ReducerSlot accumulates blocks in double, then
                    // casts once at output publication (frontend lines 1070/1087).
                    sum += double(dot) * factor * activation_scale;
                    ++self.reference_fragments;
                }
                const float result = float(sum);
                self.expected[row * args.J + col] += result;
            }
        }
        size_t nonzero_residuals = 0;
        for (const auto &handle : meta.rmd_packets) {
            require(bool(handle), "null residual packet");
            const auto &packet = *handle;
            require(packet.digit_bits == 8 && packet.row_begin + packet.row_count <= args.I &&
                    packet.logical_k == args.K && packet.logical_j == args.J, "native residual packet extent");
            std::vector<__int128> correction(packet.row_count * args.J, 0);
            for (const auto &block : packet.blocks) {
                std::vector<int64_t> wide(packet.row_count * block.compact_k_count, 0);
                for (size_t position = 0; position < block.active_lane_count; ++position) {
                    const auto lane = block.lane_ids[position];
                    require(lane < 4, "residual radix lane");
                    for (size_t row = 0; row < packet.row_count; ++row)
                        for (size_t k = 0; k < block.compact_k_count; ++k) {
                            const size_t index = block.activation_offset +
                                (position * block.rows_padded + row) * block.padded_k_count + k;
                            require(index < packet.stacked_activation.signed_int8.size(), "residual digit extent");
                            wide[row * block.compact_k_count + k] +=
                                int64_t(packet.stacked_activation.signed_int8[index]) * (INT64_C(1) << (lane * 8));
                        }
                }
                for (int64_t value : wide) nonzero_residuals += value != 0;
                for (size_t row = 0; row < packet.row_count; ++row)
                    for (size_t col = 0; col < args.J; ++col) {
                        const size_t index = col * self.blocks + block.block_id;
                        const int8_t *codes = hp1 ? args.q8_hp1_blocks[index].qs : args.q8_h1_blocks[index].qs;
                        __int128 dot = 0;
                        for (size_t k = 0; k < block.compact_k_count; ++k) {
                            const auto original = packet.k_indices.at(block.k_index_offset + k);
                            require(original < 32, "residual original K mapping");
                            dot += __int128(wide[row * block.compact_k_count + k]) * codes[original];
                        }
                        uint64_t factor;
                        if (hp1) {
                            const auto exponent = args.q8_hp1_blocks[index].m;
                            require(exponent == INT16_MIN || (exponent >= 0 && exponent < 63), "residual HP1 shift");
                            factor = exponent == INT16_MIN ? 0 : (UINT64_C(1) << exponent);
                        } else factor = uint32_t(args.q8_h1_blocks[index].c_b) + args.q8_h1_blocks[index].R;
                        correction[row * args.J + col] += dot * factor;
                    }
            }
            for (size_t row = 0; row < packet.row_count; ++row)
                for (size_t col = 0; col < args.J; ++col) {
                    const __int128 integer = correction[row * args.J + col];
                    require(integer >= INT64_MIN && integer <= INT64_MAX, "residual checked INT64 overflow");
                    const float column_scale = hp1 ? args.q8_hp1_blocks[col * self.blocks].channel_scale :
                                                    args.q8_h1_blocks[col * self.blocks].s_rf;
                    const size_t global_row = row + packet.row_begin;
                    const double activation = std::ldexp(1.0, meta.theta.at(global_row / geometry.geometry.stripe_rows));
                    self.expected[global_row * args.J + col] += float(double(int64_t(integer)) * double(column_scale) * activation);
                }
        }
        size_t direct_events = 0;
        for (const auto &handle : meta.direct_residuals) {
            require(bool(handle) && meta.rmd_packets.empty(), "mixed/null direct residual payload");
            const auto &payload = *handle;
            require(payload.row_begin + payload.row_count <= args.I &&
                    payload.logical_k == args.K && payload.logical_j == args.J, "direct residual extent");
            std::vector<__int128> correction(payload.row_count * args.J, 0);
            for (const auto &event : payload.events) {
                require(event.local_row < payload.row_count && event.original_k < args.K,
                        "direct residual event extent");
                direct_events += event.residual != 0;
                for (size_t col = 0; col < args.J; ++col) {
                    const size_t index = col * self.blocks + event.original_k / 32;
                    const int8_t *codes = hp1 ? args.q8_hp1_blocks[index].qs : args.q8_h1_blocks[index].qs;
                    uint64_t factor;
                    if (hp1) {
                        const auto shift = args.q8_hp1_blocks[index].m;
                        require(shift == INT16_MIN || (shift >= 0 && shift < 63), "direct HP1 shift");
                        factor = shift == INT16_MIN ? 0 : (UINT64_C(1) << shift);
                    } else factor = uint32_t(args.q8_h1_blocks[index].c_b) + args.q8_h1_blocks[index].R;
                    correction[event.local_row * args.J + col] +=
                        __int128(event.residual) * codes[event.original_k % 32] * factor;
                }
            }
            for (size_t row = 0; row < payload.row_count; ++row)
                for (size_t col = 0; col < args.J; ++col) {
                    const __int128 integer = correction[row * args.J + col];
                    require(integer >= INT64_MIN && integer <= INT64_MAX, "direct residual INT64 overflow");
                    const float scale = hp1 ? args.q8_hp1_blocks[col * self.blocks].channel_scale :
                                              args.q8_h1_blocks[col * self.blocks].s_rf;
                    const size_t global = payload.row_begin + row;
                    const double activation = std::ldexp(1.0, meta.theta.at(global / geometry.geometry.stripe_rows));
                    self.expected[global * args.J + col] += float(double(int64_t(integer)) * double(scale) * activation);
                }
        }
        nonzero_residuals += direct_events;
        if (std::getenv("IM2P_TEST_NONZERO_RMD")) require(nonzero_residuals > 0, "nonzero residual not exercised");
        for (size_t row = 0; row < args.I; ++row)
            for (size_t col = 0; col < args.J; ++col)
                require(std::bit_cast<uint32_t>(self.expected[row * args.J + col]) ==
                        std::bit_cast<uint32_t>(args.f_out[row * args.stride_f_out + col * args.col_stride_f_out]),
                        "main External dense/residual f_out bit-exact mismatch");
        require(std::any_of(self.expected.begin(), self.expected.end(), [](float value) {return value != 0;}),
                "External vacuous output");
        if (const char *prefix = std::getenv("IM2P_TEST_Q8_0_CAPTURE")) {
            require(self.original_q8_0 && meta.rmd_packets.empty(), "Q8_0 capture requires original bytes/direct residual");
            const std::string path = std::string(prefix) + ".invocation-" + std::to_string(self.calls) + ".bin";
            require(!std::filesystem::exists(path), "Q8_0 capture already exists");
            std::ofstream file(path, std::ios::binary);
            file.exceptions(std::ios::badbit | std::ios::failbit);
            const auto write = [&]<class T>(const T *data, size_t count) {
                file.write(reinterpret_cast<const char *>(data), count * sizeof(T));
            };
            // Q8RPLY01 schema 1: numerical inputs/results only, no call-order claim.
            const std::array<uint64_t, 12> header{0x3130594c50523851ULL, 1, args.I, args.J, args.K,
                args.tile_I, args.tile_J, args.tile_K, geometry.geometry.stripe_rows,
                args.A.row_stride_bytes, sizeof(block_q8_h1), meta.direct_residuals.size()};
            write(header.data(), header.size());
            const std::array<int32_t, 3> scale_meta{meta.e_s, meta.rho, meta.sigma};
            write(scale_meta.data(), scale_meta.size());
            write(meta.theta.data(), geometry.geometry.stripe_count);
            write(self.original_q8_0->data(), self.original_q8_0->size());
            write(args.q8_h1_blocks, args.J * self.blocks);
            write(reinterpret_cast<const uint8_t *>(args.A.raw_data()), args.I * args.A.row_stride_bytes);
            for (const auto &p : meta.direct_residuals) {
                const std::array<uint64_t, 6> extent{p->stripe_id, p->row_begin, p->row_count,
                                                    p->logical_k, p->logical_j, p->events.size()};
                write(extent.data(), extent.size());
                for (const auto &e : p->events) {
                    const std::array<int64_t, 3> event{int64_t(e.local_row), int64_t(e.original_k), e.residual};
                    write(event.data(), event.size());
                }
            }
            write(self.block_raw.data(), self.block_raw.size());
            for (size_t row = 0; row < args.I; ++row) for (size_t col = 0; col < args.J; ++col)
                write(&args.f_out[row * args.stride_f_out + col * args.col_stride_f_out], 1);
            file.close();
            std::cout << "Q8_0_CAPTURE PASS schema=1 scope=numerical_same_input path=" << path << '\n';
        }
        ++self.calls; self.scalars += self.block_raw.size();
        std::cout << "GRAPH_EXTERNAL_REFERENCE PASS native=" << (hp1 ? "Q8_HP1" : "Q8_H1")
                  << " M=" << args.I << " N=" << args.J << " K=" << args.K
                  << " raw=" << self.block_raw.size() << " f_out=" << args.I * args.J
                  << " stripes=" << geometry.geometry.stripe_count << " residual_nonzero=" << nonzero_residuals
                  << " direct_residual_events=" << direct_events
                  << " domain=1 contract=main_external exact=1\n";
    }
    // Independent G1/G2 from the validated host_dispatch test, applied to actual
    // quantized activation/weight metadata only after FPGA RTL completion.
    static void complete(const ggml_gemmini_args_t & args, const std::vector<int32_t> & actual, void * opaque) {
        auto & self = *static_cast<Observation *>(opaque);
        require(!simulator_creates && !simulator_executes && !simulator_streams, "simulator fallback");
        const bool hp1 = args.weight_format == ggml_gemmini_args_t::im2p_weight_format_t::q8_hp1;
        require(actual.size() == args.I * args.J &&
                (hp1 ? args.has_q8_hp1_im2p_contract() : args.has_q8_h1_im2p_contract()),
                "final integer domain/count");
        const auto & meta = std::get<quants::act::exsia::Meta>(args.act_quant.storage());
        require(meta.direct_residuals.empty() && meta.rmd_packets.empty(), "RMD OFF");
        const auto geometry = args.activation_geometry();
        require(geometry.ok(), "activation geometry");
        if (args.I == 321) {
            require(geometry.geometry.stripe_rows == 160 && geometry.geometry.stripe_count == 3 &&
                    meta.theta.size() == 3, "automatic 160/160/1 geometry");
            require(meta.theta[0] != meta.theta[1] && meta.theta[0] != meta.theta[2] &&
                    meta.theta[1] != meta.theta[2], "distinct stripe theta control");
        }
        self.expected.assign(args.I * args.J, 0);
        std::vector<int32_t> expected_raw(actual.size());
        size_t scu_clamps = 0, accumulator_clamps = 0;
        auto clamp = [](int64_t value, size_t & count) {
            if (value > INT32_MAX) { ++count; return INT32_MAX; }
            if (value < INT32_MIN) { ++count; return INT32_MIN; }
            return int32_t(value);
        };
        for (size_t row = 0; row < args.I; ++row) {
            const auto * a = reinterpret_cast<const int8_t *>(args.A.raw_data()) + row * args.A.row_stride_bytes;
            const size_t stripe = row / geometry.geometry.stripe_rows;
            require(stripe < meta.theta.size() && meta.theta[stripe] != INT16_MIN, "theta identity");
            const float activation_scale = std::ldexp(1.0f, meta.theta[stripe]);
            require(std::isfinite(activation_scale) && activation_scale > 0, "activation scale");
            for (size_t col = 0; col < args.J; ++col) {
                const size_t base = col * (args.K / 32);
                const float shared = hp1 ? args.q8_hp1_blocks[base].channel_scale : args.q8_h1_blocks[base].s_rf;
                const uint16_t offset = hp1 ? 0 : args.q8_h1_blocks[base].R;
                require(std::isfinite(shared) && shared >= 0, "shared channel scale");
                int32_t accumulator = 0;
                for (size_t first = 0; first < args.K; first += 16) {
                    const size_t index = base + first / 32;
                    const auto *codes = hp1 ? args.q8_hp1_blocks[index].qs : args.q8_h1_blocks[index].qs;
                    int64_t partial = 0;
                    const size_t count = std::min({size_t(16), args.K - first, size_t(32) - first % 32});
                    for (size_t lane = 0; lane < count; ++lane)
                        partial += int64_t(a[first + lane]) * int64_t(codes[first % 32 + lane]);
                    ++self.reference_fragments;
                    int32_t scaled;
                    if (hp1) {
                        const auto &weight = args.q8_hp1_blocks[index];
                        require(weight.channel_scale == shared && (weight.m == INT16_MIN || weight.m >= 0),
                                "HP1 shared scale/exponent");
                        if (weight.m == INT16_MIN || partial == 0) scaled = 0;
                        else if (weight.m >= 32) {
                            ++scu_clamps;
                            scaled = partial < 0 ? INT32_MIN : INT32_MAX;
                        } else scaled = clamp(partial * (INT64_C(1) << weight.m), scu_clamps);
                    } else {
                        const auto &weight = args.q8_h1_blocks[index];
                        require(weight.s_rf == shared && weight.R == offset, "shared S/R");
                        const uint32_t beta = uint32_t(weight.c_b) + uint32_t(weight.R);
                        require(beta <= 65790, "H1 uint17 factor");
                        scaled = clamp(partial * int64_t(beta), scu_clamps);
                    }
                    accumulator = first == 0 ? scaled : clamp(int64_t(accumulator) + scaled, accumulator_clamps);
                }
                const size_t index = row * args.J + col;
                require(actual[index] == accumulator, "G1 final integer mismatch");
                expected_raw[index] = accumulator;
                self.expected[index] = float(double(accumulator) * double(shared) * double(activation_scale));
                require(std::bit_cast<uint32_t>(args.f_out[row * args.stride_f_out + col * args.col_stride_f_out]) ==
                        std::bit_cast<uint32_t>(self.expected[index]), "G2 staged f_out mismatch");
            }
        }
        require(std::any_of(actual.begin(), actual.end(), [](int32_t x) { return x != 0; }), "nonzero input control");
        if (self.calls) require(self.raw != actual, "distinct invocation raw control");
        if (!self.capture_directory.empty()) {
            const auto prefix = self.capture_directory / ("invocation-" + std::to_string(self.calls));
            const auto write = [&](const char *suffix, const void *data, size_t count) {
                const auto path = prefix.string() + suffix;
                require(!std::filesystem::exists(path), "graph capture already exists");
                std::ofstream file(path, std::ios::binary);
                file.write(static_cast<const char *>(data), count);
                require(bool(file), "graph capture write failed");
            };
            std::vector<int8_t> activation(args.I * args.K);
            for (size_t row = 0; row < args.I; ++row)
                std::memcpy(activation.data() + row * args.K,
                            static_cast<const uint8_t *>(args.A.raw_data()) + row * args.A.row_stride_bytes, args.K);
            const size_t native_size = hp1 ? sizeof(block_q8_hp1) : sizeof(block_q8_h1);
            const void *weights = hp1 ? static_cast<const void *>(args.q8_hp1_blocks)
                                      : static_cast<const void *>(args.q8_h1_blocks);
            write(".activation-i8.bin", activation.data(), activation.size());
            write(".native-weight.bin", weights, args.J * (args.K / 32) * native_size);
            write(".expected-raw-i32.bin", expected_raw.data(), expected_raw.size() * sizeof(int32_t));
            write(".expected-fout-f32.bin", self.expected.data(), self.expected.size() * sizeof(float));
            const auto path = prefix.string() + ".json";
            require(!std::filesystem::exists(path), "graph capture manifest already exists");
            std::ofstream manifest(path);
            manifest << "{\"schema\":\"scu-graph-fixture-v1\",\"M\":" << args.I << ",\"N\":" << args.J
                     << ",\"K\":" << args.K << ",\"format\":\"" << (hp1 ? "Q8_HP1" : "Q8_H1")
                     << "\",\"mode\":\"" << self.mode << "\",\"RMD\":\"OFF\",\"seed\":" << self.seed
                     << ",\"input_generation\":\"" << (self.edges ? "native_edges" : "quantized")
                     << "\",\"native_block_bytes\":" << native_size << ",\"tile_I\":" << args.tile_I
                     << ",\"tile_J\":" << args.tile_J << ",\"stripe_rows\":" << geometry.geometry.stripe_rows
                     << ",\"stripes\":" << geometry.geometry.stripe_count << ",\"theta\":[";
            for (size_t i = 0; i < meta.theta.size(); ++i) manifest << (i ? "," : "") << meta.theta[i];
            manifest << "],\"numerical_contract\":\"scu_final_integer\",\"requested_op\":" << (hp1 ? 5 : 4)
                     << ",\"output_domain\":2,\"domain_evidence\":\"host_callback_not_wire_echo\",\"golden_source\":\"independent_fragment_integer_and_binary64\",\"final_integer_scalars\":"
                     << expected_raw.size() << "}\n";
            require(bool(manifest), "graph capture manifest write failed");
        }
        self.raw = actual;
        ++self.calls;
        self.scalars += actual.size();
        std::cout << "GRAPH_REFERENCE PASS native=" << (hp1 ? "Q8_HP1" : "Q8_H1")
                  << " M=" << args.I << " N=" << args.J << " K=" << args.K
                  << " raw=" << actual.size() << " f_out=" << args.I * args.J
                  << " requested_op=" << (hp1 ? 5 : 4) << " domain=2 contract=scu_final_integer RMD=OFF"
                  << " final_integer_scalars=" << actual.size() << " f_out_scalars=" << args.I * args.J
                  << " dense_intermediate_scalars=NOT_OBSERVED expected_dense_intermediate_scalars=0"
                  << " intermediate_evidence=transport_coverage_required"
                  << " stripes=" << geometry.geometry.stripe_count << " stripe_rows=" << geometry.geometry.stripe_rows
                  << " scu_clamps=" << scu_clamps << " accumulator_clamps=" << accumulator_clamps
                  << " source=" << (self.edges ? "explicit_native_edge_fixture" : "actual_quantizer_metadata")
                  << " endpoint=after_RTL_before_caller_commit\n";
    }
};

struct Graph {
    ggml_backend_t fpga = nullptr, cpu = nullptr;
    ggml_context * weights = nullptr, * tensors = nullptr;
    ggml_backend_buffer_t weight_buffer = nullptr;
    ggml_backend_sched_t scheduler = nullptr;
    ggml_cgraph * graph = nullptr;
    ggml_tensor * weight = nullptr, * activation = nullptr, * output = nullptr;
    size_t m, n, k;
    std::vector<uint8_t> original_q8_0;
    Graph(ggml_backend_dev_t device, size_t rows, size_t cols, size_t reduction, ggml_type type) : m(rows), n(cols), k(reduction) {
        fpga = ggml_backend_dev_init(device, nullptr);
        cpu = ggml_backend_init_by_type(GGML_BACKEND_DEVICE_TYPE_CPU, nullptr);
        require(fpga && cpu, "standard backends initialization");
        weights = ggml_init({ggml_tensor_overhead() * 2, nullptr, true});
        tensors = ggml_init({ggml_tensor_overhead() * 8 + ggml_graph_overhead(), nullptr, true});
        require(weights && tensors, "tensor metadata allocation");
        weight = ggml_new_tensor_2d(weights, type, k, n);
        activation = ggml_new_tensor_2d(tensors, GGML_TYPE_F32, k, m);
        output = ggml_mul_mat(tensors, weight, activation);
        ggml_set_name(weight, "blk.0.fpga_dense.weight");
        ggml_set_name(activation, "blk.0.fpga_dense.input");
        ggml_set_name(output, "blk.0.fpga_dense.output");
        ggml_set_input(activation);
        ggml_set_output(output);
        weight_buffer = ggml_backend_alloc_ctx_tensors(weights, fpga);
        require(weight_buffer, "weight allocation");
        ggml_backend_buffer_set_usage(weight_buffer, GGML_BACKEND_BUFFER_USAGE_WEIGHTS);
        graph = ggml_new_graph(tensors);
        ggml_build_forward_expand(graph, output);
        ggml_backend_t backends[] = {fpga, cpu};
        scheduler = ggml_backend_sched_new(backends, nullptr, 2, GGML_DEFAULT_GRAPH_SIZE, false, true);
        require(scheduler && ggml_backend_sched_alloc_graph(scheduler, graph), "scheduler graph allocation");
        // No set_tensor_backend override. Assignment follows normal supports_op
        // and the native weight buffer's backend, as in model execution.
        require(ggml_backend_sched_get_tensor_backend(scheduler, output) == fpga, "scheduler did not assign FPGA op");
    }
    ~Graph() {
        if (scheduler) ggml_backend_sched_free(scheduler);
        if (weight_buffer) ggml_backend_buffer_free(weight_buffer);
        if (tensors) ggml_free(tensors);
        if (weights) ggml_free(weights);
        if (cpu) ggml_backend_free(cpu);
        if (fpga) ggml_backend_free(fpga);
    }
    void prepare(size_t seed, bool invalid_scale = false, bool edges = false) {
        std::vector<float> a(m * k), w(n * k);
        for (size_t i = 0; i < a.size(); ++i)
            a[i] = float(int((i * 17 + seed * 13) % 251) - 125) / 32;
        if (std::getenv("IM2P_TEST_NONZERO_RMD"))
            for (size_t row = 0; row < m; row += 16) a[row * k] = 37.0f;
        if (m > 32) {
            ggml_gemmini_args_t args{};
            args.I = m; args.J = n; args.K = k; args.tiled_matmul_type = CPU;
            args.matmul_layer = "dense-pipeline-input-geometry";
            gemmini_set_tile_ws(&args);
            const auto geometry = args.activation_geometry();
            require(geometry.ok(), "input fixture geometry");
            std::cout << "GRAPH_GEOMETRY M=" << m << " N=" << n << " K=" << k
                      << " tile_I=" << args.tile_I << " tile_J=" << args.tile_J
                      << " stripe_rows=" << geometry.geometry.stripe_rows
                      << " stripes=" << geometry.geometry.stripe_count << std::endl;
            for (size_t row = 0; row < m; ++row)
                for (size_t col = 0; col < k; ++col)
                    a[row * k + col] = std::ldexp(a[row * k + col], int(row / geometry.geometry.stripe_rows % 3) * 3);
        }
        for (size_t j = 0; j < n; ++j) for (size_t l = 0; l < k; ++l)
            w[j * k + l] = float(int((j * 11 + l * 7 + seed * 5) % 253) - 126) * float(1 + (l / 32) * 3) / 128;
        if (weight->type == GGML_TYPE_Q8_0) {
            std::vector<block_q8_0> packed(n * k / 32);
            for (size_t j = 0; j < n; ++j)
                quantize_row_q8_0_ref(w.data() + j * k, packed.data() + j * k / 32, k);
            if (invalid_scale) packed.front().d = 0x7c00;
            const auto *bytes = reinterpret_cast<const uint8_t *>(packed.data());
            original_q8_0.assign(bytes, bytes + packed.size() * sizeof(block_q8_0));
            ggml_backend_tensor_set(weight, original_q8_0.data(), 0, original_q8_0.size());
        } else if (weight->type == GGML_TYPE_Q8_HP1) {
            std::vector<block_q8_hp1> packed(n * k / 32);
            for (size_t j = 0; j < n; ++j)
                quantize_row_q8_hp1_ref(w.data() + j * k, packed.data() + j * k / 32, k);
            if (edges) for (size_t col = 0; col < n; ++col) for (size_t block = 0; block < k / 32; ++block) {
                auto &value = packed[col * (k / 32) + block];
                constexpr int16_t exponents[] = {0, 1, 7, 31, 32, 32767, INT16_MIN};
                value.m = col == 0 ? INT16_MIN : col == 1 ? (block == 0 ? 0 : INT16_MIN)
                    : exponents[(col + block) % std::size(exponents)];
                if (seed % 2 == 0 && block == 0) std::fill_n(value.qs, 32, int8_t(0));
            }
            ggml_backend_tensor_set(weight, packed.data(), 0, packed.size() * sizeof(block_q8_hp1));
        } else {
        std::vector<block_q8_h1> packed(n * k / 32);
        for (size_t j = 0; j < n; ++j)
            quantize_row_q8_h1_ref(w.data() + j * k, packed.data() + j * k / 32, k);
        for (const auto & block : packed)
            require(block.s_rf > 0 && uint32_t(block.c_b) + block.R > 0, "nonzero native weight scale");
        if (edges) for (size_t col = 0; col < n; ++col) for (size_t block = 0; block < k / 32; ++block) {
            auto &value = packed[col * (k / 32) + block];
            value.R = col % 4 == 3 ? 65535 : 0;
            value.c_b = col == 0 ? 0 : col == 1 ? (block == 0 ? 1 : 0) : (block % 2 ? 255 : 1);
            if (seed % 2 == 0 && block == 0) std::fill_n(value.qs, 32, int8_t(0));
        }
        ggml_backend_tensor_set(weight, packed.data(), 0, packed.size() * sizeof(block_q8_h1));
        }
        ggml_backend_tensor_set(activation, a.data(), 0, a.size() * sizeof(float));
        std::vector<float> sentinel(m * n, 17.0f);
        ggml_backend_tensor_set(output, sentinel.data(), 0, sentinel.size() * sizeof(float));
    }
    std::vector<float> values() const {
        std::vector<float> values(m * n);
        ggml_backend_tensor_get(output, values.data(), 0, values.size() * sizeof(float));
        return values;
    }
    void check_weight_preservation() const {
        if (original_q8_0.empty()) return;
        std::vector<uint8_t> actual(original_q8_0.size());
        ggml_backend_tensor_get(weight, actual.data(), 0, actual.size());
        require(actual == original_q8_0, "Q8_0 original weight backing changed");
    }
};

int main(int argc, char ** argv) {
    using SetObserver = void (*)(ggml_gemmini_fpga_observer, void *);
    using SetBlockObserver = void (*)(ggml_gemmini_fpga_block_observer, void *);
    using SetResultObserver = void (*)(ggml_gemmini_fpga_result_observer, void *);
    SetObserver set_observer = nullptr;
    SetBlockObserver set_block_observer = nullptr;
    SetResultObserver set_result_observer = nullptr;
    try {
        const bool physical = argc >= 10 && std::string_view(argv[argc - 2]) == "--physical-device";
        const int option_argc = argc - (physical ? 2 : 0);
        require(option_argc == 8 || option_argc == 9,
                "usage: graph_dispatch BACKEND_SO M N K SEED REPS FULL|STRIPE_PIPELINE [HP1|HP1-edges|H1-edges|Q8_0|Q8_0-invalid-scale|expect-failure] [--physical-device BY_ID_PATH]");
        const auto library = std::filesystem::canonical(argv[1]);
        const size_t m = std::stoul(argv[2]), n = std::stoul(argv[3]), k = std::stoul(argv[4]);
        const size_t seed = std::stoul(argv[5]), repetitions = std::stoul(argv[6]);
        const std::string mode = argv[7];
        const bool invalid_scale = option_argc == 9 && std::string(argv[8]) == "Q8_0-invalid-scale";
        const bool failure = invalid_scale || (option_argc == 9 && std::string(argv[8]) == "expect-failure");
        const bool edges = option_argc == 9 && (std::string(argv[8]) == "HP1-edges" || std::string(argv[8]) == "H1-edges");
        const bool hp1 = option_argc == 9 && (std::string(argv[8]) == "HP1" || std::string(argv[8]) == "HP1-edges");
        const bool q8_0 = invalid_scale || (option_argc == 9 && std::string(argv[8]) == "Q8_0");
        require(option_argc == 8 || failure || hp1 || edges || q8_0, "test option");
        const char * device = std::getenv("IM2P_FPGA_DEVICE");
        const bool rtl = device && std::string_view(device).starts_with("rtl:/");
        const bool uart4 = device && std::string_view(device).starts_with("uart4:/");
        const bool bounded = rtl || uart4;
        const char *contract = std::getenv("IM2P_FPGA_NUMERICAL_CONTRACT");
        require(!contract || std::string_view(contract) == "main_external" ||
                std::string_view(contract) == "scu_final_integer", "explicit numerical contract");
        const bool external = bounded && (q8_0 || (contract && std::string_view(contract) == "main_external"));
        require(!edges || (!external && n >= 3 && k >= 64), "edge fixture requires native SCU, N>=3, K>=64");
        require(bounded ? (m > 0 && m <= 65536 && n > 0 && n <= 65536 && k > 0 && k <= 65536 && k % 32 == 0) :
                      (m > 0 && m <= 336 && n > 0 && n <= 48 && (k == 32 || k == 64 || k == 96)), "test bounds");
        require(!hp1 || bounded, "HP1 requires bounded RTL transport");
        require(!q8_0 || bounded, "Q8_0 requires bounded RTL transport");
        require(repetitions > 0 && repetitions <= 2 && (!failure || repetitions == 1), "test invocation count");
        require(mode == "FULL" || mode == "STRIPE_PIPELINE", "test mode");
        if (physical) {
            const std::filesystem::path approved(argv[argc - 1]);
            require(!failure && uart4 && std::string_view(device + 6) == approved.string() &&
                    approved.parent_path() == "/dev/serial/by-id" &&
                    std::filesystem::is_character_file(approved),
                    "physical execution requires explicit matching UART4 by-id device argument");
        }
        if (rtl) require(std::filesystem::is_regular_file(device + 4), "RTL plugin must exist");
        else if (device && *device && !physical) {
            const auto path = std::filesystem::canonical(uart4 ? device + 6 : device);
            const std::string number = path.filename().string();
            require(path.parent_path() == "/dev/pts" && !number.empty() &&
                    number.find_first_not_of("0123456789") == std::string::npos, "test accepts PTY only");
        } else require(failure || physical, "numerical test requires explicit PTY");
        unsetenv("GEMMINI_MATMUL_INVOCATION");
        setenv("GEMMINI_MATMUL_MODE", mode.c_str(), 1);
        require(!std::getenv("IM2P_FPGA_TEST_PRODUCER_OVERLAP") && !std::getenv("IM2P_FPGA_TEST_WAIT_FIRST_READ"),
                "diagnostic delays must not leak into graph test");
        require(dlsym(RTLD_DEFAULT, "im2p_sim_create") == reinterpret_cast<void *>(&im2p_sim_create) &&
                dlsym(RTLD_DEFAULT, "im2p_execute_matmul_extended") == reinterpret_cast<void *>(&im2p_execute_matmul_extended) &&
                dlsym(RTLD_DEFAULT, "im2p_begin_striped_matmul") == reinterpret_cast<void *>(&im2p_begin_striped_matmul),
                "build graph test with --export-dynamic for simulator interception");
        auto reg = ggml_backend_load(library.c_str());
        require(reg && ggml_backend_reg_dev_count(reg) == 1, "standard FPGA module load");
        require(ggml_backend_load((library.parent_path() / "libggml-cpu.so").c_str()), "standard CPU module load");
        auto stats_fn = reinterpret_cast<ggml_gemmini_fpga_stats_v1_fn>(
            ggml_backend_reg_get_proc_address(reg, "ggml_gemmini_fpga_stats_v1"));
        set_observer = reinterpret_cast<SetObserver>(ggml_backend_reg_get_proc_address(reg, "ggml_gemmini_fpga_set_observer"));
        require(stats_fn && set_observer, "standard module stats/observer API");
        ggml_gemmini_fpga_stats_v1 before{};
        require(stats_fn(&before, sizeof(before)) && !before.assigned && !before.attempted && !before.completed && !before.failed,
                "fresh module counters");
        Observation observation;
        observation.external = external;
        observation.edges = edges;
        observation.mode = mode;
        if (const char *capture = std::getenv("IM2P_GRAPH_CAPTURE_DIR")) {
            require(!external && *capture && std::filesystem::is_directory(capture),
                    "graph capture requires native SCU and existing output directory");
            observation.capture_directory = capture;
        }
        if (bounded) {
            set_block_observer = reinterpret_cast<SetBlockObserver>(ggml_backend_reg_get_proc_address(reg, "ggml_gemmini_fpga_set_block_observer"));
            set_result_observer = reinterpret_cast<SetResultObserver>(ggml_backend_reg_get_proc_address(reg, "ggml_gemmini_fpga_set_result_observer"));
            require(set_block_observer && set_result_observer, "provider observation APIs");
        }
        if (external) {
            set_block_observer(Observation::block, &observation);
            set_result_observer(Observation::external_complete, &observation);
        }
        else set_observer(Observation::complete, &observation);
        std::cout << "GRAPH_REQUEST transport=" << (rtl ? "rtl_plugin" : uart4 ? "uart4" : "uart3")
                  << " requested_contract=" << (external ? "main_external" : "scu_final_integer")
                  << " requested_op=" << (external ? 3 : hp1 ? 5 : 4) << " requested_domain=" << (external ? 1 : 2)
                  << " M=" << m << " N=" << n << " K=" << k << " mode=" << mode
                  << " repetitions=" << repetitions << " fixture=" << (edges ? "native_edges" : "quantized")
                  << " op_domain_evidence=requested_contract physical_device_selected=" << physical
                  << " physical_access=not_started\n";
        Graph test(ggml_backend_reg_dev_get(reg, 0), m, n, k,
                   q8_0 ? GGML_TYPE_Q8_0 : hp1 ? GGML_TYPE_Q8_HP1 : GGML_TYPE_Q8_H1);
        if (q8_0) observation.original_q8_0 = &test.original_q8_0;
        for (size_t iteration = 0; iteration < repetitions; ++iteration) {
            observation.begin(m, n, k);
            observation.seed = seed + iteration;
            test.prepare(seed + iteration, invalid_scale, edges);
            const auto status = ggml_backend_sched_graph_compute(test.scheduler, test.graph);
            ggml_backend_sched_synchronize(test.scheduler);
            const auto actual = test.values();
            test.check_weight_preservation();
            if (failure) {
                require(status == GGML_STATUS_FAILED && observation.calls == 0, "failure did not propagate");
                require(std::all_of(actual.begin(), actual.end(), [](float x) { return x == 17.0f; }), "failure published caller output");
            } else {
                require(status == GGML_STATUS_SUCCESS && observation.calls == iteration + 1, "missing FPGA completion");
                require(actual.size() == observation.expected.size() &&
                        std::memcmp(actual.data(), observation.expected.data(), actual.size() * sizeof(float)) == 0,
                        "caller G2 float32 exact mismatch");
            }
        }
        ggml_gemmini_fpga_stats_v1 after{};
        require(stats_fn(&after, sizeof(after)), "final stats");
        require(after.assigned == repetitions && after.attempted == (invalid_scale ? 0 : repetitions) &&
                after.completed == (failure ? 0 : repetitions) && after.failed == (failure ? 1 : 0), "execution counter conservation");
        require(!simulator_creates && !simulator_executes && !simulator_streams, "hidden simulator fallback");
        set_observer(nullptr, nullptr);
        if (set_block_observer) set_block_observer(nullptr, nullptr);
        if (set_result_observer) set_result_observer(nullptr, nullptr);
        std::cout << "STANDARD_GRAPH_" << (failure ? "EXPECTED_FAILURE" : "NUMERICAL") << " PASS"
                  << " mode=" << mode << " logical=" << repetitions << " raw=" << observation.scalars
                  << " f_out=" << (failure ? 0 : repetitions * m * n) << " domain=" << (external ? 1 : 2)
                  << " final_integer_scalars=" << (external ? 0 : observation.scalars)
                  << " f_out_scalars=" << (failure ? 0 : repetitions * m * n)
                  << " dense_intermediate_scalars=" << (external ? std::to_string(observation.scalars) : "NOT_OBSERVED")
                  << " scheduler_assigned=" << after.assigned << " adapter_attempted=" << after.attempted
                  << " completed=" << after.completed << " failed=" << after.failed
                  << " simulator_create_calls=" << simulator_creates << " simulator_execute_calls=" << simulator_executes
                  << " simulator_stream_calls=" << simulator_streams << " reference_fragments=" << observation.reference_fragments
                  << " cpu_reference_scope=independent_after_RTL PHY=" << (rtl ? "omitted" : "UART")
                  << " physical_access=" << (physical ? "1" : "0")
                  << " caller_output_preserved=" << failure << " loaded_backend=" << library << '\n';
        if (q8_0) std::cout << "Q8_0_REPROCESS_GRAPH_PASS original_backing=unchanged quantizer=existing_row_helper\n";
        return 0;
    } catch (const std::exception & error) {
        if (set_observer) set_observer(nullptr, nullptr);
        if (set_block_observer) set_block_observer(nullptr, nullptr);
        if (set_result_observer) set_result_observer(nullptr, nullptr);
        std::cerr << "STANDARD_GRAPH FAIL " << error.what() << '\n';
        return 1;
    }
}
