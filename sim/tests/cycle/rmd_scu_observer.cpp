#include "rmd_scu_observer.h"
#include "../../backends/gemmini_hp1/runtime.hpp"
#include <VIM2PGemminiWSHP1Sim___024root.h>

#if defined(IM2P_VERILATOR_TEST_HOOKS)
namespace {
im2p_rmd_scu_observer_fn callback = nullptr;
void *callback_context = nullptr;
} // namespace
extern "C" void
im2p_test_set_rmd_scu_observer(im2p_rmd_scu_observer_fn observer,
                               void *context) {
  callback = observer;
  callback_context = context;
}

#define IM2P_TAP_NAME_(bits, dim, field)                                       \
  IM2PGemminiWSHP1A##bits##W##bits##D##dim##__DOT__control__DOT__##field
#define IM2P_TAP_NAME(bits, dim, field) IM2P_TAP_NAME_(bits, dim, field)
#define TAP(field) t.rootp->IM2P_TAP_NAME(IM2P_ACTIVATION_BITS, IM2P_DIM, field)
namespace im2p::gemmini_hp1 {
void observe_rmd_scu(const Runtime &r) {
  if (!callback)
    return;
  const auto &t = r.top;
  const bool active[] = {
      bool(t.io_work_valid && t.io_work_ready),
      bool(t.io_events_loadIssued),
      bool(t.io_events_executeIssued),
      bool(t.io_events_storeIssued),
      bool(t.io_events_outputContextIssued),
      bool(t.io_events_loadDmaAccepted),
      bool(t.io_readRequest_valid && t.io_readRequest_ready),
      bool(t.io_readBeat_valid && t.io_readBeat_ready),
      bool(t.io_events_scratchpadReadAcceptedBankMask),
      bool(TAP(execute__DOT__mesh__DOT__input_next_row_into_spatial_array)),
      bool(TAP(_execute_io_completed_valid)),
      bool(t.io_events_rawRow),
      bool(t.io_events_scaledRow),
      bool(t.io_events_accumulatorCommitted),
      bool(t.io_events_storeDmaAccepted),
      bool(t.io_writeRequest_valid && t.io_writeRequest_ready),
      bool(t.io_writeCompletion_valid && t.io_writeCompletion_ready),
      bool(t.io_loopDone_valid && t.io_loopDone_ready),
      bool(t.io_logicalDone_valid)};
  im2p_rmd_scu_observation_t x{};
  x.cycle = t.io_coreCycle;
  x.dim = IM2P_DIM;
  for (unsigned i = 0; i < sizeof(active) / sizeof(active[0]); ++i)
    if (active[i])
      x.event_mask |= uint64_t{1} << i;
  if (!x.event_mask)
    return;
  if (t.io_events_rawRow) {
    // Pinned Verilator queue layout: words 0..DIM-1 are data lanes.
    // The certification records the generated-source hash and checks the tap.
    const auto &data = TAP(
        writeback__DOT__writes__DOT__queue__DOT____Vcellinp__ram_ext__W0_data);
    for (unsigned i = 0; i < IM2P_DIM; ++i)
      x.scu_data[i] = static_cast<int32_t>(data[i]);
  }
  callback(callback_context, &x);
}
} // namespace im2p::gemmini_hp1
#undef TAP
#undef IM2P_TAP_NAME
#undef IM2P_TAP_NAME_
#endif
