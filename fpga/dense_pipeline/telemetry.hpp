#pragma once

#include "im2p_gemmini_frontend.hpp"
#include "uart.hpp"
#include <ostream>
#include <stdexcept>

namespace im2p::fpga {

// Owned values only. Copy the borrowed timing view while Run still exists.
struct RunTelemetry {
    im2p_work_stats_extended_t stats;
    std::vector<gemmini::StripeRtlTiming> stripes;
    explicit RunTelemetry(const gemmini::FenceResult &result) : stats(result.stats) {
        if (!result.status.ok() || result.stripe_rtl_timings.size > 21)
            throw std::runtime_error("invalid completed telemetry snapshot");
        stripes.reserve(result.stripe_rtl_timings.size);
        for (const auto &stripe : result.stripe_rtl_timings) stripes.push_back(stripe);
    }
};

inline void optional_value(std::ostream &out, const char *name,
                           const std::optional<uint64_t> &value) {
    out << ' ' << name << '=';
    if (value) out << *value;
    else out << "unavailable";
}

inline void exchange_values(std::ostream &out, const ExchangeObservation &exchange) {
    out << " host_begin_ns=" << exchange.begin_ns;
    optional_value(out, "host_send_begin_ns", exchange.send_begin_ns);
    optional_value(out, "host_send_end_ns", exchange.send_end_ns);
    optional_value(out, "host_receive_end_ns", exchange.receive_end_ns);
    optional_value(out, "host_validated_ns", exchange.validated_ns);
}

// Call after the service/sustained endpoints. Formatting never changes polling.
inline void print_telemetry(std::ostream &out, const RunTelemetry &run,
                            const std::optional<Telemetry> &device) {
    const auto &s = run.stats.base;
    if (device && (!device->completed || !device->released || !device->statistics))
        throw std::runtime_error("partial telemetry cannot be a successful sample");
    out << "RUN_TELEMETRY source=" << (device ? "device_reply" : "simulator_abi")
        << " clock=" << (device ? "fpga_core" : "simulator_core")
        << " elapsed_cycles=" << s.work_total_cycles << " fragments=" << s.completed_fragments
        << " output_works=" << s.completed_output_tiles
        << " activation_reads=" << s.activation_read_requests << " weight_reads=" << s.weight_read_requests
        << " output_writes=" << s.output_write_requests << " output_acks=" << s.output_write_responses
        << " host_wait_cycles=" << s.stripe_host_wait_cycles << " overlap_cycles=" << s.overlap_cycles
        << " complete=1 run_retired=1";
    if (device) {
        out << " run_id=" << device->run_id << " generation=" << device->generation
            << " publication_reply_count=" << device->publications
            << " completion_reply_count=" << device->completions
            << " stripe_count_source=host_validated_replies"
            << " device_publication_total=not_exposed compute_cycles=not_exposed"
            << " cross_stripe_overlap_cycles=not_exposed detailed_wait_cycles=not_exposed";
        if (device->first_activation)
            out << " first_A_cycle=" << device->first_activation->cycle
                << " first_A_published_rows=" << device->first_activation->published_rows
                << " first_A_observed_host_ns=" << device->first_activation->observed_host_ns;
        else out << " first_A_cycle=unavailable first_A_reason=no_nonzero_observation";
    }
    out << '\n';
    for (const auto &stripe : run.stripes)
        out << "RETIRED_STRIPE run=" << stripe.run_id << " id=" << stripe.stripe_id
            << " slot=" << stripe.slot << " begin=" << stripe.row_begin << " end=" << stripe.row_end
            << " publish_cycle=" << stripe.publish_cycle << " completion_cycle=" << stripe.completion_cycle
            << " publish_to_completion_cycles=" << stripe.publish_to_completion_cycles
            << " delta_source=frontend_same_clock_subtraction owned=1 run_retired=1\n";
    if (!device) return;
    for (const auto &stripe : device->stripes) {
        out << "DEVICE_STRIPE run=" << device->run_id << " generation=" << device->generation
            << " id=" << stripe.stripe_id << " slot=" << stripe.slot << " begin=" << stripe.row_begin
            << " rows=" << stripe.rows << " context=" << stripe.context
            << " publish_cycle=" << stripe.publication_cycle;
        optional_value(out, "completion_cycle", stripe.completion_cycle);
        out << " per_stripe_first_A_cycle=not_exposed host_clock=steady_ns boundary=publication";
        exchange_values(out, stripe.publication);
        out << '\n';
        if (stripe.completion) {
            out << "DEVICE_STRIPE_RECEIVE run=" << device->run_id << " id=" << stripe.stripe_id;
            exchange_values(out, *stripe.completion);
            out << '\n';
        }
    }
    for (const auto *exchange : {&device->full, &device->begin, &device->finish, &device->release}) {
        if (!*exchange) continue;
        out << "HOST_TRANSPORT run=" << device->run_id << " operation=" << unsigned((*exchange)->operation);
        exchange_values(out, **exchange);
        out << '\n';
    }
}

} // namespace im2p::fpga
