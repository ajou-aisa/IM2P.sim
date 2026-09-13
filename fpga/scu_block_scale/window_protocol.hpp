// Generated from window_packets.py; checked by test_window_packets.py.
#pragma once
namespace im2p::fpga::ifr4 {
inline constexpr unsigned VERSION = 4;
inline constexpr unsigned HEADER_BYTES = 48;
inline constexpr unsigned MAX_PAYLOAD = 4096;
inline constexpr unsigned POLL_BYTES = 176;
inline constexpr unsigned RECORD_BYTES = 88;
inline constexpr unsigned OUTPUT_RECORDS = 16;
inline constexpr unsigned CAP = 0;
inline constexpr unsigned START = 1;
inline constexpr unsigned POLL = 2;
inline constexpr unsigned REFILL = 3;
inline constexpr unsigned PUBLISH = 4;
inline constexpr unsigned OUTPUT_ACK = 5;
inline constexpr unsigned STRIPE_ACK = 6;
inline constexpr unsigned RELEASE = 7;
}
