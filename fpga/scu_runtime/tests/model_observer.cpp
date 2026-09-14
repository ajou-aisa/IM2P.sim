// Test-only observer for an ordinary llama-cli/PPL process. The deployment
// backend and production RTL execute unchanged. This library only observes.
#define GGML_GEMMINI_MATMUL_IMPLEMENTATION 1
#include "ggml-gemmini-fpga.hpp"
#include "ggml-gemmini-args.h"
#include "ggml-backend.h"
#include "ggml-gemmini.h"
#include "quants/act/exsia/exsia.hpp"
#include "model_capture.hpp"
#include <algorithm>
#include <array>
#include <bit>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <cstdlib>
#include <dlfcn.h>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <limits>
#include <stdexcept>
#include <sstream>
#include <string>
#include <vector>
#include <unistd.h>

namespace {
struct Record { size_t block, row, column, count; std::array<int64_t,16> values; };
// Keep capture state alive through dynamic-library teardown reporting.
auto &records = *new std::vector<Record>;
auto &boundary_calls = *new nlohmann::json(nlohmann::json::array());
auto &published_events = *new std::vector<ggml::gemmini::quants::act::exsia::StripeReadyEvent>;
auto &boundary_events = *new nlohmann::json(nlohmann::json::array());
auto &pending_manifest = *new nlohmann::json;
auto &published_activation = *new std::vector<int8_t>;
auto &published_weights = *new std::vector<uint8_t>;
auto &final_raw = *new std::vector<int32_t>;
const void *weight_backing = nullptr;
size_t published_rows = 0;
bool capture_pipeline = false;
size_t completed = 0;
bool installed = false;
bool scu = true;
bool first_only = false;
bool input_only = false;
uint64_t eligible_nodes = 0, assigned_nodes = 0, originally_cpu_nodes = 0;
uint64_t other_nodes = 0, unassigned_eligible_nodes = 0, numerical_fallback_nodes = 0;
bool fpga_graph_failed = false;
std::filesystem::path directory;
void check(bool value, const char *message) {
    if (!value) throw std::runtime_error(message);
}
[[noreturn]] void fail(const char *message) {
    std::fprintf(stderr, "MODEL_OBSERVER_FAIL %s\n", message);
    std::fflush(nullptr); _exit(94);
}
void bytes(const std::filesystem::path &path, const void *data, size_t size) {
    check(!std::filesystem::exists(path), "capture already exists");
    std::ofstream output(path, std::ios::binary);
    output.write(static_cast<const char *>(data), size);
    check(bool(output), "capture write failed");
}
void boundary(const char *stage, const ggml_gemmini_args_t &args, bool pipeline,
              const ggml::gemmini::quants::act::exsia::StripeReadyEvent *event, void *) {
    try {
        using nlohmann::json;
        if (std::string(stage) == "begin") {
            check(pending_manifest.is_null() && records.empty() && final_raw.empty(), "previous capture did not commit");
            boundary_calls = json::array(); boundary_events = json::array();
            published_rows = 0; capture_pipeline = pipeline; published_events.clear();
            check(args.I && args.K && args.K % 32 == 0 && args.I <= SIZE_MAX / args.K,
                  "dense capture requires native K32 activation extent");
            published_activation.assign(args.I * args.K, 0);
            const bool hp1 = args.weight_format == ggml_gemmini_args_t::im2p_weight_format_t::q8_hp1;
            check(hp1 ? args.has_q8_hp1_im2p_contract() : args.has_q8_h1_im2p_contract(),
                  "publication requires native H1/HP1 backing");
            const size_t native_size = hp1 ? sizeof(*args.q8_hp1_blocks) : sizeof(*args.q8_h1_blocks);
            const size_t count = size_t(model_capture::product(args.J, args.K / 32));
            const size_t extent = size_t(model_capture::product(count, native_size));
            weight_backing = hp1 ? static_cast<const void *>(args.q8_hp1_blocks)
                                 : static_cast<const void *>(args.q8_h1_blocks);
            const auto *source = static_cast<const uint8_t *>(weight_backing);
            published_weights.assign(source, source + extent);
        }
        check(pipeline == capture_pipeline, "publication mode changed");
        json call{{"stage", stage}};
        if (event) call["stripe_id"] = event->stripe_id;
        boundary_calls.push_back(call);
        if (std::string(stage) == "post_fold") {
            check(event && event->activation_metadata && !event->direct_residual,
                  "publication requires owned metadata; CPU-direct residual uses separate qualification");
            check(event->row_begin == published_rows && event->row_end > published_rows && event->row_end <= args.I,
                  "publication row coverage mismatch");
            const auto &meta = *event->activation_metadata;
            const size_t height = event->row_end - event->row_begin;
            check(args.A.row_stride_bytes >= args.K && args.A.raw_data(), "publication activation layout");
            for (size_t row = event->row_begin; row < event->row_end; ++row) {
                const auto *source = reinterpret_cast<const int8_t *>(args.A.raw_data()) + row * args.A.row_stride_bytes;
                std::copy(source, source + args.K, published_activation.begin() + row * args.K);
            }
            boundary_events.push_back({{"run_id", event->run_id}, {"stripe_id", event->stripe_id},
                {"slot", event->slot}, {"row_begin", event->row_begin}, {"row_end", event->row_end},
                {"H", height}, {"K", args.K}, {"activation_row_stride_bytes", args.A.row_stride_bytes},
                {"activation_valid_bytes", height * args.K}, {"packed_offset_bytes", event->row_begin * args.K},
                {"e_s", meta.e_s}, {"rho", meta.rho}, {"sigma", meta.sigma}, {"theta", meta.theta},
                {"residual_route", event->rmd_packet ? "ws_packet" : "none"}, {"folding_commit_ns", event->folding_commit_ns}});
            if (event->rmd_packet) {
                const auto packet = model_capture::packet_json(*event->rmd_packet);
                model_capture::validate_packet(packet, args.I, args.J, args.K, event->stripe_id, event->row_begin, height);
                boundary_events.back()["residual_packet"] = packet;
            }
            published_events.push_back(*event);
            published_rows = event->row_end;
        } else if (std::string(stage) == "execute" && input_only) {
            // Deliberate diagnostic stop before frontend execution. This is an
            // input capture, with no numerical or completed-invocation claim.
            check(!pipeline && completed == 0 && published_rows == args.I && records.empty() && final_raw.empty(),
                  "input-only capture requires first FULL post-fold invocation");
            const auto geometry = args.activation_geometry();
            check(geometry.ok(), "input capture geometry");
            const auto &meta = std::get<ggml::gemmini::quants::act::exsia::Meta>(args.act_quant.storage());
            check(meta.rmd_packets.empty() && meta.direct_residuals.empty(), "input capture requires RMD OFF");
            for (size_t row = 0; row < args.I; ++row) {
                const auto *source = reinterpret_cast<const int8_t *>(args.A.raw_data()) + row * args.A.row_stride_bytes;
                check(std::equal(source, source + args.K, published_activation.begin() + row * args.K),
                      "post-fold activation changed before input capture");
            }
            check(std::memcmp(weight_backing, published_weights.data(), published_weights.size()) == 0,
                  "native weight changed before input capture");
            const bool hp1 = args.weight_format == ggml_gemmini_args_t::im2p_weight_format_t::q8_hp1;
            const auto prefix = directory / "invocation-0";
            bytes(prefix.string() + ".activation-i8.bin", published_activation.data(), published_activation.size());
            bytes(prefix.string() + ".native-weight.bin", published_weights.data(), published_weights.size());
            json manifest{{"capture_schema", "scu-input-only-v1"}, {"invocation", 0},
                {"M", args.I}, {"I", args.I}, {"N", args.J}, {"J", args.J}, {"K", args.K},
                {"format", hp1 ? "Q8_HP1" : "Q8_H1"}, {"activation_bits", 8}, {"block_size", 32},
                {"native_block_bytes", hp1 ? sizeof(block_q8_hp1) : sizeof(block_q8_h1)},
                {"tile_I", args.tile_I}, {"tile_J", args.tile_J}, {"tile_K", args.tile_K},
                {"stripe_rows", geometry.geometry.stripe_rows}, {"stripes", geometry.geometry.stripe_count},
                {"activation_row_stride_bytes", args.A.row_stride_bytes}, {"theta", meta.theta},
                {"e_s", meta.e_s}, {"rho", meta.rho}, {"sigma", meta.sigma}, {"mode", "FULL"},
                {"events", boundary_events}, {"calls", boundary_calls}, {"RMD", "OFF"},
                {"numerical_contract", scu ? "scu_final_integer" : "main_external"},
                {"numerical_status", "NOT_RUN"}, {"output_coverage", nullptr},
                {"input_lifetime", "POST_FOLD_THROUGH_EXECUTE_UNCHANGED"},
                {"expected_stop_exit", 95}};
            const auto text = manifest.dump(2) + '\n';
            bytes(prefix.string() + ".json", text.data(), text.size());
            std::fprintf(stderr, "MODEL_INPUT_CAPTURE_ONLY id=0 M=%zu N=%zu K=%zu format=%s numerical=NOT_RUN stop=before_execute exit=95\n",
                         args.I, args.J, args.K, hp1 ? "Q8_HP1" : "Q8_H1");
            std::fflush(nullptr); _exit(95);
        } else if (std::string(stage) == "commit") {
            check(!pending_manifest.is_null(), "committed invocation has no numerical capture");
            for (size_t row = 0; row < args.I; ++row) {
                const auto *source = reinterpret_cast<const int8_t *>(args.A.raw_data()) + row * args.A.row_stride_bytes;
                check(std::equal(source, source + args.K, published_activation.begin() + row * args.K),
                      "post-fold activation changed before commit");
            }
            check(std::memcmp(weight_backing, published_weights.data(), published_weights.size()) == 0,
                  "native weight bytes changed before commit");
            for (size_t id = 0; id < published_events.size(); ++id) {
                const auto &saved = published_events[id];
                if (saved.rmd_packet)
                    check(model_capture::packet_json(*saved.rmd_packet) == boundary_events.at(id).at("residual_packet"),
                          "residual packet changed before commit");
            }
            pending_manifest["calls"] = boundary_calls;
            check(model_capture::validate_publication(pending_manifest), "publication capture validation failed");
            const auto manifest_path = directory / ("invocation-" + std::to_string(completed) + ".json");
            check(!std::filesystem::exists(manifest_path), "capture manifest already exists");
            std::ofstream manifest(manifest_path);
            manifest << pending_manifest.dump(2) << '\n';
            manifest.close();
            check(bool(manifest), "capture manifest write failed");
            std::fprintf(stderr, "MODEL_INVOCATION_PASS id=%zu M=%zu N=%zu K=%zu format=%s stripes=%zu raw=%zu fout=%zu\n",
                         completed, args.I, args.J, args.K,
                         args.weight_format == ggml_gemmini_args_t::im2p_weight_format_t::q8_hp1 ? "Q8_HP1" : "Q8_H1",
                         boundary_events.size(), pending_manifest.at("raw_comparisons").get<size_t>(),
                         pending_manifest.at("fout_comparisons").get<size_t>());
            std::fprintf(stderr, "MODEL_PUBLICATION_CAPTURE_PASS id=%zu mode=%s events=%zu input_lifetime=unchanged commit=1\n",
                         completed, pipeline ? "STRIPE_PIPELINE" : "FULL", boundary_events.size());
            ++completed; records.clear(); final_raw.clear(); pending_manifest = nullptr;
            if (first_only) {
                std::fprintf(stderr, "MODEL_CAPTURE_FINITE_STOP completed=1 release=checked full_model=NOT_RUN\n");
                std::fflush(nullptr); _exit(0);
            }
        }
    } catch (const std::exception &error) { fail(error.what()); }
}
void block(void *, size_t b, size_t row, size_t column, size_t count, const int64_t *values) noexcept {
    try {
        check(!scu, "SCU model emitted an intermediate dense callback");
        check(values && count && count <= 16, "invalid block callback");
        Record record{b,row,column,count,{}};
        std::copy(values, values + count, record.values.begin());
        records.push_back(record);
    } catch (const std::exception &error) { fail(error.what()); }
}
void result(const ggml_gemmini_args_t &args, void *) {
    try {
        const bool hp1 = args.weight_format == ggml_gemmini_args_t::im2p_weight_format_t::q8_hp1;
        check(hp1 ? args.has_q8_hp1_im2p_contract() : args.has_q8_h1_im2p_contract(),
              "model invocation lacks native weight contract");
        const auto geometry = args.activation_geometry();
        check(geometry.ok(), "model activation geometry");
        const auto &meta = std::get<ggml::gemmini::quants::act::exsia::Meta>(args.act_quant.storage());
        check(meta.direct_residuals.empty(), "CPU-direct residual capture is separate from accelerator qualification");
        size_t packet_count = 0;
        for (const auto &event : published_events) if (event.rmd_packet) {
            check(packet_count < meta.rmd_packets.size() && meta.rmd_packets[packet_count] &&
                  model_capture::packet_json(*event.rmd_packet) == model_capture::packet_json(*meta.rmd_packets[packet_count]),
                  "residual metadata backing changed");
            ++packet_count;
        }
        check(packet_count == meta.rmd_packets.size(), "residual packet coverage mismatch");
        const size_t blocks = args.K / 32;
        check(blocks && args.I && args.J && args.I <= SIZE_MAX / args.J &&
              args.I * args.J <= SIZE_MAX / blocks, "capture extent overflow");
        check(!scu || meta.rmd_packets.empty(), "SCU model observer requires RMD OFF");
        std::vector<int64_t> raw(args.I * args.J * (scu ? 1 : blocks));
        std::vector<uint8_t> seen(scu ? 0 : raw.size(), 0);
        if (scu) {
            check(records.empty() && final_raw.size() == raw.size(), "SCU final model extent");
            std::copy(final_raw.begin(), final_raw.end(), raw.begin());
        }
        for (const auto &record : records) {
            check(record.block < blocks && record.row < args.I && record.column < args.J &&
                  record.count <= args.J - record.column, "model block extent");
            for (size_t lane = 0; lane < record.count; ++lane) {
                const size_t at = (record.block * args.I + record.row) * args.J + record.column + lane;
                check(seen[at]++ == 0, "duplicate model block contribution");
                raw[at] = record.values[lane];
            }
        }
        check(scu || std::all_of(seen.begin(), seen.end(), [](uint8_t value) { return value == 1; }),
              "missing model block contribution");
        std::vector<int8_t> activation(args.I * args.K);
        check(published_rows == args.I, "missing post-fold activation publication");
        std::vector<float> expected(args.I * args.J), actual(expected.size());
        std::vector<int32_t> expected_integer(scu ? expected.size() : 0);
        size_t scu_clamps = 0, accumulator_clamps = 0;
        const auto clamp = [](int64_t value, size_t &count) {
            if (value > INT32_MAX) { ++count; return INT32_MAX; }
            if (value < INT32_MIN) { ++count; return INT32_MIN; }
            return int32_t(value);
        };
        for (size_t row = 0; row < args.I; ++row) {
            const auto *a = reinterpret_cast<const int8_t *>(args.A.raw_data()) + row * args.A.row_stride_bytes;
            std::copy(a, a + args.K, activation.begin() + row * args.K);
            const size_t stripe = row / geometry.geometry.stripe_rows;
            check(stripe < meta.theta.size() && meta.theta[stripe] != INT16_MIN, "model stripe theta identity");
            const double activation_scale = std::ldexp(1.0, meta.theta[stripe]);
            check(std::isfinite(activation_scale) && activation_scale > 0, "model activation scale");
            for (size_t column = 0; column < args.J; ++column) {
                if (scu) {
                    const size_t base = column * blocks;
                    const float shared = hp1 ? args.q8_hp1_blocks[base].channel_scale : args.q8_h1_blocks[base].s_rf;
                    const uint16_t offset = hp1 ? 0 : args.q8_h1_blocks[base].R;
                    check(std::isfinite(shared) && shared >= 0, "model shared scale");
                    int32_t accumulator = 0;
                    for (size_t first = 0; first < args.K; first += 16) {
                        const size_t index = base + first / 32;
                        const int8_t *codes = hp1 ? args.q8_hp1_blocks[index].qs : args.q8_h1_blocks[index].qs;
                        int64_t partial = 0;
                        const size_t count = std::min({size_t(16), args.K - first, size_t(32) - first % 32});
                        for (size_t lane = 0; lane < count; ++lane)
                            partial += int64_t(a[first + lane]) * int64_t(codes[first % 32 + lane]);
                        int32_t contribution;
                        if (hp1) {
                            const auto &weight = args.q8_hp1_blocks[index];
                            check(weight.channel_scale == shared && (weight.m == INT16_MIN || weight.m >= 0),
                                  "model HP1 shared scale/exponent");
                            if (weight.m == INT16_MIN || partial == 0) contribution = 0;
                            else if (weight.m >= 32) {
                                ++scu_clamps; contribution = partial < 0 ? INT32_MIN : INT32_MAX;
                            } else contribution = clamp(partial * (INT64_C(1) << weight.m), scu_clamps);
                        } else {
                            const auto &weight = args.q8_h1_blocks[index];
                            const uint32_t beta = uint32_t(weight.c_b) + uint32_t(weight.R);
                            check(weight.s_rf == shared && weight.R == offset && beta <= 65790, "model H1 metadata");
                            contribution = clamp(partial * int64_t(beta), scu_clamps);
                        }
                        accumulator = first == 0 ? contribution : clamp(int64_t(accumulator) + contribution, accumulator_clamps);
                    }
                    const size_t index = row * args.J + column;
                    expected_integer[index] = accumulator;
                    check(raw[index] == accumulator, "actual model SCU final integer mismatch");
                    expected[index] = float(double(accumulator) * double(shared) * activation_scale);
                    actual[index] = args.f_out[row * args.stride_f_out + column * args.col_stride_f_out];
                    continue;
                }
                double sum = 0;
                for (size_t b = 0; b < blocks; ++b) {
                    const size_t index = column * blocks + b;
                    const int8_t *codes = hp1 ? args.q8_hp1_blocks[index].qs : args.q8_h1_blocks[index].qs;
                    int64_t dot = 0;
                    for (size_t lane = 0; lane < 32; ++lane)
                        dot += int64_t(a[b * 32 + lane]) * int64_t(codes[lane]);
                    check(raw[(b * args.I + row) * args.J + column] == dot, "actual model raw block mismatch");
                    double factor;
                    if (hp1) {
                        const auto &weight = args.q8_hp1_blocks[index];
                        factor = weight.m == INT16_MIN ? 0.0 : double(gemmini_ldexp_fast_pos(weight.channel_scale, weight.m));
                    } else {
                        const auto &weight = args.q8_h1_blocks[index];
                        factor = double(weight.s_rf) * double(uint32_t(weight.c_b) + weight.R);
                    }
                    sum += double(dot) * factor * activation_scale;
                }
                const size_t index = row * args.J + column;
                expected[index] += float(sum);
                actual[index] = args.f_out[row * args.stride_f_out + column * args.col_stride_f_out];

            }
        }
        const size_t nonzero_residuals = model_capture::residual_oracle(args, meta, expected);
        for (size_t index = 0; index < expected.size(); ++index)
            check(std::bit_cast<uint32_t>(expected[index]) == std::bit_cast<uint32_t>(actual[index]),
                  "actual model dense/residual reconstruction mismatch");
        const auto prefix = directory / ("invocation-" + std::to_string(completed));
        bytes(prefix.string() + ".activation-i8.bin", activation.data(), activation.size());
        bytes(prefix.string() + ".raw-i64.bin", raw.data(), raw.size() * sizeof(int64_t));
        bytes(prefix.string() + ".fout-f32.bin", actual.data(), actual.size() * sizeof(float));
        if (scu) {
            bytes(prefix.string() + ".expected-raw-i32.bin", expected_integer.data(), expected_integer.size() * sizeof(int32_t));
            bytes(prefix.string() + ".expected-fout-f32.bin", expected.data(), expected.size() * sizeof(float));
        }
        const size_t native_size = hp1 ? sizeof(*args.q8_hp1_blocks) : sizeof(*args.q8_h1_blocks);
        const void *weights = hp1 ? static_cast<const void *>(args.q8_hp1_blocks)
                                  : static_cast<const void *>(args.q8_h1_blocks);
        check(activation == published_activation, "post-fold activation changed before commit");
        check(weights == weight_backing && published_weights.size() == args.J * blocks * native_size &&
              std::memcmp(weights, published_weights.data(), published_weights.size()) == 0,
              "native weight backing changed during invocation");
        bytes(prefix.string() + ".native-weight.bin", published_weights.data(), published_weights.size());
        std::ostringstream manifest;
        manifest << "{\"invocation\":" << completed << ",\"M\":" << args.I << ",\"I\":" << args.I
                 << ",\"J\":" << args.J << ",\"N\":" << args.J << ",\"K\":" << args.K
                 << ",\"format\":\"" << (hp1 ? "Q8_HP1" : "Q8_H1")
                 << "\",\"activation_bits\":8,\"native_block_bytes\":" << native_size
                 << ",\"block_size\":32,\"tile_I\":" << args.tile_I << ",\"tile_J\":" << args.tile_J
                 << ",\"tile_K\":" << args.tile_K << ",\"stripe_rows\":" << geometry.geometry.stripe_rows
                 << ",\"stripes\":" << geometry.geometry.stripe_count << ",\"theta\":[";
        for (size_t i = 0; i < meta.theta.size(); ++i) manifest << (i ? "," : "") << meta.theta[i];
        manifest << "],\"e_s\":" << meta.e_s << ",\"rho\":" << meta.rho << ",\"sigma\":" << meta.sigma
                 << ",\"direct_residuals\":0,\"accelerator_residuals\":0,\"raw_comparisons\":" << raw.size()
                 << ",\"fout_comparisons\":" << actual.size()
                 << ",\"numerical_contract\":\"main_external\",\"raw_exact\":true,\"fout_exact\":true}\n";
        check(bool(manifest), "capture manifest write");
        pending_manifest = nlohmann::json::parse(manifest.str());
        pending_manifest["numerical_contract"] = scu ? "scu_final_integer" : "main_external";
        pending_manifest["requested_op"] = scu ? (hp1 ? 5 : 4) : 3;
        pending_manifest["output_domain"] = scu ? 2 : 1;
        pending_manifest["domain_evidence"] = "host_callback_contract_not_wire_echo";
        pending_manifest["final_integer_scalars"] = scu ? raw.size() : 0;
        pending_manifest["dense_intermediate_scalars"] = scu ? nlohmann::json(nullptr) : nlohmann::json(raw.size());
        pending_manifest["expected_dense_intermediate_scalars"] = scu ? 0 : raw.size();
        pending_manifest["intermediate_evidence"] = "transport_coverage_required";
        pending_manifest["f_out_scalars"] = actual.size();
        pending_manifest["scu_clamps"] = scu_clamps;
        pending_manifest["accumulator_clamps"] = accumulator_clamps;
        pending_manifest["capture_schema"] = packet_count ? 3 : 2;
        pending_manifest["accelerator_residuals"] = packet_count;
        pending_manifest["residual_nonzero"] = nonzero_residuals;
        pending_manifest["residual_oracle"] = "independent_native_radix_dot_checked_int64";
        pending_manifest["publication_identity"] = "CAPTURED";
        pending_manifest["input_lifetime"] = "POST_FOLD_THROUGH_COMMIT_UNCHANGED";
        pending_manifest["mode"] = capture_pipeline ? "STRIPE_PIPELINE" : "FULL";
        pending_manifest["activation_row_stride_bytes"] = args.A.row_stride_bytes;
        pending_manifest["activation_source_address"] = reinterpret_cast<uintptr_t>(args.A.raw_data());
        pending_manifest["weight_source_address"] = reinterpret_cast<uintptr_t>(weights);
        pending_manifest["events"] = boundary_events;
    } catch (const std::exception &error) { fail(error.what()); }
}
void final_result(const ggml_gemmini_args_t &args, const std::vector<int32_t> &values, void *) {
    if (!scu || !final_raw.empty() || values.size() != args.I * args.J)
        fail("invalid SCU final callback");
    final_raw = values;
    result(args, nullptr);
}
void install() {
    if (installed) return;
    auto reg = ggml_backend_reg_by_name("GEMMINI");
    if (!reg) return;
    using SetBlock = void (*)(ggml_gemmini_fpga_block_observer, void *);
    using SetResult = void (*)(ggml_gemmini_fpga_result_observer, void *);
    using SetBoundary = void (*)(ggml_gemmini_fpga_boundary_observer, void *);
    using SetFinal = void (*)(ggml_gemmini_fpga_observer, void *);
    const auto set_block = reinterpret_cast<SetBlock>(ggml_backend_reg_get_proc_address(reg, "ggml_gemmini_fpga_set_block_observer"));
    const auto set_result = reinterpret_cast<SetResult>(ggml_backend_reg_get_proc_address(reg, "ggml_gemmini_fpga_set_result_observer"));
    const auto set_boundary = reinterpret_cast<SetBoundary>(ggml_backend_reg_get_proc_address(reg, "ggml_gemmini_fpga_set_boundary_observer"));
    const auto set_final = reinterpret_cast<SetFinal>(ggml_backend_reg_get_proc_address(reg, "ggml_gemmini_fpga_set_observer"));
    if (!set_block || !set_result || !set_boundary || !set_final) fail("deployed backend observation APIs missing");
    const char *path = std::getenv("IM2P_MODEL_CAPTURE_DIR");
    if (!path || !*path || !std::filesystem::is_directory(path)) fail("capture directory unavailable");
    directory = path;
    const char *contract = std::getenv("IM2P_FPGA_NUMERICAL_CONTRACT");
    if (contract && std::strcmp(contract, "main_external") && std::strcmp(contract, "scu_final_integer"))
        fail("invalid observer numerical contract");
    scu = !contract || std::strcmp(contract, "main_external");
    for (const char *key : {"IM2P_MODEL_CAPTURE_FIRST_ONLY", "IM2P_MODEL_CAPTURE_INPUT_ONLY"})
        if (const char *value = std::getenv(key); value && std::strcmp(value, "1")) fail("capture stop option must be 1");
    first_only = std::getenv("IM2P_MODEL_CAPTURE_FIRST_ONLY");
    input_only = std::getenv("IM2P_MODEL_CAPTURE_INPUT_ONLY");
    if (first_only && input_only) fail("capture stop options are mutually exclusive");
    if (input_only) {
        const char *device = std::getenv("IM2P_FPGA_DEVICE");
        if (!device || std::string_view(device).substr(0, 5) != "rtl:/")
            fail("input-only capture requires an offline RTL plugin");
    }
    if (scu) set_final(final_result, nullptr);
    else { set_block(block, nullptr); set_result(result, nullptr); }
    set_boundary(boundary, nullptr); installed = true;
    std::fprintf(stderr, "MODEL_OBSERVER_INSTALLED native=1 numerical_contract=%s first_only=%u input_only=%u\n",
                 scu ? "scu_final_integer" : "main_external", unsigned(first_only), unsigned(input_only));
}
template<class Function> Function next(const char *name) {
    auto function = reinterpret_cast<Function>(dlsym(RTLD_NEXT, name));
    if (!function) fail(name);
    return function;
}
} // namespace

extern "C" void ggml_backend_load_all() {
    next<decltype(&ggml_backend_load_all)>("ggml_backend_load_all")(); install();
}
extern "C" void ggml_backend_load_all_from_path(const char *path) {
    next<decltype(&ggml_backend_load_all_from_path)>("ggml_backend_load_all_from_path")(path); install();
}
extern "C" enum ggml_status ggml_backend_graph_compute_async(ggml_backend_t backend, ggml_cgraph *graph) {
    // Observe actual scheduler splits before execution. Metadata supports_op
    // probes never enter this counter, and CPU assignment precedes any failure.
    auto reg = ggml_backend_reg_by_name("GEMMINI");
    auto device = reg ? ggml_backend_reg_dev_get(reg, 0) : nullptr;
    const bool fpga = std::strcmp(ggml_backend_name(backend), "GEMMINI") == 0;
    const bool cpu = std::strcmp(ggml_backend_name(backend), "CPU") == 0;
    for (int i = 0; i < ggml_graph_n_nodes(graph); ++i) {
        auto *node = ggml_graph_node(graph, i);
        if (node->op != GGML_OP_MUL_MAT || ggml_is_empty(node)) continue;
        const bool eligible = device && ggml_backend_dev_supports_op(device, node);
        eligible_nodes += eligible;
        assigned_nodes += fpga;
        originally_cpu_nodes += cpu;
        other_nodes += !fpga && !cpu;
        unassigned_eligible_nodes += eligible && !fpga;
        numerical_fallback_nodes += eligible && cpu && fpga_graph_failed;
    }
    const auto status = next<decltype(&ggml_backend_graph_compute_async)>("ggml_backend_graph_compute_async")(backend, graph);
    fpga_graph_failed = fpga_graph_failed || (fpga && status != GGML_STATUS_SUCCESS);
    return status;
}
__attribute__((destructor)) static void report() {
    std::fprintf(stderr, "MODEL_OBSERVER_SUMMARY installed=%u completed=%zu pending_records=%zu\n",
                 unsigned(installed), completed, records.size());
    std::fprintf(stderr, "MODEL_ASSIGNMENT_SUMMARY eligible=%llu assigned=%llu originally_cpu=%llu other=%llu unassigned_eligible=%llu numerical_fallback=%llu\n",
                 (unsigned long long)eligible_nodes, (unsigned long long)assigned_nodes,
                 (unsigned long long)originally_cpu_nodes, (unsigned long long)other_nodes,
                 (unsigned long long)unassigned_eligible_nodes, (unsigned long long)numerical_fallback_nodes);
}
