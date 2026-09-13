#pragma once
// Test-only observer layout. TbWorkLayout packs the actual BSV type independently.
#include <array>
#include <cstdint>
#include <stdexcept>

namespace work_layout {
inline constexpr unsigned record_bits = 899;
inline constexpr unsigned words = (record_bits + 31) / 32;
inline constexpr const char *identity = "WorkTypes.MatmulWork.D16.bits899.VectorOp3";
struct Field { unsigned offset, width; };
inline constexpr Field job_id{867, 32}, stripe_id{835, 32}, stripe_context{771, 64};
inline constexpr Field i_start{739, 32}, j_start{707, 32}, i_count{675, 32}, j_count{643, 32};
inline constexpr Field vector_op{64, 3};

constexpr bool in_range(Field field) {
    return field.width > 0 && field.width <= 64 && field.width <= record_bits &&
        field.offset <= record_bits - field.width;
}
static_assert(in_range(job_id) && in_range(stripe_id) && in_range(stripe_context) &&
    in_range(i_start) && in_range(j_start) && in_range(i_count) && in_range(j_count) && in_range(vector_op));

inline void validate(unsigned actual_bits, unsigned actual_words) {
    if (actual_bits != record_bits || actual_words != words)
        throw std::runtime_error("MatmulWork packed layout/record width mismatch");
}
template<class Wide> uint64_t extract(const Wide &packed, Field field) {
    validate(record_bits, packed.size());
    if (!in_range(field)) throw std::runtime_error("MatmulWork field width/range invalid");
    uint64_t value = 0;
    for (unsigned bit = 0; bit < field.width; ++bit) {
        const unsigned at = field.offset + bit;
        value |= uint64_t((packed[at / 32] >> (at % 32)) & 1) << bit;
    }
    return value;
}
} // namespace work_layout
