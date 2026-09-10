#pragma once

#include "im2p_sim.h"
#include <cstdint>
#include <chrono>
#include <atomic>
#include <deque>
#include <mutex>
#include <optional>
#include <string>
#include <vector>

namespace im2p::fpga {

struct Metrics {
    uint64_t run_id = 0;
    uint32_t generation = 0;
    uint64_t request_bytes = 0;
    uint64_t response_bytes = 0;
    uint64_t transactions = 0;
    uint64_t core_cycles = 0;
    double prepare_seconds = 0;
    double transfer_seconds = 0;
    double reconstruction_seconds = 0;
    double release_seconds = 0;
    double service_seconds = 0;
    double sustained_seconds = 0;
};

struct FullCycleExpectation {
    std::string fixture;
    size_t m, n, k;
    uint64_t cycles;
};

// The fixed measurement reference is checked before opening any device. It is
// board-provider evidence, never a simulator or PIPELINE elapsed-time policy.
class FullCycleReference {
public:
    static FullCycleReference load(const std::string &path);
    FullCycleExpectation expect(const std::string &fixture, size_t m, size_t n, size_t k) const;
private:
    FullCycleReference() = default;
};

struct ExchangeObservation {
    uint8_t operation = 0;
    // steady_clock epoch, nanoseconds. These are host observations, not cycles.
    uint64_t begin_ns = 0;
    std::optional<uint64_t> send_begin_ns, send_end_ns, receive_end_ns, validated_ns;
};

struct ReplyStatistics {
    uint64_t elapsed_cycles, fragments, output_works, activation_reads, weight_reads;
    uint64_t output_writes, output_acknowledgements;
    std::optional<uint64_t> host_wait_cycles, overlap_cycles;
};

struct FirstActivationObservation {
    uint64_t cycle, published_rows, observed_host_ns;
};

struct StripeTelemetry {
    uint32_t stripe_id;
    size_t slot, row_begin, rows;
    uint64_t context, publication_cycle;
    ExchangeObservation publication;
    std::optional<uint64_t> completion_cycle;
    std::optional<ExchangeObservation> completion;
};

struct Telemetry {
    uint64_t run_id = 0;
    uint32_t generation = 0;
    bool streaming = false, completed = false, released = false;
    // Counts are validated publication replies and consumed completion replies;
    // the wire has no independent device publication/stripe-ACK total register.
    size_t publications = 0, completions = 0;
    std::optional<ReplyStatistics> statistics;
    std::optional<FirstActivationObservation> first_activation;
    std::optional<ExchangeObservation> full, begin, finish, release;
    std::vector<StripeTelemetry> stripes;
    std::vector<uint8_t> final_response_header;
};

// One persistent, serialized IFR1/IFR2 connection. Construction performs OPEN/CAP.
// The caller must authorize device access before constructing this object.
class UART {
public:
    explicit UART(const std::string &device, double timeout_seconds = 30.0,
                  unsigned version = 1);
    ~UART();
    UART(const UART &) = delete;
    UART &operator=(const UART &) = delete;
    int full(const im2p_matmul_desc_t *descriptor,
             im2p_work_stats_extended_t *stats) noexcept;
    int begin(const im2p_stripe_work_desc_t *descriptor, size_t stripe_rows) noexcept;
    int publish(const im2p_activation_stripe_t *stripe) noexcept;
    // Returns 1 after raw validation/reconstruction, 0 if no completion, <0 on error.
    int poll(im2p_stripe_completion_extended_t *completion) noexcept;
    int finish(im2p_work_stats_extended_t *stats) noexcept;
    // Call only after frontend fence has committed caller f_out.
    int release() noexcept;
    const std::string &error() const noexcept { return error_; }
    uint32_t generation() const noexcept { return generation_; }
    double initialization_seconds() const noexcept { return initialization_seconds_; }
    const Metrics &metrics() const noexcept { return metrics_; }
    void expect_full(const FullCycleExpectation &expectation);
    Telemetry telemetry() const;
    std::vector<int32_t> last_raw; // block/row/column; valid until the next full()/begin().
    const std::vector<uint8_t> &last_run_request() const noexcept { return run_request_; }
    const std::vector<uint8_t> &last_run_response() const noexcept { return run_response_; }
    // Device observations, safe for a producer diagnostic to sample concurrently.
    std::atomic<uint64_t> last_first_activation_cycle{0};
    std::atomic<uint64_t> last_first_activation_published_rows{0};
    std::atomic<size_t> published_count{0};

private:
    int fd_ = -1;
    double timeout_seconds_;
    unsigned version_;
    double initialization_seconds_ = 0;
    uint32_t generation_ = 0;
    uint64_t next_run_id_ = 1;
    std::string error_;
    mutable std::mutex mutex_;
    Metrics metrics_;
    Telemetry telemetry_;
    ExchangeObservation last_exchange_;
    std::optional<FullCycleExpectation> full_expectation_;
    bool full_reference_required_ = false;
    bool owned_ = false;
    bool completed_ = false;
    bool streaming_ = false;
    im2p_stripe_work_desc_t stream_{};
    size_t stripe_rows_ = 0, next_row_ = 0, completed_stripes_ = 0;
    uint64_t host_context_ = 0, previous_completion_cycle_ = 0;
    struct Pending {
        im2p_stripe_completion_t identity;
        uint64_t publish_cycle;
    };
    std::deque<Pending> pending_;
    std::chrono::steady_clock::time_point invocation_begin_;
    std::vector<uint8_t> run_request_, run_response_;
    std::vector<uint8_t> transfer(const std::vector<uint8_t> &request,
                                  uint64_t identity, uint32_t generation,
                                  bool capability, size_t payload_bytes,
                                  bool allow_empty = false);
    std::vector<uint8_t> exchange(const std::vector<uint8_t> &request,
                                 size_t payload_bytes, bool allow_empty = false);
};

} // namespace im2p::fpga
