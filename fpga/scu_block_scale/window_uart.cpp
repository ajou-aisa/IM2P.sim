// Native IFR4 service of the existing frontend's A/W/S/output requests.
// No host dot products and no new numerical work or quantization boundaries.
#include "window_uart.hpp"
#include "window_protocol.hpp"
#include <algorithm>
#include <array>
#include <bit>
#include <cerrno>
#include <chrono>
#include <cmath>
#include <cstring>
#include <deque>
#include <fcntl.h>
#include <limits>
#include <poll.h>
#include <stdexcept>
#include <termios.h>
#include <unistd.h>
#include <vector>

namespace im2p::fpga {
namespace {
using Bytes = std::vector<uint8_t>;
using Clock = std::chrono::steady_clock;
enum Operation : uint8_t { Cap=ifr4::CAP, Start=ifr4::START, Poll=ifr4::POLL, Refill=ifr4::REFILL,
    Publish=ifr4::PUBLISH, OutputAck=ifr4::OUTPUT_ACK, StripeAck=ifr4::STRIPE_ACK, Release=ifr4::RELEASE };
constexpr size_t header_bytes=ifr4::HEADER_BYTES, poll_bytes=ifr4::POLL_BYTES,
                 record_bytes=ifr4::RECORD_BYTES, max_payload=ifr4::MAX_PAYLOAD;
void check(bool ok, const char *message) { if (!ok) throw std::runtime_error(message); }
uint64_t get(const Bytes &b, size_t at, size_t count) {
    check(count <= 8 && at <= b.size() && count <= b.size() - at, "IFR4 truncated field");
    uint64_t value = 0;
    for (size_t i = 0; i < count; ++i) value |= uint64_t(b[at + i]) << (8 * i);
    return value;
}
void put(Bytes &b, uint64_t value, size_t count) {
    for (size_t i = 0; i < count; ++i) b.push_back(uint8_t(value >> (8 * i)));
}
uint32_t crc(const Bytes &b, size_t count) {
    uint32_t value = UINT32_MAX;
    for (size_t i = 0; i < count; ++i) {
        value ^= b[i];
        for (unsigned bit = 0; bit < 8; ++bit)
            value = (value >> 1) ^ ((value & 1) ? UINT32_C(0xedb88320) : 0);
    }
    return ~value;
}
unsigned stride_log(size_t n) { return im2p_scu_stride_log(n); }

struct Device {
    int fd = -1;
    double timeout;
    std::string failure;
    uint64_t run = 0, progress_mark = 0;
    uint32_t generation = 0, sequence = 0, batch = 1;
    im2p_matmul_desc_t desc{};
    im2p_scu_rtl_observation_v1 observed{};
    WindowUARTMetrics metrics;
    im2p_scu_rtl_output_observer_v1 observer = nullptr;
    void *observer_context = nullptr;
    std::deque<im2p_activation_stripe_t> stripes;
    std::deque<im2p_stripe_completion_extended_t> completions;
    size_t published = 0, retired = 0, expected_stripes = 0;
    size_t output_i = 0, output_j = 0, output_block = 0, output_row = 0, output_count = 0;
    uint32_t last_output_tag = 0;
    bool owned = false, streaming = false, done = false, failed = false, have_tag = false;
    Bytes last_poll;
    Clock::time_point progress_time = Clock::now();
    static constexpr unsigned window_k_log = 6, window_n_log = 6;

    Device(const std::string &path, double seconds) : timeout(seconds) {
        check(!path.empty() && path.front() == '/' && std::isfinite(timeout) && timeout > 0 && timeout <= 3600,
              "IFR4 invalid path/timeout");
        fd = ::open(path.c_str(), O_RDWR | O_NOCTTY | O_NONBLOCK | O_CLOEXEC);
        if (fd < 0) throw std::runtime_error("IFR4 open: " + std::string(std::strerror(errno)));
        try {
            termios attr{};
            check(tcgetattr(fd, &attr) == 0, "IFR4 tcgetattr");
            cfmakeraw(&attr); attr.c_cflag = CS8 | CREAD | CLOCAL;
            check(cfsetispeed(&attr, B1000000) == 0 && cfsetospeed(&attr, B1000000) == 0, "IFR4 baud");
            attr.c_cc[VMIN] = attr.c_cc[VTIME] = 0;
            check(tcsetattr(fd, TCSANOW, &attr) == 0, "IFR4 tcsetattr");
            const auto cap = exchange(Cap);
            generation = uint32_t(get(cap, 16, 4));
        } catch (...) { ::close(fd); fd = -1; throw; }
    }
    ~Device() { if (fd >= 0) ::close(fd); }
    void io(uint8_t *bytes, size_t size, bool writing) {
        auto deadline = Clock::now() + std::chrono::duration<double>(timeout);
        for (size_t at = 0; at < size;) {
            const auto remaining = deadline - Clock::now();
            check(remaining.count() > 0, "IFR4 I/O progress timeout");
            pollfd port{fd, short(writing ? POLLOUT : POLLIN), 0};
            const int ready = ::poll(&port, 1, std::max(1, int(std::chrono::duration_cast<std::chrono::milliseconds>(remaining).count())));
            if (ready < 0 && errno == EINTR) continue;
            check(ready > 0 && !(port.revents & (POLLERR | POLLNVAL)), "IFR4 I/O poll failed");
            const auto count = writing ? ::write(fd, bytes + at, size - at) : ::read(fd, bytes + at, size - at);
            if (count < 0 && (errno == EINTR || errno == EAGAIN)) continue;
            check(count > 0, "IFR4 I/O disconnected");
            at += size_t(count);
            deadline = Clock::now() + std::chrono::duration<double>(timeout);
        }
    }
    Bytes exchange(Operation op, std::array<uint32_t, 5> args = {}, const Bytes &payload = {}, uint8_t flags = 0) {
        const auto began = Clock::now();
        check(!failed && payload.size() <= max_payload && sequence != UINT32_MAX, "IFR4 session/packet extent");
        const uint32_t seq = op == Cap ? 0 : ++sequence;
        Bytes request;
        put(request, 0x34524649, 4); put(request, ifr4::VERSION, 1); put(request, op, 1);
        put(request, 0, 1); put(request, flags, 1); put(request, op == Cap ? 0 : run, 8);
        put(request, op == Cap ? 0 : generation, 4); put(request, seq, 4); put(request, payload.size(), 4);
        for (auto value : args) put(request, value, 4);
        request.insert(request.end(), payload.begin(), payload.end()); put(request, crc(request, request.size()), 4);
        io(request.data(), request.size(), true);
        Bytes reply(header_bytes); io(reply.data(), reply.size(), false);
        check(get(reply, 0, 4) == 0x3452464f && reply[4] == ifr4::VERSION && reply[5] == op &&
              get(reply, 20, 4) == seq && get(reply, 24, 4) <= max_payload,
              "IFR4 response version/operation/sequence/extent");
        const size_t length = size_t(get(reply, 24, 4));
        reply.resize(header_bytes + length + 4); io(reply.data() + header_bytes, length + 4, false);
        check(get(reply, reply.size() - 4, 4) == crc(reply, reply.size() - 4), "IFR4 response CRC");
        check(op == Cap || (get(reply, 8, 8) == run && get(reply, 16, 4) == generation), "IFR4 stale response");
        check(get(reply, 28, 4) == 0x08100420 && get(reply, 32, 8) == 0 && get(reply, 40, 8) == 0,
              "IFR4 profile/layout mismatch");
        check(reply[6] == 0 && !(reply[7] & 0xc0), "IFR4 device rejected operation");
        check(op == Poll || (length == 0 && reply[7] == 0), "IFR4 unexpected response payload");
        metrics.request_bytes += request.size();
        metrics.response_bytes += reply.size();
        ++metrics.transactions;
        metrics.transfer_seconds += std::chrono::duration<double>(Clock::now() - began).count();
        return reply;
    }
    static bool valid(const im2p_matmul_desc_t &d) {
        return d.abi_version == IM2P_ABI_VERSION && d.activation_bits == 8 && d.weight_bits == 8 &&
            d.activation_storage_bytes == 1 && d.weight_storage_bytes == 1 && d.dim == 16 &&
            im2p_scu_logical_extent_valid(d.m, d.n, d.k, d.vector_op == IM2P_VECTOR_EXTERNAL) &&
            d.tile_i_rows == std::min(size_t(16), d.m) && d.tile_j_columns == std::min(size_t(16), d.n) &&
            d.weight_row_stride_bytes >= d.n && d.output_row_stride >= d.n &&
            d.provider.read_weight_i8 && !d.provider.read_weight_i16 && d.provider.write_output &&
            ((d.vector_op == IM2P_VECTOR_EXTERNAL && d.output_domain == IM2P_OUTPUT_LEGACY_BLOCK &&
              d.block_size == 32 && d.k % 32 == 0 && d.provider.read_scale) ||
             (d.vector_op == IM2P_VECTOR_BYPASS && d.output_domain == IM2P_OUTPUT_LEGACY_FINAL));
    }
    static bool activation_layout(const void *a, size_t rows, size_t k, size_t stride) {
        return a && rows && stride >= k && rows - 1 <= (SIZE_MAX - k) / stride &&
            uintptr_t(a) <= UINTPTR_MAX - ((rows - 1) * stride + k);
    }
    void start(const im2p_matmul_desc_t &d, bool live, size_t count) {
        check(!owned && valid(d) && generation != UINT32_MAX && run != UINT64_MAX, "IFR4 invalid/busy descriptor");
        if (!live) check(activation_layout(d.activations, d.m, d.k, d.activation_row_stride_bytes), "IFR4 activation extent");
        desc = d; ++run; ++generation; sequence = 0; batch = 1;
        observed = {}; metrics = {}; stripes.clear(); completions.clear(); last_poll.clear();
        published = live ? 0 : d.m; retired = 0; expected_stripes = count;
        output_i = output_j = output_block = output_row = output_count = 0;
        have_tag = false; streaming = live; done = false;
        const uint32_t control = stride_log(d.k) | stride_log(d.n) << 5 | window_k_log << 10 |
                                 window_n_log << 13 | uint32_t(d.vector_op) << 16;
        exchange(Start, {uint32_t(d.m), uint32_t(d.n), uint32_t(d.k), control, 0}, {}, live);
        owned = true; progress_mark = 0; progress_time = Clock::now();
    }
    int8_t activation(size_t row, size_t column) const {
        if (row >= desc.m || column >= desc.k) return 0;
        if (!streaming) return static_cast<const int8_t *>(desc.activations)[row * desc.activation_row_stride_bytes + column];
        for (const auto &stripe : stripes)
            if (row >= stripe.i_start && row - stripe.i_start < stripe.rows)
                return static_cast<const int8_t *>(stripe.activations)[(row - stripe.i_start) * stripe.activation_row_stride_bytes + column];
        return 0; // Padding cannot authorize reads from completed/unpublished backing.
    }
    void refill(unsigned kind, const Bytes &reply) {
        check(kind != 2 || (desc.vector_op == IM2P_VECTOR_EXTERNAL && desc.provider.read_scale),
              "IFR4 unexpected scale request");
        const size_t at = header_bytes + 64 + kind * 24;
        const size_t row_origin = get(reply, at, 4), word_origin = get(reply, at + 4, 4);
        const size_t valid_rows = get(reply, at + 8, 4), words = get(reply, at + 16, 4);
        const auto gen = uint32_t(get(reply, at + 12, 4));
        const size_t per_row = size_t(1) << (kind == 0 ? window_k_log - 4 : kind == 1 ? window_n_log - 4 : window_n_log - 2);
        const size_t capacity = kind == 0 ? 64 : kind == 1 ? 256 : 32;
        const size_t row_span = capacity / per_row;
        const size_t row_limit = kind == 0 ? published : kind == 1 ? desc.k : (desc.k + 31) / 32;
        const size_t columns = kind == 0 ? desc.k : desc.n;
        const size_t lanes = kind == 2 ? 4 : 16;
        check(gen && words && words <= capacity && words % per_row == 0 &&
              valid_rows && valid_rows <= words / per_row && get(reply, at + 20, 4) == 0,
              "IFR4 invalid resident window");
        check(words == capacity && row_origin < row_limit && row_origin % row_span == 0 &&
              word_origin % per_row == 0 && word_origin <= (columns - 1) / lanes &&
              valid_rows == std::min(row_span, row_limit - row_origin), "IFR4 resident origin/valid extent");
        Bytes payload;
        payload.reserve(words * 16);
        for (size_t index = 0; index < words; ++index) {
            const size_t row = row_origin + index / per_row;
            const size_t column = (word_origin + index % per_row) * (kind == 2 ? 4 : 16);
            if (kind == 2) {
                std::array<uint32_t, 4> values{};
                if (row < row_origin + valid_rows && row < (desc.k + 31) / 32 && column < desc.n)
                    check(desc.provider.read_scale(desc.provider.context, row, column, std::min(size_t(4), desc.n - column), values.data()) == IM2P_OK,
                          "IFR4 scale provider failed");
                for (auto value : values) put(payload, value, 4);
            } else {
                std::array<int8_t, 16> values{};
                if (row < row_origin + valid_rows) {
                    if (kind == 0) for (size_t lane = 0; lane < 16; ++lane) values[lane] = activation(row, column + lane);
                    else if (row < desc.k && column < desc.n)
                        check(desc.provider.read_weight_i8(desc.provider.context, row, column, std::min(size_t(16), desc.n - column), values.data()) == IM2P_OK,
                              "IFR4 weight provider failed");
                }
                for (auto value : values) payload.push_back(uint8_t(value));
            }
        }
        exchange(Refill, {kind, gen, 0, uint32_t(words), 1}, payload);
        if (kind == 0) ++observed.activation_refills;
        else if (kind == 1) ++observed.weight_refills;
        else ++observed.scale_refills;
    }
    void output(const Bytes &reply, size_t count) {
        for (size_t index = 0; index < count; ++index) {
            check(output_i < desc.m && (!streaming || !stripes.empty()), "IFR4 output without live work");
            const size_t end = streaming ? stripes.front().i_start + stripes.front().rows : desc.m;
            check(output_i < end && output_row < std::min(size_t(16), end - output_i), "IFR4 output beyond stripe");
            const size_t at = header_bytes + poll_bytes + record_bytes * index;
            const auto address = get(reply, at, 8), tag = get(reply, at + 8, 8);
            const size_t columns = get(reply, at + 16, 4);
            const uint64_t stride = uint64_t(4) << stride_log(desc.n), plane = desc.m * stride;
            const uint64_t expected = (desc.vector_op == IM2P_VECTOR_EXTERNAL ? output_block * plane : 0) +
                                      (output_i + output_row) * stride + 4 * output_j;
            check(address == expected && get(reply, at + 20, 4) == 0 &&
                  columns == std::min(size_t(16), desc.n - output_j) &&
                  uint32_t(tag >> 32) == generation && (!have_tag || uint32_t(tag) == uint32_t(last_output_tag + 1)),
                  "IFR4 output address/tag/order/extent mismatch");
            last_output_tag = uint32_t(tag); have_tag = true;
            std::array<int64_t, 16> values{};
            for (size_t lane = 0; lane < 16; ++lane) {
                values[lane] = std::bit_cast<int32_t>(uint32_t(get(reply, at + 24 + lane * 4, 4)));
                check(lane < columns || values[lane] == 0, "IFR4 nonzero output padding");
            }
            if (observer) observer(observer_context, output_block, output_i + output_row, output_j, columns, values.data());
            check(desc.provider.write_output(desc.provider.context, output_block, output_i + output_row, output_j, columns,
                  values.data(), desc.output_domain) == IM2P_OK, "IFR4 output provider failed");
            ++output_count;
            if (++output_row == std::min(size_t(16), end - output_i)) {
                output_row = 0;
                if (++output_block == (desc.vector_op == IM2P_VECTOR_EXTERNAL ? desc.k / 32 : 1)) {
                    output_block = 0;
                    output_j += columns;
                    if (output_j == desc.n) { output_j = 0; output_i += std::min(size_t(16), end - output_i); }
                }
            }
        }
    }
    void step() {
        auto reply = exchange(Poll);
        const auto flags = reply[7];
        check(reply.size() >= header_bytes + poll_bytes + 4, "IFR4 truncated poll");
        const size_t count = get(reply, header_bytes + 172, 4);
        check(count <= ifr4::OUTPUT_RECORDS && bool(flags & 8) == bool(count) && reply.size() == header_bytes + poll_bytes + count * record_bytes + 4,
              "IFR4 output batch extent");
        for (unsigned kind = 0; kind < 3; ++kind) if (flags & (1 << kind)) refill(kind, reply);
        if (count) {
            check(get(reply, header_bytes + 168, 4) == batch && batch != UINT32_MAX, "IFR4 output batch identity");
            output(reply, count); exchange(OutputAck, {batch++, uint32_t(count), 0, 0, 0});
        }
        if ((flags & 16) && count == 0) {
            check(streaming && !stripes.empty(), "IFR4 unexpected stripe completion");
            const auto &s = stripes.front();
            check(get(reply, header_bytes + 136, 4) == s.stripe_id && get(reply, header_bytes + 140, 4) == s.i_start &&
                  get(reply, header_bytes + 144, 4) == s.rows && output_i == s.i_start + s.rows &&
                  output_j == 0 && output_block == 0 && output_row == 0, "IFR4 stripe identity/coverage");
            const auto begin = get(reply, header_bytes + 152, 8), end = get(reply, header_bytes + 160, 8);
            check(end >= begin, "IFR4 stripe cycle order");
            exchange(StripeAck, {s.stripe_id, uint32_t(s.i_start), uint32_t(s.rows), 0, 0});
            completions.push_back({{s.stripe_id, s.i_start, s.rows, s.context}, begin, end, end - begin});
            stripes.pop_front(); ++observed.completions;
        }
        done = (flags & 32) && !count && !(flags & 16);
        const auto mark = get(reply, header_bytes + 8, 8) + get(reply, header_bytes + 24, 8) +
            get(reply, header_bytes + 32, 8) + get(reply, header_bytes + 48, 8) +
            observed.activation_refills + observed.weight_refills + observed.scale_refills +
            observed.publications + observed.completions;
        if (mark != progress_mark) { progress_mark = mark; progress_time = Clock::now(); }
        check((streaming && stripes.empty() && published < desc.m) ||
              std::chrono::duration<double>(Clock::now() - progress_time).count() < timeout, "IFR4 work progress timeout");
        last_poll = std::move(reply);
    }
    void finish(im2p_work_stats_extended_t *out) {
        check(out && owned && done && output_i == desc.m && output_j == 0 && output_block == 0 && output_row == 0,
              "IFR4 incomplete output coverage");
        *out = {};
        auto &s = out->base;
        s.work_total_cycles = get(last_poll, header_bytes, 8);
        s.completed_fragments = get(last_poll, header_bytes + 8, 8);
        s.completed_output_tiles = get(last_poll, header_bytes + 16, 8);
        s.activation_read_requests = get(last_poll, header_bytes + 24, 8);
        s.weight_read_requests = get(last_poll, header_bytes + 32, 8);
        s.output_write_requests = get(last_poll, header_bytes + 40, 8);
        s.output_write_responses = get(last_poll, header_bytes + 48, 8);
        check(s.work_total_cycles && s.output_write_requests == output_count && s.output_write_responses == output_count,
              "IFR4 completion count mismatch");
        s.completed_stripes = streaming ? observed.completions : 1;
        s.stripes_published = streaming ? observed.publications : 1;
        s.stripe_rows_published = published;
    }
};

template<class F> int invoke(void *handle, F function) noexcept {
    if (!handle) return IM2P_ERROR;
    auto &d = *static_cast<Device *>(handle);
    if (d.failed) return IM2P_ERROR;
    try { return function(d); }
    catch (const std::exception &e) { d.failure = e.what(); }
    catch (...) { d.failure = "IFR4 unexpected failure"; }
    d.failed = true; return IM2P_ERROR;
}
int full(void *handle, const im2p_matmul_desc_t *desc, im2p_work_stats_extended_t *stats) {
    if (!desc || !stats) return IM2P_INVALID_LAYOUT;
    return invoke(handle, [&](Device &d) { d.start(*desc, false, 1); while (!d.done) d.step(); d.finish(stats); return IM2P_OK; });
}
int begin(void *handle, const im2p_stripe_work_desc_t *s, size_t rows) {
    if (!s || !rows || !s->stripe_count || s->stripe_count != (s->m + rows - 1) / rows) return IM2P_INVALID_LAYOUT;
    im2p_matmul_desc_t desc{};
#define COPY(field) desc.field = s->field
    COPY(abi_version); COPY(activation_bits); COPY(activation_storage_bytes); COPY(weight_bits); COPY(weight_storage_bytes);
    COPY(dim); COPY(weights); COPY(scales); COPY(output); COPY(m); COPY(n); COPY(k); COPY(weight_row_stride_bytes);
    COPY(output_row_stride); COPY(tile_i_rows); COPY(tile_j_columns); COPY(block_size); COPY(scale_total_k);
    COPY(scale_row_stride); COPY(scale_column_offset); COPY(scale_valid_columns); COPY(scale_values_len);
    COPY(vector_op); COPY(output_domain); COPY(work_context); COPY(provider);
#undef COPY
    return invoke(handle, [&](Device &d) { d.start(desc, true, s->stripe_count); return IM2P_OK; });
}
int publish(void *handle, const im2p_activation_stripe_t *s) {
    if (!s) return IM2P_INVALID_LAYOUT;
    return invoke(handle, [&](Device &d) {
        check(d.owned && d.streaming && !d.done && s->abi_version == IM2P_ABI_VERSION && s->activation_bits == 8 &&
              s->activation_storage_bytes == 1 && s->weight_bits == 8 && s->weight_storage_bytes == 1 && s->dim == 16 &&
              s->i_start == d.published && s->rows <= d.desc.m - d.published && s->stripe_id == d.observed.publications &&
              d.observed.publications < d.expected_stripes &&
              Device::activation_layout(s->activations, s->rows, d.desc.k, s->activation_row_stride_bytes), "IFR4 stripe publication layout");
        // One device stripe retains its original immutable A backing until its
        // semantic completion. Frontend producer slots remain independent.
        if (!d.stripes.empty()) return IM2P_BACKPRESSURE;
        d.exchange(Publish, {uint32_t(s->i_start), uint32_t(s->rows), s->stripe_id, s->stripe_id % 2, 0});
        d.stripes.push_back(*s); d.published += s->rows; ++d.observed.publications;
        d.progress_time = Clock::now(); return IM2P_OK;
    });
}
int poll(void *handle, im2p_stripe_completion_extended_t *out) {
    if (!out) return IM2P_INVALID_LAYOUT;
    return invoke(handle, [&](Device &d) {
        check(d.owned && d.streaming, "IFR4 no active stream");
        if (d.completions.empty() && !d.done) d.step();
        if (d.completions.empty()) return 0;
        *out = d.completions.front(); d.completions.pop_front(); ++d.retired; return 1;
    });
}
int finish(void *handle, im2p_work_stats_extended_t *out) {
    return invoke(handle, [&](Device &d) {
        check(d.streaming && d.published == d.desc.m && d.retired == d.expected_stripes && d.stripes.empty(), "IFR4 unfinished stream");
        while (!d.done) d.step();
        d.finish(out);
        return IM2P_OK;
    });
}
int release(void *handle) {
    return invoke(handle, [](Device &d) {
        check(d.owned && d.done, "IFR4 unfinished invocation");
        d.exchange(Release); d.owned = false; return IM2P_OK;
    });
}
void destroy(void *handle) { delete static_cast<Device *>(handle); }
const char *error(void *handle) { return handle ? static_cast<Device *>(handle)->failure.c_str() : "IFR4 no session"; }
void set_observer(void *handle, im2p_scu_rtl_output_observer_v1 callback, void *context) {
    auto &d = *static_cast<Device *>(handle); d.observer = callback; d.observer_context = context;
}
int observation(void *handle, im2p_scu_rtl_observation_v1 *out) {
    if (!handle || !out) return IM2P_INVALID_LAYOUT;
    *out = static_cast<Device *>(handle)->observed; return IM2P_OK;
}
const im2p_scu_rtl_api_v1 api{sizeof(api), 1, IM2P_ABI_VERSION, 16, 8, 8,
    IM2P_VECTOR_EXTERNAL, IM2P_OUTPUT_LEGACY_BLOCK, nullptr, nullptr, nullptr,
    destroy, error, full, begin, publish, poll, finish, release, set_observer, observation};
}
void *open_window_uart(const std::string &path, double seconds) { return new Device(path, seconds); }
const im2p_scu_rtl_api_v1 *window_uart_api() { return &api; }
WindowUARTMetrics window_uart_metrics(void *handle) { return static_cast<Device *>(handle)->metrics; }
}
