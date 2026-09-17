# Gemmini scheduling and cycle-model boundary

This document preserves the integrated numerical RTL/planner boundary established
by the refactor. The separate single-GEMM value-free model is now implemented in
`sim/cycle/`; [GEMMINI_CYCLE_MODEL.md](GEMMINI_CYCLE_MODEL.md) defines its API,
regression timing profile, explicit submission-framing distinction and scoped RTL
certificates. `regression-tiles` remains 268/268 exact. Production
`planner-blocks` is separately 262/262 exact over the representable subset of the
same corpus, with six K8256 cases explicitly excluded by the 256-entry global
scale-address contract and rejected by the model. RMD_RAW timing is separately
certified equivalent in 24/24 paired cases over the documented compact raw domain.
All event certificates mean selected per-cycle event-type multiset equality, not
full signal/payload/event-stream equality. Numerical RTL remains the golden.
Op-trace replay, CPU/NPU system timelines and Fmax/resource/TOPS remain
unimplemented; the refactor and cleanup reports retain their historical
phase-specific states.

## Inputs and dependency direction

The value-free consumer needs GEMM M/N/K, the resolved hardware profile, the
existing tile plan, the actual published stripe range/height, and layout/stride
facts that change memory requests. It does not need activation values, weight
values, SCU result values, main-versus-RMD algorithm labels, llama layer semantics,
CPU timing, or C ABI callback implementations.

The current implementation has these boundaries:

```
scuCore (SCU arithmetic, scale protocol/storage, reusable control primitives)
   ^
gemminiIntegration (Gemmini output metadata, writeback and completion)
   ^
root standalone (HostCommandBridge, ScaleBackingLoader, backing adapter/top)
   ^
Verilator runtime / stable C ABI

sim/common/gemmini_schedule.{hpp,cpp} <- integrated runtime
                                   <- single-GEMM cycle model (sim/cycle/)
```

The four SBT source sets are declared in `src/gemmini/build.sbt`. The `diagnostics` project now contains only lower-level tests; its production
source set is empty. The alternate standalone GEMM top and its elaboration option
have been removed. ScratchpadBankHarness preserves the packed-bank isolation and
response-order checks; integrated tests cover the removed top's numerical work.
SCU.scala still takes only `partialWidth: Int`. Scale protocol messages take a
lane count, not a resolved profile or a host descriptor. Gemmini integration uses
Hp1LoopMetadata; only HostCommandBridge's derived Hp1LoopDescriptor adds backing
addresses, host ownership, and logical host work identity.

## One configuration, checked at boundaries

`config/gemmini_hp1_profiles.json` plus the selected JSON under
`config/gemmini_host_memory_contracts/` are resolved by
`scripts/gemmini_resolve_profile.py`. `gemmini_build.py` writes
`resolved-profile.json`; `gemmini_hardware_contract.py` derives
`resolved-hardware.properties` and `im2p_gemmini_hardware.h` from that same object.
Neither derived format is a second resolver.

The checked facts are A/W bits, DIM, block size, array partial width, accumulator
bits, scratchpad bank count and rows, total accumulator rows, scratchpad and
accumulator row bytes, scratchpad read delay, and accumulator latency.
HardwareContract verifies these against the instantiated Gemmini configuration
before standalone elaboration. Missing/mismatched facts fail closed. The C++
bridge asserts generated model/profile versus simulator macros and planner block
size. The cache builder copies the generated header beside the model; content
identity includes the resolver, contract serializer, source sets, patches,
planner, packing helper and backend sources.

The actual Scala timing values still originate in UpstreamWsConfig (read delay 4,
accumulator latency 2). They are explicitly asserted equal to resolved JSON,
not silently treated as a second authoritative configuration. SCU arithmetic
never loads a properties file. The RTL array partial width and the full-INT32
software/RMD transport width are different facts and are not conflated.

## Pure planning API

`im2p::gemmini::ScheduleConfig` contains GemmShape, HardwareShape, TilePlan and
Layout. HardwareShape exposes DIM, matched operand width and the fixed block-32
contract. Booleans describe existing execution facts: whether K is split at block
boundaries, whether scales are fetched, and whether the first contribution
accumulates. They are not algorithm names or numerical values.

`plan_loop(config, stripe_end, cursor)` returns a LoopPlan with logical origin,
valid M/N/K extents, padded M/N/K extents, fragment identity/count, first/last work,
first/accumulate/final contribution flags, scale-context indices/release extent,
packed operand extents, host payload extents and final output payload extent.
`advance_loop` is the only production implementation of K-fast, then J, then I
traversal. `plan_fragment` describes the existing ExecuteController context order
(I-fast, then J, then K), output index, scale index and contribution intent.
DIM64 still has a 32-element valid reduction within its padded 64-element work;
DIM16 can contribute twice inside a block32. No different numerical algorithm is
introduced.

The cursor order is local to a published stripe. A caller combines its own work/
stripe identity with that ordinal for a logical work sequence. Hardware's current
16-bit fragment encoding is preserved, including narrowing; representability is
not silently redesigned. TilePlan.stripe_rows retains the host plan, but actual
stripe ranges come from publication. The planner does not invent publication
order, buffer ownership, or lookahead.

`activation_read`, `weight_read`, and `scale_read` map packed request offsets to
base-free ReadExtent objects. A valid empty extent is synthesized padding, not a
host request. Backing object base addresses are added by the runtime. Host A4 and
A8 scalar operands still occupy one signed byte before hardware packing. Output
payload counts include only valid final stores, not row-stride gaps.

These byte counts are **request/buffer extents, not measured total bus traffic**.
Repeated requests, actual arbitration, stalls, queue occupancy, overlap and
completion remain RTL facts. The separate timing model consumes this boundary,
but must not mistake unique logical payload for a DMA transaction count.

The production runtime calls these helpers when driving work and resolving reads.
Golden schedule digests were captured from the unmodified runtime before editing:
4,608 configurations / 12,700 loops across all six profiles. Tests retain only the
golden digests, not a duplicate production scheduling implementation. Fragment,
extent, strided/padded access and all signed A4/A8 packing codes are also tested.

## Existing logical cycle interval (unchanged)

In UpstreamWsHp1Top, start is `io.work.fire && io.work.bits.firstLoop`; done is
`bridge.io.logicalDone.valid`. MatmulCycleCounter latches the current coreCycle on
those accepted events and reports `doneCycle - startCycle`. The host bridge does
not expose architectural completion before the required execution/writeback and
store completion conditions. Final-only numerical output remains a separate
required invariant.

C ABI `im2p_start_matmul` initially records the current core counter as a provisional
value. On logical completion, `finish_stripe` replaces the cached start/done/elapsed
values with the RTL's startCycle/doneCycle/elapsedCycles. Those completed values,
not the host call's wall-clock or a loop count, define the measured interval.
Each published stripe has the existing logical interval; host aggregation and
multiple model contexts must not be silently replaced by a shared CPU/NPU timebase.

The runtime still evaluates low/high/low and advances one positive edge per tick.
It samples handshakes before that edge, then performs the existing state updates.
One outstanding backing read and one backing write, two stripe slots, release
ordering, generation advancement on every attempted drive (even stalled), reset
sequence, and all existing ready/response phases are intentionally preserved.
There is no added latency knob, queue-depth change, overlap optimization, or
normalization of changed cycle goldens.

## Reproduction and limits

`make gemmini-schedule-test` needs a C++17 compiler and Python, not SBT, Verilator,
or numerical buffers. The integrated matrix and Scala commands are in the root
README. The independent RTL fixture remains `fpga/gemmini_hp1/host/test_ws_rtl.cpp`.
`tests/gemmini_runtime_fixture.cpp` services the production C ABI using the existing
frontend and RMD numerical fixtures; it does not implement a MAC/SCU oracle.

The historical LEGACY_BSV backend, its scheduling logic and numerical tests remain
supported separately. This preparation does not claim that every legacy frontend
operation is accepted by GEMMINI_HP1. In particular, the existing main_external
frontend regression requests operation 3 while this integrated bridge admits HP1
operation 5 or raw operation 0; that baseline failure is not bypassed by this
refactor. See the report for precise passing and failing gates. The separate cycle library is now available under the explicitly scoped
[cycle-model contract](GEMMINI_CYCLE_MODEL.md); it does not extend numerical
backend admission or implement multi-operation/system simulation.
