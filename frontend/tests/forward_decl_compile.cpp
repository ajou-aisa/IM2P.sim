#include "im2p_gemmini_frontend.hpp"

#include <type_traits>

static_assert(!std::is_copy_constructible_v<im2p::gemmini::Run>);
static_assert(std::is_move_constructible_v<im2p::gemmini::ExecuteResult>);

int main() { return 0; }
