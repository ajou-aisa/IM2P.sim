#pragma once

#include "frontend_rtl_fixture.hpp"
#include "rmd.hpp"

namespace im2p::gemmini_hp1 {
// Raw overload is retained only for frozen historical evidence objects.
void run_bound_rmd_ws_rtl_fixture(const Capability &, void *,
                                  FrontendRtlWorkExecute, RmdRawExecute);
void run_bound_rmd_ws_rtl_fixture(const Capability &, void *,
                                  FrontendRtlWorkExecute, RmdScuExecute,
                                  RmdRunExecute);
} // namespace im2p::gemmini_hp1
