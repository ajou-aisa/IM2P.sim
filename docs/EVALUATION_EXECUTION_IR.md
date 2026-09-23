# Evaluation execution boundary

The existing `reconstruct.py` output remains a structural three-source dataset.
It is not directly schedulable: operation containers do not mean completed
outputs, and the historical PUBLISH dependency includes synchronous collection
ordering. No old dataset is rewritten or silently promoted.

`sim.cycle.execution_cli` adds a separate version-1 execution IR, service table,
and deterministic rational-time scheduler. Synthetic schedules remain diagnostic.
Certified scheduling requires the drained-sequence certificate and a validated
post-route operating clock; neither a user-supplied frequency nor a synthetic
schedule establishes paper latency.

## Required lifecycle supplement

`im2p-execution-lifecycle` binds immutable dataset and NPU-result SHA256s.
Version 1 preserves the blocking FULL contract; version 2 adds actual
STRIPE_PIPELINE source ownership. Both explicitly declare:

- `source_kind`: `SYNTHETIC` or `PRODUCER_DECLARED`;
- `operation_exit_policy=ALL_MEMBER_COMPLETIONS`;
- `publish_policy=NONBLOCKING_SUBMISSION`;
- `slot_release_policy=NPU_RESOURCE_READY`;
- `phase_graph_policy=EXPLICIT_EDGES`;
- `entry_dependencies`: one declaration for every operation container;
- `barriers`: explicit graph/phase causal boundaries;
- `call_slots`: every NPU call mapped to null, slot 0, or slot 1;
- `submission_order`: complete, unique NPU node IDs;
- `cpu_policy` and `worker_resources`: explicit measured metric/resource mapping.

The adapter replaces each operation container with an enter/exit pair. Input
dependencies wait for predecessor exits. Exits wait for their actual member
services and call completions. A stripe PUBLISH releases submission, not result
consumption; NPU acceptance, result-ready, and resource-ready remain distinct.
Declared submission ordering uses accepted milestones, not a guessed duration.
Functional-emulation and input WAIT nodes carry no service or resource claim,
but their causal dependencies remain.

The supplement cannot be manufactured from tensor shapes or file order.
`llama-eval-workload` now buffers a separate
`potal-execution-lifecycle/v1` (FULL) or `/v3` (PIPELINE) sidecar at actual phase
changes, dispatch begin/end, and sample completion. A read-only semantic-session counter binds each blocking
`llama_decode`/`llama_synchronize` dispatch to its exact completed graph window.
The sidecar is flushed only at finish, outside measured sampling and canonical
host intervals. It records successful synchronized completion, not CPU-emulation
duration.

`execution_lifecycle_cli` binds this sidecar, semantic graph, application tokens,
native executable/build receipt and official join/result hashes. The projection
uses the producer's verified complete-before-next-graph policy, never shapes.
Every sample is matched against its next decode input fingerprint. PIPELINE v3
also declares `REQUEST_START`, each measured `PREFILL_BATCH_READY`, final parent
geometry, exact fence work IDs, residual child bindings, and source-recorded
workspace/queue/capacity/stream/callback transitions. It does not claim RTL
acceptance during CPU-functional collection. The adapter validates parent row
coverage, stripe IDs, residual child ownership, queue credit, workspace reuse,
and fence membership. ExSIA workspace slot is not an NPU output slot; `call_slots`
remain null in this scenario. Frontend credit release depends on result-ready,
while the shared NPU resource remains reserved until resource-ready.

```bash
python3 -m sim.cycle.execution_lifecycle_cli \
  --sidecar execution-lifecycle.jsonl --semantic-graph semantic-graph.jsonl \
  --provenance collection-provenance.json --application application-cpu.jsonl \
  --dataset joined.jsonl.gz --join-summary join-summary.json \
  --npu-results npu-cycle-result.jsonl --worker-resources worker-resources.json \
  --cpu-policy THREAD_CPU_NS_GANG --sampler-resource cpu:0 --output lifecycle.json
```

Resource mapping and CPU metric selection are explicit caller scenario inputs.
They are not inferred from process-local thread IDs.

## CPU and application services

CPU policies are `THREAD_CPU_NS_GANG`, `HOST_ELAPSED_NS_GANG`, or
`CPU_WORK_CYCLES_GANG`. Each worker retains its original measurement fields,
selected source/unit/validity and resource. Workers start as an explicit gang;
individual resource releases and node completion use their durations, never the
sum as elapsed latency. Cycle conversion requires an explicit CPU frequency.
This is a declared service scenario, not a calibrated parallel CPU model.

PoTal `application-cpu.jsonl` is optional for synthetic adapter fixtures and
required for bound E2E execution. `lifecycle.application` contains its SHA256,
`sampler_policy=SINGLE_CALLING_THREAD`, resource, metric policy, expected sample
count, and ordered `steps`. Each step contains `sample_index`, `logits_ready`
execution IDs and `next_decode_entries` operation IDs. The final sample has no
next decode. Source rows must be PoTal `sample_accept` measurements with complete
ordered sample/phase coverage and one actual sampler thread. Sampling is added
once as `APPLICATION_CPU`; FullCPU sampling is never substituted.
PIPELINE v3 additionally requires version-2 application rows: one measured
`prefill_batch_prepare` per declared prefill dispatch, followed by sample rows.
`application:request:begin` is scheduled t0. Each preparation is its own
`APPLICATION_CPU` service before the matching dispatch; diagnostic JSON
construction and the numerical functional emulation are excluded. Old sample-only
sidecars cannot be promoted to this endpoint contract.

```bash
python3 -m sim.cycle.execution_cli adapt \
  --dataset joined.jsonl.gz --join-summary join-summary.json \
  --npu-results npu-cycle-result.jsonl --lifecycle lifecycle.json \
  --application application-cpu.jsonl --output execution-bundle.json
```

For large joined datasets, add `--streaming --output execution.sqlite` instead.
The SQLite adapter uses a 16 MiB page cache and retains every service, operation
member and dependency. It verifies the entire DAG with a bounded batch of ready
IDs. `nodes`, `services` and the authoritative `edges` table represent the same
execution contract; node JSON bodies omit dependencies because those live in
`edges`. This route removes the convenience JSON adapter's 100,000-row ceiling.
The same `schedule` CLI also accepts SQLite IR and writes a SQLite schedule.
Both formats use the shared `ServiceExecutor` for worker service, clock alignment,
and result/resource milestones; SQLite keeps dependency readiness and results on
disk. Use `--bundle execution.sqlite --output schedule.sqlite` with the same
explicit provider/scenario options below. `scheduler_sqlite.read_schedule_node`
reads an individual result, including application endpoints, without loading the
whole schedule. This storage path does not relax clock or service-proof gates.

Producer-derived FULL IR proves dispatch/graph completion and data dependencies.
It does not establish that independent nodes may overlap while a host thread is
blocked inside a FULL call. Host call-resource ownership and a validated target
dispatch scenario remain required before real timing publication. A serialized
reference scenario must be explicitly selected and source-checked; it is not
silently inferred from CPU-functional completion order.

## NPU service and scheduling

`NpuProvider.estimate(work, accepted_cycle)` receives exact profile/request
identity and the actual scheduled acceptance epoch. The response supplies
separate result-ready and resource-ready offsets. One NPU resource is shared by
dense/residual work. Slots are not released at result-ready by assumption.

`CycleServiceProvider` reuses the strict NPU trace parser, `model_document`, and
the additive `cli.estimate_service` C binding. It passes the original selected
geometry/runs, reference-memory timing, scheduled epoch and initial halves to
`im2p_cycle_estimate_service`. Returned halves carry into the next drained work.
Its identity digest includes profile, exact request, epoch, timing and halves.
No NPU equations, retiling, measured answers, or tensor computation are copied
into Python. Without three validated certificates it remains
`DIAGNOSTIC_SERVICE_API`. The current drained certificate validates fixed
two-work RTL fixtures and exposes `DRAINED_FIXTURE_PARITY`, not production
schedule admission. Actual producer-generated, same-RTL-instance sequence/phase
comparisons across the required profiles are still missing. Reconstructed
scheduling therefore rejects this certificate even though fixture estimates
remain available. See
`docs/HP1_SERVICE_CERTIFICATE.md` for its source/artifact binding and the
one-NPU, one-outstanding, drained reference-memory domain. A string label alone
cannot admit real scheduling.

```bash
python3 -m sim.cycle.execution_cli schedule \
  --bundle execution-bundle.json --cycle-library libim2p_cycle_model.dylib \
  --npu-trace npu-cycle-trace.jsonl --timing reference-memory.json \
  --initial-scratchpad-half 0 --initial-accumulator-half 0 \
  --frequency-hz 1000000000 --synthetic --output diagnostic-schedule.json
```

The frequency above is a synthetic scenario input, not a measured clock.
Alternatively `--phase-table` accepts only `SYNTHETIC_ONLY` finite samples keyed
by profile, exact request digest and accepted phase. Such tables cannot claim
general production service coverage.

Scheduling considers currently ready nodes, in declared order then stable ID.
It never reserves a future resource for an unready node. Time is exact
`Fraction` nanoseconds; NPU acceptance rounds upward to the next clock edge.
Independent CPU work can proceed before an NPU result; consumers wait for
result-ready, while the next NPU request waits for resource-ready. No isolated
work cache is reused across acceptance phases or buffer state.

Real reconstruction rejects absent validated operating clocks, a non-current
sequence provider, or unbound lifecycle data. Finite RTL sequence comparisons
do not independently certify arbitrary whole-model persistent scheduling.
`paper_latency_ready` remains false: application endpoints, actual target-host
measurements and the complete campaign are separate gates.

For certified scheduling, omit `--synthetic` and provide `--service-certificate`,
`--cycle-certificate`, `--run-aware-certificate`, `--clock-selection`, `--profile`,
the exact trace/library/timing, and initial halves. `verify-schedule` accepts
those same sources plus `--schedule` and `--bundle`; it recomputes every JSON or
SQLite scheduled node, including CPU/application endpoints, before a PoTal run
result may be published. SQLite verification streams observed rows against the
same scheduler, rather than trusting a stamped validation string.

## Verification

```bash
IM2P_CYCLE_LIBRARY=/absolute/path/libim2p_cycle_model.dylib \
python3 -m unittest sim.tests.cycle.test_execution_scheduler \
  sim.tests.cycle.test_execution_adapter sim.tests.cycle.test_execution_cli \
  sim.tests.cycle.test_execution_application sim.tests.cycle.test_execution_service_api \
  sim.tests.cycle.test_scheduler_sqlite
```

Tests cover ready-set scheduling, worker vectors, result/resource tails, rational
clock alignment, phase/profile negatives, functional exclusion, operation exits,
nonblocking PUBLISH, application ownership, missing/duplicate dependencies,
fresh-output behavior and the actual C service API. They are not a Jetson or
paper campaign.

The native producer recorder can be verified with `test-evaluation-lifecycle`;
set `POTAL_LIFECYCLE_FIXTURE` to that executable when running
`sim.tests.cycle.test_execution_lifecycle`. `test_execution_stream` exercises
100,005 source records without dropping rows or materializing the host corpus.
