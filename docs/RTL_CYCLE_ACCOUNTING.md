# RTL logical-cycle accounting

For current Gemmini HP1 the timing authority is the integrated RTL, not wall-clock
measurements or a planner loop count. `MatmulCycleCounter` records the free-running
core counter at `io.work.fire && io.work.bits.firstLoop` and at
`bridge.io.logicalDone.valid`. The completed duration is `doneCycle - startCycle`.
Completion remains after required execution/writeback and final backing-store
completion. Final output validation is a separate required invariant.

The C ABI may cache a provisional counter at `im2p_start_matmul`. On completion,
`finish_stripe` replaces it with RTL start/done/elapsed. Use those completed values
for regression. The runtime clock still evaluates low/high/low with one positive
edge per tick and retains the original handshake/state-update phase ordering.

Per-stripe logical intervals, work/loop counts and load/store/scale request and
response counters must be compared under identical input, latency/backpressure,
profile and start/done definitions. Cycle goldens must not be updated to hide a
mismatch. The completed refactor recorded 268/268 exact work cases over six
profiles, with maximum absolute delta zero; its [report](GEMMINI_REFACTOR_REPORT.md)
is a phase record. Cleanup results are recorded in the cleanup report.

Planner byte fields describe request/buffer extents, not total repeated bus
traffic. Occupancy and overlap counters may overlap and are not additive parts
of elapsed time. Host provider work, operating-system scheduling and condition
variable waits do not establish NPU cycles or a unified CPU/NPU timeline.
No frequency, Fmax, TOPS or physical timing is derived here.

See [the detailed prepared boundary](GEMMINI_CYCLE_SIM_BOUNDARY.md) for unchanged
ownership and endpoint details, and [LEGACY_BSV cycle accounting](legacy/RTL_CYCLE_ACCOUNTING.md)
for the retained BSV counter/edge conventions. Do not apply one backend's interval
definition to the other.
