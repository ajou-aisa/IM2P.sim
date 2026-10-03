#include "accepted_work_observer.h"
#include "../../backends/gemmini_hp1/runtime.hpp"

#if defined(IM2P_VERILATOR_TEST_HOOKS)
namespace {
im2p_accepted_work_observer_fn callback = nullptr;
void *callback_context = nullptr;
} // namespace
extern "C" void
im2p_test_set_work_observer(im2p_accepted_work_observer_fn observer,
                            void *context) {
  callback_context = context;
  callback = observer;
}
namespace im2p::gemmini_hp1 {
void observe_geometry(const Runtime &r, std::uint64_t event,
                      const im2p_production_geometry_v1_t *geometry) {
  if (!callback)
    return;
  const auto &t = r.top;
  const auto &l = r.current_loop;
  im2p_accepted_work_observation_t x{};
  x.event = event;
  x.cycle = t.io_coreCycle;
  x.explicit_geometry = r.explicit_geometry;
  x.work_ready = t.io_work_ready;
  x.release_column = t.io_scaleRelease_bits_column;
  x.release_address = t.io_scaleRelease_bits_address;
  x.release_generation = t.io_scaleRelease_bits_generation;
  x.geometry = geometry ? *geometry : r.current_stripe.geometry;
  x.source_m = r.descriptor.row_count;
  x.source_n = r.descriptor.column_count;
  x.source_k = r.descriptor.reduction_count;
  x.activation_host_stride = r.descriptor.activation_row_stride;
  x.weight_host_stride = r.descriptor.weight_row_stride;
  x.scale_host_stride = r.descriptor.scale_row_stride;
  x.output_host_stride = r.descriptor.output_row_stride;
  x.lowerer_tile_i = r.schedule.tile.tile_i;
  x.lowerer_tile_j = r.schedule.tile.tile_j;
  x.lowerer_tile_k = r.schedule.tile.tile_k;
  x.stripe_id = r.current_stripe.id;
  x.stripe_row_begin = r.current_stripe.row_begin;
  x.stripe_row_count = r.current_stripe.row_count;
  x.host_slot = t.io_work_bits_hostSlot;
  x.i = l.i;
  x.j = l.j;
  x.k = l.k;
  x.rows = l.is;
  x.columns = l.js;
  x.reduction = l.ks;
  x.order = l.order;
  x.fragment_count = l.fragment_count;
#define FIELD(member, signal) x.member = t.signal
  FIELD(max_i, io_work_bits_maxI);
  FIELD(max_j, io_work_bits_maxJ);
  FIELD(max_k, io_work_bits_maxK);
  FIELD(pad_i, io_work_bits_padI);
  FIELD(pad_j, io_work_bits_padJ);
  FIELD(pad_k, io_work_bits_padK);
  FIELD(a_address, io_work_bits_aAddress);
  FIELD(b_address, io_work_bits_bAddress);
  FIELD(c_address, io_work_bits_cAddress);
  FIELD(scale_address, io_work_bits_scaleBackingAddress);
  FIELD(a_stride, io_work_bits_aStrideBytes);
  FIELD(b_stride, io_work_bits_bStrideBytes);
  FIELD(c_stride, io_work_bits_cStrideBytes);
  FIELD(scale_base, io_work_bits_scaleBase);
  FIELD(scale_generation, io_work_bits_scaleGeneration);
  FIELD(fragment_base, io_work_bits_fragmentBase);
  FIELD(work_base, io_work_bits_workBase);
  FIELD(accumulate, io_work_bits_accumulate);
  FIELD(final_fragment, io_work_bits_finalFragment);
  FIELD(first_loop, io_work_bits_firstLoop);
  FIELD(final_loop, io_work_bits_finalLoop);
  FIELD(logical_work_id, io_work_bits_logicalWorkId);
  FIELD(rmd_raw, io_work_bits_rmdRaw);
  FIELD(start_cycle, io_startCycle);
  FIELD(done_cycle, io_doneCycle);
  FIELD(elapsed_cycles, io_elapsedCycles);
  FIELD(load_requests, io_loadRequests);
  FIELD(load_responses, io_loadResponses);
  FIELD(store_requests, io_storeRequests);
  FIELD(store_responses, io_storeResponses);
  FIELD(scale_requests, io_scaleReadRequests);
  FIELD(scale_responses, io_scaleReadResponses);
#undef FIELD
  callback(callback_context, &x);
}
} // namespace im2p::gemmini_hp1
#endif
