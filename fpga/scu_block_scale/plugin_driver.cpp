// Independent contract test for the dynamically loaded production RTL path.
#include "rtl_plugin.hpp"
#include <dlfcn.h>
#include <algorithm>
#include <cstdint>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

static void require(bool value, const std::string &message) {
    if (!value) throw std::runtime_error(message);
}
static void result(const im2p_scu_rtl_api_v1 *api, void *device, int actual, int expected,
                   const char *operation) {
    require(actual == expected, std::string(operation) + " status=" + std::to_string(actual) +
            " error=" + api->error(device));
}
struct Fixture {
    size_t m, n, k, blocks;
    bool external;
    std::vector<int8_t> activations;
    std::vector<int64_t> actual;
    std::vector<bool> seen;
    size_t weight_reads = 0, scale_reads = 0, outputs = 0;
    Fixture(size_t rows, size_t columns, size_t reduction, bool block)
        : m(rows), n(columns), k(reduction), blocks(block ? reduction / 32 : 1), external(block),
          activations(m * k), actual(blocks * m * n), seen(actual.size()) {
        for (size_t row = 0; row < m; ++row)
            for (size_t at = 0; at < k; ++at)
                activations[row * k + at] = int((row * 7 + at * 3 + 5) % 19) - 9;
    }
    static int8_t weight(size_t row, size_t column) {
        return int((row * 5 + column * 11 + 7) % 23) - 11;
    }
    static int read_weight(void *context, size_t row, size_t column, size_t count, int8_t *out) {
        auto &f = *static_cast<Fixture *>(context);
        if (row >= f.k || column > f.n || count > f.n - column) return IM2P_INVALID_LAYOUT;
        for (size_t lane = 0; lane < count; ++lane) out[lane] = weight(row, column + lane);
        ++f.weight_reads;
        return IM2P_OK;
    }
    static int read_scale(void *context, size_t row, size_t column, size_t count, uint32_t *out) {
        auto &f = *static_cast<Fixture *>(context);
        if (!f.external || row >= f.blocks || column > f.n || count > f.n - column)
            return IM2P_INVALID_LAYOUT;
        // VectorExternal ignores scaling in RTL; reconstruction remains the
        // caller's original block operation. Distinct values expose bad reuse.
        for (size_t lane = 0; lane < count; ++lane) out[lane] = 1 + row * 3 + (column + lane) % 7;
        ++f.scale_reads;
        return IM2P_OK;
    }
    static int output(void *context, size_t block, size_t row, size_t column,
                      size_t count, const int64_t *values, uint32_t domain) {
        auto &f = *static_cast<Fixture *>(context);
        if (block >= f.blocks || row >= f.m || column > f.n || count > f.n - column ||
            domain != (f.external ? IM2P_OUTPUT_LEGACY_BLOCK : IM2P_OUTPUT_LEGACY_FINAL))
            return IM2P_INVALID_LAYOUT;
        for (size_t lane = 0; lane < count; ++lane) {
            const size_t at = (block * f.m + row) * f.n + column + lane;
            if (f.seen[at]) return IM2P_INVALID_LAYOUT;
            f.seen[at] = true; f.actual[at] = values[lane];
        }
        ++f.outputs;
        return IM2P_OK;
    }
    im2p_matmul_desc_t descriptor() {
        im2p_matmul_desc_t d{};
        d.abi_version = IM2P_ABI_VERSION; d.activation_bits = d.weight_bits = 8;
        d.activation_storage_bytes = d.weight_storage_bytes = 1; d.dim = 16;
        d.activations = activations.data(); d.m = m; d.n = n; d.k = k;
        d.activation_row_stride_bytes = k; d.weight_row_stride_bytes = n; d.output_row_stride = n;
        d.tile_i_rows = std::min(m, size_t(16)); d.tile_j_columns = std::min(n, size_t(16));
        d.block_size = external ? 32 : 1; d.scale_total_k = k; d.scale_row_stride = n;
        d.scale_valid_columns = n;
        d.vector_op = external ? IM2P_VECTOR_EXTERNAL : IM2P_VECTOR_BYPASS;
        d.output_domain = external ? IM2P_OUTPUT_LEGACY_BLOCK : IM2P_OUTPUT_LEGACY_FINAL;
        d.work_context = 0x1234567887654321ULL;
        d.provider = {this, read_weight, nullptr, external ? read_scale : nullptr, output};
        return d;
    }
    im2p_stripe_work_desc_t striped_descriptor(size_t count) {
        const auto source = descriptor();
        im2p_stripe_work_desc_t d{};
#define COPY(field) d.field = source.field
        COPY(abi_version); COPY(activation_bits); COPY(activation_storage_bytes);
        COPY(weight_bits); COPY(weight_storage_bytes); COPY(dim); COPY(m); COPY(n); COPY(k);
        COPY(weight_row_stride_bytes); COPY(output_row_stride); COPY(tile_i_rows); COPY(tile_j_columns);
        COPY(block_size); COPY(scale_total_k); COPY(scale_row_stride); COPY(scale_valid_columns);
        COPY(vector_op); COPY(output_domain); COPY(work_context); COPY(provider);
#undef COPY
        d.stripe_count = count;
        return d;
    }
    void verify(const im2p_work_stats_extended_t &stats) const {
        for (size_t block = 0; block < blocks; ++block)
            for (size_t row = 0; row < m; ++row)
                for (size_t column = 0; column < n; ++column) {
                    int64_t expected = 0;
                    const size_t begin = external ? block * 32 : 0;
                    const size_t end = external ? begin + 32 : k;
                    for (size_t at = begin; at < end; ++at)
                        expected += int(activations[row * k + at]) * int(weight(at, column));
                    const size_t index = (block * m + row) * n + column;
                    require(seen[index] && actual[index] == expected,
                            "independent plugin dot mismatch at " + std::to_string(index));
                }
        require(stats.base.output_write_requests == outputs && stats.base.output_write_responses == outputs,
                "plugin output request/ack counts");
        require(stats.base.stripe_rows_published == m && stats.base.work_total_cycles > 0,
                "plugin work stats");
        require(weight_reads && (external || scale_reads == 0), "provider callback selection");
    }
};

int main(int argc, char **argv) {
    try {
        require(argc == 2, "usage: plugin_driver plugin.so|--extent-only");
        struct Extent { size_t m, n, k; bool external, valid; };
        const Extent extents[] = {
            {1,128256,2048,true,true}, {UINT32_MAX,128256,2048,true,true},
            {16,65537,192,true,true}, {16,96,65568,true,true},
            {31,1ULL<<31,1ULL<<31,true,true}, {32,1ULL<<31,1ULL<<31,true,false},
            {32,1ULL<<31,1ULL<<31,false,true}, {UINT32_MAX,1ULL<<31,32,false,false},
            {1,(1ULL<<31)+1,32,true,false}, {1,16,(1ULL<<31)+1,true,false},
            {size_t(UINT32_MAX)+1,16,32,true,false}, {0,16,32,true,false},
            {1,0,32,true,false}, {1,16,0,true,false}, {SIZE_MAX,SIZE_MAX,SIZE_MAX,true,false}
        };
        for (const auto &e : extents) {
            require(im2p_scu_logical_extent_valid(e.m,e.n,e.k,e.external) == e.valid,
                    "logical virtual address boundary");
            if (e.valid) {
                // Independent exact A/W/S/output span proof with wider arithmetic.
                using Wide = unsigned __int128;
                const Wide ks = Wide(1) << im2p_scu_stride_log(e.k);
                const Wide ns = Wide(1) << im2p_scu_stride_log(e.n);
                require(Wide(e.m)*ks <= UINT64_MAX && Wide(e.k)*ns <= UINT64_MAX &&
                        Wide((e.k+31)/32)*ns*4 <= UINT64_MAX &&
                        Wide(e.m)*ns*4*(e.external ? (e.k+31)/32 : 1) <= UINT64_MAX,
                        "accepted virtual span overflows");
            }
        }
        std::cout << "LOGICAL_EXTENT_PASS cases=" << std::size(extents) << std::endl;
        if (std::string(argv[1]) == "--extent-only") return 0;
        void *library = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL);
        require(library != nullptr, "cannot load RTL plugin");
        auto getter = reinterpret_cast<im2p_scu_rtl_get_api_v1_fn>(dlsym(library, "im2p_scu_rtl_get_api_v1"));
        require(getter != nullptr, "plugin entry missing");
        const auto *api = getter();
        require(api && api->version == 1 && api->abi_version == IM2P_ABI_VERSION && api->dim == 16 &&
                api->activation_bits == 8 && api->weight_bits == 8 &&
                api->vector_op == IM2P_VECTOR_EXTERNAL && api->output_domain == IM2P_OUTPUT_LEGACY_BLOCK,
                "plugin identity");
        void *device = api->create();
        require(device != nullptr, "cannot create RTL core");
        unsigned jobs = 0;
        // Reuse one physical RTL instance across dense and compact residual jobs.
        for (const auto &shape : {std::vector<size_t>{16, 96, 192}, {13, 79, 192},
                                 {33, 16, 13}, {16, 48, 192},
                                 {1,65537,32}, {1,128256,32}, {1,1,65568}}) {
            Fixture f(shape[0], shape[1], shape[2], shape[2] % 32 == 0);
            auto desc = f.descriptor();
            if (jobs == 0) {
                auto bad = desc; bad.abi_version = 4;
                require(api->full(device, &bad, nullptr) == IM2P_INVALID_LAYOUT, "stale ABI accepted");
                bad = desc; bad.k = 191;
                require(api->full(device, &bad, nullptr) == IM2P_INVALID_LAYOUT, "bad block extent accepted");
                bad = desc; bad.activation_row_stride_bytes = SIZE_MAX;
                require(api->full(device, &bad, nullptr) == IM2P_INVALID_LAYOUT, "overflowing activation stride accepted");
            }
            im2p_work_stats_extended_t stats{};
            result(api, device, api->full(device, &desc, &stats), IM2P_OK, "full");
            f.verify(stats);
            require(api->release(device) == IM2P_OK, "full release");
            std::cout << "{\"mode\":\"FULL\",\"op\":" << unsigned(desc.vector_op)
                      << ",\"M\":" << f.m << ",\"N\":" << f.n << ",\"K\":" << f.k
                      << ",\"comparisons\":" << f.actual.size() << ",\"status\":\"PASS\"}" << std::endl;
            ++jobs;
        }
        for (const size_t stripe_count : {3, 5}) {
            const size_t rows = 7;
            Fixture f(rows * stripe_count, 79, 192, true);
            auto desc = f.striped_descriptor(stripe_count);
            result(api, device, api->begin(device, &desc, rows), IM2P_OK, "begin");
            for (size_t id = 0; id < stripe_count; ++id) {
                im2p_activation_stripe_t stripe{};
                stripe.abi_version = IM2P_ABI_VERSION; stripe.activation_bits = stripe.weight_bits = 8;
                stripe.activation_storage_bytes = stripe.weight_storage_bytes = 1; stripe.dim = 16;
                stripe.stripe_id = id; stripe.i_start = rows * id; stripe.rows = rows;
                stripe.activations = f.activations.data() + stripe.i_start * f.k;
                stripe.activation_row_stride_bytes = f.k; stripe.context = 0x123000 + id;
                int accepted = api->publish(device, &stripe);
                for (unsigned wait = 0; accepted == IM2P_BACKPRESSURE; ++wait) {
                    require(wait < 10000, "publication credit watchdog");
                    im2p_stripe_completion_extended_t unexpected{};
                    result(api, device, api->poll(device, &unexpected), 0, "publication progress");
                    accepted = api->publish(device, &stripe);
                }
                result(api, device, accepted, IM2P_OK, "publish");
                require(api->publish(device, &stripe) == IM2P_INVALID_LAYOUT, "duplicate publication accepted");
                im2p_stripe_completion_extended_t completed{};
                int status = 0;
                for (size_t polls = 0; !(status = api->poll(device, &completed)); ++polls)
                    require(polls < 10000, "plugin live completion watchdog");
                result(api, device, status, 1, "poll");
                require(completed.base.stripe_id == id && completed.base.i_start == stripe.i_start &&
                        completed.base.rows == rows && completed.base.context == stripe.context,
                        "semantic completion identity");
                if (id + 1 < stripe_count) {
                    // Do not publish yet. A window spans unpublished rows; no
                    // stale zero may survive their later publication.
                    for (unsigned wait = 0; wait < 3; ++wait)
                        require(api->poll(device, &completed) == 0, "unpublished stripe completed");
                    require(api->finish(device, nullptr) == IM2P_UNFINISHED_STREAM,
                            "unfinished logical run finalized");
                }
            }
            im2p_work_stats_extended_t stats{};
            result(api, device, api->finish(device, &stats), IM2P_OK, "finish");
            f.verify(stats);
            im2p_scu_rtl_observation_v1 observation{};
            require(api->observation(device, &observation) == IM2P_OK &&
                    observation.publications == stripe_count && observation.completions == stripe_count &&
                    observation.first_activation_published_rows == rows,
                    "live first-A and completion observation");
            require(stats.base.completed_stripes == stripe_count && stats.base.stripes_published == stripe_count,
                    "live stripe stats");
            require(api->release(device) == IM2P_OK, "live release");
            std::cout << "{\"mode\":\"STRIPE_PIPELINE\",\"stripes\":" << stripe_count
                      << ",\"rows_per_stripe\":7,\"M\":" << f.m << ",\"N\":" << f.n
                      << ",\"K\":" << f.k << ",\"comparisons\":" << f.actual.size()
                      << ",\"status\":\"PASS\"}" << std::endl;
            ++jobs;
        }
        api->destroy(device); dlclose(library);
        std::cout << "PROVIDER_PLUGIN_RTL_PASS jobs=" << jobs
                  << " physical_access=0 uart_bypassed=1" << std::endl;
    } catch (const std::exception &error) {
        std::cerr << "PROVIDER_PLUGIN_RTL_FAIL " << error.what() << std::endl;
        return 1;
    }
}
