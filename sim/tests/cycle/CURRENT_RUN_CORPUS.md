# Current production run-aware input authority

The default `load_manifest()` and certifier route retain the historical,
byte-pinned `production_run_aware_corpus.json`. Its old producer source gate
still rejects a changed producer. Historical raw files and certificates must
not be updated to current hashes.
The additive selector is `current_run_corpus.load_selected_manifest`; the
original `production_run_work.py` producer/runner source bytes stay unchanged.

The explicit current route is limited to six A4/A8 × D16/D32/D64 profiles and
the seven `PRODUCTION_CASE_NAMES` (42 inputs). The eighth native
`numeric_zero_first_run` case is supplemental. The new route requires fresh
native `production-fixtures` captures that are byte-identical to the historical
42 inputs, plus the reviewed current producer source identity. Input differences
stop issuance and need a separately reviewed corpus revision.

```sh
python3 -B -m sim.cycle.current_run_corpus --capture-index /absolute/capture-index.json --out /new/current-corpus.json
python3 -B -m sim.cycle.current_run_corpus --validate /new/current-corpus.json
python3 -B sim/tests/cycle/certify_production_run_aware.py --inputs /new/rtl-inputs.json --library /current/libim2p_cycle_model.dylib --corpus /new/current-corpus.json --out /new/certificate
```

Issuance validates input equivalence only; it does not run RTL or establish a
PASS certificate. Run the 42 actual consumer cases after source/corpus review.
The certificate records the selected corpus path and hash. Existing downstream
consumers call `validate_run_certificate`, which reopens and validates that same
authority, its native build/capture provenance, and raw RTL/model comparisons.

The capture index uses schema `im2p-production-run-aware-capture`, version 1,
role `PRODUCTION_NATIVE_GEMMINI_HP1`, exact `profiles`, `expected_case_names`,
`case_count: 42`, six ordered `producers`, and 42 unique `cases`. Each case has
`profile`, `case`, and a `fixture` path/hash reference.

Each producer has `profile`, absolute `build_root` and `fixture_root`, path/hash
references `executable`, `real_lib_manifest`, `build_receipt`, `capture_receipt`,
`capture_context`, `precompile_snapshot`, plus `compile_inputs_before`,
`compile_inputs`, `source_before`, `source_after`, `build_artifacts`, and
`runtime_dependencies`. The actual retained compiler `.o.d` closure and build
artifacts determine required keys; they are not derived from a passed subset.
Only the selected executable's transitive CMake link graph supplies objects,
archives, and shared libraries. Each object's depfile must name that object
and include the actual `-c` translation unit from its compile command.
Unrelated built targets cannot supply missing producer inputs or archive links.
The precompile snapshot records `inputs` and exact
`compiler_dependency_commands` before compilation. Generated-header changes
between snapshot and compilation reject admission too. Materialize such build
inputs before taking the snapshot; never retroactively replace its hashes.

Build/capture receipts require successful, reaped executions of the real target
and `--case production-fixtures`. Capture context binds the receipt hash and
actual `environment.GEMMINI_LOG_DIR`. It must also reference an immutable
`pre_capture_context` containing `binary_sha256`, `command`, `cwd`,
`environment`, `build_artifacts`, and `runtime_dependencies` taken before the
capture. Binary bytes, full selected build/runtime maps, command, and execution
directory must agree across that boundary. The validator checks CYCLE_SIM=0,
GEMMINI_HP1, WS, dimensions/widths, executable and runtime byte identities,
the selected numerical library's current source fingerprint, actual archive
linkage, and current compiler bytes against the earlier numerical tool identity.
The native C/C++ compilers must share that recorded executable identity; this
matches the audited macOS route and deliberately rejects unreviewed toolchains.

Both issuance and successful certification validate before atomic publication
and refuse an existing destination. Unit tests with a mocked capture validator
exercise serialization/selection only; they are never native or RTL evidence.
