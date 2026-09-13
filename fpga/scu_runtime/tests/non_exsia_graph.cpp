// Reuse the existing ordinary scheduler harness without changing ExSIA tests.
#define main preserved_exsia_graph_main
#include "graph_dispatch.cpp"
#undef main
#include "quants/act/quantize.hpp"

struct NonExsiaObservation : Observation {
    using Quantize = bool (*)(const ggml_tensor *, ggml_gemmini_args_t &);
    Quantize quantize = nullptr;
    Graph *graph = nullptr;
    bool channel = false;
    std::vector<uint8_t> original_weights;
    std::vector<float> original_activation;
    size_t boundaries = 0;

    static float scale(const ggml_gemmini_args_t &a, size_t row) {
        return std::visit([&](const auto &meta) -> float {
            using T = std::decay_t<decltype(meta)>;
            if constexpr (std::is_same_v<T, quants::act::tensor::Meta>) return meta.scale;
            else if constexpr (std::is_same_v<T, quants::act::token::Meta> ||
                               std::is_same_v<T, quants::act::block::Meta>) return meta.scales.at(row);
            else if constexpr (std::is_same_v<T, quants::act::stripe::Meta>)
                return meta.scales.at(row / a.activation_rows_per_stripe);
            else throw std::runtime_error("non-ExSIA activation metadata required");
        }, a.act_quant.storage());
    }
    void prepare(size_t seed) {
        if (!channel) graph->prepare(seed);
        else {
            std::vector<uint8_t> weights(graph->n * (sizeof(float) + graph->k));
            for (size_t col = 0; col < graph->n; ++col) {
                const float multiplier = float(col % 7 + 1) / 16;
                auto *row = weights.data() + col * (sizeof(float) + graph->k);
                std::memcpy(row, &multiplier, sizeof(multiplier));
                for (size_t k = 0; k < graph->k; ++k)
                    row[sizeof(float) + k] = uint8_t(int8_t(int((col * 3 + k * 7 + seed) % 63) - 31));
            }
            ggml_backend_tensor_set(graph->weight, weights.data(), 0, weights.size());
            std::vector<float> activation(graph->m * graph->k);
            for (size_t row = 0; row < graph->m; ++row) for (size_t k = 0; k < graph->k; ++k)
                activation[row * graph->k + k] = std::ldexp(float(int((row * 17 + k * 11 + seed) % 127) - 63), int(row / 16 % 3) - 5);
            ggml_backend_tensor_set(graph->activation, activation.data(), 0, activation.size() * sizeof(float));
            std::vector<float> sentinel(graph->m * graph->n, 17.0f);
            ggml_backend_tensor_set(graph->output, sentinel.data(), 0, sentinel.size() * sizeof(float));
        }
        m = graph->m; n = graph->n; blocks = channel ? 1 : graph->k / 32;
        block_raw.assign(m * n * blocks, 0); coverage.assign(block_raw.size(), 0); failure.clear();
        original_weights.resize(ggml_nbytes(graph->weight));
        ggml_backend_tensor_get(graph->weight, original_weights.data(), 0, original_weights.size());
        original_activation.resize(m * graph->k);
        ggml_backend_tensor_get(graph->activation, original_activation.data(), 0, original_activation.size() * sizeof(float));
    }
    static void complete(const ggml_gemmini_args_t &args, void *opaque) {
        auto &self = *static_cast<NonExsiaObservation *>(opaque);
        require(self.failure.empty(), self.failure.c_str());
        require(std::all_of(self.coverage.begin(), self.coverage.end(), [](uint8_t value) {return value == 1;}), "raw contribution coverage");
        require(!simulator_creates && !simulator_executes && !simulator_streams, "SIM fallback");
        // Re-run the original quantizer independently from the original F32
        // tensor into fresh storage, only after successful RTL completion.
        ggml_gemmini_args_t reference{};
        reference.I = args.I; reference.J = args.J; reference.K = args.K;
        reference.sA = args.K; reference.tile_I = args.tile_I;
        reference.tile_J = args.tile_J; reference.tile_K = args.tile_K;
        reference.activation_rows_per_stripe = args.activation_rows_per_stripe;
        reference.tiled_matmul_type = WS;
        require(reference.A.allocate(args.I, args.K, 8), "reference activation allocation");
        ggml_tensor source = *self.graph->activation;
        source.data = self.original_activation.data();
        require(self.quantize(&source, reference), "original quantizer independent replay");
        require(reference.act_quant.storage().index() == args.act_quant.storage().index(), "activation algorithm identity");
        for (size_t row = 0; row < args.I; ++row) {
            require(std::bit_cast<uint32_t>(scale(reference, row)) == std::bit_cast<uint32_t>(scale(args, row)), "original activation metadata mismatch");
            for (size_t k = 0; k < args.K; ++k)
                require(reference.A.get(row, k) == args.A.get(row, k), "original activation codes mismatch");
        }
        self.expected.assign(args.I * args.J, 0);
        const bool hp1 = args.weight_format == ggml_gemmini_args_t::im2p_weight_format_t::q8_hp1;
        require(self.channel ? args.has_q8_channel_dense_sidecar_contract() :
                hp1 ? args.has_q8_hp1_im2p_contract() : args.has_q8_h1_im2p_contract(), "native route contract");
        for (size_t row = 0; row < args.I; ++row) for (size_t col = 0; col < args.J; ++col) {
            double sum = 0;
            for (size_t block = 0; block < self.blocks; ++block) {
                int64_t dot = 0;
                const size_t first = self.channel ? 0 : block * 32;
                const size_t count = self.channel ? args.K : 32;
                for (size_t k = first; k < first + count; ++k) {
                    const int8_t weight = self.channel ? int8_t(self.original_weights[col * (sizeof(float) + args.K) + sizeof(float) + k]) :
                        hp1 ? args.q8_hp1_blocks[col * self.blocks + block].qs[k % 32] :
                              args.q8_h1_blocks[col * self.blocks + block].qs[k % 32];
                    dot += int64_t(reference.A.get(row, k)) * weight;
                }
                require(self.block_raw[(block * args.I + row) * args.J + col] == dot, "independent raw dot mismatch");
                double weight_scale;
                if (self.channel) {
                    float original_scale;
                    std::memcpy(&original_scale, self.original_weights.data() + col * (sizeof(float) + args.K), sizeof(float));
                    require(std::bit_cast<uint32_t>(original_scale) == std::bit_cast<uint32_t>(args.weight_channel_scales[col]), "original channel scale mismatch");
                    require(std::memcmp(static_cast<const int8_t *>(args.B) + col * args.K,
                        self.original_weights.data() + col * (sizeof(float) + args.K) + sizeof(float), args.K) == 0,
                        "original channel codes mismatch");
                    weight_scale = original_scale;
                } else if (hp1) {
                    const auto &w = args.q8_hp1_blocks[col * self.blocks + block];
                    weight_scale = w.m == INT16_MIN ? 0.0 : double(gemmini_ldexp_fast_pos(w.channel_scale, w.m));
                } else {
                    const auto &w = args.q8_h1_blocks[col * self.blocks + block];
                    weight_scale = double(w.s_rf) * double(uint32_t(w.c_b) + w.R);
                }
                sum += double(dot) * weight_scale * double(scale(reference, row));
            }
            const float expected = float(sum);
            self.expected[row * args.J + col] = expected;
            require(std::bit_cast<uint32_t>(expected) == std::bit_cast<uint32_t>(args.f_out[row * args.stride_f_out + col * args.col_stride_f_out]), "independent f_out mismatch");
        }
        if (const char *prefix = std::getenv("IM2P_TEST_CHANNEL_CAPTURE")) {
            require(self.channel, "channel capture requires native channel weights");
            const std::string path = std::string(prefix) + ".invocation-" + std::to_string(self.calls) + ".bin";
            require(!std::filesystem::exists(path), "channel capture already exists");
            std::ofstream file(path, std::ios::binary);
            file.exceptions(std::ios::badbit | std::ios::failbit);
            const uint64_t family = std::visit([](const auto &meta) -> uint64_t {
                using T = std::decay_t<decltype(meta)>;
                if constexpr (std::is_same_v<T, quants::act::tensor::Meta>) return 1;
                if constexpr (std::is_same_v<T, quants::act::token::Meta>) return 2;
                if constexpr (std::is_same_v<T, quants::act::block::Meta>) return 3;
                if constexpr (std::is_same_v<T, quants::act::stripe::Meta>) return 4;
                return 0;
            }, args.act_quant.storage());
            require(family != 0, "channel capture activation family");
            const auto write = [&]<class T>(const T *data, size_t count) {
                file.write(reinterpret_cast<const char *>(data), count * sizeof(T));
            };
            const std::array<uint64_t, 11> header{0x313050525358454eULL, 1, args.I, args.J, args.K,
                args.tile_I, args.tile_J, args.tile_K, args.activation_rows_per_stripe, args.A.row_stride_bytes, family};
            write(header.data(), header.size());
            write(self.original_activation.data(), self.original_activation.size());
            write(reinterpret_cast<const uint8_t *>(args.A.raw_data()), args.I * args.A.row_stride_bytes);
            for (size_t row = 0; row < args.I; ++row) { const float s = scale(reference, row); write(&s, 1); }
            write(self.original_weights.data(), self.original_weights.size());
            write(self.block_raw.data(), self.block_raw.size());
            for (size_t row = 0; row < args.I; ++row) for (size_t col = 0; col < args.J; ++col)
                write(&args.f_out[row * args.stride_f_out + col * args.col_stride_f_out], 1);
            file.close();
            std::cout << "CHANNEL_CAPTURE_PASS schema=1 path=" << path << '\n';
        }
        ++self.calls; self.scalars += self.block_raw.size();
        std::cout << "NON_EXSIA_REFERENCE PASS raw=" << self.block_raw.size() << " f_out=" << self.expected.size()
                  << " activation_provenance=original_F32 quantizer=original_function independent_storage=1"
                  << " domain=" << (self.channel ? 0 : 1) << " stripe_rows=" << args.activation_rows_per_stripe << '\n';
    }
};

int main(int argc, char **argv) {
    try {
        require(argc == 7, "usage: non_exsia_graph LIB M N K FULL|STRIPE_PIPELINE CHANNEL|H1|HP1|Q8_0");
        const auto library = std::filesystem::canonical(argv[1]);
        const size_t m = std::stoul(argv[2]), n = std::stoul(argv[3]), k = std::stoul(argv[4]);
        const std::string mode = argv[5], format = argv[6];
        require(m > 0 && m <= 1024 && n > 0 && n <= 128 && k > 0 && k <= 1024, "bounded fixture dimensions");
        require(mode == "FULL" || mode == "STRIPE_PIPELINE", "invocation mode");
        const bool channel = format == "CHANNEL";
        require(channel || ((format == "H1" || format == "HP1" || format == "Q8_0") && k % 32 == 0), "weight format/alignment");
        const char *device = std::getenv("IM2P_FPGA_DEVICE");
        require(device && std::string_view(device).starts_with("rtl:/") && std::filesystem::is_regular_file(device + 4), "explicit RTL plugin required; no physical device");
        unsetenv("GEMMINI_MATMUL_INVOCATION"); setenv("GEMMINI_MATMUL_MODE", mode.c_str(), 1);
        require(dlsym(RTLD_DEFAULT, "im2p_sim_create") == reinterpret_cast<void *>(&im2p_sim_create), "SIM interposition");
        auto reg = ggml_backend_load(library.c_str());
        require(reg && ggml_backend_reg_dev_count(reg) == 1, "ordinary FPGA backend load");
        require(ggml_backend_load((library.parent_path() / "libggml-cpu.so").c_str()), "ordinary CPU backend load");
        auto module = dlopen(library.c_str(), RTLD_NOW | RTLD_NOLOAD);
        require(module, "ordinary module handle");
        NonExsiaObservation observation;
        observation.quantize = reinterpret_cast<NonExsiaObservation::Quantize>(dlsym(module,
            "_ZN4ggml7gemmini6quants19quantize_activationEPK11ggml_tensorR19ggml_gemmini_args_t"));
        require(observation.quantize, "original quantizer symbol");
        auto set_block = reinterpret_cast<void (*)(ggml_gemmini_fpga_block_observer, void *)>(ggml_backend_reg_get_proc_address(reg, "ggml_gemmini_fpga_set_block_observer"));
        auto set_result = reinterpret_cast<void (*)(ggml_gemmini_fpga_result_observer, void *)>(ggml_backend_reg_get_proc_address(reg, "ggml_gemmini_fpga_set_result_observer"));
        auto stats = reinterpret_cast<ggml_gemmini_fpga_stats_v1_fn>(ggml_backend_reg_get_proc_address(reg, "ggml_gemmini_fpga_stats_v1"));
        require(set_block && set_result && stats, "ordinary observer APIs");
        Graph graph(ggml_backend_reg_dev_get(reg, 0), m, n, k, channel ? GGML_TYPE_Q8_CHANNEL :
                    format == "HP1" ? GGML_TYPE_Q8_HP1 : format == "Q8_0" ? GGML_TYPE_Q8_0 : GGML_TYPE_Q8_H1);
        observation.graph = &graph; observation.channel = channel;
        set_block(Observation::block, static_cast<Observation *>(&observation));
        set_result(NonExsiaObservation::complete, &observation);
        for (size_t iteration = 0; iteration < 2; ++iteration) {
            observation.prepare(3 + iteration);
            require(ggml_backend_sched_graph_compute(graph.scheduler, graph.graph) == GGML_STATUS_SUCCESS, "normal graph execution");
            ggml_backend_sched_synchronize(graph.scheduler);
            require(observation.calls == iteration + 1, "one successful RTL completion per invocation");
            const auto actual = graph.values();
            require(actual.size() == observation.expected.size() && std::memcmp(actual.data(), observation.expected.data(), actual.size() * sizeof(float)) == 0, "caller output exact");
            std::vector<uint8_t> weights(observation.original_weights.size());
            ggml_backend_tensor_get(graph.weight, weights.data(), 0, weights.size());
            require(weights == observation.original_weights, "original weight backing preserved");
        }
        ggml_gemmini_fpga_stats_v1 final{};
        require(stats(&final, sizeof(final)) && final.assigned == 2 && final.attempted == 2 && final.completed == 2 && !final.failed, "scheduler/adapter counter conservation");
        require(!simulator_creates && !simulator_executes && !simulator_streams, "SIM fallback");
        set_block(nullptr, nullptr); set_result(nullptr, nullptr);
        std::cout << "NON_EXSIA_GRAPH_PASS mode=" << mode << " format=" << format << " M=" << m << " N=" << n << " K=" << k
                  << " invocations=2 raw=" << observation.scalars << " f_out=" << 2 * m * n << " SIM=0 physical=0\n";
        return 0;
    } catch (const std::exception &error) {
        std::cerr << "NON_EXSIA_GRAPH_FAIL " << error.what() << '\n';
        return 1;
    }
}
