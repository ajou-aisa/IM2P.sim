// Standalone: c++ -std=c++20 -I <llama.cpp-gemmini/common> model_capture_test.cpp -o model_capture_test
#include "model_capture.hpp"
#include <array>
#include <iostream>

using model_capture::json;
static json fixture(bool pipeline) {
    json value{{"capture_schema", 2}, {"publication_identity", "CAPTURED"},
        {"input_lifetime", "POST_FOLD_THROUGH_COMMIT_UNCHANGED"},
        {"M", 65}, {"N", 96}, {"K", 192}, {"tile_I", 1}, {"tile_K", 7},
        {"stripe_rows", 16}, {"stripes", 5}, {"activation_row_stride_bytes", 208},
        {"mode", pipeline ? "STRIPE_PIPELINE" : "FULL"},
        {"e_s", 0}, {"rho", 1}, {"sigma", 2}, {"theta", {3, 4, 5, 6, 7}},
        {"events", json::array()}, {"calls", json::array({json{{"stage", "begin"}}})}};
    if (pipeline) value["calls"].push_back({{"stage", "execute"}});
    for (unsigned id = 0; id < 5; ++id) {
        const unsigned row = id * 16, height = std::min(16u, 65u - row);
        value["events"].push_back({{"run_id", UINT64_MAX}, {"stripe_id", id}, {"slot", id % 2},
            {"row_begin", row}, {"row_end", row + height}, {"H", height}, {"K", 192},
            {"activation_row_stride_bytes", 208}, {"activation_valid_bytes", height * 192},
            {"packed_offset_bytes", row * 192}, {"e_s", 0}, {"rho", 1}, {"sigma", 2},
            {"theta", id + 3}, {"residual_route", "none"}, {"folding_commit_ns", id + 100}});
        value["calls"].push_back({{"stage", "post_fold"}, {"stripe_id", id}});
        if (pipeline) for (const auto stage : {"submit", "accepted"})
            value["calls"].push_back({{"stage", stage}, {"stripe_id", id}});
    }
    if (!pipeline) value["calls"].push_back({{"stage", "execute"}});
    for (const auto stage : {"fence", "fenced", "commit"}) value["calls"].push_back({{"stage", stage}});
    return value;
}
static json packet_fixture() {
    json digits = json::array();
    for (unsigned i = 0; i < 512; ++i) digits.push_back(0);
    digits[0] = 1; digits[1] = -1; digits[256] = 1; digits[257] = -1;
    return {{"version",2},{"digit_bits",8},{"lane_capacity",4},{"digit_storage",2},{"int4_packing",0},
        {"stripe_id",4},{"row_begin",64},{"row_count",1},{"logical_k",192},{"logical_j",96},{"j_padded",96},
        {"block_size",32},{"array_dim",16},{"activation_value_count",512},{"residual_event_count",2},
        {"total_output_values",3072},{"k_indices",{1,7}},{"signed_int8",digits},{"packed_int4",json::array()},
        {"signed_int16",json::array()},{"blocks",json::array({json{
            {"block_id",2},{"global_k_begin",64},{"compact_k_count",2},{"padded_k_count",16},
            {"active_lane_mask",3},{"active_lane_count",2},{"lane_ids",{0,1,0,0,0,0,0,0}},
            {"k_index_offset",0},{"activation_offset",0},{"activation_byte_offset",0},
            {"activation_byte_count",512},{"output_value_offset",0},{"rows_padded",16},{"lane_stride_values",1536}}})}};
}
static void packet_tests() {
    auto good = fixture(true); good["capture_schema"] = 3; good["accelerator_residuals"] = 1; good["direct_residuals"] = 0;
    good["events"][4]["residual_route"] = "ws_packet";
    good["events"][4]["residual_packet"] = packet_fixture();
    model_capture::require(model_capture::validate_publication(good), "valid packet capture rejected");
    unsigned mutations = 0;
    const auto reject = [&](json value) {
        bool rejected = false;
        try { (void)model_capture::validate_publication(value); } catch (const std::exception &) { rejected = true; }
        model_capture::require(rejected, "residual mutation accepted"); ++mutations;
    };
    for (const auto field : {"version","digit_bits","lane_capacity","digit_storage","int4_packing", "stripe_id",
                            "row_begin","row_count","logical_k","logical_j","j_padded","block_size","array_dim",
                            "activation_value_count","residual_event_count","total_output_values"}) {
        auto bad = good; auto &p = bad["events"][4]["residual_packet"];
        p[field] = model_capture::natural(p[field]) + 1; reject(bad);
    }
    for (const auto field : {"block_id","global_k_begin","compact_k_count","padded_k_count","active_lane_mask",
                            "active_lane_count","k_index_offset","activation_offset","activation_byte_offset",
                            "activation_byte_count","output_value_offset","rows_padded","lane_stride_values"}) {
        auto bad = good; auto &b = bad["events"][4]["residual_packet"]["blocks"][0];
        b[field] = model_capture::natural(b[field]) + 1; reject(bad);
    }
    for (unsigned n = 0; n < 6; ++n) {
        auto bad = good; auto &p = bad["events"][4]["residual_packet"];
        if (n == 0) p["signed_int8"][2] = 1; // padded K must remain zero
        if (n == 1) p["signed_int8"][0] = 128; // native byte overflow
        if (n == 2) p["k_indices"][1] = 1; // duplicate contribution
        if (n == 3) p["blocks"][0]["lane_ids"][1] = 0; // alias radix lane
        if (n == 4) p["blocks"][0]["lane_ids"][2] = 3; // stale unused lane
        if (n == 5) p["signed_int8"].erase(p["signed_int8"].end()-1);
        reject(bad);
    }
    const auto native_digits = [&](std::array<int, 4> digits) {
        auto value = good; auto &p = value["events"][4]["residual_packet"];
        p["activation_value_count"] = 1024; p["total_output_values"] = 6144;
        p["blocks"][0]["active_lane_count"] = 4; p["blocks"][0]["active_lane_mask"] = 15;
        p["blocks"][0]["lane_ids"] = {0,1,2,3,0,0,0,0};
        p["blocks"][0]["activation_byte_count"] = 1024;
        p["signed_int8"] = std::vector<int>(1024, 0);
        for (unsigned lane = 0; lane < 4; ++lane) {
            p["signed_int8"][lane * 256] = digits[lane];
            p["signed_int8"][lane * 256 + 1] = -1;
        }
        return value;
    };
    for (const auto digits : {std::array<int,4>{24,-113,-19,0},
                              std::array<int,4>{0,0,0,-128},
                              std::array<int,4>{127,127,127,127}})
        model_capture::require(model_capture::validate_publication(native_digits(digits)),
                               "valid native INT32 residual rejected");
    reject(native_digits({-1,0,0,-128})); // -2147483649 must reject despite four valid bytes.
    std::cout << "MODEL_RESIDUAL_CAPTURE_SCHEMA_PASS mutations=" << mutations << " native_packet=v2 numerical=NOT_RUN\n";
}
int main() {
    try {
        packet_tests();
        for (bool pipeline : {false, true}) {
            const auto good = fixture(pipeline);
            model_capture::require(model_capture::validate_publication(good), "valid publication rejected");
            for (const auto field : {"run_id", "stripe_id", "slot", "row_begin", "row_end", "H", "K",
                                    "activation_row_stride_bytes", "activation_valid_bytes", "packed_offset_bytes",
                                    "theta", "e_s", "rho", "sigma", "residual_route"}) {
                auto bad = good;
                if (std::string(field) == "residual_route") bad["events"][4][field] = "ws_packet";
                else {
                    const auto old = model_capture::natural(bad["events"][4][field]);
                    bad["events"][4][field] = old == UINT64_MAX ? old - 1 : old + 1;
                }
                bool rejected = false;
                try { (void)model_capture::validate_publication(bad); } catch (const std::exception &) { rejected = true; }
                model_capture::require(rejected, "mutation accepted");
            }
            for (const auto field : {"calls", "events", "theta"}) {
                auto bad = good; bad[field].erase(bad[field].end() - 1);
                bool rejected = false;
                try { (void)model_capture::validate_publication(bad); } catch (const std::exception &) { rejected = true; }
                model_capture::require(rejected, "missing lifetime stage accepted");
            }
        }
        model_capture::require(!model_capture::validate_publication(json::object()), "legacy capture promoted");
        bool rejected = false;
        try { (void)model_capture::validate_publication({{"publication_identity", "CAPTURED"}}); }
        catch (const std::exception &) { rejected = true; }
        model_capture::require(rejected, "legacy identity claim accepted");
        std::cout << "MODEL_CAPTURE_CONTRACT_PASS full=1 pipeline=1 stripes=5 tail=1 mutations=37 legacy=NOT_CAPTURED\n";
    } catch (const std::exception &error) {
        std::cerr << "MODEL_CAPTURE_CONTRACT_FAIL " << error.what() << '\n'; return 1;
    }
}
