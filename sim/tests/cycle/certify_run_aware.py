#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

# ─── How to run ───
# 1. Install uv: curl -LsSf https://astral.sh/uv/install.sh | sh
# 2. Run: uv run sim/tests/cycle/certify_run_aware.py --rtl-root DIR --library FILE --out DIR
# ──────────────────

from __future__ import annotations

import argparse
from collections import Counter
import ctypes as C
from hashlib import sha256
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from sim.cycle import cli
from sim.tests.cycle.rtl_hardening import normalized_model_events
from sim.tests.cycle.run_aware_events import events_exact, parse_events_csv
from sim.tests.cycle.run_aware_fixture import (
    Case, Corpus, Observation, check_hash, load_manifest, parse_rtl_log,
)

WORKSPACE = ROOT.parent
EVIDENCE = WORKSPACE / ".omo/evidence/rmd-cross-block-compact-scu"
MANIFEST = Path(__file__).with_name("run_aware_corpus.json")


class CompactRun(C.Structure):
    _fields_ = [(name, C.c_uint32) for name in (
        "original_block_id", "original_k_mask", "compact_k_begin", "compact_k_count")]


class CompactRuns(C.Structure):
    _fields_ = [("version", C.c_uint32), ("struct_size", C.c_uint32),
                ("original_k", C.c_uint32), ("run_count", C.c_size_t),
                ("runs", C.POINTER(CompactRun))]


def estimate_runs(library: Path, profile: str, case: Case, corpus: Corpus) -> dict:
    lib = cli.load_library(library)
    lib.im2p_cycle_estimate_runs.argtypes = [
        C.c_void_p, C.POINTER(cli.Request), C.POINTER(CompactRuns), C.POINTER(cli.Result)]
    lib.im2p_cycle_estimate_runs.restype = C.c_int
    bits, dim = int(profile[1]), int(profile.split("-d")[1].split("-")[0])
    selection = cli.ProfileSelection(bits, bits, dim, cli.Scu.HP1_LEFT_SHIFT)
    resolved = cli.resolve_profile(selection, cli.DEFAULT_CATALOG,
                                   ROOT / "config/gemmini_host_memory_contracts" / f"{profile}.json")
    memory, hardware = resolved.memory, resolved.catalog
    cfg, request = cli.Config(), cli.Request()
    lib.im2p_cycle_model_config_init(C.byref(cfg))
    lib.im2p_cycle_request_init(C.byref(request))
    if cfg.struct_size != C.sizeof(cfg) or request.struct_size != C.sizeof(request):
        raise ValueError("cycle C library/Python ABI mismatch")
    cfg.hardware = cli.Hardware(bits, bits, dim, hardware.block_size,
        hardware.accumulator_bits, memory.bank_count, memory.bank_rows,
        memory.accumulator_rows, memory.scratchpad_row_bytes,
        memory.accumulator_row_bytes, hardware.scratchpad_read_delay,
        hardware.accumulator_latency)
    for key, value in corpus.timing.items():
        setattr(cfg.timing, key, value)
    request.m = request.n = 1
    request.k = case.k
    request.tile_i = request.tile_j = 1
    request.tile_k = 2
    request.accepted_cycle = 1
    request.logical_work_id = 71 + case.runs[1].original_block_id + case.runs[0].compact_k_count
    request.record_events = 1
    request.submission = 0
    spans = (CompactRun * 2)(*(CompactRun(run.original_block_id, run.original_k_mask,
                                           run.compact_k_begin, run.compact_k_count)
                              for run in case.runs))
    runs = CompactRuns(1, C.sizeof(CompactRuns), case.original_k, 2, spans)
    handle = lib.im2p_cycle_model_create(C.byref(cfg))
    if not handle:
        raise ValueError(f"cycle model rejected resolved profile: {profile}")
    try:
        answer = cli.Result()
        status = lib.im2p_cycle_estimate_runs(handle, C.byref(request), C.byref(runs), C.byref(answer))
        if status:
            error = lib.im2p_cycle_model_error(handle).decode("utf-8", errors="replace")
            raise ValueError(f"cycle model status={status}: {error}")
        events = []
        for index in range(lib.im2p_cycle_model_event_count(handle)):
            event = cli.Event()
            if lib.im2p_cycle_model_event(handle, index, C.byref(event)):
                raise ValueError("cycle event retrieval failed")
            events.append({"cycle": event.cycle,
                           "type": lib.im2p_cycle_event_name(event.type).decode("utf-8")})
        result = {key: getattr(answer, key) for key in cli.RESULT_FIELDS}
        return {"profile": profile, "case": case.name,
                "request": {"m": 1, "n": 1, "k": case.k, "tile_i": 1, "tile_j": 1,
                            "tile_k": 2, "accepted_cycle": 1, "submission": "planner-blocks",
                            "logical_work_id": request.logical_work_id, "runs": [
                                [run.original_block_id, run.original_k_mask,
                                 run.compact_k_begin, run.compact_k_count] for run in case.runs],
                            "original_k": case.original_k},
                "result": result, "events": events}
    finally:
        lib.im2p_cycle_model_destroy(handle)


def first_event_difference(model: list[tuple[int, str]], rtl: list[tuple[int, str]]) -> dict | None:
    left, right = sorted(model), sorted(rtl)
    for index in range(max(len(left), len(right))):
        a = left[index] if index < len(left) else None
        b = right[index] if index < len(right) else None
        if a != b:
            return {"index": index, "model": a, "rtl": b}
    return None


def compare_case(observed: Observation, model: dict, case: Case,
                 rtl_events: tuple[tuple[int, str], ...] | None, dim: int) -> dict:
    result = model["result"]
    event_counts = Counter(event["type"] for event in model["events"])
    expected = {
        "cycles": result["total_cycles"], "loops": result["loop_count"],
        "loads": event_counts["load_issue"], "executes": event_counts["execute_issue"],
        "stores": event_counts["store_issue"], "commits": event_counts["accumulator_commit"],
        "scale_reads": result["scale_request_count"],
        "scale_responses": result["scale_response_count"],
        "completions": result["store_response_count"],
    }
    differences = {name: {"rtl": observed.values[name], "model": value}
                   for name, value in expected.items() if observed.values[name] != value}
    if observed.values["start"] != 1:
        differences["start"] = {"rtl": observed.values["start"], "model": 1}
    if result["start_cycle"] != 1:
        differences["start"] = {"rtl_normalized": 1, "model": result["start_cycle"]}
    normalized_done = observed.values["done"] - observed.values["start"] + 1
    if result["done_cycle"] != normalized_done:
        differences["done"] = {"rtl": normalized_done, "model": result["done_cycle"]}
    fragments = sum((run.compact_k_count + min(dim, 32) - 1) // min(dim, 32)
                    for run in case.runs)
    if result["fragment_count"] != fragments:
        differences["fragments"] = {"rtl_geometry": fragments, "model": result["fragment_count"]}
    if result["planner_loop_count"] != len(observed.loops):
        differences["planner_loops"] = {"rtl": len(observed.loops), "model": result["planner_loop_count"]}
    model_events = normalized_model_events(model["events"])
    observed_events = list(rtl_events) if rtl_events is not None else []
    event_exact = events_exact(model_events, observed_events) if rtl_events is not None else False
    exact = not differences and event_exact
    return {"profile": observed.profile, "case": case.name, "status": "PASS" if exact else "FAIL",
            "rtl_admitted": True, "model_admitted": True, "endpoint_counter_exact": not differences,
            "selected_event_multiset": event_exact if rtl_events is not None else "NOT_OBSERVED",
            "selected_event_comparison": {
                "model_count": len(model_events), "rtl_count": len(observed_events),
                "model_sha256": sha256(json.dumps(sorted(model_events)).encode()).hexdigest(),
                "rtl_sha256": sha256(json.dumps(sorted(observed_events)).encode()).hexdigest()
                    if rtl_events is not None else "NOT_OBSERVED",
                "first_difference": first_event_difference(model_events, observed_events)
                    if rtl_events is not None else "NOT_OBSERVED"},
            "differences": differences, "delta_cycles": result["total_cycles"] - observed.values["cycles"]}


def certify(rtl_root: Path, library: Path, out: Path, task07: Path, task08: Path) -> dict:
    corpus = load_manifest(MANIFEST)
    task7, task8 = json.loads(task07.read_text()), json.loads(task08.read_text())
    library_sha = check_hash(library, task7["library"]["sha256"])
    source_hashes = {name: check_hash(WORKSPACE / name, digest)
                     for name, digest in task8["source_sha256"].items()}
    cycle_source_hashes = {name: check_hash(ROOT / name, digest)
                           for name, digest in task7["source_sha256"].items()}
    if (task8["artifact_role"] != "FIXTURE_ONLY" or
            task8["case_names"] != [case.name for case in corpus.cases]):
        raise ValueError("task-08 fixture provenance/case set mismatch")
    profiles = {row["profile"]: row for row in task8["profiles"]}
    if set(profiles) != set(corpus.profiles) or len(task8["profiles"]) != 6:
        raise ValueError("task-08 profile set mismatch")
    rtl_answers, model_answers, cases = [], [], []
    for profile in corpus.profiles:
        metadata = profiles[profile]
        root = rtl_root / profile
        bits, dim = profile[1], int(profile.split("-d")[1].split("-")[0])
        if metadata["selected_top"] != f"IM2PGemminiWSHP1A{bits}W{bits}D{dim}":
            raise ValueError(f"wrong selected RTL top: {profile}")
        raw = root / "isolated/run-aware-manual.log"
        events_path = root / "isolated/events.csv"
        hashes = {
            "resolved_profile": check_hash(root / "resolved-profile.json", metadata["resolved_profile_sha256"]),
            "filelist": check_hash(root / "filelist.f", metadata["filelist_sha256"]),
            "selected_top": check_hash(root / "rtl" / f'{metadata["selected_top"]}.sv',
                                       metadata["selected_top_sha256"]),
            "rtl_binary": check_hash(root / "host-build/run-aware-rtl/VIM2PGemminiWSHP1RtlTest",
                                     metadata["rtl_binary_sha256"]),
            "rtl_log": check_hash(raw, metadata["isolated_manual_sha256"]),
            "events_csv": check_hash(events_path, metadata["isolated_events_sha256"])
                if events_path.is_file() else "NOT_OBSERVED",
        }
        observations = parse_rtl_log(raw.read_text(), profile, corpus)
        observed_events = parse_events_csv(events_path.read_text(), corpus, profile) if events_path.is_file() else None
        for index, (case, observed) in enumerate(zip(corpus.cases, observations)):
            rtl_answers.append({"profile": profile, "case": case.name, "values": observed.values,
                                "loops": observed.loops, "source_hashes": hashes,
                                "selected_events": observed_events[index]
                                    if observed_events is not None else "NOT_OBSERVED",
                                "events_csv": str(events_path) if observed_events is not None else "NOT_OBSERVED"})
            model = estimate_runs(library, profile, case, corpus)
            model_answers.append(model)
            cases.append(compare_case(observed, model, case,
                                      observed_events[index] if observed_events is not None else None, dim))
    out.mkdir(parents=True, exist_ok=False)
    for name, answer in (("rtl-answers.json", rtl_answers), ("model-answers.json", model_answers)):
        (out / name).write_text(json.dumps(answer, indent=2, sort_keys=True) + "\n")
    first = next((row for row in cases if row["status"] != "PASS"), None)
    result = {"status": "PASS" if first is None else "FAIL", "artifact_role": "FIXTURE_ONLY",
              "production_one_logical_cross_block_gemm": "NOT_READY",
              "expected": corpus.case_count, "attempted": len(cases),
              "rtl_admitted": sum(row["rtl_admitted"] for row in cases),
              "model_admitted": sum(row["model_admitted"] for row in cases),
              "exact": sum(row["status"] == "PASS" for row in cases),
              "selected_event_multisets_exact": sum(row["selected_event_multiset"] is True for row in cases),
              "max_abs_delta_cycles": max(abs(row["delta_cycles"]) for row in cases),
              "first_mismatch": first, "case_key_sha256": corpus.case_key_sha256,
              "manifest_sha256": sha256(MANIFEST.read_bytes()).hexdigest(),
              "library_sha256": library_sha, "source_sha256": source_hashes,
              "cycle_source_sha256": cycle_source_hashes, "cases": cases}
    (out / "run-aware-certificate.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rtl-root", type=Path, required=True)
    parser.add_argument("--library", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--task07", type=Path, default=EVIDENCE / "task-07.json")
    parser.add_argument("--task08", type=Path, default=EVIDENCE / "task-08.json")
    args = parser.parse_args()
    try:
        result = certify(args.rtl_root, args.library, args.out, args.task07, args.task08)
    except (OSError, ValueError, KeyError, TypeError) as error:
        if not args.out.exists():
            args.out.mkdir(parents=True)
            failure = {"status": "FAIL", "artifact_role": "FIXTURE_ONLY",
                       "expected": 48, "attempted": 0, "rtl_admitted": 0,
                       "model_admitted": 0, "exact": 0,
                       "selected_event_multisets_exact": 0,
                       "max_abs_delta_cycles": None,
                       "first_mismatch": {"reason": str(error)}}
            (args.out / "run-aware-certificate.json").write_text(json.dumps(failure, indent=2) + "\n")
        print(f"RUN_AWARE_CERTIFICATE_FAILED: {error}", file=sys.stderr)
        return 1
    print(json.dumps({key: result[key] for key in ("status", "expected", "attempted", "exact",
                       "max_abs_delta_cycles", "first_mismatch")}))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
