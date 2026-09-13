// Test-only export from existing native quantizers and automatic host geometry.
// No matrix execution, UART, simulator creation, or replacement quantizer.
#define GGML_GEMMINI_MATMUL_IMPLEMENTATION 1
#include "ggml-gemmini-matmul.hpp"
#include "ggml-quants.h"
#include "quants/act/quantize.hpp"
#include "im2p_sim.h"
#include "im2p_gemmini_frontend.hpp"
#include <gemmini.h>
#include <bit>
#include <cmath>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <vector>

using namespace ggml::gemmini;
using Bytes = std::vector<uint8_t>;
namespace fs = std::filesystem;
static void require(bool valid, const char *why) { if (!valid) throw std::runtime_error(why); }
static void put(Bytes &bytes, uint64_t value, size_t count) {
    for (size_t byte = 0; byte < count; ++byte) bytes.push_back(uint8_t(value >> (8 * byte)));
}
static std::vector<float> read_f32(const fs::path &path, size_t count) {
    std::ifstream stream(path, std::ios::binary);
    require(bool(stream), "FP32 input missing");
    Bytes bytes(std::istreambuf_iterator<char>(stream), {});
    require(bytes.size() == count * 4, "FP32 input length");
    std::vector<float> values(count);
    for (size_t index = 0; index < count; ++index) {
        uint32_t bits = 0;
        for (size_t byte = 0; byte < 4; ++byte) bits |= uint32_t(bytes[index * 4 + byte]) << (8 * byte);
        values[index] = std::bit_cast<float>(bits);
        require(std::isfinite(values[index]), "nonfinite FP32 input");
    }
    return values;
}

int main(int argc, char **argv) {
    try {
        require(argc == 4, "usage: portable-fixture-export input-a-f32.bin input-w-f32.bin NEW_OUTPUT_DIR");
        require(IM2P_ABI_VERSION == 5 && im2p::gemmini::compiled_activation_bits() == 8 &&
                im2p::gemmini::compiled_weight_bits() == 8 && im2p::gemmini::compiled_dim() == 16 &&
                std::string(im2p_compiled_numerical_semantics_revision()) == "signed-scu-sat-v2",
                "linked ABI/profile/numerical revision mismatch");
        static_assert(sizeof(float) == 4 && std::numeric_limits<float>::is_iec559);
        constexpr size_t m = 16, n = 16, k = 64;
        const auto activation = read_f32(argv[1], m * k);
        const auto weight = read_f32(argv[2], n * k);
        const fs::path output(argv[3]);
        require(!fs::exists(output), "export output must be new");
        // Same initialization order as the verified capture: ggml_init first.
        auto *context = ggml_init({activation.size() * 4 + ggml_tensor_overhead() + 1024, nullptr, false});
        require(context != nullptr, "ggml_init");
        std::vector<block_q8_h1> packed(n * k / 32);
        for (size_t column = 0; column < n; ++column)
            quantize_row_q8_h1_ref(weight.data() + column * k, packed.data() + column * k / 32, k);
        std::vector<float> sentinel(m * (n + 3), 17.0f);
        ggml_gemmini_args_t args{};
        args.I = m; args.J = n; args.K = k;
        args.weight_format = ggml_gemmini_args_t::im2p_weight_format_t::q8_h1;
        args.q8_h1_blocks = packed.data(); args.q8_h1_block_count = packed.size();
        args.q8_h1_rows = n; args.blocks_per_row = k / 32;
        args.native_weight_bytes = packed.size() * sizeof(block_q8_h1);
        args.sA = args.sB = k; args.tiled_matmul_type = CPU;
        args.matmul_layer = "scu-migration-portable-quantizer-test";
        args.f_out = sentinel.data(); args.stride_f_out = n + 3; args.col_stride_f_out = 1;
        gemmini_set_tile_ws(&args);
        const auto geometry = args.activation_geometry();
        require(geometry.ok(), "automatic host geometry");
        args.activation_rows_per_stripe = geometry.geometry.stripe_rows;
        require(args.A.allocate(m, k, 8), "owned activation allocation");
        auto *tensor = ggml_new_tensor_2d(context, GGML_TYPE_F32, k, m);
        std::memcpy(tensor->data, activation.data(), activation.size() * 4);
        require(quants::quantize_activation(tensor, args), "existing activation quantizer failed");
        ggml_free(context);
        require(std::holds_alternative<quants::act::exsia::Meta>(args.act_quant.storage()), "EXSIA metadata required");
        const auto &meta = std::get<quants::act::exsia::Meta>(args.act_quant.storage());
        require(meta.direct_residuals.empty() && meta.rmd_packets.empty(), "RMD OFF dense ablation required");
        require(meta.theta.size() == geometry.geometry.stripe_count, "theta/geometry identity");
        for (const auto theta : meta.theta) require(theta != INT16_MIN, "nonzero stripe control");
        for (const auto &block : packed)
            require(std::isfinite(block.s_rf) && block.s_rf > 0 && uint32_t(block.c_b) + block.R > 0,
                    "native nonzero scale control");
        require(packed[0].s_rf * (uint32_t(packed[0].c_b) + packed[0].R) !=
                packed[1].s_rf * (uint32_t(packed[1].c_b) + packed[1].R), "distinct block-scale control");
        // Reuse the explicit IFX1 field contract; never serialize native structs.
        Bytes bytes; put(bytes, 0x31584649, 4);
        for (const auto value : {args.I, args.J, args.K, args.tile_I, args.tile_J, args.tile_K,
                args.activation_rows_per_stripe, args.activation_row_offset, args.A.row_stride_bytes,
                args.stride_f_out, args.col_stride_f_out, size_t(1), meta.theta.size()}) put(bytes, value, 8);
        put(bytes, uint16_t(meta.e_s), 2); put(bytes, uint16_t(meta.rho), 2); put(bytes, uint32_t(meta.sigma), 4);
        for (const auto theta : meta.theta) put(bytes, uint16_t(theta), 2);
        const auto *codes = static_cast<const uint8_t *>(args.A.raw_data());
        bytes.insert(bytes.end(), codes, codes + m * args.A.row_stride_bytes);
        for (const auto &block : packed) {
            for (const auto q : block.qs) put(bytes, uint8_t(q), 1);
            put(bytes, block.c_b, 1); put(bytes, std::bit_cast<uint32_t>(block.s_rf), 4); put(bytes, block.R, 2);
        }
        fs::create_directory(output);
        std::ofstream stream(output / "fixture.bin", std::ios::binary);
        stream.write(reinterpret_cast<const char *>(bytes.data()), bytes.size());
        require(bool(stream), "fixture write failed");
        std::cout << "PORTABLE_HOST_QUANTIZATION_PASS M=16 N=16 K=64 native=Q8_H1 activation=EXSIA residual=OFF"
                  << " tile_I=" << args.tile_I << " tile_J=" << args.tile_J << " tile_K=" << args.tile_K
                  << " stripe_rows=" << args.activation_rows_per_stripe << " stripes=" << meta.theta.size()
                  << " matrix_execution=not_called_source_audit\n";
        return 0;
    } catch (const std::exception &error) {
        std::cerr << "PORTABLE_HOST_QUANTIZATION_FAIL " << error.what() << '\n';
        return 1;
    }
}
