#include "work_layout.hpp"
#include "activation_monitor.hpp"
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>

static void require(bool value, const char *why) {
    if (!value) throw std::runtime_error(why);
}
template<class Fn> static void rejects(const char *name, const char *reason, Fn fn) {
    try { fn(); }
    catch (const std::runtime_error &error) {
        require(std::string(error.what()).find(reason) != std::string::npos, "negative failed at wrong check");
        std::cout << "EXPECTED_REJECTION " << name << ": " << error.what() << '\n';
        return;
    }
    throw std::runtime_error(std::string("negative was accepted: ") + name);
}

int main(int argc, char **argv) {
    try {
        require(argc == 2, "usage: test_work_layout oracle.log");
        std::ifstream input(argv[1]);
        require(input.good(), "cannot open BSV pack oracle output");
        unsigned vectors = 0, comparisons = 0, old_nine_rows = 0;
        std::string line;
        while (std::getline(input, line)) {
            if (line.rfind("WORK_LAYOUT_VECTOR ", 0) != 0) continue;
            std::istringstream values(line);
            std::string marker, hex;
            unsigned index, bits;
            std::array<uint64_t, 8> expected{};
            values >> marker >> index >> bits >> hex >> std::hex;
            for (auto &value : expected) values >> value;
            require(!values.fail(), "malformed BSV pack oracle line");
            std::string extra;
            require(!(values >> extra), "extra BSV pack oracle field");
            require(index == vectors && index < 3, "oracle vector identity/count mismatch");
            const unsigned expected_rows[] = {1, 9, 16};
            require(expected[5] == expected_rows[index], "oracle missing required iCount");
            work_layout::validate(bits, work_layout::words);
            require(hex.size() == (bits + 3) / 4, "oracle packed hex width mismatch");
            std::array<uint32_t, work_layout::words> packed{};
            for (unsigned at = 0; at < hex.size(); ++at) {
                const char digit = hex[hex.size() - at - 1];
                const unsigned nibble = digit >= '0' && digit <= '9' ? digit - '0' :
                    digit >= 'a' && digit <= 'f' ? digit - 'a' + 10 :
                    digit >= 'A' && digit <= 'F' ? digit - 'A' + 10 : 16;
                require(nibble < 16, "invalid oracle packed digit");
                packed[at / 8] |= uint32_t(nibble) << (4 * (at % 8));
            }
            require((packed.back() >> (bits % 32)) == 0, "nonzero packed padding bits");
            const work_layout::Field fields[] = {work_layout::job_id, work_layout::stripe_id,
                work_layout::stripe_context, work_layout::i_start, work_layout::j_start,
                work_layout::i_count, work_layout::j_count, work_layout::vector_op};
            for (unsigned field = 0; field < expected.size(); ++field) {
                require(work_layout::extract(packed, fields[field]) == expected[field], "BSV pack / observer decode mismatch");
                ++comparisons;
            }
            const auto old_rows = work_layout::extract(packed, {674, 32});
            const auto old_stripe = work_layout::extract(packed, {834, 32});
            const auto old_context = work_layout::extract(packed, {770, 64});
            require(old_rows != expected[5] && old_stripe != expected[1] && old_context != expected[2],
                "old layout negative control did not distinguish each changed field");
            if (index == 1) old_nine_rows = unsigned(old_rows);
            std::cout << "WORK_LAYOUT_DECODE index=" << index << " bits=" << bits
                << " iCount=" << expected[5] << " jCount=" << expected[6]
                << " stripeId=" << expected[1] << " stripeContext=" << expected[2]
                << " old_required_rows=" << old_rows << " old_stripeId=" << old_stripe
                << " old_stripeContext=" << old_context << '\n';
            rejects("old-layout", "packed layout", [] { work_layout::validate(898, 29); });
            rejects("short-storage", "packed layout", [] { work_layout::validate(899, 28); });
            rejects("zero-width", "field width/range", [&] { work_layout::extract(packed, {675, 0}); });
            rejects("oversize-width", "field width/range", [&] { work_layout::extract(packed, {675, 65}); });
            rejects("outside-record", "field width/range", [&] { work_layout::extract(packed, {899, 1}); });
            rejects("offset-overflow", "field width/range", [&] { work_layout::extract(packed, {UINT32_MAX, 32}); });
            rejects("wrong-in-range-width", "width lost", [&] {
                require(work_layout::extract(packed, {835, 31}) == expected[1], "field width lost high identity bit");
            });
            ++vectors;
        }
        require(vectors == 3 && comparisons == 24, "incomplete layout oracle execution");
        require(old_nine_rows == 18, "old iCount9 decode counterexample changed");

        // Monitor negative controls use local snapshots, never mutate a numerical DUT.
        activation_monitor::Snapshot pre, post;
        activation_monitor::Events event;
        pre.tick = 41; pre.job_id = 0x89abcdef; pre.required_rows = 9;
        pre.slots[0].valid = true; pre.slots[0].k_count = 1;
        pre.request = {true, (uint64_t(pre.job_id) << 32) | 7, 0, 0, 0, 1};
        event.return_accepted = true; event.return_tag = pre.request.tag;
        event.return_values = {1, 0, 0, 0};
        post = pre; post.request.valid = false;
        post.pending = {true, 0, 0, event.return_values};
        activation_monitor::Monitor positive;
        positive.observe(pre, event, post);
        require(positive.stats.demand_returns == 1, "valid monitor control not accepted");
        rejects("monitor-old-iCount9-decodes18", "work bounds", [&] {
            auto wrong = pre; wrong.required_rows = old_nine_rows;
            activation_monitor::Monitor monitor; monitor.observe(wrong, event, post);
        });
        rejects("monitor-bad-response-tag", "response tag mismatch", [&] {
            auto wrong = event; wrong.return_tag ^= 1;
            activation_monitor::Monitor monitor; monitor.observe(pre, wrong, post);
        });
        rejects("monitor-bad-request-job", "request job mismatch", [&] {
            auto wrong = pre; wrong.request.tag ^= uint64_t(1) << 32;
            activation_monitor::Monitor monitor; monitor.observe(wrong, event, post);
        });
        std::cout << "WORK_LAYOUT_PASS identity=" << work_layout::identity
            << " vectors=3 field_comparisons=24 old_layout_mismatches=9 expected_rejections=24 monitor_positive=1\n";
        return 0;
    } catch (const std::exception &error) {
        std::cerr << "WORK_LAYOUT_FAIL " << error.what() << '\n';
        return 1;
    }
}
