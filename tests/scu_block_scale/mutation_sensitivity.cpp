// Fault-injection sensitivity, using the real frozen frontend and RTL runtime.
// Fixture construction is reused; expected results below are independent literals.
#ifndef IM2P_MUTATION_FIXTURE
#error "build through run_mutations.py"
#endif
#define main scalar_fixture_original_main
#include IM2P_MUTATION_FIXTURE
#undef main

struct Mutation {
    std::string name;
    im2p_provider_t downstream{};
    unsigned executes = 0, callbacks = 0, scale_reads = 0;
    uint8_t sent_op = 255, sent_domain = 255;
    uint32_t received_domain = UINT32_MAX;
    int64_t raw = 0;
    int execution_status = IM2P_ERROR;

    static int weight(void *context, size_t row, size_t col, size_t count, int8_t *values) {
        auto &self = *static_cast<Mutation *>(context);
        return self.downstream.read_weight_i8(self.downstream.context, row, col, count, values);
    }
    static int scale(void *context, size_t block, size_t col, size_t count, uint32_t *values) {
        auto &self = *static_cast<Mutation *>(context);
        const int status = self.downstream.read_scale(self.downstream.context, block, col, count, values);
        if (status == IM2P_OK) {
            ++self.scale_reads;
            for (size_t lane=0; lane<count; ++lane) {
                const uint32_t original = values[lane];
                if (self.name == "identity") values[lane] = 1;
                if (self.name == "truncate8") values[lane] &= 255;
                if (self.name == "truncate16") values[lane] &= 65535;
                std::cout << "SCU_MUTATION_METADATA case=" << self.name << " block=" << block
                          << " original=" << original << " supplied=" << values[lane] << '\n';
            }
        }
        return status;
    }
    static int output(void *context, size_t block, size_t row, size_t col, size_t count,
                      const int64_t *values, uint32_t domain) {
        auto &self = *static_cast<Mutation *>(context);
        ++self.callbacks;
        self.received_domain = domain;
        self.raw = values[0];
        const size_t forwarded_block = self.name == "callback_block" ? 1 : block;
        std::cout << "SCU_MUTATION_ACTUAL_RTL case=" << self.name << " callback=" << self.callbacks
                  << " block=" << block << " forwarded_block=" << forwarded_block
                  << " row=" << row << " column=" << col << " count=" << count
                  << " domain=" << domain << " raw=" << values[0] << '\n';
        return self.downstream.write_output(self.downstream.context, forwarded_block, row, col, count, values, domain);
    }
    static int execute(void *context, const im2p_matmul_desc_t *desc, im2p_work_stats_extended_t *stats) {
        auto &self = *static_cast<Mutation *>(context);
        if (desc->abi_version != 5 || desc->vector_op != 4 || desc->output_domain != 2) return IM2P_ERROR;
        auto copy = *desc;
        self.downstream = copy.provider;
        copy.provider = {&self, weight, nullptr, scale, output};
        if (self.name == "bypass") { copy.vector_op = 0; copy.output_domain = 0; }
        if (self.name == "external") { copy.vector_op = 3; copy.output_domain = 1; }
        self.sent_op = copy.vector_op;
        self.sent_domain = copy.output_domain;
        auto *simulator = im2p_sim_create();
        if (!simulator) return IM2P_ERROR;
        ++self.executes;
        self.execution_status = im2p_execute_matmul_extended(simulator, &copy, stats);
        im2p_sim_destroy(simulator);
        return self.execution_status;
    }
};

int main(int argc, char **argv) {
    try {
        require(argc == 2 && IM2P_ABI_VERSION == 5 &&
                std::string(im2p_compiled_numerical_semantics_revision()) == "signed-scu-sat-v2",
                "S2 ABI5 mutation test arguments/revision");
        const std::string name = argv[1];
        const bool wide = name == "truncate16" || name == "control_wide";
        require(name == "control" || name == "control_wide" || name == "identity" ||
                name == "truncate8" || name == "truncate16" || name == "bypass" ||
                name == "external" || name == "callback_block" || name == "shared_twice",
                "unknown mutation");
        Fixture fixture("h1", {0,255}, wide ? 65535 : 1, 1/256.0f, 0,
                        {1,1,1,1}, {1,1,1,1}, false);
        // K16 fragment specification: 16*beta, saturate32, then add/saturate32.
        // Both controls are overflow-free. Shared channel S=1/256, theta=0.
        const int64_t expected_raw = wide ? 4202400 : 8224;
        const float expected_fout = wide ? 16415.625f : 32.125f;
        Mutation observed{name};
        Options options;
        options.full_executor_context = &observed;
        options.full_executor = Mutation::execute;
        auto started = execute(&fixture.args, Mode::full, options);
        const auto done = started.run ? fence(*started.run) : FenceResult{};
        std::cout << "SCU_MUTATION_EXECUTION case=" << name << " executor_calls=" << observed.executes
                  << " actual_rtl_callbacks=" << observed.callbacks << " sent_op=" << unsigned(observed.sent_op)
                  << " sent_domain=" << unsigned(observed.sent_domain) << " received_domain=" << observed.received_domain
                  << " execution_status=" << observed.execution_status << " expected_raw=" << expected_raw
                  << " actual_raw=" << observed.raw << " expected_fout_bits=" << std::bit_cast<uint32_t>(expected_fout)
                  << " actual_fout_bits=" << std::bit_cast<uint32_t>(fixture.output[0])
                  << " completed_fragments=" << done.stats.base.completed_fragments
                  << " completed_works=" << done.stats.base.completed_output_tiles
                  << " actual_rtl=1\n";
        require(observed.executes == 1 && observed.callbacks > 0, "mutation never reached a real RTL output");
        require(fixture.output[1] == 17 && fixture.output[2] == 17, "mutation corrupted caller padding");
        if (!started.status.ok() || !started.run || !done.status.ok()) {
            require(fixture.output[0] == 17, "failed transaction exposed partial caller output");
            std::cerr << "SCU_MUTATION_DETECTED case=" << name << " checker=output_contract caller_output_preserved=1\n";
            return 1;
        }
        require(observed.callbacks == 1 && observed.received_domain == 2 &&
                done.stats.base.completed_fragments == 4 && done.stats.base.completed_output_tiles == 1,
                "successful mutation violated completion contract");
        if (observed.raw != expected_raw ||
                std::bit_cast<uint32_t>(fixture.output[0]) != std::bit_cast<uint32_t>(expected_fout)) {
            std::cerr << "SCU_MUTATION_DETECTED case=" << name << " checker=raw_or_fout_exact\n";
            return 1;
        }
        std::cout << "SCU_MUTATION_UNCHANGED_PASS case=" << name << '\n';
        return 0;
    } catch (const std::exception &error) {
        std::cerr << "SCU_MUTATION_TEST_ERROR " << error.what() << '\n';
        return 2;
    }
}
