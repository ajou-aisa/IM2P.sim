#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

# ─── How to run ───
# uv run sim/tests/cycle/certify_production_run_aware.py --inputs FILE --library FILE --out DIR
# ──────────────────

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from hashlib import sha256
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
WORKSPACE = ROOT.parent
sys.path.insert(0, str(ROOT))
from scripts.gemmini_rtl_build_binding import verify_build
from sim.cycle.certificate_contract import array_value, read_document
from sim.cycle.npu_trace_schema import Record, object_value, text
from sim.cycle.run_aware_certificate import validate_run_certificate
from sim.cycle.run_aware_production_evidence import (
    compare_case,
    compare_events,
    parse_rtl_events,
    parse_rtl_log,
)
from sim.tests.cycle.production_run_work import (
    PRODUCTION_MANIFEST_SHA256,
    PRODUCTION_SOURCE_FILES,
    Artifact,
    Certificate,
    CertificateCase,
    estimate_case,
    load_manifest,
    read_work_fixture,
)
from sim.tests.cycle.rtl_hardening import normalized_model_events

RTL_BINARY_REL = Path("host-build/run-aware-rtl/VIM2PGemminiWSHP1RtlTest")


def official_profile_root(files: Mapping[str, Path]) -> Path:
    root = files["resolved_profile"].parent
    if (files["resolved_profile"] != root / "resolved-profile.json" or
            files["rtl_binary"] != root / RTL_BINARY_REL):
        raise ValueError("official RTL binary/resolved-profile route required")
    return root


def certify(inputs: Path, library: Path, out: Path) -> Certificate:
    manifest = load_manifest()
    routes = array_value(read_document(inputs).get("cases"), "input routes")
    indexed: dict[tuple[str, str], Record] = {}
    for value in routes:
        route = object_value(value)
        key = (text(route, "profile"), text(route, "case"))
        if key in indexed:
            raise ValueError("duplicate production input route")
        indexed[key] = route
    cases = manifest["cases"]
    if len(indexed) != len(routes) or set(indexed) != {
            (case["profile"], case["case"]) for case in cases}:
        raise ValueError("input routing must cover the fixed production corpus exactly")
    source_hashes = {name: sha256((WORKSPACE / name).read_bytes()).hexdigest()
                     for name in PRODUCTION_SOURCE_FILES}
    answer_rows: list[CertificateCase] = []
    mutation_case = ""
    for case in cases:
        route = indexed[case["profile"], case["case"]]
        files = {name: Path(text(route, name)).resolve(strict=True) for name in
                 ("fixture", "rtl_log", "events_csv", "rtl_binary", "resolved_profile")}
        profile_root = official_profile_root(files)
        binding = verify_build(profile_root, case["profile"])
        hashes = object_value(binding.get("artifact_sha256"))
        if (binding.get("execution_kind") != "FRESH_BUILD" or
                text(hashes, RTL_BINARY_REL.as_posix()) !=
                sha256(files["rtl_binary"].read_bytes()).hexdigest()):
            raise ValueError("official RTL binary not bound to fresh build")
        files["rtl_build_binding"] = (profile_root / "rtl-build-binding.json").resolve(strict=True)
        artifacts: dict[str, Artifact] = {
            name: {"path": str(path), "sha256": sha256(path.read_bytes()).hexdigest()}
            for name, path in files.items()}
        if artifacts["fixture"]["sha256"] != case["fixture_sha256"]:
            raise ValueError(f"producer fixture changed: {case['profile']}/{case['case']}")
        work = read_work_fixture(files["fixture"])
        for field in ("descriptor", "geometry", "runs_header", "runs", "row_map",
                      "shape", "tile", "original_k"):
            if work[field] != case[field]:
                raise ValueError(f"producer work metadata changed: {field}")
        resolved = read_document(files["resolved_profile"])
        if text(resolved, "profile") != case["profile"]:
            raise ValueError("resolved hardware profile changed")
        rtl = parse_rtl_log(files["rtl_log"], case, work)
        events = parse_rtl_events(files["events_csv"], case, work)
        model = estimate_case(library, case)
        row = compare_case(case, work, rtl, events, model)
        if not answer_rows and row["status"] == "PASS":
            index = next((i for i, (_, kind) in enumerate(events) if kind == "load_issue"), None)
            if index is None:
                raise ValueError("production mutation case has no load_issue")
            shifted = list(events)
            cycle, kind = shifted[index]
            shifted[index] = (cycle + 1, kind)
            if compare_events(normalized_model_events(model["events"]), shifted)["exact"]:
                raise ValueError("production +1 event mutation was admitted")
            mutation_case = case["profile"] + "/" + case["case"]
        row["artifacts"] = artifacts
        answer_rows.append(row)
    first = next((row for row in answer_rows if row["status"] != "PASS"), None)
    result: Certificate = {"status": "PASS" if first is None else "FAIL",
              "artifact_role": "PRODUCTION_GENERATED",
              "production_one_logical_cross_block_gemm": "READY" if first is None else "NOT_READY",
              "expected": manifest["case_count"], "attempted": len(answer_rows),
              "rtl_admitted": sum(row["rtl_admitted"] for row in answer_rows),
              "model_admitted": sum(row["model_admitted"] for row in answer_rows),
              "exact": sum(row["status"] == "PASS" for row in answer_rows),
              "selected_event_multisets_exact": sum(row["selected_event_multiset"] is True
                                                      for row in answer_rows),
              "event_plus_one_mutation_rejected": bool(mutation_case),
              "event_plus_one_mutation_case": mutation_case,
              "max_abs_delta_cycles": max(abs(row["delta_cycles"]) for row in answer_rows),
              "first_mismatch": first, "manifest_sha256": PRODUCTION_MANIFEST_SHA256,
              "library_sha256": sha256(library.read_bytes()).hexdigest(),
              "source_sha256": source_hashes, "cases": answer_rows}
    out.mkdir(parents=True, exist_ok=False)
    _ = (out / "production-run-aware-certificate.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n")
    if result["status"] == "PASS" and validate_run_certificate(
            out / "production-run-aware-certificate.json", library) != "PRODUCTION_GENERATED":
        raise ValueError("production certificate failed self-admission")
    return result


def main() -> int:
    class Options(argparse.Namespace):
        inputs: Path = Path()
        library: Path = Path()
        out: Path = Path()

    parser = argparse.ArgumentParser()
    _ = parser.add_argument("--inputs", type=Path, required=True)
    _ = parser.add_argument("--library", type=Path, required=True)
    _ = parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(namespace=Options())
    result = certify(args.inputs, args.library, args.out)
    print(json.dumps({key: result[key] for key in
                      ("status", "expected", "exact", "first_mismatch")}))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
