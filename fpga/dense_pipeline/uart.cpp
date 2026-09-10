#include "uart.hpp"

#include <algorithm>
#include <array>
#include <bit>
#include <cerrno>
#include <chrono>
#include <cmath>
#include <cstring>
#include <fcntl.h>
#include <fstream>
#include <iomanip>
#include <limits>
#include <poll.h>
#include <sstream>
#include <stdexcept>
#include <termios.h>
#include <unistd.h>

namespace im2p::fpga {
namespace {
using Bytes = std::vector<uint8_t>;
using Clock = std::chrono::steady_clock;
constexpr uint16_t profile = 0x0810;
constexpr const char *hardware_sha256 = "8aef393d040bb306e6ddf7b4b977976a9a924dbc5aec62b37b1690f0aa18c0ca";
constexpr const char *production_rtl_sha256 = "b29147ce384e9384b3821c358863350f03c0696cbc8b8e286a768f2e7155b853";
const std::array<FullCycleExpectation, 3> full_references{{
    {"m16n16k32", 16, 16, 32, 361},
    {"m321n48k64", 321, 48, 64, 39907},
    {"m321n48k96", 321, 48, 96, 58695}
}};

void require(bool condition, const char *message) {
    if (!condition) throw std::runtime_error(message);
}
uint64_t get(const Bytes &bytes, size_t offset, size_t count) {
    require(offset <= bytes.size() && count <= bytes.size() - offset,
            "IFR1 truncated integer");
    uint64_t value = 0;
    for (size_t i = 0; i < count; ++i) value |= uint64_t(bytes[offset + i]) << (8 * i);
    return value;
}
void put(Bytes &bytes, uint64_t value, size_t count) {
    for (size_t i = 0; i < count; ++i) bytes.push_back(uint8_t(value >> (8 * i)));
}
uint32_t crc32(const Bytes &bytes, size_t count) {
    uint32_t value = UINT32_MAX;
    for (size_t i = 0; i < count; ++i) {
        value ^= bytes[i];
        for (unsigned bit = 0; bit < 8; ++bit)
            value = (value >> 1) ^ ((value & 1) ? UINT32_C(0xedb88320) : 0);
    }
    return ~value;
}
Bytes packet(unsigned version, uint8_t op, uint64_t run_id, uint32_t generation,
             size_t m = 0, size_t n = 0, size_t k = 0, const Bytes &payload = {}, size_t t = 0) {
    Bytes bytes;
    bytes.reserve(36 + payload.size());
    put(bytes, version == 1 ? UINT32_C(0x31524649) : UINT32_C(0x32524649), 4);
    put(bytes, version, 1); put(bytes, op, 1); put(bytes, profile, 2);
    put(bytes, run_id, 8); put(bytes, generation, 4);
    put(bytes, m, 2); put(bytes, n, 2); put(bytes, k, 2); put(bytes, t, 2);
    put(bytes, payload.size(), 4);
    bytes.insert(bytes.end(), payload.begin(), payload.end());
    put(bytes, crc32(bytes, bytes.size()), 4);
    return bytes;
}
double seconds(Clock::time_point begin, Clock::time_point end) {
    return std::chrono::duration<double>(end - begin).count();
}
uint64_t host_ns() {
    return uint64_t(std::chrono::duration_cast<std::chrono::nanoseconds>(Clock::now().time_since_epoch()).count());
}
const FullCycleExpectation &reference_entry(const std::string &fixture, size_t m, size_t n, size_t k) {
    for (const auto &entry : full_references)
        if (entry.fixture == fixture && entry.m == m && entry.n == n && entry.k == k) return entry;
    throw std::runtime_error("IFR2 FULL reference fixture/shape missing: " + fixture);
}
ReplyStatistics reply_statistics(const Bytes &reply, unsigned version) {
    ReplyStatistics result{get(reply, 32, 8), get(reply, 40, 8), get(reply, 48, 8),
        get(reply, 56, 8), get(reply, 64, 8), get(reply, 72, 8), get(reply, 80, 8), {}, {}};
    if (version == 2) {
        result.host_wait_cycles = get(reply, 128, 8);
        result.overlap_cycles = get(reply, 136, 8);
    }
    return result;
}
std::string response_hex(const Bytes &reply) {
    std::ostringstream out;
    out << std::hex << std::setfill('0');
    for (size_t i = 0; i < std::min(size_t(144), reply.size()); ++i)
        out << std::setw(2) << unsigned(reply[i]);
    return out.str();
}
template<class Descriptor>
void validate(const Descriptor *d, unsigned version) {
    require(d, "IFR null descriptor");
    require(d->abi_version == IM2P_ABI_VERSION && d->activation_bits == 8 &&
            d->activation_storage_bytes == 1 && d->weight_bits == 8 &&
            d->weight_storage_bytes == 1 && d->dim == 16,
            "IFR1 unsupported ABI/profile");
    require(d->m > 0 && d->m <= (version == 1 ? 32 : 336) && d->n > 0 && d->n <= 48 &&
            (d->k == 32 || d->k == 64 || d->k == 96),
            "IFR1 unsupported shape/capacity/alignment");
    require(d->vector_op == IM2P_VECTOR_EXTERNAL && d->block_size == 32 &&
            d->tile_i_rows == std::min(size_t(16), d->m) &&
            d->tile_j_columns == std::min(size_t(16), d->n) &&
            d->scale_total_k == d->k && d->scale_column_offset == 0 &&
            d->scale_valid_columns == d->n && d->scale_row_stride == d->n &&
            d->weight_row_stride_bytes == d->n && d->output_row_stride == d->n &&
            d->provider.read_weight_i8 && d->provider.read_scale &&
            d->provider.write_output,
            "IFR1 unsupported provider/layout/scale descriptor");
}
void validate_activation(const void *data, size_t rows, size_t k, size_t stride) {
    require(data && rows && stride >= k &&
            stride <= (std::numeric_limits<size_t>::max() - k) / rows,
            "IFR activation stride overflow");
    const uintptr_t base = reinterpret_cast<uintptr_t>(data);
    const size_t last = (rows - 1) * stride + k;
    require(base <= std::numeric_limits<uintptr_t>::max() - last,
            "IFR activation address overflow");
}
void weights(Bytes &staging, size_t offset, size_t n, size_t k, const im2p_provider_t &provider) {
    for (size_t row = 0; row < k; ++row)
        for (size_t col = 0; col < n; col += 16)
            require(provider.read_weight_i8(provider.context, row, col,
                    std::min(size_t(16), n - col),
                    reinterpret_cast<int8_t *>(staging.data() + offset + row * 64 + col)) == IM2P_OK,
                    "IFR weight provider failed");
}
void shape(const Bytes &reply, size_t m, size_t n, size_t k) {
    require(get(reply, 24, 2) == m && get(reply, 26, 2) == n && get(reply, 28, 2) == k,
            "IFR response shape mismatch");
}
void statistics(const Bytes &reply, size_t m, size_t n, size_t k,
                im2p_work_stats_extended_t *stats, unsigned version) {
    require(stats, "IFR null stats");
    *stats = {};
    auto &s = stats->base;
    s.work_total_cycles = get(reply, 32, 8);
    s.completed_fragments = get(reply, 40, 8);
    s.completed_output_tiles = get(reply, 48, 8);
    s.activation_read_requests = get(reply, 56, 8);
    s.weight_read_requests = get(reply, 64, 8);
    s.output_write_requests = get(reply, 72, 8);
    s.output_write_responses = get(reply, 80, 8);
    const size_t tiles = (n + 15) / 16;
    const size_t works = ((m + 15) / 16) * tiles;
    const size_t writes = m * tiles * (k / 32);
    require(s.work_total_cycles > 0 && s.completed_fragments == works * (k / 16) &&
            s.completed_output_tiles == works && s.activation_read_requests > 0 &&
            s.weight_read_requests > 0 && s.output_write_requests == writes &&
            s.output_write_responses == writes, "IFR completion count mismatch");
    if (version == 2) {
        s.stripe_host_wait_cycles = get(reply, 128, 8);
        s.overlap_cycles = get(reply, 136, 8);
    }
}
void reconstruct(const Bytes &reply, size_t header_bytes, std::vector<int32_t> &raw,
                 size_t m, size_t n, size_t k, size_t first, size_t rows,
                 const im2p_provider_t &provider) {
    const size_t tiles = (n + 15) / 16;
    // Validate all wire padding before exposing any callback from this response.
    for (size_t block = 0; block < k / 32; ++block)
        for (size_t local = 0; local < rows; ++local)
            for (size_t col = 0; col < tiles * 16; ++col) {
                const auto bits = uint32_t(get(reply, header_bytes +
                    ((block * rows + local) * tiles * 16 + col) * 4, 4));
                if (col < n) raw[(block * m + first + local) * n + col] = std::bit_cast<int32_t>(bits);
                else require(bits == 0, "IFR wire padding mismatch");
            }
    // Wire is block-major. Existing External callbacks are tile/block/row ordered.
    for (size_t i = first; i < first + rows; i += 16)
        for (size_t j = 0; j < n; j += 16)
            for (size_t block = 0; block < k / 32; ++block)
                for (size_t row = i; row < std::min(i + 16, first + rows); ++row) {
                    int64_t lanes[16]{};
                    const size_t count = std::min(size_t(16), n - j);
                    for (size_t lane = 0; lane < count; ++lane)
                        lanes[lane] = raw[(block * m + row) * n + j + lane];
                    require(provider.write_output(provider.context, block, row, j, count, lanes) == IM2P_OK,
                            "IFR host reconstruction failed");
                }
}
} // namespace

FullCycleReference FullCycleReference::load(const std::string &path) {
    require(!path.empty(), "IFR2 FULL reference path missing");
    std::ifstream input(path, std::ios::ate);
    require(input.is_open(), "IFR2 FULL reference open failed");
    require(input.tellg() >= 0 && input.tellg() <= 4096, "IFR2 FULL reference length invalid");
    input.seekg(0);
    auto token = [&](const std::string &expected) {
        std::string actual;
        require(bool(input >> actual) && actual == expected, "IFR2 FULL reference identity/entry mismatch");
    };
    token("IFR2_FULL_REFERENCE_V1");
    token("hardware_sha256"); token(hardware_sha256);
    token("production_rtl_sha256"); token(production_rtl_sha256);
    token("backend"); token("FPGA_UART");
    token("protocol"); token("2");
    token("profile"); token("0810");
    token("mode"); token("FULL");
    for (const auto &entry : full_references) {
        token("fixture"); token(entry.fixture);
        token(std::to_string(entry.m)); token(std::to_string(entry.n)); token(std::to_string(entry.k));
        token(std::to_string(entry.cycles));
    }
    std::string extra;
    require(!(input >> extra) && input.eof(), "IFR2 FULL reference trailing data/read failure");
    return FullCycleReference();
}

FullCycleExpectation FullCycleReference::expect(const std::string &fixture, size_t m, size_t n, size_t k) const {
    return reference_entry(fixture, m, n, k);
}

void UART::expect_full(const FullCycleExpectation &expectation) {
    std::lock_guard lock(mutex_);
    require(error_.empty() && !owned_ && version_ == 2, "IFR2 FULL reference backend/state mismatch");
    const auto &fixed = reference_entry(expectation.fixture, expectation.m, expectation.n, expectation.k);
    require(expectation.cycles == fixed.cycles, "IFR2 FULL reference cycle mismatch");
    full_expectation_ = expectation;
    full_reference_required_ = true;
}

Telemetry UART::telemetry() const {
    std::lock_guard lock(mutex_);
    return telemetry_;
}

UART::UART(const std::string &device, double timeout_seconds, unsigned version)
    : timeout_seconds_(timeout_seconds), version_(version) {
    const auto start = Clock::now();
    require(version == 1 || version == 2, "IFR unsupported protocol version");
    require(std::isfinite(timeout_seconds) && timeout_seconds > 0 &&
            timeout_seconds <= 3600, "IFR1 invalid host timeout");
    fd_ = ::open(device.c_str(), O_RDWR | O_NOCTTY | O_NONBLOCK | O_CLOEXEC);
    if (fd_ < 0) throw std::runtime_error("IFR1 device open failed: " + std::string(std::strerror(errno)));
    try {
        termios attr{};
        require(tcgetattr(fd_, &attr) == 0, "IFR1 tcgetattr failed");
        cfmakeraw(&attr);
        attr.c_cflag = CS8 | CREAD | CLOCAL;
        require(cfsetispeed(&attr, B1000000) == 0 &&
                cfsetospeed(&attr, B1000000) == 0, "IFR1 baud configuration failed");
        attr.c_cc[VMIN] = attr.c_cc[VTIME] = 0;
        require(tcsetattr(fd_, TCSANOW, &attr) == 0, "IFR1 tcsetattr failed");
        const auto reply = transfer(packet(version_, 0, 0, 0), 0, 0, true, 0);
        require(get(reply, 24, 2) == (version_ == 1 ? 32 : 336) && get(reply, 26, 2) == 48 &&
                get(reply, 28, 2) == 96, "IFR1 capability capacity mismatch");
        generation_ = uint32_t(get(reply, 16, 4));
        initialization_seconds_ = seconds(start, Clock::now());
    } catch (...) {
        ::close(fd_); fd_ = -1;
        throw;
    }
}
UART::~UART() { if (fd_ >= 0) ::close(fd_); }

Bytes UART::transfer(const Bytes &request, uint64_t identity,
                     uint32_t generation, bool capability, size_t payload_bytes, bool allow_empty) {
    const size_t header_bytes = version_ == 1 ? 96 : 144;
    const size_t maximum_response_bytes = version_ == 1 ? 18432 : 193536;
    require(error_.empty(), "IFR1 sticky transport failure");
    last_exchange_ = {};
    last_exchange_.operation = request[5];
    last_exchange_.begin_ns = host_ns();
    const auto deadline = Clock::now() + std::chrono::duration<double>(timeout_seconds_);
    Bytes response;
    response.reserve(header_bytes + 4 + payload_bytes);
    size_t sent = 0;
    size_t target = header_bytes;
    while (sent < request.size() || response.size() < target) {
        const double remaining = std::chrono::duration<double>(deadline - Clock::now()).count();
        require(remaining > 0, "IFR1 UART timeout");
        pollfd descriptor{fd_, short(POLLIN | (sent < request.size() ? POLLOUT : 0)), 0};
        const int ready = ::poll(&descriptor, 1, int(std::min(remaining * 1000 + 1, double(INT32_MAX))));
        if (ready < 0 && errno == EINTR) continue;
        require(ready >= 0, "IFR1 poll failed");
        require(ready != 0, "IFR1 UART timeout");
        require(!(descriptor.revents & (POLLERR | POLLNVAL)), "IFR1 UART disconnected");
        if (descriptor.revents & POLLIN) {
            std::array<uint8_t, 4096> buffer{};
            const auto count = ::read(fd_, buffer.data(), std::min(buffer.size(), target - response.size()));
            if (count < 0) {
                require(errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR, "IFR1 read failed");
            } else {
                require(count != 0, "IFR1 UART closed");
                response.insert(response.end(), buffer.begin(), buffer.begin() + count);
                if (response.size() == header_bytes && target == header_bytes) {
                    const auto length = get(response, 20, 4);
                    require(length <= maximum_response_bytes && (length == payload_bytes || (allow_empty && length == 0)),
                            "IFR1 response payload length mismatch");
                    target = header_bytes + 4 + size_t(length);
                }
                if (response.size() == target) last_exchange_.receive_end_ns = host_ns();
            }
        }
        if (descriptor.revents & POLLOUT) {
            if (!last_exchange_.send_begin_ns) last_exchange_.send_begin_ns = host_ns();
            const auto count = ::write(fd_, request.data() + sent, request.size() - sent);
            if (count < 0) {
                require(errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR, "IFR1 write failed");
            } else {
                require(count != 0, "IFR1 zero UART write");
                sent += size_t(count);
                if (sent == request.size()) last_exchange_.send_end_ns = host_ns();
            }
        }
        require(!(descriptor.revents & POLLHUP) || response.size() == target,
                "IFR1 UART hangup");
    }
    require(get(response, 0, 4) == (version_ == 1 ? UINT32_C(0x3152464f) : UINT32_C(0x3252464f)) &&
            get(response, 4, 1) == version_ && get(response, 6, 2) == profile,
            "IFR1 response version/profile mismatch");
    require(get(response, 8, 8) == identity &&
            (capability || get(response, 16, 4) == generation),
            "IFR1 stale run/generation");
    require(get(response, response.size() - 4, 4) == crc32(response, response.size() - 4),
            "IFR1 response CRC mismatch");
    require(get(response, 5, 1) == 0, "IFR1 FPGA error status");
    require(get(response, 30, 2) == 0, "IFR response reserved fields");
    if (version_ == 1) {
        require(get(response, 88, 8) == 0, "IFR1 response reserved fields");
    } else {
        const auto op = request[5];
        const auto flags = get(response, 89, 1);
        require(get(response, 88, 1) == op && (op == 6 ? flags <= 1 : flags == 0),
                "IFR2 response operation/flags mismatch");
        require(op != 6 || ((flags == 1) == (get(response, 20, 4) != 0)),
                "IFR2 completion payload mismatch");
        if (op != 5 && !(op == 6 && flags == 1))
            require(get(response, 90, 6) == 0 && get(response, 96, 8) == 0 && get(response, 104, 8) == 0,
                    "IFR2 unexpected completion identity");
        if (op == 5) require(get(response, 104, 8) == 0, "IFR2 premature publication completion");
        if (capability) {
            for (size_t offset = 32; offset < 88; offset += 8)
                require(get(response, offset, 8) == 0, "IFR2 nonidle capability counters");
            for (size_t offset = 112; offset < 144; offset += 8)
                require(get(response, offset, 8) == 0, "IFR2 nonidle capability observation");
        }
    }
    last_exchange_.validated_ns = host_ns();
    if (owned_ && version_ == 2 && !telemetry_.first_activation && get(response, 112, 8) != 0)
        telemetry_.first_activation = FirstActivationObservation{
            get(response, 112, 8), get(response, 120, 8), *last_exchange_.validated_ns};
    if (version_ == 2) {
        last_first_activation_cycle.store(get(response, 112, 8));
        last_first_activation_published_rows.store(get(response, 120, 8));
    }
    return response;
}

Bytes UART::exchange(const Bytes &request, size_t payload_bytes, bool allow_empty) {
    metrics_.request_bytes += request.size();
    ++metrics_.transactions;
    const auto start = Clock::now();
    auto reply = transfer(request, metrics_.run_id, metrics_.generation, false, payload_bytes, allow_empty);
    metrics_.transfer_seconds += seconds(start, Clock::now());
    metrics_.response_bytes += reply.size();
    return reply;
}

int UART::full(const im2p_matmul_desc_t *d, im2p_work_stats_extended_t *stats) noexcept {
    std::lock_guard lock(mutex_);
    if (!error_.empty()) return IM2P_ERROR;
    try {
        require(!owned_, "IFR previous invocation not released");
        telemetry_ = {};
        validate(d, version_);
        require(!full_reference_required_ || full_expectation_.has_value(), "IFR2 FULL reference expectation missing");
        if (full_expectation_)
            require(version_ == 2 && d->m == full_expectation_->m && d->n == full_expectation_->n &&
                    d->k == full_expectation_->k, "IFR2 FULL reference descriptor mismatch");
        require(stats, "IFR null stats");
        validate_activation(d->activations, d->m, d->k, d->activation_row_stride_bytes);
        require(generation_ != UINT32_MAX && next_run_id_ != UINT64_MAX,
                "IFR invocation identity exhausted");
        metrics_ = {};
        last_raw.clear(); run_request_.clear(); run_response_.clear();
        published_count.store(0);
        last_first_activation_cycle.store(0);
        last_first_activation_published_rows.store(0);
        const auto begin = Clock::now();
        invocation_begin_ = begin;
        metrics_.generation = generation_ + 1;
        metrics_.run_id = next_run_id_;
        telemetry_.run_id = metrics_.run_id;
        telemetry_.generation = metrics_.generation;
        stream_.m = d->m; stream_.n = d->n; stream_.k = d->k;
        Bytes staging(d->m * 128 + d->k * 64, 0);
        for (size_t row = 0; row < d->m; ++row)
            std::memcpy(staging.data() + row * 128,
                        static_cast<const uint8_t *>(d->activations) + row * d->activation_row_stride_bytes, d->k);
        weights(staging, d->m * 128, d->n, d->k, d->provider);
        run_request_ = packet(version_, 1, metrics_.run_id, metrics_.generation, d->m, d->n, d->k, staging);
        metrics_.prepare_seconds = seconds(begin, Clock::now());
        owned_ = true;
        run_response_ = exchange(run_request_, (d->k / 32) * d->m * ((d->n + 15) / 16) * 64);
        telemetry_.full = last_exchange_;
        telemetry_.final_response_header.assign(run_response_.begin(), run_response_.begin() + (version_ == 1 ? 96 : 144));
        telemetry_.statistics = reply_statistics(run_response_, version_);
        const auto received = Clock::now();
        shape(run_response_, d->m, d->n, d->k);
        statistics(run_response_, d->m, d->n, d->k, stats, version_);
        metrics_.core_cycles = stats->base.work_total_cycles;
        // A verified response must pass this gate before any reconstruction,
        // caller output commit, RELEASE, or later invocation can occur.
        if (full_expectation_ && metrics_.core_cycles != full_expectation_->cycles) {
            std::ostringstream failure;
            failure << "IFR2 FULL cycle mismatch expected=" << full_expectation_->cycles
                    << " actual=" << metrics_.core_cycles << " fixture=" << full_expectation_->fixture
                    << " mode=FULL shape=" << d->m << ',' << d->n << ',' << d->k
                    << " run=" << metrics_.run_id << " generation=" << metrics_.generation
                    << " backend=FPGA_UART protocol=2 profile=0810 hardware_sha256=" << hardware_sha256
                    << " production_rtl_sha256=" << production_rtl_sha256
                    << " response_header=" << response_hex(run_response_)
                    << " response_crc=" << std::hex << get(run_response_, run_response_.size() - 4, 4);
            throw std::runtime_error(failure.str());
        }
        last_raw.resize(d->m * d->n * (d->k / 32));
        reconstruct(run_response_, version_ == 1 ? 96 : 144, last_raw,
                    d->m, d->n, d->k, 0, d->m, d->provider);
        const auto reconstructed = Clock::now();
        metrics_.reconstruction_seconds = seconds(received, reconstructed);
        metrics_.service_seconds = seconds(begin, reconstructed);
        completed_ = true;
        telemetry_.completed = true;
        generation_ = metrics_.generation;
        return IM2P_OK;
    } catch (const std::exception &exception) { error_ = exception.what(); }
      catch (...) { error_ = "IFR unexpected host failure"; }
    return IM2P_ERROR;
}

int UART::begin(const im2p_stripe_work_desc_t *d, size_t stripe_rows) noexcept {
    std::lock_guard lock(mutex_);
    if (!error_.empty()) return IM2P_ERROR;
    try {
        require(version_ == 2 && !owned_, "IFR2 stream requires an unowned IFR2 device");
        telemetry_ = {};
        validate(d, version_);
        require(stripe_rows > 0 && stripe_rows <= 336 && stripe_rows % 16 == 0 &&
                d->stripe_count == (d->m + stripe_rows - 1) / stripe_rows,
                "IFR2 unsupported stripe geometry");
        require(generation_ != UINT32_MAX && next_run_id_ != UINT64_MAX,
                "IFR invocation identity exhausted");
        invocation_begin_ = Clock::now();
        metrics_ = {};
        metrics_.generation = generation_ + 1;
        metrics_.run_id = next_run_id_;
        telemetry_.run_id = metrics_.run_id;
        telemetry_.generation = metrics_.generation;
        telemetry_.streaming = true;
        telemetry_.stripes.reserve(d->stripe_count);
        full_expectation_.reset();
        stream_ = *d; // Provider and host metadata remain borrowed through fence.
        stripe_rows_ = stripe_rows;
        next_row_ = completed_stripes_ = 0;
        previous_completion_cycle_ = 0;
        host_context_ = 0;
        pending_.clear();
        published_count.store(0);
        last_first_activation_cycle.store(0);
        last_first_activation_published_rows.store(0);
        last_raw.assign(d->m * d->n * (d->k / 32), 0);
        Bytes staging(d->k * 64, 0);
        weights(staging, 0, d->n, d->k, d->provider);
        run_request_ = packet(version_, 4, metrics_.run_id, metrics_.generation,
                              d->m, d->n, d->k, staging, stripe_rows);
        metrics_.prepare_seconds = seconds(invocation_begin_, Clock::now());
        owned_ = true;
        run_response_ = exchange(run_request_, 0);
        telemetry_.begin = last_exchange_;
        shape(run_response_, d->m, d->n, d->k);
        streaming_ = true;
        generation_ = metrics_.generation;
        return IM2P_OK;
    } catch (const std::exception &exception) { error_ = exception.what(); }
      catch (...) { error_ = "IFR2 unexpected begin failure"; }
    return IM2P_ERROR;
}

int UART::publish(const im2p_activation_stripe_t *s) noexcept {
    std::lock_guard lock(mutex_);
    if (!error_.empty()) return IM2P_ERROR;
    try {
        require(streaming_ && owned_ && !completed_ && s, "IFR2 no active stream/stripe");
        require(s->abi_version == IM2P_ABI_VERSION && s->activation_bits == 8 &&
                s->activation_storage_bytes == 1 && s->weight_bits == 8 &&
                s->weight_storage_bytes == 1 && s->dim == 16, "IFR2 stripe profile mismatch");
        const size_t id = published_count.load();
        require(id < stream_.stripe_count && s->stripe_id == id && s->i_start == next_row_ &&
                s->rows == std::min(stripe_rows_, stream_.m - next_row_) &&
                (id == 0 || s->context == host_context_), "IFR2 stripe identity/range mismatch");
        validate_activation(s->activations, s->rows, stream_.k, s->activation_row_stride_bytes);
        if (pending_.size() >= 2) return IM2P_BACKPRESSURE; // No acceptance or transfer.
        const auto begin = Clock::now();
        Bytes staging(s->rows * 128, 0);
        for (size_t row = 0; row < s->rows; ++row)
            std::memcpy(staging.data() + row * 128,
                        static_cast<const uint8_t *>(s->activations) + row * s->activation_row_stride_bytes, stream_.k);
        const auto request = packet(version_, 5, metrics_.run_id, metrics_.generation,
                                    s->i_start, s->rows, s->stripe_id, staging, s->stripe_id % 2);
        metrics_.prepare_seconds += seconds(begin, Clock::now());
        const auto reply = exchange(request, 0);
        shape(reply, stream_.m, stream_.n, stream_.k);
        require(get(reply, 90, 2) == s->stripe_id && get(reply, 92, 2) == s->i_start &&
                get(reply, 94, 2) == s->rows, "IFR2 publication identity mismatch");
        pending_.push_back({{s->stripe_id, s->i_start, s->rows, s->context}, get(reply, 96, 8)});
        telemetry_.stripes.push_back({s->stripe_id, s->stripe_id % 2, s->i_start, s->rows,
            s->context, get(reply, 96, 8), last_exchange_, {}, {}});
        ++telemetry_.publications;
        host_context_ = s->context;
        next_row_ += s->rows;
        published_count.store(id + 1);
        return IM2P_OK;
    } catch (const std::exception &exception) { error_ = exception.what(); }
      catch (...) { error_ = "IFR2 unexpected publish failure"; }
    return IM2P_ERROR;
}

int UART::poll(im2p_stripe_completion_extended_t *completion) noexcept {
    std::lock_guard lock(mutex_);
    if (!error_.empty()) return IM2P_ERROR;
    try {
        require(streaming_ && owned_ && !completed_ && completion, "IFR2 no active stream/completion");
        const size_t bytes = pending_.empty() ? 0 :
            (stream_.k / 32) * pending_.front().identity.rows * ((stream_.n + 15) / 16) * 64;
        const auto reply = exchange(packet(version_, 6, metrics_.run_id, metrics_.generation), bytes, true);
        shape(reply, stream_.m, stream_.n, stream_.k);
        if (get(reply, 89, 1) == 0) return 0;
        require(!pending_.empty(), "IFR2 stale/duplicate completion");
        const auto &expected = pending_.front();
        require(get(reply, 90, 2) == expected.identity.stripe_id &&
                get(reply, 92, 2) == expected.identity.i_start &&
                get(reply, 94, 2) == expected.identity.rows &&
                get(reply, 96, 8) == expected.publish_cycle &&
                get(reply, 104, 8) >= expected.publish_cycle &&
                get(reply, 104, 8) >= previous_completion_cycle_, "IFR2 completion identity/cycle mismatch");
        const auto begin = Clock::now();
        reconstruct(reply, 144, last_raw, stream_.m, stream_.n, stream_.k,
                    expected.identity.i_start, expected.identity.rows, stream_.provider);
        metrics_.reconstruction_seconds += seconds(begin, Clock::now());
        *completion = {expected.identity, expected.publish_cycle, get(reply, 104, 8),
                       get(reply, 104, 8) - expected.publish_cycle};
        auto &stripe_telemetry = telemetry_.stripes.at(expected.identity.stripe_id);
        stripe_telemetry.completion_cycle = get(reply, 104, 8);
        stripe_telemetry.completion = last_exchange_;
        ++telemetry_.completions;
        previous_completion_cycle_ = completion->completion_cycle;
        pending_.pop_front();
        ++completed_stripes_;
        return 1;
    } catch (const std::exception &exception) { error_ = exception.what(); }
      catch (...) { error_ = "IFR2 unexpected poll failure"; }
    return IM2P_ERROR;
}

int UART::finish(im2p_work_stats_extended_t *stats) noexcept {
    std::lock_guard lock(mutex_);
    if (!error_.empty()) return IM2P_ERROR;
    try {
        require(streaming_ && owned_ && !completed_ && stats && next_row_ == stream_.m &&
                completed_stripes_ == stream_.stripe_count && pending_.empty(),
                "IFR2 unfinished stream");
        run_response_ = exchange(packet(version_, 7, metrics_.run_id, metrics_.generation), 0);
        telemetry_.finish = last_exchange_;
        telemetry_.final_response_header.assign(run_response_.begin(), run_response_.begin() + 144);
        telemetry_.statistics = reply_statistics(run_response_, version_);
        shape(run_response_, stream_.m, stream_.n, stream_.k);
        statistics(run_response_, stream_.m, stream_.n, stream_.k, stats, version_);
        require(stats->base.work_total_cycles >= previous_completion_cycle_, "IFR2 premature final completion");
        stats->base.completed_stripes = completed_stripes_;
        stats->base.stripes_published = published_count.load();
        stats->base.stripe_rows_published = next_row_;
        metrics_.core_cycles = stats->base.work_total_cycles;
        metrics_.service_seconds = seconds(invocation_begin_, Clock::now());
        completed_ = true;
        telemetry_.completed = true;
        return IM2P_OK;
    } catch (const std::exception &exception) { error_ = exception.what(); }
      catch (...) { error_ = "IFR2 unexpected finish failure"; }
    return IM2P_ERROR;
}

int UART::release() noexcept {
    std::lock_guard lock(mutex_);
    if (!error_.empty()) return IM2P_ERROR;
    const auto begin = Clock::now();
    try {
        require(owned_ && completed_, "IFR no completed invocation to release");
        const double service_transfer_seconds = metrics_.transfer_seconds;
        const auto reply = exchange(packet(version_, 2, metrics_.run_id, metrics_.generation), 0);
        shape(reply, stream_.m, stream_.n, stream_.k);
        telemetry_.release = last_exchange_;
        telemetry_.released = true;
        metrics_.transfer_seconds = service_transfer_seconds; // RELEASE has its own interval.
        const auto end = Clock::now();
        metrics_.release_seconds = seconds(begin, end);
        metrics_.sustained_seconds = seconds(invocation_begin_, end);
        owned_ = completed_ = streaming_ = false;
        full_expectation_.reset();
        ++next_run_id_;
        return IM2P_OK;
    } catch (const std::exception &exception) { error_ = exception.what(); }
      catch (...) { error_ = "IFR unexpected release failure"; }
    // No automatic ABORT, reset, retry, or reprogramming after any failure.
    return IM2P_ERROR;
}
} // namespace im2p::fpga
