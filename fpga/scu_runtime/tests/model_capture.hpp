#pragma once

#include "json.hpp"
#include <algorithm>
#include <cstdint>
#include <cmath>
#include <type_traits>
#include <vector>
#include <limits>
#include <stdexcept>
#include <string>

// Shared validation for observer output and replay input. Schema 1 captures
// numerical inputs only; they do not contain original publication identities.
namespace model_capture {
using nlohmann::json;
inline void require(bool value, const char *message) {
    if (!value) throw std::runtime_error(message);
}
inline uint64_t natural(const json &value) {
    require(value.is_number_unsigned() || (value.is_number_integer() && value.get<int64_t>() >= 0),
            "capture requires nonnegative integer");
    return value.get<uint64_t>();
}
inline uint64_t product(uint64_t a, uint64_t b) {
    require(!b || a <= UINT64_MAX / b, "capture extent overflow");
    return a * b;
}
// Capture exact native packet fields; never serialize compiler padding or pointers.
#define MODEL_CAPTURE_PACKET_FIELDS(X) \
    X(version) X(digit_bits) X(lane_capacity) X(digit_storage) X(int4_packing) \
    X(stripe_id) X(row_begin) X(row_count) X(logical_k) X(logical_j) X(j_padded) \
    X(block_size) X(array_dim) X(activation_value_count) X(residual_event_count) X(total_output_values)
#define MODEL_CAPTURE_BLOCK_FIELDS(X) \
    X(block_id) X(global_k_begin) X(compact_k_count) X(padded_k_count) \
    X(active_lane_mask) X(active_lane_count) X(k_index_offset) X(activation_offset) \
    X(activation_byte_offset) X(activation_byte_count) X(output_value_offset) X(rows_padded) X(lane_stride_values)
template<class T> inline void scalar(const json &j, T &out) {
    if constexpr (std::is_enum_v<T>) {
        std::underlying_type_t<T> value{}; scalar(j, value); out = static_cast<T>(value);
    } else {
        require(j.is_number_integer(), "residual field requires integer");
        if (j.is_number_unsigned()) {
            const auto n = j.get<uint64_t>();
            require(n <= uint64_t(std::numeric_limits<T>::max()), "residual field overflow");
            out = static_cast<T>(n);
        } else {
            const auto n = j.get<int64_t>();
            require(n >= int64_t(std::numeric_limits<T>::lowest()) &&
                    (n < 0 || uint64_t(n) <= uint64_t(std::numeric_limits<T>::max())),
                    "residual field out of range");
            out = static_cast<T>(n);
        }
    }
}
template<class Container> inline void array_values(const json &j, Container &out) {
    require(j.is_array() && j.size() == out.size(), "residual array extent");
    for (size_t i = 0; i < out.size(); ++i) scalar(j.at(i), out[i]);
}
template<class Packet> inline json packet_json(const Packet &p) {
    json j;
#define FIELD(name) j[#name] = static_cast<uint64_t>(p.name);
    MODEL_CAPTURE_PACKET_FIELDS(FIELD)
#undef FIELD
    j["k_indices"] = p.k_indices;
    j["packed_int4"] = p.stacked_activation.packed_int4;
    j["signed_int8"] = p.stacked_activation.signed_int8;
    j["signed_int16"] = p.stacked_activation.signed_int16;
    j["blocks"] = json::array();
    for (const auto &b : p.blocks) {
        json block;
#define FIELD(name) block[#name] = b.name;
        MODEL_CAPTURE_BLOCK_FIELDS(FIELD)
#undef FIELD
        block["lane_ids"] = b.lane_ids;
        j["blocks"].push_back(std::move(block));
    }
    return j;
}
// A8 capture contract. Other precision captures remain explicitly unsupported.
inline void validate_packet(const json &p, uint64_t m, uint64_t n, uint64_t k,
                            uint64_t stripe, uint64_t row, uint64_t height) {
    require(p.at("version") == 2 && p.at("digit_bits") == 8 && p.at("lane_capacity") == 4 &&
            p.at("digit_storage") == 2 && p.at("int4_packing") == 0 &&
            p.at("block_size") == 32 && p.at("array_dim") == 16,
            "capture requires native A8 residual packet v2");
    require(p.at("stripe_id") == stripe && p.at("row_begin") == row && p.at("row_count") == height &&
            row < m && height <= m - row && p.at("logical_j") == n && p.at("logical_k") == k &&
            k && k % 32 == 0 && n && n <= UINT32_MAX - 15 && height <= UINT16_MAX - 15,
            "residual packet stripe identity");
    const auto rows = (height + 15) / 16 * 16, columns = (n + 15) / 16 * 16;
    require(p.at("j_padded") == columns && !p.at("blocks").empty() && p.at("blocks").is_array() &&
            p.at("k_indices").is_array() && p.at("signed_int8").is_array() &&
            p.at("packed_int4").empty() && p.at("signed_int16").empty(), "residual packet layout");
    uint64_t kcursor = 0, acursor = 0, ocursor = 0, previous = 0, nonzero = 0;
    bool first = true;
    for (const auto &b : p.at("blocks")) {
        const auto block = natural(b.at("block_id")), compact = natural(b.at("compact_k_count"));
        const auto padded = (compact + 15) / 16 * 16, lanes = natural(b.at("active_lane_count"));
        require((first || block > previous) && block < k / 32 && b.at("global_k_begin") == block * 32 &&
                compact && compact <= 32 && b.at("padded_k_count") == padded && b.at("rows_padded") == rows &&
                lanes && lanes <= 4 && b.at("lane_ids").size() == 8 &&
                b.at("lane_stride_values") == product(rows, columns), "residual block geometry");
        first = false; previous = block;
        uint64_t mask = 0, last_lane = 0;
        for (uint64_t pos = 0; pos < 8; ++pos) {
            const auto lane = natural(b.at("lane_ids").at(pos));
            if (pos >= lanes) require(lane == 0, "residual unused lane identity");
            else {
                require(lane < 4 && (!pos || lane > last_lane), "residual radix lane identity");
                mask |= 1ULL << lane; last_lane = lane;
            }
        }
        const auto values = product(product(lanes, rows), padded);
        require(b.at("active_lane_mask") == mask && b.at("k_index_offset") == kcursor &&
                b.at("activation_offset") == acursor && b.at("activation_byte_offset") == acursor &&
                b.at("activation_byte_count") == values && b.at("output_value_offset") == ocursor,
                "residual packet offset/coverage");
        uint64_t last_k = 0;
        for (uint64_t i = 0; i < compact; ++i) {
            const auto local = natural(p.at("k_indices").at(kcursor + i));
            require(local < 32 && (!i || local > last_k), "residual global K identity"); last_k = local;
        }
        uint64_t live_lanes = 0;
        for (uint64_t r = 0; r < rows; ++r) for (uint64_t c = 0; c < padded; ++c) {
            int64_t wide = 0;
            for (uint64_t pos = 0; pos < lanes; ++pos) {
                int8_t digit{}; scalar(p.at("signed_int8").at(acursor + (pos * rows + r) * padded + c), digit);
                if (r >= height || c >= compact) require(digit == 0, "residual padding nonzero");
                if (digit) live_lanes |= 1ULL << pos;
                wide += int64_t(digit) * (INT64_C(1) << (natural(b.at("lane_ids").at(pos)) * 8));
            }
            require(wide >= INT32_MIN && wide <= INT32_MAX, "residual INT32 radix envelope");
            nonzero += wide != 0;
        }
        require(live_lanes == (1ULL << lanes) - 1, "residual active lane has no value");
        kcursor += compact; acursor += values; ocursor += product(product(lanes, rows), columns);
        require(kcursor <= UINT32_MAX && acursor <= UINT32_MAX && ocursor <= UINT32_MAX, "residual storage overflow");
    }
    require(p.at("k_indices").size() == kcursor && p.at("signed_int8").size() == acursor &&
            p.at("activation_value_count") == acursor && p.at("total_output_values") == ocursor &&
            nonzero && p.at("residual_event_count") == nonzero, "residual payload extent/event coverage");
}
template<class Packet> inline Packet packet_from_json(const json &j) {
    Packet p;
#define FIELD(name) scalar(j.at(#name), p.name);
    MODEL_CAPTURE_PACKET_FIELDS(FIELD)
#undef FIELD
    p.k_indices.resize(j.at("k_indices").size()); array_values(j.at("k_indices"), p.k_indices);
    p.stacked_activation.packed_int4.resize(j.at("packed_int4").size());
    array_values(j.at("packed_int4"), p.stacked_activation.packed_int4);
    p.stacked_activation.signed_int8.resize(j.at("signed_int8").size());
    array_values(j.at("signed_int8"), p.stacked_activation.signed_int8);
    p.stacked_activation.signed_int16.resize(j.at("signed_int16").size());
    array_values(j.at("signed_int16"), p.stacked_activation.signed_int16);
    for (const auto &block : j.at("blocks")) {
        typename std::decay_t<decltype(p.blocks)>::value_type b{};
#define FIELD(name) scalar(block.at(#name), b.name);
        MODEL_CAPTURE_BLOCK_FIELDS(FIELD)
#undef FIELD
        array_values(block.at("lane_ids"), b.lane_ids); p.blocks.push_back(b);
    }
    return p;
}
#undef MODEL_CAPTURE_PACKET_FIELDS
#undef MODEL_CAPTURE_BLOCK_FIELDS

// Independent packet radix reconstruction and native-weight scalar oracle.
// Called only after the DUT publishes, never as a backend execution fallback.
template<class Args, class Meta> inline size_t residual_oracle(const Args &args, const Meta &meta,
                                                              std::vector<float> &expected) {
    require(meta.direct_residuals.empty(), "CPU-direct residual capture is separate from accelerator qualification");
    const bool hp1 = args.q8_hp1_blocks != nullptr;
    const size_t blocks = args.K / 32, stripe_rows = args.activation_geometry().geometry.stripe_rows;
    size_t nonzero = 0;
    for (const auto &handle : meta.rmd_packets) {
        require(bool(handle), "null captured residual packet");
        const auto &p = *handle;
        validate_packet(packet_json(p), args.I, args.J, args.K, p.stripe_id, p.row_begin, p.row_count);
        std::vector<__int128_t> correction(product(p.row_count, args.J), 0);
        for (const auto &b : p.blocks) {
            std::vector<int64_t> wide(product(p.row_count, b.compact_k_count), 0);
            for (size_t r = 0; r < p.row_count; ++r) for (size_t c = 0; c < b.compact_k_count; ++c) {
                for (size_t pos = 0; pos < b.active_lane_count; ++pos)
                    wide[r * b.compact_k_count + c] += int64_t(p.stacked_activation.signed_int8.at(
                        b.activation_offset + (pos * b.rows_padded + r) * b.padded_k_count + c)) *
                        (INT64_C(1) << (b.lane_ids[pos] * 8));
                nonzero += wide[r * b.compact_k_count + c] != 0;
            }
            for (size_t r = 0; r < p.row_count; ++r) for (size_t col = 0; col < args.J; ++col) {
                const auto index = col * blocks + b.block_id;
                const int8_t *codes = hp1 ? args.q8_hp1_blocks[index].qs : args.q8_h1_blocks[index].qs;
                __int128_t dot = 0;
                for (size_t c = 0; c < b.compact_k_count; ++c)
                    dot += __int128_t(wide[r * b.compact_k_count + c]) * codes[p.k_indices.at(b.k_index_offset + c)];
                uint64_t factor;
                if (hp1) {
                    const auto exponent = args.q8_hp1_blocks[index].m;
                    require(exponent == INT16_MIN || (exponent >= 0 && exponent < 63), "residual HP1 integer shift");
                    factor = exponent == INT16_MIN ? 0 : UINT64_C(1) << exponent;
                } else factor = uint32_t(args.q8_h1_blocks[index].c_b) + args.q8_h1_blocks[index].R;
                correction[r * args.J + col] += dot * factor;
            }
        }
        for (size_t r = 0; r < p.row_count; ++r) for (size_t col = 0; col < args.J; ++col) {
            const auto integer = correction[r * args.J + col];
            require(integer >= INT64_MIN && integer <= INT64_MAX, "residual checked INT64 overflow");
            const float channel = hp1 ? args.q8_hp1_blocks[col * blocks].channel_scale : args.q8_h1_blocks[col * blocks].s_rf;
            const size_t row = p.row_begin + r;
            const double scale = std::ldexp(1.0, meta.theta.at(row / stripe_rows));
            const float correction_value = float(double(int64_t(integer)) * double(channel) * scale);
            require(std::isfinite(correction_value), "residual reconstruction overflow");
            expected.at(row * args.J + col) += correction_value;
        }
    }
    return nonzero;
}
inline bool validate_publication(const json &meta) {
    const auto version = natural(meta.value("capture_schema", json(1)));
    require(version == 1 || version == 2 || version == 3, "unknown capture schema");
    if (version == 1) {
        require(!meta.contains("publication_identity") ||
                meta.at("publication_identity") == "NOT_CAPTURED",
                "legacy capture cannot claim publication identity");
        return false;
    }
    require(meta.at("publication_identity") == "CAPTURED" &&
            meta.at("input_lifetime") == "POST_FOLD_THROUGH_COMMIT_UNCHANGED",
            "capture publication/lifetime not qualified");
    const auto m = natural(meta.at("M")), k = natural(meta.at("K"));
    const auto rows = natural(meta.at("stripe_rows"));
    const auto stride = natural(meta.at("activation_row_stride_bytes"));
    require(m && k && rows && stride >= k &&
            product(natural(meta.at("tile_I")), 16) == rows,
            "capture stripe geometry mismatch");
    const auto count = 1 + (m - 1) / rows;
    require(natural(meta.at("stripes")) == count &&
            meta.at("events").is_array() && meta.at("events").size() == count &&
            meta.at("theta").is_array() && meta.at("theta").size() == count,
            "capture event coverage mismatch");
    const bool pipeline = meta.at("mode") == "STRIPE_PIPELINE";
    require(pipeline || meta.at("mode") == "FULL", "capture mode invalid");
    json expected = json::array({json{{"stage", "begin"}}});
    if (pipeline) expected.push_back({{"stage", "execute"}});
    const auto run = natural(meta.at("events").at(0).at("run_id"));
    uint64_t packet_count = 0;
    for (uint64_t id = 0, row = 0; id < count; ++id) {
        const auto &event = meta.at("events").at(id);
        const auto height = std::min(rows, m - row);
        require(natural(event.at("run_id")) == run &&
                natural(event.at("stripe_id")) == id && natural(event.at("slot")) == id % 2 &&
                natural(event.at("row_begin")) == row && natural(event.at("row_end")) == row + height &&
                natural(event.at("H")) == height && natural(event.at("K")) == k &&
                natural(event.at("activation_row_stride_bytes")) == stride &&
                natural(event.at("activation_valid_bytes")) == product(height, k) &&
                natural(event.at("packed_offset_bytes")) == product(row, k),
                "capture event identity/extent mismatch");
        require(event.at("theta") == meta.at("theta").at(id) &&
                event.at("e_s") == meta.at("e_s") && event.at("rho") == meta.at("rho") &&
                event.at("sigma") == meta.at("sigma"),
                "capture immutable metadata mismatch");
        const auto route = event.at("residual_route");
        if (route == "ws_packet") {
            ++packet_count;
            require(version == 3, "legacy schema cannot claim residual packet capture");
            validate_packet(event.at("residual_packet"), m, natural(meta.at("N")), k, id, row, height);
        } else require(route == "none" && !event.contains("residual_packet"), "unsupported residual route");
        expected.push_back({{"stage", "post_fold"}, {"stripe_id", id}});
        if (pipeline) {
            expected.push_back({{"stage", "submit"}, {"stripe_id", id}});
            expected.push_back({{"stage", "accepted"}, {"stripe_id", id}});
        }
        row += height;
    }
    if (version == 3)
        require(meta.at("accelerator_residuals") == packet_count && meta.at("direct_residuals") == 0,
                "residual manifest route/packet coverage");
    if (!pipeline) expected.push_back({{"stage", "execute"}});
    for (const auto stage : {"fence", "fenced", "commit"}) expected.push_back({{"stage", stage}});
    require(meta.at("calls") == expected, "capture invocation/publication order mismatch");
    return true;
}
} // namespace model_capture
