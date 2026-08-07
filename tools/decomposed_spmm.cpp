#include <array>
#include <bit>
#include <charconv>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <system_error>
#include <type_traits>
#include <utility>
#include <vector>

namespace {

constexpr std::size_t kDim = 16;
constexpr std::size_t kStripeRows = 16;
constexpr std::size_t kI = 32;
constexpr std::size_t kK = 64;
constexpr std::size_t kJ = 32;
constexpr std::size_t kMaxLanes = 4;
constexpr std::size_t kStripeCount = kI / kStripeRows;
constexpr std::size_t kJTileCount = (kJ + kDim - 1) / kDim;


enum class TestCaseKind {
    Int8Only,
    Carry,
    LaneGap,
    Lane4,
    MultiK,
    Mixed,
};

struct TestCaseSpec {
    TestCaseKind kind;
    std::string_view name;
    std::string_view description;
};

constexpr std::array<TestCaseSpec, 6> kTestCases{{
    {TestCaseKind::Int8Only, "int8", "single signed-INT8 lane"},
    {TestCaseKind::Carry, "carry", "balanced radix-256 carry boundaries"},
    {TestCaseKind::LaneGap, "lane-gap", "non-contiguous active lanes"},
    {TestCaseKind::Lane4, "lane4", "all four digit lanes"},
    {TestCaseKind::MultiK, "multi-k", "more than 16 compact K columns"},
    {TestCaseKind::Mixed, "mixed", "combined integration case"},
}};

const TestCaseSpec& test_case_spec(TestCaseKind kind) {
    for (const auto& spec : kTestCases) {
        if (spec.kind == kind) {
            return spec;
        }
    }
    throw std::runtime_error("unknown test case kind");
}

TestCaseKind parse_test_case(std::string_view name) {
    for (const auto& spec : kTestCases) {
        if (spec.name == name) {
            return spec.kind;
        }
    }
    throw std::runtime_error("unknown test case: " + std::string(name));
}

static_assert(kI % kStripeRows == 0);
static_assert(kStripeRows == kDim);
static_assert(kJ % kDim == 0);

template <typename T, std::size_t Rows, std::size_t Cols>
using Matrix = std::array<std::array<T, Cols>, Rows>;

using ActivationMatrix = Matrix<std::int32_t, kI, kK>;
using WeightMatrix = Matrix<std::int8_t, kK, kJ>;
using OutputMatrix = Matrix<std::int64_t, kI, kJ>;
using Tile8 = Matrix<std::int8_t, kDim, kDim>;
using Tile32 = Matrix<std::int32_t, kDim, kDim>;

struct Digits4 {
    std::array<std::int8_t, kMaxLanes> digit{};
    std::uint8_t active_mask = 0;
    bool fits_four_lanes = false;
};

struct StripePlan {
    std::size_t stripe_id = 0;
    std::size_t row_begin = 0;
    std::array<std::uint8_t, kK> column_lane_mask{};
    std::uint8_t active_lane_mask = 0;
    std::vector<std::size_t> active_lanes;
    std::vector<std::size_t> unique_k;

    // A_stacked = [D_lane0; D_lane1; ...], shape (L * 16) x H.
    std::vector<std::vector<std::int8_t>> a_stacked;
    // W_compact, shape H x 32.
    std::vector<std::vector<std::int8_t>> w_compact;
    // C_stacked = A_stacked * W_compact, shape (L * 16) x 32.
    std::vector<std::vector<std::int32_t>> c_stacked;
};

struct Job {
    std::size_t job_id = 0;
    std::size_t stripe_id = 0;
    std::size_t lane_position = 0;
    std::size_t lane_id = 0;
    std::size_t j_tile = 0;
    std::size_t k_tile_count = 0;
};

using DigitTensor = std::array<
    std::array<std::array<std::int8_t, kMaxLanes>, kK>,
    kI
>;

using RtlLaneOutput = std::array<
    std::array<Matrix<std::int32_t, kStripeRows, kJ>, kMaxLanes>,
    kStripeCount
>;

ActivationMatrix make_activations(TestCaseKind kind) {
    ActivationMatrix x{};

    switch (kind) {
    case TestCaseKind::Int8Only:
        x[0][1] = 95;
        x[1][4] = -128;
        x[2][7] = 127;
        x[16][2] = 1;
        x[17][44] = 127;
        x[18][52] = -128;
        break;

    case TestCaseKind::Carry:
        x[0][1] = 128;
        x[1][4] = -129;
        x[2][7] = 32767;
        x[3][10] = -32768;
        x[4][15] = 1048575;
        x[5][18] = -1048576;

        x[16][2] = -129;
        x[17][8] = 128;
        x[18][17] = -32768;
        x[19][25] = 32767;
        x[20][37] = -1048576;
        x[21][44] = 1048575;
        break;

    case TestCaseKind::LaneGap:
        // No lane-0 digit is active. This verifies lane-position mapping.
        x[0][1] = 256;
        x[1][4] = 65536;
        x[2][7] = 16777216;
        x[16][2] = -256;
        x[17][8] = -65536;
        x[18][17] = -16777216;
        break;

    case TestCaseKind::Lane4:
        x[0][1] = 95;
        x[1][4] = 256;
        x[2][7] = 65536;
        x[3][10] = 16777216;
        x[16][2] = -128;
        x[17][8] = -256;
        x[18][17] = -65536;
        x[19][25] = -16777216;
        break;

    case TestCaseKind::MultiK:
        // Twenty union-K columns force two DIM=16 K tiles.
        for (std::size_t offset = 0; offset < 20; ++offset) {
            x[offset % kStripeRows][offset] =
                static_cast<std::int32_t>(offset + 1);
            x[kStripeRows + (offset % kStripeRows)][32 + offset] =
                -static_cast<std::int32_t>(offset + 1);
        }
        break;

    case TestCaseKind::Mixed:
        x[0][1] = 95;
        x[0][18] = 7000;
        x[0][33] = -129;
        x[1][4] = -128;
        x[1][31] = 32767;
        x[1][48] = 12;
        x[2][7] = 127;
        x[2][18] = -32768;
        x[2][63] = 1048575;
        x[3][15] = -1048576;
        x[3][29] = 256;
        x[3][35] = -1;
        x[4][10] = 16777216;

        x[16][2] = 1;
        x[16][17] = -129;
        x[16][37] = 7000;
        x[17][8] = 128;
        x[17][25] = -32768;
        x[17][44] = 127;
        x[18][9] = -1;
        x[18][17] = 32767;
        x[18][52] = -128;
        x[19][30] = 1048575;
        x[19][37] = -1048576;
        x[19][62] = 255;
        x[20][40] = -16777216;
        break;
    }

    return x;
}

WeightMatrix make_weights() {
    WeightMatrix weights{};

    for (std::size_t k = 0; k < kK; ++k) {
        for (std::size_t j = 0; j < kJ; ++j) {
            const int raw = static_cast<int>((17 * k + 13 * j + 29) % 256);
            weights[k][j] = static_cast<std::int8_t>(
                raw <= 127 ? raw : raw - 256
            );
        }
    }

    return weights;
}

Digits4 decompose_balanced4(std::int32_t value) {
    Digits4 result{};
    std::int64_t q = value;

    for (std::size_t lane = 0; lane < kMaxLanes; ++lane) {
        const auto raw = static_cast<std::uint8_t>(
            static_cast<std::uint64_t>(q) & 0xffu
        );
        const auto digit = std::bit_cast<std::int8_t>(raw);
        result.digit[lane] = digit;

        if (digit != 0) {
            result.active_mask |= static_cast<std::uint8_t>(1u << lane);
        }

        // q - digit is exactly divisible by 256.
        q = (q - static_cast<std::int64_t>(digit)) / 256;
    }

    result.fits_four_lanes = q == 0;
    return result;
}

std::int64_t compose_digits(const std::array<std::int8_t, kMaxLanes>& d) {
    std::int64_t value = 0;
    std::int64_t place = 1;

    for (const std::int8_t digit : d) {
        value += static_cast<std::int64_t>(digit) * place;
        place *= 256;
    }

    return value;
}

DigitTensor decompose_tensor(const ActivationMatrix& x) {
    DigitTensor digits{};

    for (std::size_t i = 0; i < kI; ++i) {
        for (std::size_t k = 0; k < kK; ++k) {
            const Digits4 d = decompose_balanced4(x[i][k]);
            if (!d.fits_four_lanes) {
                throw std::runtime_error(
                    "activation does not fit four balanced INT8 digits at i="
                    + std::to_string(i) + " k=" + std::to_string(k)
                );
            }
            if (compose_digits(d.digit) != x[i][k]) {
                throw std::runtime_error(
                    "balanced digit reconstruction failed at i="
                    + std::to_string(i) + " k=" + std::to_string(k)
                );
            }
            digits[i][k] = d.digit;
        }
    }

    return digits;
}

OutputMatrix direct_matmul(
    const ActivationMatrix& x,
    const WeightMatrix& weights
) {
    OutputMatrix output{};

    for (std::size_t i = 0; i < kI; ++i) {
        for (std::size_t j = 0; j < kJ; ++j) {
            std::int64_t sum = 0;
            for (std::size_t k = 0; k < kK; ++k) {
                sum += static_cast<std::int64_t>(x[i][k])
                    * static_cast<std::int64_t>(weights[k][j]);
            }
            output[i][j] = sum;
        }
    }

    return output;
}

std::vector<StripePlan> build_plans(
    const ActivationMatrix& x,
    const WeightMatrix& weights,
    const DigitTensor& digits
) {
    std::vector<StripePlan> plans;
    plans.reserve(kStripeCount);

    for (std::size_t stripe = 0; stripe < kStripeCount; ++stripe) {
        StripePlan plan{};
        plan.stripe_id = stripe;
        plan.row_begin = stripe * kStripeRows;

        // Determine, per original K column, which digit planes are needed by any
        // row in the stripe. This is the exact "one column -> N decomposed columns"
        // metadata, even though the single-GEMM realization stacks planes on M.
        for (std::size_t k = 0; k < kK; ++k) {
            std::uint8_t mask = 0;
            for (std::size_t local_i = 0; local_i < kStripeRows; ++local_i) {
                const std::size_t i = plan.row_begin + local_i;
                for (std::size_t lane = 0; lane < kMaxLanes; ++lane) {
                    if (digits[i][k][lane] != 0) {
                        mask |= static_cast<std::uint8_t>(1u << lane);
                    }
                }
            }
            plan.column_lane_mask[k] = mask;
            plan.active_lane_mask |= mask;
            if (mask != 0) {
                plan.unique_k.push_back(k);
            }
        }

        for (std::size_t lane = 0; lane < kMaxLanes; ++lane) {
            if ((plan.active_lane_mask & (1u << lane)) != 0) {
                plan.active_lanes.push_back(lane);
            }
        }

        const std::size_t h = plan.unique_k.size();
        const std::size_t stacked_rows = plan.active_lanes.size() * kStripeRows;

        plan.a_stacked.assign(
            stacked_rows,
            std::vector<std::int8_t>(h, 0)
        );
        plan.w_compact.assign(h, std::vector<std::int8_t>(kJ, 0));
        plan.c_stacked.assign(
            stacked_rows,
            std::vector<std::int32_t>(kJ, 0)
        );

        for (std::size_t lane_pos = 0;
             lane_pos < plan.active_lanes.size();
             ++lane_pos) {
            const std::size_t lane = plan.active_lanes[lane_pos];
            for (std::size_t local_i = 0; local_i < kStripeRows; ++local_i) {
                const std::size_t i = plan.row_begin + local_i;
                const std::size_t stacked_i = lane_pos * kStripeRows + local_i;
                for (std::size_t compact_k = 0; compact_k < h; ++compact_k) {
                    const std::size_t original_k = plan.unique_k[compact_k];
                    plan.a_stacked[stacked_i][compact_k] =
                        digits[i][original_k][lane];
                }
            }
        }

        for (std::size_t compact_k = 0; compact_k < h; ++compact_k) {
            const std::size_t original_k = plan.unique_k[compact_k];
            for (std::size_t j = 0; j < kJ; ++j) {
                plan.w_compact[compact_k][j] = weights[original_k][j];
            }
        }

        for (std::size_t stacked_i = 0;
             stacked_i < stacked_rows;
             ++stacked_i) {
            for (std::size_t j = 0; j < kJ; ++j) {
                std::int64_t sum = 0;
                for (std::size_t compact_k = 0; compact_k < h; ++compact_k) {
                    sum += static_cast<std::int64_t>(
                        plan.a_stacked[stacked_i][compact_k]
                    ) * static_cast<std::int64_t>(
                        plan.w_compact[compact_k][j]
                    );
                }
                if (sum < std::numeric_limits<std::int32_t>::min()
                        || sum > std::numeric_limits<std::int32_t>::max()) {
                    throw std::runtime_error("stacked GEMM INT32 overflow");
                }
                plan.c_stacked[stacked_i][j] = static_cast<std::int32_t>(sum);
            }
        }

        // The union-K selection must preserve every nonzero original residual.
        for (std::size_t local_i = 0; local_i < kStripeRows; ++local_i) {
            const std::size_t i = plan.row_begin + local_i;
            for (std::size_t k = 0; k < kK; ++k) {
                if (x[i][k] != 0 && plan.column_lane_mask[k] == 0) {
                    throw std::runtime_error("nonzero K column was lost");
                }
            }
        }

        plans.push_back(std::move(plan));
    }

    return plans;
}

std::vector<Job> build_jobs(const std::vector<StripePlan>& plans) {
    std::vector<Job> jobs;

    for (const StripePlan& plan : plans) {
        const std::size_t k_tile_count =
            (plan.unique_k.size() + kDim - 1) / kDim;

        for (std::size_t lane_pos = 0;
             lane_pos < plan.active_lanes.size();
             ++lane_pos) {
            for (std::size_t j_tile = 0; j_tile < kJTileCount; ++j_tile) {
                jobs.push_back(Job{
                    .job_id = jobs.size(),
                    .stripe_id = plan.stripe_id,
                    .lane_position = lane_pos,
                    .lane_id = plan.active_lanes[lane_pos],
                    .j_tile = j_tile,
                    .k_tile_count = k_tile_count,
                });
            }
        }
    }

    return jobs;
}

Tile8 activation_tile(
    const StripePlan& plan,
    const Job& job,
    std::size_t k_tile
) {
    Tile8 tile{};
    const std::size_t stacked_row_base = job.lane_position * kStripeRows;
    const std::size_t compact_k_base = k_tile * kDim;

    for (std::size_t row = 0; row < kDim; ++row) {
        for (std::size_t kk = 0; kk < kDim; ++kk) {
            const std::size_t compact_k = compact_k_base + kk;
            if (compact_k < plan.unique_k.size()) {
                tile[row][kk] =
                    plan.a_stacked[stacked_row_base + row][compact_k];
            }
        }
    }

    return tile;
}

Tile8 weight_tile(
    const StripePlan& plan,
    const Job& job,
    std::size_t k_tile
) {
    Tile8 tile{};
    const std::size_t compact_k_base = k_tile * kDim;
    const std::size_t j_base = job.j_tile * kDim;

    for (std::size_t kk = 0; kk < kDim; ++kk) {
        const std::size_t compact_k = compact_k_base + kk;
        if (compact_k >= plan.unique_k.size()) {
            continue;
        }
        for (std::size_t col = 0; col < kDim; ++col) {
            tile[kk][col] = plan.w_compact[compact_k][j_base + col];
        }
    }

    return tile;
}

Tile32 golden_tile(const StripePlan& plan, const Job& job) {
    Tile32 tile{};
    const std::size_t stacked_row_base = job.lane_position * kStripeRows;
    const std::size_t j_base = job.j_tile * kDim;

    for (std::size_t row = 0; row < kDim; ++row) {
        for (std::size_t col = 0; col < kDim; ++col) {
            tile[row][col] =
                plan.c_stacked[stacked_row_base + row][j_base + col];
        }
    }

    return tile;
}

OutputMatrix compose_stacked(const std::vector<StripePlan>& plans) {
    OutputMatrix output{};

    for (const StripePlan& plan : plans) {
        for (std::size_t lane_pos = 0;
             lane_pos < plan.active_lanes.size();
             ++lane_pos) {
            const std::size_t lane = plan.active_lanes[lane_pos];
            std::int64_t place = 1;
            for (std::size_t p = 0; p < lane; ++p) {
                place *= 256;
            }

            for (std::size_t local_i = 0; local_i < kStripeRows; ++local_i) {
                const std::size_t i = plan.row_begin + local_i;
                const std::size_t stacked_i = lane_pos * kStripeRows + local_i;
                for (std::size_t j = 0; j < kJ; ++j) {
                    output[i][j] += static_cast<std::int64_t>(
                        plan.c_stacked[stacked_i][j]
                    ) * place;
                }
            }
        }
    }

    return output;
}

template <typename T, std::size_t Rows, std::size_t Cols>
void write_hex_matrix(
    const std::filesystem::path& path,
    const Matrix<T, Rows, Cols>& matrix
) {
    using Unsigned = std::make_unsigned_t<T>;
    std::ofstream output(path);
    if (!output) {
        throw std::runtime_error("failed to open: " + path.string());
    }

    output << std::hex << std::setfill('0');
    for (const auto& row : matrix) {
        for (const T value : row) {
            output << std::setw(static_cast<int>(sizeof(T) * 2))
                   << static_cast<std::uint64_t>(std::bit_cast<Unsigned>(value))
                   << '\n';
        }
    }
}

void write_plan(
    const std::filesystem::path& path,
    TestCaseKind kind,
    const std::vector<StripePlan>& plans,
    const std::vector<Job>& jobs
) {
    std::ofstream out(path);
    if (!out) {
        throw std::runtime_error("failed to open: " + path.string());
    }

    const auto& spec = test_case_spec(kind);
    out << "case=" << spec.name << " description=\"" << spec.description
        << "\"\n";
    out << "DIM=" << kDim << " I=" << kI << " K=" << kK
        << " J=" << kJ << " max_lanes=" << kMaxLanes << "\n\n";

    for (const StripePlan& plan : plans) {
        out << "STRIPE " << plan.stripe_id
            << " rows=[" << plan.row_begin << ','
            << (plan.row_begin + kStripeRows) << ")\n";
        out << "  active_lane_mask=0x" << std::hex
            << static_cast<int>(plan.active_lane_mask) << std::dec << "\n";
        out << "  active_lanes=";
        for (const std::size_t lane : plan.active_lanes) {
            out << lane << ' ';
        }
        out << "\n  union_unique_k(" << plan.unique_k.size() << ")=";
        for (const std::size_t k : plan.unique_k) {
            out << k << ' ';
        }
        out << "\n  per-column masks:\n";
        for (const std::size_t k : plan.unique_k) {
            out << "    k=" << std::setw(2) << k
                << " lane_mask=0x" << std::hex
                << static_cast<int>(plan.column_lane_mask[k])
                << std::dec << '\n';
        }
        out << "  logical GEMM: A_stacked=" << plan.a_stacked.size()
            << 'x' << plan.unique_k.size()
            << " W_compact=" << plan.unique_k.size() << 'x' << kJ
            << " C_stacked=" << plan.c_stacked.size() << 'x' << kJ
            << "\n\n";
    }

    out << "PHYSICAL DIM=16 JOBS\n";
    for (const Job& job : jobs) {
        out << "  job=" << job.job_id
            << " stripe=" << job.stripe_id
            << " lane_position=" << job.lane_position
            << " lane_id=" << job.lane_id
            << " j_tile=" << job.j_tile
            << " k_tiles=" << job.k_tile_count << '\n';
    }
}

void emit_tile_assignments(
    std::ostream& out,
    const Tile8& tile,
    std::string_view variable,
    int indent
) {
    const std::string pad(static_cast<std::size_t>(indent), ' ');
    for (std::size_t row = 0; row < kDim; ++row) {
        for (std::size_t col = 0; col < kDim; ++col) {
            if (tile[row][col] != 0) {
                out << pad << variable << '[' << row << "][" << col
                    << "] = " << static_cast<int>(tile[row][col]) << ";\n";
            }
        }
    }
}

void emit_tile_assignments(
    std::ostream& out,
    const Tile32& tile,
    std::string_view variable,
    int indent
) {
    const std::string pad(static_cast<std::size_t>(indent), ' ');
    for (std::size_t row = 0; row < kDim; ++row) {
        for (std::size_t col = 0; col < kDim; ++col) {
            if (tile[row][col] != 0) {
                out << pad << variable << '[' << row << "][" << col
                    << "] = " << tile[row][col] << ";\n";
            }
        }
    }
}

void write_generated_bsv(
    const std::filesystem::path& path,
    const std::vector<StripePlan>& plans,
    const std::vector<Job>& jobs
) {
    std::ofstream out(path);
    if (!out) {
        throw std::runtime_error("failed to open: " + path.string());
    }

    out << "package GeneratedDecomposedData;\n\n"
        << "import Vector::*;\n\n"
        << "typedef Vector#(16, Vector#(16, Int#(8))) GeneratedTile8;\n"
        << "typedef Vector#(16, Vector#(16, Int#(32))) GeneratedTile32;\n\n";

    out << "function UInt#(8) generatedJobCount();\n"
        << "    return " << jobs.size() << ";\n"
        << "endfunction\n\n";

    auto emit_meta_function = [&](std::string_view name, auto getter, int width) {
        out << "function UInt#(" << width << ") " << name
            << "(UInt#(8) job);\n"
            << "    UInt#(" << width << ") value = 0;\n"
            << "    case (job)\n";
        for (const Job& job : jobs) {
            out << "        " << job.job_id << ": value = "
                << getter(job) << ";\n";
        }
        out << "        default: value = 0;\n"
            << "    endcase\n"
            << "    return value;\n"
            << "endfunction\n\n";
    };

    emit_meta_function("generatedStripe", [](const Job& j) { return j.stripe_id; }, 2);
    emit_meta_function("generatedLane", [](const Job& j) { return j.lane_id; }, 2);
    emit_meta_function("generatedJTile", [](const Job& j) { return j.j_tile; }, 2);
    emit_meta_function(
        "generatedKTileCount",
        [](const Job& j) { return j.k_tile_count; },
        3
    );

    out << "function GeneratedTile8 generatedActivationTile(\n"
        << "    UInt#(8) job, UInt#(3) kTile\n"
        << ");\n"
        << "    GeneratedTile8 tile = replicate(replicate(0));\n"
        << "    case (job)\n";

    for (const Job& job : jobs) {
        const StripePlan& plan = plans.at(job.stripe_id);
        out << "        " << job.job_id << ": begin\n"
            << "            case (kTile)\n";
        for (std::size_t kt = 0; kt < job.k_tile_count; ++kt) {
            out << "                " << kt << ": begin\n";
            emit_tile_assignments(out, activation_tile(plan, job, kt), "tile", 20);
            out << "                end\n";
        }
        out << "                default: tile = tile;\n"
            << "            endcase\n"
            << "        end\n";
    }
    out << "        default: tile = tile;\n"
        << "    endcase\n"
        << "    return tile;\n"
        << "endfunction\n\n";

    out << "function GeneratedTile8 generatedWeightTile(\n"
        << "    UInt#(8) job, UInt#(3) kTile\n"
        << ");\n"
        << "    GeneratedTile8 tile = replicate(replicate(0));\n"
        << "    case (job)\n";

    for (const Job& job : jobs) {
        const StripePlan& plan = plans.at(job.stripe_id);
        out << "        " << job.job_id << ": begin\n"
            << "            case (kTile)\n";
        for (std::size_t kt = 0; kt < job.k_tile_count; ++kt) {
            out << "                " << kt << ": begin\n";
            emit_tile_assignments(out, weight_tile(plan, job, kt), "tile", 20);
            out << "                end\n";
        }
        out << "                default: tile = tile;\n"
            << "            endcase\n"
            << "        end\n";
    }
    out << "        default: tile = tile;\n"
        << "    endcase\n"
        << "    return tile;\n"
        << "endfunction\n\n";

    out << "function GeneratedTile32 generatedGoldenTile(UInt#(8) job);\n"
        << "    GeneratedTile32 tile = replicate(replicate(0));\n"
        << "    case (job)\n";
    for (const Job& job : jobs) {
        const StripePlan& plan = plans.at(job.stripe_id);
        out << "        " << job.job_id << ": begin\n";
        emit_tile_assignments(out, golden_tile(plan, job), "tile", 12);
        out << "        end\n";
    }
    out << "        default: tile = tile;\n"
        << "    endcase\n"
        << "    return tile;\n"
        << "endfunction\n\n"
        << "endpackage\n";
}

void write_job_golden_hex(
    const std::filesystem::path& path,
    const std::vector<StripePlan>& plans,
    const std::vector<Job>& jobs
) {
    std::ofstream out(path);
    if (!out) {
        throw std::runtime_error("failed to open: " + path.string());
    }
    out << std::hex << std::setfill('0');
    for (const Job& job : jobs) {
        const Tile32 tile = golden_tile(plans.at(job.stripe_id), job);
        for (const auto& row : tile) {
            for (const std::int32_t value : row) {
                out << std::setw(8)
                    << static_cast<std::uint64_t>(
                        std::bit_cast<std::uint32_t>(value)
                    ) << '\n';
            }
        }
    }
}

void assert_equal(
    const OutputMatrix& a,
    const OutputMatrix& b,
    std::string_view what
) {
    for (std::size_t i = 0; i < kI; ++i) {
        for (std::size_t j = 0; j < kJ; ++j) {
            if (a[i][j] != b[i][j]) {
                throw std::runtime_error(
                    std::string(what) + " mismatch at i=" + std::to_string(i)
                    + " j=" + std::to_string(j)
                    + " lhs=" + std::to_string(a[i][j])
                    + " rhs=" + std::to_string(b[i][j])
                );
            }
        }
    }
}

void validate_case_properties(
    TestCaseKind kind,
    const std::vector<StripePlan>& plans,
    const std::vector<Job>& jobs
) {
    if (plans.size() != kStripeCount) {
        throw std::runtime_error("unexpected stripe plan count");
    }

    std::uint8_t expected_mask = 0;
    std::size_t expected_unique_k = 0;
    bool require_exact_unique_k = false;
    bool require_two_k_tiles = false;

    switch (kind) {
    case TestCaseKind::Int8Only:
        expected_mask = 0x01;
        break;
    case TestCaseKind::Carry:
        expected_mask = 0x07;
        break;
    case TestCaseKind::LaneGap:
        expected_mask = 0x0e;
        break;
    case TestCaseKind::Lane4:
    case TestCaseKind::Mixed:
        expected_mask = 0x0f;
        break;
    case TestCaseKind::MultiK:
        expected_mask = 0x01;
        expected_unique_k = 20;
        require_exact_unique_k = true;
        require_two_k_tiles = true;
        break;
    }

    for (const StripePlan& plan : plans) {
        if (plan.active_lane_mask != expected_mask) {
            throw std::runtime_error(
                "unexpected active lane mask in case "
                + std::string(test_case_spec(kind).name)
                + " stripe=" + std::to_string(plan.stripe_id)
            );
        }
        if (require_exact_unique_k && plan.unique_k.size() != expected_unique_k) {
            throw std::runtime_error("unexpected union-K count");
        }
        const std::size_t k_tiles =
            (plan.unique_k.size() + kDim - 1) / kDim;
        if (require_two_k_tiles && k_tiles != 2) {
            throw std::runtime_error("multi-k case did not create two K tiles");
        }
    }

    const std::size_t expected_jobs = [&] {
        std::size_t count = 0;
        for (const StripePlan& plan : plans) {
            count += plan.active_lanes.size() * kJTileCount;
        }
        return count;
    }();
    if (jobs.size() != expected_jobs) {
        throw std::runtime_error("unexpected physical job count");
    }
}

struct SelfTestSummary {
    std::string_view name;
    std::size_t jobs = 0;
    std::size_t k_tiles = 0;
    std::array<std::size_t, kStripeCount> union_k{};
    std::array<std::uint8_t, kStripeCount> lane_masks{};
};

SelfTestSummary run_case_self_test(TestCaseKind kind) {
    const ActivationMatrix x = make_activations(kind);
    const WeightMatrix weights = make_weights();
    const DigitTensor digits = decompose_tensor(x);
    const auto plans = build_plans(x, weights, digits);
    const auto jobs = build_jobs(plans);
    const OutputMatrix direct = direct_matmul(x, weights);
    const OutputMatrix composed = compose_stacked(plans);

    assert_equal(composed, direct, "CPU decomposed SpMM");
    validate_case_properties(kind, plans, jobs);

    SelfTestSummary summary{};
    summary.name = test_case_spec(kind).name;
    summary.jobs = jobs.size();
    for (const Job& job : jobs) {
        summary.k_tiles += job.k_tile_count;
    }
    for (const StripePlan& plan : plans) {
        summary.union_k[plan.stripe_id] = plan.unique_k.size();
        summary.lane_masks[plan.stripe_id] = plan.active_lane_mask;
    }
    return summary;
}

void run_self_test(std::string_view requested) {
    // Fixed arithmetic checks independent of the matrix test cases.
    const auto d128 = decompose_balanced4(128);
    const auto dm129 = decompose_balanced4(-129);
    const auto d32767 = decompose_balanced4(32767);
    const auto d1048575 = decompose_balanced4(1048575);
    const auto dLane4 = decompose_balanced4(16777216);

    if (d128.digit != std::array<std::int8_t, 4>{-128, 1, 0, 0}
            || dm129.digit != std::array<std::int8_t, 4>{127, -1, 0, 0}
            || d32767.digit != std::array<std::int8_t, 4>{-1, -128, 1, 0}
            || d1048575.digit != std::array<std::int8_t, 4>{-1, 0, 16, 0}
            || dLane4.digit != std::array<std::int8_t, 4>{0, 0, 0, 1}) {
        throw std::runtime_error("fixed balanced decomposition check failed");
    }

    std::vector<SelfTestSummary> summaries;
    if (requested == "all") {
        for (const auto& spec : kTestCases) {
            summaries.push_back(run_case_self_test(spec.kind));
        }
    }
    else {
        summaries.push_back(run_case_self_test(parse_test_case(requested)));
    }

    std::cout << "CPU TEST CASES\n";
    std::cout << "  case       jobs  k-tiles  stripe0(mask/K)  stripe1(mask/K)  status\n";
    for (const auto& summary : summaries) {
        std::cout << "  " << std::left << std::setw(10) << summary.name
                  << std::right << std::setw(5) << summary.jobs
                  << std::setw(9) << summary.k_tiles
                  << "  0x" << std::hex << static_cast<int>(summary.lane_masks[0])
                  << std::dec << '/' << summary.union_k[0]
                  << "              0x" << std::hex
                  << static_cast<int>(summary.lane_masks[1])
                  << std::dec << '/' << summary.union_k[1]
                  << "             PASS\n";
    }
    std::cout << "CPU DECOMPOSED SpMM: PASS cases=" << summaries.size() << '\n';
}

void generate_files(const std::filesystem::path& directory, TestCaseKind kind) {
    std::filesystem::create_directories(directory);

    const ActivationMatrix x = make_activations(kind);
    const WeightMatrix weights = make_weights();
    const DigitTensor digits = decompose_tensor(x);
    const auto plans = build_plans(x, weights, digits);
    const auto jobs = build_jobs(plans);
    const OutputMatrix direct = direct_matmul(x, weights);
    const OutputMatrix composed = compose_stacked(plans);

    assert_equal(composed, direct, "generated CPU decomposed SpMM");

    write_hex_matrix(directory / "x.hex", x);
    write_hex_matrix(directory / "w.hex", weights);
    write_hex_matrix(directory / "direct_golden.hex", direct);
    write_hex_matrix(directory / "cpu_composed.hex", composed);
    write_job_golden_hex(directory / "job_golden.hex", plans, jobs);
    write_plan(directory / "plan.txt", kind, plans, jobs);
    write_generated_bsv(
        directory / "GeneratedDecomposedData.bsv",
        plans,
        jobs
    );

    std::ofstream case_file(directory / "case.txt");
    case_file << test_case_spec(kind).name << '\n';

    std::cout << "GENERATE: PASS case=" << test_case_spec(kind).name
              << " directory=" << directory << '\n';
    std::cout << "Copy GeneratedDecomposedData.bsv next to the BSV sources,\n"
              << "or add the generated directory to the bsc search path.\n";
}

std::int32_t parse_hex32(std::string_view token) {
    std::uint32_t raw = 0;
    const auto [end, error] = std::from_chars(
        token.data(), token.data() + token.size(), raw, 16
    );
    if (error != std::errc{} || end != token.data() + token.size()) {
        throw std::runtime_error("invalid RTL hex token: " + std::string(token));
    }
    return std::bit_cast<std::int32_t>(raw);
}

std::vector<std::int32_t> read_rtl_tokens(const std::filesystem::path& path) {
    std::ifstream in(path);
    if (!in) {
        throw std::runtime_error("failed to open RTL log: " + path.string());
    }

    std::vector<std::int32_t> values;
    std::string line;
    while (std::getline(in, line)) {
        if (line.rfind("RTL_ROW ", 0) == 0) {
            std::istringstream words(line);
            std::string tag;
            std::string job_field;
            std::string row_field;
            words >> tag >> job_field >> row_field;
            if (tag != "RTL_ROW"
                    || job_field.rfind("job=", 0) != 0
                    || row_field.rfind("row=", 0) != 0) {
                throw std::runtime_error("malformed RTL_ROW line: " + line);
            }
            for (std::size_t col = 0; col < kDim; ++col) {
                std::string token;
                if (!(words >> token)) {
                    throw std::runtime_error(
                        "RTL_ROW has fewer than 16 values: " + line
                    );
                }
                values.push_back(parse_hex32(token));
            }
            std::string extra;
            if (words >> extra) {
                throw std::runtime_error(
                    "RTL_ROW has more than 16 values: " + line
                );
            }
            continue;
        }

        // Backward-compatible parser for the original one-value-per-line log.
        const std::size_t marker = line.find("RTL_OUT");
        if (marker == std::string::npos) {
            continue;
        }

        std::istringstream words(line.substr(marker));
        std::string tag;
        std::string token;
        words >> tag >> token;
        if (tag != "RTL_OUT" || token.empty()) {
            throw std::runtime_error("malformed RTL_OUT line: " + line);
        }
        values.push_back(parse_hex32(token));
    }

    return values;
}

int compare_rtl(const std::filesystem::path& rtl_log, TestCaseKind kind) {
    const ActivationMatrix x = make_activations(kind);
    const WeightMatrix weights = make_weights();
    const DigitTensor digits = decompose_tensor(x);
    const auto plans = build_plans(x, weights, digits);
    const auto jobs = build_jobs(plans);
    const OutputMatrix direct = direct_matmul(x, weights);
    const std::vector<std::int32_t> tokens = read_rtl_tokens(rtl_log);
    const std::size_t expected_tokens = jobs.size() * kDim * kDim;

    if (tokens.size() != expected_tokens) {
        throw std::runtime_error(
            "expected " + std::to_string(expected_tokens)
            + " RTL_OUT tokens, got " + std::to_string(tokens.size())
        );
    }

    RtlLaneOutput rtl{};
    std::size_t cursor = 0;
    std::size_t tile_mismatches = 0;

    for (const Job& job : jobs) {
        const StripePlan& plan = plans.at(job.stripe_id);
        const Tile32 golden = golden_tile(plan, job);
        const std::size_t j_base = job.j_tile * kDim;

        for (std::size_t row = 0; row < kDim; ++row) {
            for (std::size_t col = 0; col < kDim; ++col) {
                const std::int32_t actual = tokens.at(cursor++);
                if (actual != golden[row][col]) {
                    if (tile_mismatches < 16) {
                        std::cerr << "tile mismatch job=" << job.job_id
                                  << " row=" << row << " col=" << col
                                  << " expected=" << golden[row][col]
                                  << " actual=" << actual << '\n';
                    }
                    ++tile_mismatches;
                }

                rtl[job.stripe_id][job.lane_id][row][j_base + col] = actual;
            }
        }
    }

    OutputMatrix composed{};
    for (std::size_t stripe = 0; stripe < kStripeCount; ++stripe) {
        for (std::size_t local_i = 0; local_i < kStripeRows; ++local_i) {
            const std::size_t i = stripe * kStripeRows + local_i;
            for (std::size_t j = 0; j < kJ; ++j) {
                std::int64_t place = 1;
                for (std::size_t lane = 0; lane < kMaxLanes; ++lane) {
                    composed[i][j] += static_cast<std::int64_t>(
                        rtl[stripe][lane][local_i][j]
                    ) * place;
                    place *= 256;
                }
            }
        }
    }

    std::size_t composed_mismatches = 0;
    for (std::size_t i = 0; i < kI; ++i) {
        for (std::size_t j = 0; j < kJ; ++j) {
            if (composed[i][j] != direct[i][j]) {
                if (composed_mismatches < 16) {
                    std::cerr << "compose mismatch i=" << i << " j=" << j
                              << " expected=" << direct[i][j]
                              << " actual=" << composed[i][j] << '\n';
                }
                ++composed_mismatches;
            }
        }
    }

    std::cout << "RTL CASE: " << test_case_spec(kind).name << '\n';
    std::cout << "RTL TILE GEMM: "
              << (tile_mismatches == 0 ? "PASS" : "FAIL")
              << " mismatches=" << tile_mismatches << '\n';
    std::cout << "RTL DECOMPOSED SpMM: "
              << (composed_mismatches == 0 ? "PASS" : "FAIL")
              << " mismatches=" << composed_mismatches << '\n';

    return tile_mismatches == 0 && composed_mismatches == 0 ? 0 : 1;
}

void print_cases() {
    std::cout << "available cases:\n";
    for (const auto& spec : kTestCases) {
        std::cout << "  " << std::left << std::setw(10) << spec.name
                  << " " << spec.description << '\n';
    }
}

void print_usage(std::string_view program) {
    std::cerr << "usage:\n"
              << "  " << program << " list-cases\n"
              << "  " << program << " self-test [all|case]\n"
              << "  " << program << " generate <output-directory> [case]\n"
              << "  " << program << " compare <bsv-simulation-log> [case]\n";
}

} // namespace

int main(int argc, char** argv) {
    try {
        if (argc == 2 && std::string_view(argv[1]) == "list-cases") {
            print_cases();
            return 0;
        }
        if ((argc == 2 || argc == 3)
                && std::string_view(argv[1]) == "self-test") {
            run_self_test(argc == 3 ? std::string_view(argv[2]) : "all");
            return 0;
        }
        if ((argc == 3 || argc == 4)
                && std::string_view(argv[1]) == "generate") {
            const TestCaseKind kind = argc == 4
                ? parse_test_case(argv[3])
                : TestCaseKind::Mixed;
            run_case_self_test(kind);
            generate_files(argv[2], kind);
            return 0;
        }
        if ((argc == 3 || argc == 4)
                && std::string_view(argv[1]) == "compare") {
            const TestCaseKind kind = argc == 4
                ? parse_test_case(argv[3])
                : TestCaseKind::Mixed;
            return compare_rtl(argv[2], kind);
        }

        print_usage(argv[0]);
        return 2;
    } catch (const std::exception& error) {
        std::cerr << "error: " << error.what() << '\n';
        return 2;
    }
}
