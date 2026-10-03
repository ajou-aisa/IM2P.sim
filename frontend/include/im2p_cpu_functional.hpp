#pragma once

#if defined(IM2P_CPU_FUNCTIONAL_TEST_HOOKS)
#include "im2p_sim.h"
#endif

namespace im2p::cpu_functional {
using TimingCallback = void (*)(void *, const char *, bool) noexcept;
struct TimingObserver {
  TimingCallback callback = nullptr;
  void *context = nullptr;
};
TimingObserver set_timing_observer(TimingObserver observer) noexcept;
#if defined(IM2P_CPU_FUNCTIONAL_TEST_HOOKS)
using DispatchCallback = void (*)(void *, const im2p_matmul_desc_t &,
                                 const im2p_production_geometry_v1_t &,
                                 const im2p_compact_runs_t *) noexcept;
void set_dispatch_observer(DispatchCallback callback, void *context) noexcept;
#endif
class TimingRegistration {
public:
  explicit TimingRegistration(TimingObserver observer) noexcept
      : previous_(set_timing_observer(observer)) {}
  ~TimingRegistration() { set_timing_observer(previous_); }
  TimingRegistration(const TimingRegistration &) = delete;
  TimingRegistration &operator=(const TimingRegistration &) = delete;

private:
  TimingObserver previous_;
};
} // namespace im2p::cpu_functional
