// Test-only synthetic model. Uses the repository's GGUF writer and native H1 quantizer.
#include "ggml.h"
#include "gguf.h"
#include "ggml-quants.h"
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <stdexcept>
#include <string>
#include <vector>

static void require(bool ok, const char * text) { if (!ok) throw std::runtime_error(text); }

int main(int argc, char ** argv) {
    try {
        require(argc == 3, "usage: tiny_model MODEL.gguf H1|F32");
        require(!std::filesystem::exists(argv[1]), "refusing to overwrite existing model");
        constexpr int vocab = 16;
        const bool h1 = std::strcmp(argv[2], "H1") == 0;
        require(h1 || std::strcmp(argv[2], "F32") == 0, "unknown output type");
        require(ggml_blck_size(GGML_TYPE_Q8_H1) == 32 && ggml_type_size(GGML_TYPE_Q8_H1) == sizeof(block_q8_h1), "native H1 ABI mismatch");
        auto * ctx = ggml_init({1024 * 1024, nullptr, false});
        auto * file = gguf_init_empty();
        require(ctx && file, "context allocation");
        gguf_set_val_str(file, "general.architecture", "llama");
        gguf_set_val_str(file, "general.name", "SCU test-only tiny native H1 model; not trained");
        gguf_set_val_u32(file, "general.file_type", h1 ? 38 : 0);
        gguf_set_val_u32(file, "general.quantization_version", 2);
        gguf_set_val_u32(file, "llama.context_length", 64);
        gguf_set_val_u32(file, "llama.embedding_length", 64);
        gguf_set_val_u32(file, "llama.feed_forward_length", 64);
        gguf_set_val_u32(file, "llama.block_count", 1);
        gguf_set_val_u32(file, "llama.attention.head_count", 4);
        gguf_set_val_u32(file, "llama.attention.head_count_kv", 4);
        gguf_set_val_u32(file, "llama.rope.dimension_count", 16);
        gguf_set_val_f32(file, "llama.attention.layer_norm_rms_epsilon", 1e-5f);
        gguf_set_val_f32(file, "llama.rope.freq_base", 10000.0f);
        gguf_set_val_u32(file, "llama.vocab_size", vocab);
        gguf_set_val_str(file, "tokenizer.ggml.model", "llama");
        std::vector<std::string> names{"<unk>", "<s>", "</s>", "▁", "a", "▁a", "<0x0A>"};
        for (char c = 'b'; c <= 'j'; ++c) names.emplace_back(1, c);
        require(names.size() == vocab, "vocabulary size");
        std::vector<const char *> tokens;
        for (const auto & name : names) tokens.push_back(name.c_str());
        std::vector<int32_t> types(vocab, 1); // NORMAL, from llama_vocab token type contract.
        types[0] = 2; types[1] = types[2] = 3; types[6] = 6; // UNKNOWN/CONTROL/BYTE.
        std::vector<float> scores(vocab, 0); scores[5] = 10;
        gguf_set_arr_str(file, "tokenizer.ggml.tokens", tokens.data(), tokens.size());
        gguf_set_arr_data(file, "tokenizer.ggml.scores", GGUF_TYPE_FLOAT32, scores.data(), scores.size());
        gguf_set_arr_data(file, "tokenizer.ggml.token_type", GGUF_TYPE_INT32, types.data(), types.size());
        gguf_set_val_u32(file, "tokenizer.ggml.bos_token_id", 1);
        gguf_set_val_u32(file, "tokenizer.ggml.eos_token_id", 2);
        gguf_set_val_u32(file, "tokenizer.ggml.unknown_token_id", 0);
        gguf_set_val_bool(file, "tokenizer.ggml.add_bos_token", true);
        gguf_set_val_bool(file, "tokenizer.ggml.add_eos_token", false);
        gguf_set_val_bool(file, "tokenizer.ggml.add_space_prefix", true);
        auto add_f32 = [&](const char * name, int n, int k, int pattern) {
            auto * tensor = n == 1 ? ggml_new_tensor_1d(ctx, GGML_TYPE_F32, k)
                                  : ggml_new_tensor_2d(ctx, GGML_TYPE_F32, k, n);
            ggml_set_name(tensor, name);
            auto * values = static_cast<float *>(tensor->data);
            for (int j = 0; j < n; ++j) for (int i = 0; i < k; ++i) {
                values[j * k + i] = pattern == 0 ? 1.0f : pattern == 1 ?
                    float(((j + 1) * 17 + (i + 3) * 13) % 127 - 63) / 128.0f :
                    (i == j ? 0.125f : 0.0f);
            }
            gguf_add_tensor(file, tensor);
        };
        add_f32("token_embd.weight", vocab, 64, 1);
        add_f32("output_norm.weight", 1, 64, 0);
        add_f32("blk.0.attn_norm.weight", 1, 64, 0);
        add_f32("blk.0.ffn_norm.weight", 1, 64, 0);
        for (const char * name : {"attn_q", "attn_k", "attn_v", "attn_output", "ffn_gate", "ffn_up", "ffn_down"}) {
            const std::string full = std::string("blk.0.") + name + ".weight";
            add_f32(full.c_str(), 64, 64, 2);
        }
        std::vector<float> output(vocab * 64);
        for (int j = 0; j < vocab; ++j) for (int k = 0; k < 64; ++k) {
            // Exact dyadic input, independent of std::random and host RNG state.
            output[j * 64 + k] = float(((j + 2) * 11 + k * 7) % 37 - 18) /
                                (k < 32 ? 256.0f : 64.0f);
        }
        auto * tensor = ggml_new_tensor_2d(ctx, h1 ? GGML_TYPE_Q8_H1 : GGML_TYPE_F32, 64, vocab);
        ggml_set_name(tensor, "output.weight");
        size_t quantized_bytes = 0;
        uint32_t beta_min = 65790, beta_max = 0;
        if (h1) {
            quantized_bytes = ggml_quantize_chunk(GGML_TYPE_Q8_H1, output.data(), tensor->data, 0, vocab, 64, nullptr);
            require(quantized_bytes == ggml_nbytes(tensor), "quantizer output extent");
            const auto * blocks = static_cast<const block_q8_h1 *>(tensor->data);
            for (int j = 0; j < vocab; ++j) {
                require(blocks[2*j].s_rf == blocks[2*j+1].s_rf && blocks[2*j].R == blocks[2*j+1].R, "shared H1 S/R");
                require(std::isfinite(blocks[2*j].s_rf) && blocks[2*j].s_rf > 0, "finite positive H1 S");
                for (int b = 0; b < 2; ++b) {
                    const auto & block = blocks[2*j+b];
                    const uint32_t beta = uint32_t(block.c_b) + uint32_t(block.R);
                    require(beta <= 65790, "H1 execution carrier");
                    beta_min = std::min(beta_min, beta); beta_max = std::max(beta_max, beta);
                }
            }
            require(beta_min != beta_max, "test should retain nonidentity block metadata");
        } else std::memcpy(tensor->data, output.data(), output.size() * sizeof(float));
        gguf_add_tensor(file, tensor);
        require(gguf_write_to_file(file, argv[1], false), "GGUF write failed");
        std::printf("TINY_MODEL_GENERATED format=%s tensors=12 embedding=64 vocabulary=16 layers=1 H1_bytes=%zu beta_min=%u beta_max=%u trained=0\n",
                    h1 ? "native_Q8_H1_output" : "all_F32_control", quantized_bytes,
                    h1 ? beta_min : 0, h1 ? beta_max : 0);
        gguf_free(file); ggml_free(ctx);
        return 0;
    } catch (const std::exception & e) {
        std::fprintf(stderr, "TINY_MODEL_FAIL %s\n", e.what()); return 1;
    }
}
