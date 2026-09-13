#pragma once
// Passive A8/DIM16 demand-activation observer. No RTL rule or method calls.
#include <array>
#include <cstdint>
#include <stdexcept>
#include <string>
#include <unordered_set>

namespace activation_monitor {
using Row = std::array<uint32_t, 4>; // Four raw words, lane0 in bits7:0 of word0.
struct Request {
    bool valid = false;
    uint64_t tag = 0;
    unsigned slot = 0, row = 0;
    uint32_t k_start = 0;
    unsigned k_count = 0;
};
struct Pending {
    bool valid = false;
    unsigned slot = 0, row = 0;
    Row values{};
};
struct Slot {
    bool valid = false;
    uint32_t k_start = 0;
    unsigned k_count = 0;
    uint16_t row_valid = 0;
    std::array<Row, 16> rows{};
};
struct Snapshot {
    uint64_t tick = 0;
    bool reset_n = true;
    uint32_t job_id = 0;
    unsigned required_rows = 0, current_slot = 0, feed_row = 0;
    Request request;
    bool lookahead_request_valid = false;
    uint64_t lookahead_request_tag = 0;
    Pending pending;
    std::array<Slot, 2> slots{};
};
struct Events {
    bool return_accepted = false;
    uint64_t return_tag = 0;
    Row return_values{};
    bool publish = false, feed = false;
    bool feed_values_valid = false;
    Row feed_values{}; // Actual accepted engine argument, when the adapter can observe it.
    bool begin_work = false;
    std::array<bool, 2> slot_reallocate{};
    bool fragment_finish = false, job_complete = false;
};
struct Statistics {
    uint64_t edges = 0, reset_edges = 0, work_begins = 0;
    uint64_t demand_returns = 0, lookahead_returns = 0, publications = 0, feeds = 0;
    uint64_t legal_turnovers = 0, exact_assertion_pending_accept_violation = 0;
    uint64_t feed_data_unobserved = 0, completed_jobs = 0;
    std::array<uint64_t, 2> slot_epochs{};
};

inline Row padded(Row values, unsigned count) {
    for (unsigned lane = count; lane < 16; ++lane)
        values[lane / 4] &= ~(uint32_t(255) << (8 * (lane % 4)));
    return values;
}

class Monitor {
    bool have_previous_ = false;
    Snapshot previous_;
    Request pending_identity_;
    uint64_t pending_epoch_ = 0;
    std::unordered_set<uint64_t> issued_tags_;

    [[noreturn]] static void fail(uint64_t tick, const char *reason) {
        throw std::runtime_error("activation monitor tick " + std::to_string(tick) + ": " + reason);
    }
    static bool same_request(const Request &a, const Request &b) {
        return a.valid == b.valid && (!a.valid || (a.tag == b.tag && a.slot == b.slot &&
            a.row == b.row && a.k_start == b.k_start && a.k_count == b.k_count));
    }
    static bool same_pending(const Pending &a, const Pending &b) {
        return a.valid == b.valid && (!a.valid ||
            (a.slot == b.slot && a.row == b.row && a.values == b.values));
    }
    static bool same_metadata(const Slot &a, const Slot &b) {
        return a.valid == b.valid && a.k_start == b.k_start && a.k_count == b.k_count;
    }
    static void request_contract(const Snapshot &s, const Request &r) {
        if (!r.valid || r.slot >= 2 || r.row >= 16 || r.row >= s.required_rows ||
            !r.k_count || r.k_count > 16) fail(s.tick, "request bounds/validity");
        if (uint32_t(r.tag >> 32) != s.job_id) fail(s.tick, "request job mismatch");
        const auto &slot = s.slots[r.slot];
        if (!slot.valid || slot.k_start != r.k_start || slot.k_count != r.k_count)
            fail(s.tick, "request slot metadata mismatch");
    }

public:
    Statistics stats;

    void observe(const Snapshot &pre, const Events &event, const Snapshot &post) {
        ++stats.edges;
        if (!pre.reset_n || !post.reset_n) {
            ++stats.reset_edges;
            have_previous_ = false;
            issued_tags_.clear();
            pending_identity_ = {};
            return; // Reset is an explicit abort; uninitialized invalid row data is ignored.
        }
        if (pre.current_slot >= 2 || pre.required_rows > 16 || pre.feed_row > 16)
            fail(pre.tick, "work bounds");
        if (have_previous_) {
            if (!same_pending(previous_.pending, pre.pending)) fail(pre.tick, "pending changed between sampled edges");
            if (!same_request(previous_.request, pre.request)) fail(pre.tick, "request changed between sampled edges");
        } else if (pre.pending.valid) {
            fail(pre.tick, "monitor attached with unknown pending response");
        }
        if (event.begin_work) {
            if (pre.pending.valid || pre.request.valid || event.return_accepted || event.publish || event.feed)
                fail(pre.tick, "work reallocation with live demand response");
            if (post.pending.valid || post.request.valid) fail(pre.tick, "work clear left live demand response");
            ++stats.work_begins;
            ++stats.slot_epochs[0]; ++stats.slot_epochs[1];
            issued_tags_.clear();
            previous_ = post; have_previous_ = true;
            return; // acquireMatrixWork may legitimately promote prevalidated lookahead rows.
        }

        const bool lookahead = event.return_accepted && pre.lookahead_request_valid &&
                               event.return_tag == pre.lookahead_request_tag;
        const bool demand = event.return_accepted && !lookahead;
        if (lookahead) ++stats.lookahead_returns; // The lookahead storage datapath is outside this observer.
        if (demand && pre.pending.valid) ++stats.exact_assertion_pending_accept_violation;
        if (event.publish && !pre.pending.valid) fail(pre.tick, "publication without pending response");
        if (demand) {
            request_contract(pre, pre.request);
            if (event.return_tag != pre.request.tag) fail(pre.tick, "response tag mismatch");
            if (pre.pending.valid && !event.publish) fail(pre.tick, "pending overwrite without publication");
            if (pre.slots[pre.request.slot].row_valid & (1u << pre.request.row))
                fail(pre.tick, "duplicate activation response");
            if (pre.pending.valid && pre.pending.slot == pre.request.slot && pre.pending.row == pre.request.row)
                fail(pre.tick, "duplicate response for pending row");
            ++stats.demand_returns;
            if (pre.pending.valid) ++stats.legal_turnovers;
        }
        if (event.publish) {
            if (pre.pending.slot >= 2 || pre.pending.row >= pre.required_rows || pre.pending.row >= 16)
                fail(pre.tick, "publication bounds");
            const auto &slot = pre.slots[pre.pending.slot];
            if (!slot.valid || slot.k_start != pending_identity_.k_start || slot.k_count != pending_identity_.k_count ||
                stats.slot_epochs[pre.pending.slot] != pending_epoch_)
                fail(pre.tick, "publication slot epoch mismatch");
            if (slot.row_valid & (1u << pre.pending.row)) fail(pre.tick, "duplicate activation publication");
            ++stats.publications;
        }
        if (event.feed) {
            const auto &slot = pre.slots[pre.current_slot];
            if (!slot.valid || pre.feed_row >= pre.required_rows || pre.feed_row >= 16 ||
                !(slot.row_valid & (1u << pre.feed_row))) fail(pre.tick, "premature activation consume");
            if (event.feed_values_valid && event.feed_values != slot.rows[pre.feed_row])
                fail(pre.tick, "engine activation value mismatch");
            if (!event.feed_values_valid) ++stats.feed_data_unobserved;
            if (post.feed_row != pre.feed_row + 1) fail(pre.tick, "activation feed sequence mismatch");
            ++stats.feeds;
        }

        Pending expected = pre.pending;
        if (event.publish) expected.valid = false;
        if (demand) expected = {true, pre.request.slot, pre.request.row, padded(event.return_values, pre.request.k_count)};
        if (!same_pending(expected, post.pending)) fail(pre.tick, "pending response conservation/data mismatch");
        if (demand && post.request.valid) fail(pre.tick, "accepted response did not retire request");
        if (!demand && pre.request.valid && !same_request(pre.request, post.request))
            fail(pre.tick, "outstanding request lost or replaced");
        if (!pre.request.valid && post.request.valid) {
            request_contract(post, post.request);
            if (!issued_tags_.insert(post.request.tag).second) fail(pre.tick, "duplicate request tag within work");
        }

        for (unsigned index = 0; index < 2; ++index) {
            const auto &before = pre.slots[index];
            const auto &after = post.slots[index];
            uint16_t valid = before.row_valid;
            if (event.publish && pre.pending.slot == index) valid |= uint16_t(1u << pre.pending.row);
            if (event.feed && pre.current_slot == index) valid &= uint16_t(~(1u << pre.feed_row));
            const bool reallocated = event.slot_reallocate[index] || !same_metadata(before, after);
            if (reallocated) {
                if (valid || (post.pending.valid && post.pending.slot == index) ||
                    (post.request.valid && post.request.slot == index))
                    fail(pre.tick, "slot reallocation before demand consumption");
                ++stats.slot_epochs[index];
            }
            if (after.row_valid != valid) fail(pre.tick, "slot row-valid conservation mismatch");
            for (unsigned row = 0; row < 16; ++row) {
                if (!(valid & (1u << row))) continue;
                const Row &values = event.publish && pre.pending.slot == index && pre.pending.row == row
                    ? pre.pending.values : before.rows[row];
                if (after.rows[row] != values) fail(pre.tick, "published row data mismatch");
            }
        }
        if (event.fragment_finish) {
            const unsigned current = pre.current_slot;
            if (pre.feed_row + unsigned(event.feed) != pre.required_rows || post.slots[current].row_valid ||
                (post.pending.valid && post.pending.slot == current) ||
                (post.request.valid && post.request.slot == current))
                fail(pre.tick, "fragment completion before activation consumption");
        }
        if (event.job_complete) {
            if (post.pending.valid || post.request.valid || post.slots[0].row_valid || post.slots[1].row_valid)
                fail(pre.tick, "job completion with live activation obligations");
            ++stats.completed_jobs;
        }
        if (demand) {
            pending_identity_ = pre.request;
            pending_epoch_ = stats.slot_epochs[pre.request.slot];
        }
        previous_ = post;
        have_previous_ = true;
    }
};
} // namespace activation_monitor
