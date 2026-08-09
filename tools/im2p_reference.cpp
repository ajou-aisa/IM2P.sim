#include <array>
#include <bit>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>

namespace im2p {

enum class VectorOp {
    Bypass,
    Multiply,
    Shift,
};

[[nodiscard]] std::int32_t bits_to_int32(const std::uint32_t bits) noexcept {
    return std::bit_cast<std::int32_t>(bits);
}

[[nodiscard]] std::int32_t wrap_add(
    const std::int32_t left,
    const std::int32_t right
) noexcept {
    const auto sum = static_cast<std::uint32_t>(left)
        + static_cast<std::uint32_t>(right);
    return bits_to_int32(sum);
}

[[nodiscard]] std::int32_t wrap_multiply(
    const std::int32_t partial,
    const std::int8_t scale
) noexcept {
    const auto wide = static_cast<std::int64_t>(partial)
        * static_cast<std::int64_t>(scale);
    return bits_to_int32(static_cast<std::uint32_t>(wide));
}

[[nodiscard]] std::int32_t shift_partial(
    const std::int32_t partial,
    const std::int8_t exponent
) noexcept {
    const int signed_exponent = static_cast<int>(exponent);
    const unsigned amount = static_cast<unsigned>(
        signed_exponent < 0 ? -signed_exponent : signed_exponent
    );

    if (signed_exponent < 0) {
        if (amount >= 32U) {
            return partial < 0 ? -1 : 0;
        }
        return static_cast<std::int32_t>(partial >> amount);
    }

    if (amount >= 32U) {
        return 0;
    }

    const auto shifted = static_cast<std::uint32_t>(partial) << amount;
    return bits_to_int32(shifted);
}

[[nodiscard]] std::int32_t transform_partial(
    const VectorOp operation,
    const std::int32_t partial,
    const std::int8_t scale
) {
    switch (operation) {
        case VectorOp::Bypass:
            return partial;
        case VectorOp::Multiply:
            return wrap_multiply(partial, scale);
        case VectorOp::Shift:
            return shift_partial(partial, scale);
    }

    throw std::logic_error("unreachable VectorOp");
}

[[nodiscard]] std::int32_t commit_accumulator(
    const std::int32_t old_value,
    const std::int32_t contribution,
    const bool accumulate
) noexcept {
    return accumulate ? wrap_add(old_value, contribution) : contribution;
}

template <std::size_t N>
[[nodiscard]] std::array<std::int32_t, N> transform_vector(
    const VectorOp operation,
    const std::array<std::int32_t, N>& partials,
    const std::array<std::int8_t, N>& scales
) {
    std::array<std::int32_t, N> result{};

    for (std::size_t column = 0; column < N; ++column) {
        result[column] = transform_partial(
            operation,
            partials[column],
            scales[column]
        );
    }

    return result;
}

template <std::size_t Rows, std::size_t Inner, std::size_t Columns>
[[nodiscard]] std::array<std::array<std::int32_t, Columns>, Rows>
matmul_int8(
    const std::array<std::array<std::int8_t, Inner>, Rows>& activations,
    const std::array<std::array<std::int8_t, Columns>, Inner>& weights
) {
    std::array<std::array<std::int32_t, Columns>, Rows> result{};

    for (std::size_t row = 0; row < Rows; ++row) {
        for (std::size_t column = 0; column < Columns; ++column) {
            std::int32_t partial = 0;

            for (std::size_t inner = 0; inner < Inner; ++inner) {
                const auto product = static_cast<std::int16_t>(
                    static_cast<std::int16_t>(activations[row][inner])
                    * static_cast<std::int16_t>(weights[inner][column])
                );
                partial = wrap_add(partial, static_cast<std::int32_t>(product));
            }

            result[row][column] = partial;
        }
    }

    return result;
}

template <std::size_t Rows, std::size_t Columns>
[[nodiscard]] std::array<std::array<std::int32_t, Columns>, Rows>
apply_execution(
    const VectorOp operation,
    const std::array<std::array<std::int32_t, Columns>, Rows>& partials,
    const std::array<std::array<std::int8_t, Columns>, Rows>& scales,
    const std::array<std::array<std::int32_t, Columns>, Rows>& old_values,
    const bool accumulate
) {
    std::array<std::array<std::int32_t, Columns>, Rows> result{};

    for (std::size_t row = 0; row < Rows; ++row) {
        const auto contributions = transform_vector(
            operation,
            partials[row],
            scales[row]
        );

        for (std::size_t column = 0; column < Columns; ++column) {
            result[row][column] = commit_accumulator(
                old_values[row][column],
                contributions[column],
                accumulate
            );
        }
    }

    return result;
}

template <typename T, std::size_t N>
void require_equal(
    const std::array<T, N>& actual,
    const std::array<T, N>& expected,
    const std::string& label
) {
    if (actual != expected) {
        std::cerr << "FAIL: " << label << '\n';
        std::exit(EXIT_FAILURE);
    }
}

void self_test() {
    constexpr std::array<std::int32_t, 4> partials{3, -4, 5, -6};

    require_equal(
        transform_vector(
            VectorOp::Bypass,
            partials,
            std::array<std::int8_t, 4>{7, 7, 7, 7}
        ),
        std::array<std::int32_t, 4>{3, -4, 5, -6},
        "VectorBypass"
    );

    require_equal(
        transform_vector(
            VectorOp::Multiply,
            partials,
            std::array<std::int8_t, 4>{2, -3, 4, -5}
        ),
        std::array<std::int32_t, 4>{6, 12, 20, 30},
        "VectorMultiply"
    );

    require_equal(
        transform_vector(
            VectorOp::Shift,
            partials,
            std::array<std::int8_t, 4>{1, -1, 2, -2}
        ),
        std::array<std::int32_t, 4>{6, -2, 20, -2},
        "VectorShift"
    );

    // RTL reference policy의 경계 동작도 고정한다.
    // Add/Multiply는 accumulator 폭의 two's-complement wrap을 사용한다.
    if (wrap_add(std::numeric_limits<std::int32_t>::max(), 1)
            != std::numeric_limits<std::int32_t>::min()) {
        std::cerr << "FAIL: Accumulator wrap add\n";
        std::exit(EXIT_FAILURE);
    }
    if (wrap_multiply(std::numeric_limits<std::int32_t>::max(), 2) != -2) {
        std::cerr << "FAIL: VectorMultiply wrap\n";
        std::exit(EXIT_FAILURE);
    }

    // Shift amount가 accumulator 폭 이상이면 BSV의 fixed-width shift와 같은
    // zero/sign-fill 결과를 기대한다.
    if (shift_partial(1, 32) != 0
            || shift_partial(1, -32) != 0
            || shift_partial(-1, -32) != -1) {
        std::cerr << "FAIL: wide shift policy\n";
        std::exit(EXIT_FAILURE);
    }

    constexpr std::array<std::int32_t, 4> old_values{10, 20, 30, 40};
    constexpr std::array<std::int32_t, 4> contributions{1, 2, -5, 10};
    std::array<std::int32_t, 4> accumulated{};
    std::array<std::int32_t, 4> replaced{};

    for (std::size_t column = 0; column < old_values.size(); ++column) {
        accumulated[column] = commit_accumulator(
            old_values[column],
            contributions[column],
            true
        );
        replaced[column] = commit_accumulator(
            old_values[column],
            contributions[column],
            false
        );
    }

    require_equal(
        accumulated,
        std::array<std::int32_t, 4>{11, 22, 25, 50},
        "Accumulator add"
    );
    require_equal(replaced, contributions, "Accumulator replace");

    constexpr std::array<std::array<std::int8_t, 2>, 2> activations{{
        {{5, 6}},
        {{7, 8}},
    }};
    constexpr std::array<std::array<std::int8_t, 2>, 2> weights{{
        {{1, 2}},
        {{3, 4}},
    }};

    const auto matrix_partials = matmul_int8(activations, weights);
    require_equal(
        matrix_partials,
        std::array<std::array<std::int32_t, 2>, 2>{{
            {{23, 34}},
            {{31, 46}},
        }},
        "SystolicArray matrix partials"
    );

    // Identity weight tile에서는 array partial이 activation row와 같다.
    // 동일 accumulator state에 Bypass -> Multiply -> Shift를 순차 적용한다.
    constexpr std::array<std::array<std::int32_t, 2>, 2> tile_partials{{
        {{5, 6}},
        {{7, 8}},
    }};
    constexpr std::array<std::array<std::int8_t, 2>, 2> tile_scales{{
        {{2, 3}},
        {{1, -1}},
    }};
    constexpr std::array<std::array<std::int32_t, 2>, 2> zeros{};

    const auto bypass_state = apply_execution(
        VectorOp::Bypass,
        tile_partials,
        tile_scales,
        zeros,
        false
    );
    const auto multiply_state = apply_execution(
        VectorOp::Multiply,
        tile_partials,
        tile_scales,
        bypass_state,
        true
    );
    const auto shift_state = apply_execution(
        VectorOp::Shift,
        tile_partials,
        tile_scales,
        multiply_state,
        true
    );

    require_equal(
        bypass_state,
        std::array<std::array<std::int32_t, 2>, 2>{{
            {{5, 6}},
            {{7, 8}},
        }},
        "Core Bypass execution"
    );
    require_equal(
        multiply_state,
        std::array<std::array<std::int32_t, 2>, 2>{{
            {{15, 24}},
            {{14, 0}},
        }},
        "Core Multiply execution"
    );
    require_equal(
        shift_state,
        std::array<std::array<std::int32_t, 2>, 2>{{
            {{35, 72}},
            {{28, 4}},
        }},
        "Core Shift execution"
    );

    std::cout << "IM2P C++ REFERENCE SELF-TEST: PASS\n";
}

}  // namespace im2p

int main() {
    im2p::self_test();
    return EXIT_SUCCESS;
}
