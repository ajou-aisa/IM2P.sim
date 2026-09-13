// Actual frozen frontend + RTL simulator replay of unchanged IFX1 captures.
// The runner extracts Fixture fields/bind/restore verbatim from the old reader;
// legacy ABI4 execution/reconstruction code is deliberately not compiled here.
#include "ggml-gemmini-args.h"
#include "im2p_gemmini_frontend.hpp"
#include "quants/act/exsia/exsia.hpp"
#include <gemmini.h>
#include <algorithm>
#include <bit>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <vector>

namespace fs = std::filesystem;
using namespace ggml::gemmini;
using namespace im2p::gemmini;
using Bytes = std::vector<uint8_t>;
namespace exsia = ggml::gemmini::quants::act::exsia;
static void require(bool ok, const char *message) { if (!ok) throw std::runtime_error(message); }
static Bytes read(const fs::path &path) {
    std::ifstream input(path, std::ios::binary);
    require(bool(input), "missing fixture/expected file");
    return Bytes(std::istreambuf_iterator<char>(input), {});
}
static uint64_t get(const Bytes &data, size_t &at, size_t count) {
    require(at <= data.size() && count <= data.size() - at, "truncated fixture");
    uint64_t value = 0;
    for (size_t byte = 0; byte < count; ++byte) value |= uint64_t(data[at++]) << (8 * byte);
    return value;
}
#define IM2P_FULL_REPLAY_MAX_ROWS 336
#include "ifx1_fixture_reader.hpp"

struct Observation {
    Fixture &fixture;
    im2p_provider_t downstream{};
    std::vector<int64_t> raw;
    std::vector<uint8_t> seen;
    size_t starts = 0, full_starts = 0, stream_starts = 0, callbacks = 0, scale_lanes = 0;
    explicit Observation(Fixture &f) : fixture(f), raw(f.args.I * f.args.J), seen(raw.size()) {}
    template<class Descriptor> bool capture(const Descriptor &d) {
        if (d.abi_version != 5 || d.output_domain != 2 || d.vector_op != 4 ||
            d.dim != 16 || d.activation_bits != 8 || d.weight_bits != 8 ||
            d.m != fixture.args.I || d.n != fixture.args.J || d.k != fixture.args.K)
            return false;
        downstream = d.provider;
        return ++starts == 1;
    }
    static int weight(void *context, size_t row, size_t column, size_t count, int8_t *values) {
        auto &self = *static_cast<Observation *>(context);
        return self.downstream.read_weight_i8(self.downstream.context, row, column, count, values);
    }
    static int scale(void *context, size_t block, size_t column, size_t count, uint32_t *values) {
        auto &self = *static_cast<Observation *>(context);
        if (block >= self.fixture.args.K / 32 || column > self.fixture.args.J ||
            count > self.fixture.args.J - column) return IM2P_ERROR;
        const int status = self.downstream.read_scale(self.downstream.context, block, column, count, values);
        if (status != IM2P_OK) return status;
        for (size_t lane = 0; lane < count; ++lane) {
            const auto &w = self.fixture.weights[(column + lane) * (self.fixture.args.K / 32) + block];
            if (values[lane] != uint32_t(w.c_b) + uint32_t(w.R)) return IM2P_ERROR;
        }
        self.scale_lanes += count;
        return IM2P_OK;
    }
    static int output(void *context, size_t block, size_t row, size_t column, size_t count,
                      const int64_t *values, uint32_t domain) {
        auto &self = *static_cast<Observation *>(context);
        const auto &a = self.fixture.args;
        if (domain != 2 || block != 0 || !values || row >= a.I || column > a.J ||
            count == 0 || count > a.J - column) return IM2P_ERROR;
        for (size_t lane = 0; lane < count; ++lane) {
            const size_t index = row * a.J + column + lane;
            if (self.seen[index] || values[lane] < INT32_MIN || values[lane] > INT32_MAX) return IM2P_ERROR;
            self.seen[index] = 1;
            self.raw[index] = values[lane];
        }
        ++self.callbacks;
        return self.downstream.write_output(self.downstream.context, block, row, column, count, values, domain);
    }
    im2p_provider_t provider() { return {this, weight, nullptr, scale, output}; }
};
static Observation *active = nullptr;

extern "C" int __real_im2p_execute_matmul_extended(im2p_sim_t *, const im2p_matmul_desc_t *, im2p_work_stats_extended_t *);
extern "C" int __wrap_im2p_execute_matmul_extended(im2p_sim_t *sim, const im2p_matmul_desc_t *d,
                                                   im2p_work_stats_extended_t *stats) {
    if (!active || !d || !active->capture(*d)) return IM2P_ERROR;
    ++active->full_starts;
    auto copy = *d; copy.provider = active->provider();
    return __real_im2p_execute_matmul_extended(sim, &copy, stats);
}
extern "C" int __real_im2p_begin_striped_matmul(im2p_sim_t *, const im2p_stripe_work_desc_t *, im2p_stream_t **);
extern "C" int __wrap_im2p_begin_striped_matmul(im2p_sim_t *sim, const im2p_stripe_work_desc_t *d,
                                               im2p_stream_t **stream) {
    if (!active || !d || !active->capture(*d)) return IM2P_ERROR;
    ++active->stream_starts;
    auto copy = *d; copy.provider = active->provider();
    return __real_im2p_begin_striped_matmul(sim, &copy, stream);
}

int main(int argc, char **argv) {
    try {
        require(argc == 3, "usage: portable_replay REPO_ROOT EXPECTED_ROOT");
        require(IM2P_ABI_VERSION == 5 && compiled_activation_bits() == 8 &&
                compiled_weight_bits() == 8 && compiled_dim() == 16 &&
                im2p_compiled_accumulator_bits() == 32 &&
                std::string(im2p_compiled_numerical_semantics_revision()) == "signed-scu-sat-v2",
                "frozen S2 profile/revision mismatch");
        const fs::path root(argv[1]), expected_root(argv[2]);
        const std::vector<std::pair<const char *, const char *>> inputs{
            {"full_replay", "m16n16k32"}, {"full_replay", "m16n16k32-changed"},
            {"full_replay", "m16n16k64"}, {"full_replay", "m16n16k96"},
            {"full_replay", "m17n19k64-stride"}, {"full_replay", "m32n48k64"},
            {"dense_pipeline", "m321n48k64"}, {"dense_pipeline", "m321n48k96"}};
        size_t invocations = 0, raw_count = 0, fout_count = 0, padding_count = 0;
        for (const auto &[group, name] : inputs) {
            const auto expected_raw = read(expected_root / name / "expected-final-raw.bin");
            const auto expected_fout = read(expected_root / name / "expected-fout.bin");
            for (Mode mode : {Mode::full, Mode::stripe_pipeline}) {
                Fixture fixture; fixture.restore(root / "fpga" / group / "fixtures" / name);
                const auto geometry = fixture.args.activation_geometry();
                require(geometry.ok() && geometry.geometry.stripe_rows == fixture.args.activation_rows_per_stripe,
                        "captured existing geometry mismatch");
                auto &meta = std::get<exsia::Meta>(fixture.args.act_quant.storage());
                const auto theta = meta.theta;
                require(theta.size() == geometry.geometry.stripe_count, "captured theta count");
                Observation observation(fixture); active = &observation;
                if (mode == Mode::stripe_pipeline) meta.theta.assign(theta.size(), INT16_MIN);
                auto started = execute(&fixture.args, mode);
                require(started.status.ok() && started.run, "actual frontend execute failed");
                if (mode == Mode::stripe_pipeline) {
                    for (size_t stripe = 0; stripe < theta.size(); ++stripe) {
                        exsia::StripeReadyEvent event{};
                        event.run_id = fixture.identity; event.stripe_id = stripe; event.slot = stripe % 2;
                        event.row_begin = stripe * fixture.args.activation_rows_per_stripe;
                        event.row_end = std::min(fixture.args.I, event.row_begin + fixture.args.activation_rows_per_stripe);
                        event.activation_metadata = exsia::StripeMetadataSnapshot{meta.e_s, meta.rho, meta.sigma, theta[stripe]};
                        require(submit_stripe(*started.run, event, {true, theta[stripe]}).ok(), "actual stripe submit failed");
                    }
                }
                const auto done = fence(*started.run);
                require(done.status.ok(), "actual frontend fence failed");
                const size_t works = ((fixture.args.I + 15) / 16) * ((fixture.args.J + 15) / 16);
                require(observation.starts == 1 && done.stats.base.completed_output_tiles == works &&
                        done.stats.base.completed_fragments == works * (fixture.args.K / 16) &&
                        observation.callbacks > 0 && observation.scale_lanes > 0,
                        "actual descriptor/work/fragment/output counts");
                if (mode == Mode::stripe_pipeline) {
                    require(observation.stream_starts == 1 && observation.full_starts == 0 &&
                            done.stats.base.completed_stripes == theta.size() &&
                            done.stats.base.stripes_published == theta.size() &&
                            done.stripe_rtl_timings.size == theta.size(), "stripe completion count");
                    require(std::all_of(fixture.output.begin(), fixture.output.end(), [](float x) { return x == 17.0f; }),
                            "caller output exposed before authorization");
                    for (size_t stripe = 0; stripe < theta.size(); ++stripe) {
                        const auto &timing = done.stripe_rtl_timings[stripe];
                        const size_t begin = stripe * fixture.args.activation_rows_per_stripe;
                        require(timing.run_id == fixture.identity && timing.stripe_id == stripe &&
                                timing.slot == stripe % 2 && timing.row_begin == begin &&
                                timing.row_end == std::min(fixture.args.I, begin + fixture.args.activation_rows_per_stripe) &&
                                timing.completion_cycle >= timing.publish_cycle, "stripe timing identity");
                    }
                    require(authorize_output_commit(*started.run, true).ok(), "actual output authorization failed");
                } else require(observation.full_starts == 1 && observation.stream_starts == 0, "FULL execution path");
                require(expected_raw.size() == observation.raw.size() * 4 &&
                        expected_fout.size() == fixture.output.size() * 4, "expected layout");
                size_t cursor = 0;
                for (size_t i = 0; i < observation.raw.size(); ++i)
                    require(observation.seen[i] && observation.raw[i] == int32_t(uint32_t(get(expected_raw, cursor, 4))),
                            "independent G1 raw mismatch");
                cursor = 0;
                for (float value : fixture.output)
                    require(std::bit_cast<uint32_t>(value) == uint32_t(get(expected_fout, cursor, 4)),
                            "independent G2 fout/padding mismatch");
                const size_t logical = fixture.args.I * fixture.args.J;
                const size_t padding = fixture.output.size() - logical;
                raw_count += logical; fout_count += logical; padding_count += padding; ++invocations;
                std::cout << "SCU_PORTABLE_RESULT fixture=" << name << " mode=" << (mode == Mode::full ? "FULL" : "PIPELINE")
                          << " raw=" << logical << " f_out=" << logical << " padding=" << padding
                          << " works=" << works << " fragments=" << done.stats.base.completed_fragments
                          << " stripes=" << (mode == Mode::full ? 0 : theta.size())
                          << " callbacks=" << observation.callbacks << " cycles=" << done.stats.base.work_total_cycles
                          << " domain=2 operation=4 backend=actual_rtl_simulator\n";
                started.run.reset(); active = nullptr;
            }
        }
        std::cout << "SCU_PORTABLE_PASS unique_inputs=8 complete_invocations=" << invocations
                  << " raw=" << raw_count << " f_out=" << fout_count << " padding=" << padding_count
                  << " quantizer=not_run deterministic_pipeline=1 physical_jobs=0\n";
        return 0;
    } catch (const std::exception &error) {
        std::cerr << "SCU_PORTABLE_FAIL " << error.what() << '\n';
        return 1;
    }
}
