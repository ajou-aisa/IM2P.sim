"""Bounded diagnostic replay of an exact v2 NPU trace in one native session."""
from __future__ import annotations

import argparse
import json
import math
import os
import resource
import shutil
import subprocess
import sys
import time
from pathlib import Path

from scripts.gemmini_replay_contract import compatible, hardware_contract
from scripts.gemmini_resolve_profile import BuildFailure
from sim.cycle import sequence_binding
from sim.cycle.certificate_contract import read_document
from sim.cycle.cli import RESULT_FIELDS
from sim.cycle.npu_trace_integrity import read_records, start_trace
from sim.cycle.npu_trace_schema import (
    VERSION,
    NpuTraceError,
    Work,
    object_value,
    require,
)
from sim.cycle.reconstruct_graph import sha256
from sim.cycle.sequence_binding import Code, SequenceSession, Settings
from sim.cycle.sequence_domain import LEGACY_REVISION, profile_domain
from sim.cycle.stateful_sequence_certificate import (
    validate as validate_stateful_certificate,
)
from sim.cycle.stateful_sequence_evidence import EvidenceContext

MAX_WORK_CYCLES, MAX_SESSION_CYCLES = 10_000_000, 1_000_000_000
RESULT_SCHEMA = "im2p-sequence-trace-result"
PENDING_RESULT = "pending-result.json"
PENDING_STATUS = "PENDING_VALIDATION"
SOURCE_NAMES = ("sim/cycle/sequence_binding.py", "sim/cycle/sequence_binding_abi.py",
                "sim/cycle/cli.py", "sim/cycle/reconstruct_graph.py",
                "sim/cycle/npu_trace_integrity.py", "sim/cycle/npu_trace_schema.py",
                "sim/cycle/npu_trace_calls.py", "sim/cycle/npu_trace_hosts.py",
                "sim/cycle/npu_trace_runs.py", "scripts/gemmini_replay_contract.py",
                "scripts/gemmini_resolve_profile.py", "sim/cycle/sequence_trace_cli.py",
                "sim/cycle/sequence_domain.py", "sim/cycle/stateful_sequence_certificate.py",
                "sim/cycle/stateful_sequence_evidence.py", "sim/cycle/stateful_sequence_evidence_v2.py",
                "sim/cycle/stateful_sequence_evidence_tag5.py",
                "sim/cycle/stateful_sequence_evidence_tag6.py", "sim/cycle/stateful_sequence_tag6_pins.py",
                "sim/cycle/stateful_sequence_replay_certificate.py", "sim/cycle/stateful_sequence_replay_state.py",
                "sim/cycle/stateful_sequence_evidence_equivalence.py",
                "sim/tests/cycle/compositional_sequence_bounded.py")


def publish(path: Path, value: dict[str, object]) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def source_hashes(root: Path) -> dict[str, str]:
    return {name: sha256(root / name) for name in SOURCE_NAMES}


def verify_source_hashes(root: Path, expected: dict[str, str]) -> None:
    require(source_hashes(root) == expected, "Python source changed")


def validated_domain(args: argparse.Namespace) -> tuple[str, str | None]:
    certificate = getattr(args, "stateful_sequence_certificate", None)
    evidence_root = getattr(args, "stateful_evidence_root", None)
    require((certificate is None) == (evidence_root is None),
            "stateful certificate and evidence root must be supplied together")
    if certificate is None:
        return LEGACY_REVISION, None
    if not isinstance(evidence_root, Path):
        raise NpuTraceError("stateful evidence root must be a path")
    document = read_document(certificate)
    tag6 = document.get("schema") in ("stateful-tag6-domain-v1", "stateful-full374-replay-v1")
    parents = object_value(document["parents"])
    context = EvidenceContext(
        evidence_root, Path(str(object_value(document["library"])["path"])),
        Path(str(object_value(document["shared_library"])["path"])),
        Path(str(object_value(parents["base"])["path"])),
        Path(str(object_value(parents["run_aware"])["path"])),
        Path(str(object_value(parents["service"])["path"])),
        Path(str(object_value(document["evidence_input"])["path"])) if not tag6 else None,
        (Path(str(object_value(object_value(object_value(document["domain_delta"])["artifacts"])
                              ["input"])["path"])) if document["version"] == 3 else None),
        Path(str(object_value(document["evidence_input"])["path"])) if tag6 else None,
    )
    require(args.library.resolve(strict=True) == context.shared_library.resolve(strict=True),
            "native library differs from stateful certificate")
    scoped = validate_stateful_certificate(certificate, context)
    if tag6:
        require(sha256(args.trace) == object_value(document["trace"])["sha256"],
                "Tag6 requires the certified original374 trace")
    return scoped.state_domain_revision, scoped.certificate_sha256


def load_trace(path: Path) -> tuple[str, tuple[Work, ...], str]:
    digest = sha256(path)
    records = read_records(path)
    state = start_trace(records)
    require(state.run.trace_version == VERSION, "exact trace v2 required")
    compatible(state.run.contract, hardware_contract(state.run.profile))
    works = tuple(work for record in records if (work := state.consume(record)) is not None)
    require(state.summary()["npu_work_count"] == len(works) and bool(works), "empty or incomplete trace")
    require(sha256(path) == digest, "trace changed during validation")
    return state.run.profile, works, digest


def selected_works(works: tuple[Work, ...], parent_index: int | None,
                   pilot_count: int | None) -> tuple[Work, ...]:
    parents = tuple(dict.fromkeys(work.parent_id for work in works))
    if parent_index is not None:
        require(0 <= parent_index < len(parents), "through-parent-index outside trace")
        chosen = set(parents[:parent_index + 1])
        works = tuple(work for work in works if work.parent_id in chosen)
    if pilot_count is not None:
        require(0 < pilot_count <= len(works), "pilot-work-count outside selected works")
        works = works[:pilot_count]
    return works


def preflight(output: Path) -> None:
    require(not output.exists() and not output.is_symlink(), "output must be a fresh directory")
    parent = output.parent
    while not parent.exists():
        parent = parent.parent
    require(shutil.disk_usage(parent).free >= 16 << 20, "less than 16 MiB free")
    require(resource.getrlimit(resource.RLIMIT_NOFILE)[0] >= 32, "file descriptor limit below 32")


def descriptor(work: Work) -> sequence_binding.Work:
    runs = tuple(sequence_binding.Run(run.original_block_id, run.original_k_mask,
                                      run.compact_k_begin, run.compact_k_count)
                 for run in work.runs)
    return sequence_binding.Work(work.identity, *work.inputs, original_k=work.original_k,
                                 runs=runs, record_events=False)


def completed_domain_failure(status: sequence_binding.Status,
                             domain: sequence_binding.DomainSnapshot,
                             rows: sequence_binding.RowPressure,
                             report: sequence_binding.Report,
                             work_id: int, offered: int, *,
                             profile: str = "a8w8-d16-hp1",
                             domain_revision: str = LEGACY_REVISION) -> dict[str, object] | None:
    limits = profile_domain(profile, domain_revision)
    tag_predicate = {4: "tag_peak_le_four", 5: "tag_peak_le_five", 6: "tag_peak_le_six"}[
        limits.max_tag_occupancy]
    checks = {
        "generation": (status.generation, domain.generation, report.generation) == (1, 1, 1),
        "cursor": status.cursor == domain.cursor == report.resource_ready_cycle,
        "work_id": report.logical_work_id == work_id,
        "offered_cycle": report.offered_cycle == offered,
        "accepted_ge_offer": offered <= report.accepted_cycle,
        "result_after_accepted": report.accepted_cycle < report.result_ready_cycle,
        "scale_ge_result": report.result_ready_cycle <= report.final_scale_release_cycle,
        "resource_ge_scale": report.final_scale_release_cycle <= report.resource_ready_cycle,
        "pending_zero": status.has_pending == 0,
        "active_zero": status.has_active == 0,
        "faulted_zero": status.faulted == 0,
        "ready_violation_zero": domain.ready_violation_mask == 0,
        tag_predicate: domain.max_tag_occupancy <= limits.max_tag_occupancy,
        "logical_work_count_one": report.counters.logical_work_count == 1,
        "row_generation": rows.generation == domain.generation,
        "row_cursor": rows.cursor == domain.cursor,
        "row_count_matches": rows.row_count == domain.row_count,
        "row_count_le_peak": rows.row_count <= rows.max_row_occupancy,
        "row_peak_lt_six": rows.max_row_occupancy < 6,
    }
    failed = [name for name, passed in checks.items() if not passed]
    if not failed:
        return None
    observed = {
        "expected": {"work_id": work_id, "offered_cycle": offered},
        "status": {name: getattr(status, name) for name in
                   ("generation", "cursor", "has_pending", "has_active", "faulted")},
        "domain": {name: getattr(domain, name) for name in
                   ("generation", "cursor", "ready_violation_mask", "tag_count",
                    "max_tag_occupancy", "row_count")},
        "row_pressure": {name: getattr(rows, name) for name in
                         ("generation", "cursor", "row_count", "max_row_occupancy")},
        "report": {name: getattr(report, name) for name in
                   ("generation", "logical_work_id", "offered_cycle", "accepted_cycle",
                    "result_ready_cycle", "final_scale_release_cycle", "resource_ready_cycle")},
        "logical_work_count": report.counters.logical_work_count,
    }
    return {"status": "FAILED", "scope": "DIAGNOSTIC_ACTUAL_TRACE", "production_admitted": False,
            "native_counters": {name: getattr(report.counters, name) for name in RESULT_FIELDS},
            "domain_snapshot_hex": bytes(domain).hex(), "row_pressure_hex": bytes(rows).hex(),
            "classification": "OUTSIDE_REVIEWED_STATE_DOMAIN" if
            set(failed) <= {tag_predicate, "row_peak_lt_six"} else "NATIVE_COMPLETED_REPORT_MISMATCH",
            "failed_predicates": failed, "predicates": checks, "observed": observed,
            "domain_scope": {"implemented": "native completed report observed; queue capacity six is not validation permission",
                             "reviewed": f"tag_peak<={limits.max_tag_occupancy}, sticky row_peak<6 and boundary consistency checks",
                             "state_domain_revision": domain_revision,
                             "rtl_exactness_for_this_trace": "NOT_ESTABLISHED"}}


def replay(args: argparse.Namespace) -> int:
    started, cpu_started = time.monotonic(), time.process_time_ns()
    preflight(args.output)
    args.output.mkdir(parents=True)
    progress: dict[str, object] = {"status": "VALIDATING", "completed_work_ids": [],
                                   "last_work_id": None, "active_work_id": None, "cursor": 0,
                                   "modeled_cycles": 0, "simulator_wall_seconds": 0.0}
    publish(args.output / "progress.json", progress)
    validation_start = time.monotonic()
    profile, works, trace_digest = load_trace(args.trace)
    domain_revision, domain_certificate_sha256 = validated_domain(args)
    validation_seconds = time.monotonic() - validation_start
    chosen = selected_works(works, args.through_parent_index, args.pilot_work_count)
    require(args.expected_work_count in (None, len(chosen)), "expected-work-count differs")
    identity = sequence_binding.source_identity(args.library, profile)
    source_root = Path(__file__).resolve().parents[2]
    sources = source_hashes(source_root)
    progress.update(profile=profile, trace_sha256=trace_digest, library_sha256=identity.library_sha256,
                    state_domain_revision=domain_revision,
                    domain_certificate_sha256=domain_certificate_sha256,
                    hardware_contract_sha256=hardware_contract(profile)["sha256"],
                    source_sha256=dict(identity.source_sha256) | sources,
                    selected_work_count=len(chosen), trace_work_count=len(works), status="LOADING")
    publish(args.output / "progress.json", progress)
    load_start = time.monotonic()
    timings = {"validation_seconds": validation_seconds, "load_seconds": 0.0,
               "lowering_seconds": 0.0, "native_stepping_seconds": 0.0,
               "event_seconds": 0.0, "serialization_seconds": 0.0,
               "native_advance_calls": 0}
    settings = Settings(max_work_cycles=MAX_WORK_CYCLES, max_session_cycles=MAX_SESSION_CYCLES)
    with SequenceSession(args.library, profile, settings=settings, expected_identity=identity) as session, \
         (args.output / "works.jsonl").open("x", encoding="utf-8") as stream:
        timings["load_seconds"] = time.monotonic() - load_start
        cold = session.domain_snapshot()
        require((cold.generation, cold.cursor, cold.resource_ready, cold.row_count, cold.tag_count) ==
                (1, 0, 1, 0, 0), "native session not cold")
        completed: list[int] = []
        modeled_cycles = 0
        for work in chosen:
            offer_cycle = session.status().cursor
            progress.update(status="ACTIVE", active_work_id=work.identity, cursor=offer_cycle,
                            simulator_wall_seconds=time.monotonic() - started)
            publish(args.output / "progress.json", progress)
            lower_start = time.monotonic()
            code = session.offer(descriptor(work), offer_cycle)
            timings["lowering_seconds"] += time.monotonic() - lower_start
            require(code == Code.OK, "native offer rejected")
            step_start = time.monotonic()
            while True:
                code = session.advance_to_boundary(MAX_SESSION_CYCLES, 65_536)
                timings["native_advance_calls"] += 1
                step_status = session.status()
                progress.update(cursor=step_status.cursor,
                                native_advance_calls=timings["native_advance_calls"],
                                simulator_wall_seconds=time.monotonic() - started)
                publish(args.output / "progress.json", progress)
                if code == Code.OK:
                    require(step_status.stop_reason == sequence_binding.StopReason.REPORT_AVAILABLE,
                            "native report boundary missing")
                    break
                require(code == Code.INCOMPLETE and
                        step_status.stop_reason == sequence_binding.StopReason.SOFT_BUDGET,
                        f"native advance failed: {code.name}, stop={step_status.stop_reason}")
            timings["native_stepping_seconds"] += time.monotonic() - step_start
            status = session.status()
            domain = session.domain_snapshot()
            rows = session.row_pressure()
            report = session.pop_report()
            if not isinstance(report, sequence_binding.Report):
                raise NpuTraceError("completed work report missing")
            failure = completed_domain_failure(status, domain, rows, report, work.identity, offer_cycle,
                                               profile=profile, domain_revision=domain_revision)
            if failure is not None:
                failure.update(parent_id=work.parent_id, call_id=work.call_id,
                               stripe_id=work.stripe_id, profile=profile,
                               trace_sha256=trace_digest, library_sha256=identity.library_sha256)
                failure.update(native_completed_report_work_id=report.logical_work_id,
                               validated_completed_work_ids=completed[:])
                detail = (str(failure["classification"]) + ": failed=" +
                          json.dumps(failure["failed_predicates"]) + "; observed=" +
                          json.dumps(failure["observed"], sort_keys=True))
                publish(args.output / "failure.json", failure | {"error": detail})
                progress.update(status="FAILED", cursor=status.cursor,
                                native_completed_report_work_id=report.logical_work_id,
                                classification=failure["classification"],
                                failed_predicates=failure["failed_predicates"])
                publish(args.output / "progress.json", progress)
                raise NpuTraceError(detail)
            counters = report.counters
            require(counters.start_cycle == report.accepted_cycle and
                    counters.done_cycle == report.result_ready_cycle and
                    counters.total_cycles == counters.done_cycle - counters.start_cycle and
                    all(getattr(counters, kind + "_request_count") ==
                        getattr(counters, kind + "_response_count")
                        for kind in ("load", "store", "scale")) and
                    counters.event_count == report.event_count and
                    report.next_scratchpad_half in (0, 1) and
                    report.next_accumulator_half in (0, 1),
                    "native work counters inconsistent")
            event_start = time.monotonic()
            require(session.event_count() == 0, "event-off session unexpectedly retained events")
            timings["event_seconds"] += time.monotonic() - event_start
            modeled_cycles += report.counters.total_cycles
            completed.append(work.identity)
            row = {"work_id": work.identity, "parent_id": work.parent_id, "generation": report.generation,
                   "call_id": work.call_id, "stripe_id": work.stripe_id, "operation_id": work.operation_id,
                   "offered_cycle": report.offered_cycle, "accepted_cycle": report.accepted_cycle,
                   "request_available_cycle": offer_cycle, "pending_admission_cycle": offer_cycle,
                   "port_offer_cycle": offer_cycle,
                   "result_ready_cycle": report.result_ready_cycle,
                   "final_scale_release_cycle": report.final_scale_release_cycle,
                   "resource_ready_cycle": report.resource_ready_cycle,
                   "next_scratchpad_half": report.next_scratchpad_half,
                   "next_accumulator_half": report.next_accumulator_half,
                   "event_count": report.event_count,
                   "domain_snapshot_hex": bytes(domain).hex(),
                   "row_pressure_hex": bytes(rows).hex(),
                   "counters": {name: getattr(counters, name) for name in RESULT_FIELDS},
                   "modeled_cycles": counters.total_cycles}
            serialize_start = time.monotonic()
            stream.write(json.dumps(row, sort_keys=True) + "\n")
            stream.flush()
            progress.update(status="PARTIAL", completed_work_ids=completed[:], last_work_id=work.identity,
                            active_work_id=None, cursor=status.cursor, modeled_cycles=modeled_cycles,
                            simulator_wall_seconds=time.monotonic() - started)
            publish(args.output / "progress.json", progress)
            timings["serialization_seconds"] += time.monotonic() - serialize_start
            print(json.dumps({"completed": len(completed), "selected": len(chosen),
                              "work_id": work.identity, "cursor": status.cursor}), flush=True)
        session.verify_identity()
        final = session.status()
        final_domain = bytes(session.domain_snapshot()).hex()
        final_rows = bytes(session.row_pressure()).hex()
        cumulative = {name: getattr(session.counters(), name) for name in RESULT_FIELDS}
        require(final.generation == 1 and final.has_pending == final.has_active == final.has_report ==
                final.faulted == 0 and session.counters().logical_work_count == len(chosen),
                "native final state incomplete")
    verify_source_hashes(source_root, sources)
    require(validated_domain(args) == (domain_revision, domain_certificate_sha256),
            "stateful domain changed before publication")
    require(sha256(args.trace) == trace_digest and
            hardware_contract(profile)["sha256"] == progress["hardware_contract_sha256"] and
            sequence_binding.source_identity(args.library, profile) == identity,
            "trace, source, or library changed before publication")
    status_name = ("PILOT_PARTIAL" if args.pilot_work_count is not None else
                   "SUBSET_DIAGNOSTIC" if args.through_parent_index is not None else "DIAGNOSTIC_PASS")
    result = {**progress, **timings, "schema": RESULT_SCHEMA, "version": 2,
              "status": status_name, "generation": 1,
              "production_admitted": False, "validation_scope": "DIAGNOSTIC_ACTUAL_TRACE",
              "offer_policy": "back-to-back-npu-only", "host_arrival_measured": False,
              "availability_source": "own previous resource-ready boundary, not measured host arrival",
              "final_domain_snapshot_hex": final_domain, "final_row_pressure_hex": final_rows,
              "cumulative_counters": cumulative,
              "cpu_process_nanoseconds": time.process_time_ns() - cpu_started,
              "simulator_wall_seconds": time.monotonic() - started}
    pending = getattr(args, "watchdog_pending", False)
    publish(args.output / (PENDING_RESULT if pending else "result.json"), result)
    progress.update(status=PENDING_STATUS if pending else status_name, active_work_id=None)
    publish(args.output / "progress.json", progress)
    return 0


def valid_replay_result(output: Path, options: argparse.Namespace,
                        progress: dict[str, object] | None, *, pending: bool = False) -> bool:
    try:
        result = json.loads((output / (PENDING_RESULT if pending else "result.json")).read_text(
            encoding="utf-8"))
        rows = [json.loads(line) for line in (output / "works.jsonl").read_text(
            encoding="utf-8").splitlines()]
        if not isinstance(result, dict) or not isinstance(progress, dict):
            return False
        ids = result.get("completed_work_ids")
        count = result.get("selected_work_count")
        if (not isinstance(ids, list) or any(type(value) is not int for value in ids) or
                type(count) is not int or count <= 0 or
                len(ids) != count or len(set(ids)) != count or len(rows) != count or
                options.trace is None or options.library is None):
            return False
        profile, trace_works, trace_digest = load_trace(options.trace)
        domain_revision, domain_certificate_sha256 = validated_domain(options)
        limits = profile_domain(profile, domain_revision)
        chosen = selected_works(trace_works, options.through_parent_index,
                                options.pilot_work_count)
        if (result.get("profile") != profile or count != len(chosen) or
                ids != [work.identity for work in chosen]):
            return False
        identity = sequence_binding.source_identity(options.library, profile)
        sources = dict(identity.source_sha256) | source_hashes(Path(__file__).resolve().parents[2])
        status_name = ("PILOT_PARTIAL" if options.pilot_work_count is not None else
                       "SUBSET_DIAGNOSTIC" if options.through_parent_index is not None else
                       "DIAGNOSTIC_PASS")
        expected_count = options.expected_work_count or options.pilot_work_count
        if (result.get("schema") != RESULT_SCHEMA or type(result.get("version")) is not int or
                result["version"] not in (1, 2) or result.get("status") != status_name or
                type(result.get("generation")) is not int or result["generation"] != 1 or
                result.get("validation_scope") != "DIAGNOSTIC_ACTUAL_TRACE" or
                result.get("production_admitted") is not False or
                result.get("offer_policy") != "back-to-back-npu-only" or
                result.get("host_arrival_measured") is not False or
                (expected_count is not None and count != expected_count) or
                type(result.get("trace_work_count")) is not int or
                result["trace_work_count"] != len(trace_works) or
                result.get("trace_sha256") != trace_digest or
                result.get("library_sha256") != identity.library_sha256 or
                result.get("hardware_contract_sha256") != hardware_contract(profile)["sha256"] or
                result.get("source_sha256") != sources or
                result.get("state_domain_revision", LEGACY_REVISION) != domain_revision or
                result.get("domain_certificate_sha256") != domain_certificate_sha256 or
                result.get("active_work_id") is not None or
                result.get("last_work_id") != ids[-1]):
            return False
        cursor = cycles = 0
        tag_peak = row_peak = 0
        for row, work in zip(rows, chosen, strict=True):
            counters = row["counters"]
            if (row["work_id"] != work.identity or row["parent_id"] != work.parent_id or
                    type(row["generation"]) is not int or
                    row["generation"] != 1 or
                    row["offered_cycle"] != cursor or
                    not row["offered_cycle"] <= row["accepted_cycle"] <
                    row["result_ready_cycle"] <= row["final_scale_release_cycle"] <=
                    row["resource_ready_cycle"] or
                    row["next_scratchpad_half"] not in (0, 1) or
                    row["next_accumulator_half"] not in (0, 1) or
                    not isinstance(counters, dict) or set(counters) != set(RESULT_FIELDS) or
                    any(type(value) is not int or value < 0 for value in counters.values()) or
                    counters["start_cycle"] != row["accepted_cycle"] or
                    counters["done_cycle"] != row["result_ready_cycle"] or
                    counters["total_cycles"] != row["modeled_cycles"] or
                    counters["total_cycles"] != counters["done_cycle"] - counters["start_cycle"] or
                    counters["logical_work_count"] != 1 or
                    counters["event_count"] != row["event_count"] or
                    any(counters[kind + "_request_count"] != counters[kind + "_response_count"]
                        for kind in ("load", "store", "scale"))):
                return False
            if result["version"] == 2:
                if (row["call_id"] != work.call_id or row["stripe_id"] != work.stripe_id or
                        row["operation_id"] != work.operation_id or
                        row["request_available_cycle"] != row["offered_cycle"] or
                        row["pending_admission_cycle"] != row["offered_cycle"] or
                        row["port_offer_cycle"] != row["offered_cycle"]):
                    return False
                domain = sequence_binding.DomainSnapshot.from_buffer_copy(
                    bytes.fromhex(row["domain_snapshot_hex"]))
                pressure = sequence_binding.RowPressure.from_buffer_copy(
                    bytes.fromhex(row["row_pressure_hex"]))
                if (domain.abi_version != 2 or domain.struct_size != len(bytes(domain)) or
                        len(row["domain_snapshot_hex"]) != 2 * domain.struct_size or
                        pressure.abi_version != 1 or pressure.struct_size != len(bytes(pressure)) or
                        len(row["row_pressure_hex"]) != 2 * pressure.struct_size or
                        domain.generation != 1 or domain.resource_ready != 1 or
                        domain.cursor != row["resource_ready_cycle"] or
                        domain.ready_violation_mask != limits.ready_violation_mask or
                        not tag_peak <= domain.max_tag_occupancy <= limits.max_tag_occupancy or
                        domain.tag_count > domain.max_tag_occupancy or
                        any(tag.rob_valid for tag in domain.tags[:domain.tag_count]) or
                        pressure.generation != 1 or pressure.cursor != domain.cursor or
                        pressure.row_count != domain.row_count or
                        not row_peak <= pressure.max_row_occupancy < limits.max_row_occupancy_exclusive or
                        pressure.row_count > pressure.max_row_occupancy):
                    return False
                tag_peak, row_peak = domain.max_tag_occupancy, pressure.max_row_occupancy
            cursor = row["resource_ready_cycle"]
            cycles += row["modeled_cycles"]
        if result["version"] == 2 and (
                result.get("final_domain_snapshot_hex") != rows[-1]["domain_snapshot_hex"] or
                result.get("final_row_pressure_hex") != rows[-1]["row_pressure_hex"] or
                result.get("cumulative_counters") != {
                    name: (rows[0]["accepted_cycle"] if name == "start_cycle" else
                           rows[-1]["result_ready_cycle"] if name == "done_cycle" else
                           sum(row["counters"][name] for row in rows))
                    for name in RESULT_FIELDS}):
            return False
        return (result.get("cursor") == cursor and result.get("modeled_cycles") == cycles and
                progress.get("status") == (PENDING_STATUS if pending else status_name) and
                all(progress.get(key) == result.get(key) for key in
                    ("completed_work_ids", "selected_work_count", "trace_work_count",
                     "trace_sha256", "library_sha256", "source_sha256", "last_work_id",
                     "active_work_id", "cursor", "modeled_cycles")))
    except (OSError, ValueError, TypeError, KeyError, AttributeError, BuildFailure,
            sequence_binding.SequenceError):
        return False


def verify_candidate(args: argparse.Namespace) -> int:
    try:
        progress = json.loads((args.output / "progress.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 1
    return 0 if valid_replay_result(args.output, args, progress, pending=True) else 1


def publish_candidate(args: argparse.Namespace) -> int:
    progress = json.loads((args.output / "progress.json").read_text(encoding="utf-8"))
    result = json.loads((args.output / PENDING_RESULT).read_text(encoding="utf-8"))
    require(isinstance(progress, dict) and progress.get("status") == PENDING_STATUS and
            isinstance(result, dict) and result.get("status") in
            ("PILOT_PARTIAL", "SUBSET_DIAGNOSTIC", "DIAGNOSTIC_PASS"),
            "publication candidate malformed")
    require(time.monotonic() < args.deadline, "publication deadline expired")
    terminal_progress = progress | {"status": result["status"]}
    publish(args.output / "progress.json", terminal_progress)
    require(json.loads((args.output / "progress.json").read_text(encoding="utf-8")) == terminal_progress,
            "publication progress changed")
    candidate_stat = (args.output / PENDING_RESULT).lstat()
    require((candidate_stat.st_dev, candidate_stat.st_ino) ==
            (args.candidate_device, args.candidate_inode), "publication candidate replaced")
    require(sha256(args.output / PENDING_RESULT) == args.candidate_sha256,
            "publication candidate changed")
    require(time.monotonic() < args.deadline, "publication deadline expired")
    # The validated, serialized receipt commits here. Nothing after this link
    # may revoke it; supervisor exit/status bookkeeping is auxiliary.
    os.link(args.output / PENDING_RESULT, args.output / "result.json")
    return 0


def reap_owned(process: subprocess.Popen[bytes]) -> float:
    started = time.monotonic()
    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
    return time.monotonic() - started


def watchdog(args: argparse.Namespace) -> int:
    command = args.command[1:] if args.command and args.command[0] == "--" else args.command
    if not command or not math.isfinite(args.timeout_seconds) or args.timeout_seconds <= 0:
        raise NpuTraceError("watchdog requires command and positive finite timeout")
    require(not args.status.exists() and not args.status.is_symlink(), "watchdog status must be fresh")
    output = None
    replay_options = None
    marker = ["-m", "sim.cycle.sequence_trace_cli", "replay"]
    is_replay = any(command[index:index + 3] == marker
                    for index in range(len(command) - 2))
    if is_replay:
        executable = shutil.which(command[0])
        require(command[1:5] == ["-B", *marker] and executable is not None and
                Path(executable).resolve() == Path(sys.executable).resolve(),
                "unsupported replay command")
        parser = argparse.ArgumentParser(add_help=False, exit_on_error=False)
        parser.add_argument("--trace", type=Path)
        parser.add_argument("--library", type=Path)
        parser.add_argument("--stateful-sequence-certificate", type=Path)
        parser.add_argument("--stateful-evidence-root", type=Path)
        parser.add_argument("--through-parent-index", type=int)
        parser.add_argument("--pilot-work-count", type=int)
        parser.add_argument("--expected-work-count", type=int)
        parser.add_argument("--output", type=Path)
        replay_options, _ = parser.parse_known_args(command[5:])
        output = replay_options.output
        if output is None:
            raise NpuTraceError("replay output missing")
        require(not output.exists() and not output.is_symlink(), "replay output must be fresh")
    started = time.monotonic()
    deadline = started + args.timeout_seconds
    try:
        process = subprocess.Popen([*command, "--watchdog-pending"] if is_replay else command,
                                   start_new_session=True)
    except OSError as error:
        args.status.parent.mkdir(parents=True, exist_ok=True)
        publish(args.status, {"status": "FAILED", "error": str(error), "child_reaped": True,
                              "elapsed_seconds": time.monotonic() - started})
        return 1
    timed_out = False
    cancelled = False
    cleanup_seconds = 0.0
    try:
        process.wait(timeout=max(0.0, deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
        timed_out = True
    except KeyboardInterrupt:
        cancelled = True
    if timed_out or cancelled:
        cleanup_seconds += reap_owned(process)
    progress = None
    if output is not None and (output / "progress.json").exists():
        try:
            progress = json.loads((output / "progress.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            progress = None
    valid_result = not is_replay and process.returncode == 0
    result_committed = False
    verifier = None
    publisher = None
    verifier_error = None
    publisher_error = None
    if (is_replay and process.returncode == 0 and not timed_out and not cancelled and
            output is not None and replay_options is not None and
            replay_options.trace is not None and replay_options.library is not None and
            (output / PENDING_RESULT).is_file()):
        candidate = output / PENDING_RESULT
        candidate_stat = candidate.stat()
        candidate_identity = (candidate_stat.st_dev, candidate_stat.st_ino)
        candidate_sha256 = sha256(candidate)
        verify_command = [sys.executable, "-B", "-m", "sim.cycle.sequence_trace_cli",
                          "_verify-candidate", "--trace", str(replay_options.trace),
                          "--library", str(replay_options.library), "--output", str(output)]
        for name in ("through_parent_index", "pilot_work_count", "expected_work_count",
                     "stateful_sequence_certificate", "stateful_evidence_root"):
            value = getattr(replay_options, name)
            if value is not None:
                verify_command.extend(("--" + name.replace("_", "-"), str(value)))
        if time.monotonic() >= deadline:
            timed_out = True
        else:
            try:
                verifier = subprocess.Popen(verify_command, start_new_session=True,
                                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except OSError as error:
                verifier_error = str(error)
            if verifier is not None:
                try:
                    verifier.wait(timeout=max(0.0, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    timed_out = True
                    cleanup_seconds += reap_owned(verifier)
                except KeyboardInterrupt:
                    cancelled = True
                    cleanup_seconds += reap_owned(verifier)
                if verifier.returncode == 0 and not timed_out and not cancelled:
                    if time.monotonic() >= deadline:
                        timed_out = True
                    else:
                        try:
                            publisher = subprocess.Popen(
                                [sys.executable, "-B", "-m", "sim.cycle.sequence_trace_cli",
                                 "_publish-candidate", "--output", str(output),
                                 "--deadline", str(deadline),
                                 "--candidate-sha256", candidate_sha256,
                                 "--candidate-device", str(candidate_identity[0]),
                                 "--candidate-inode", str(candidate_identity[1])], start_new_session=True)
                            publisher.wait(timeout=max(0.0, deadline - time.monotonic()))
                        except subprocess.TimeoutExpired:
                            timed_out = True
                        except KeyboardInterrupt:
                            cancelled = True
                        except OSError as error:
                            publisher_error = str(error)
                        if publisher is not None and publisher.poll() is None:
                            try:
                                cleanup_seconds += reap_owned(publisher)
                            except OSError as error:
                                publisher_error = str(error)
                        # Observe the commit even if the publisher was interrupted
                        # afterwards. Bind it to the inode validated before launch,
                        # never merely to an existing result or a zero exit code.
                        if publisher is not None:
                            try:
                                final_path = output / "result.json"
                                final_stat = final_path.lstat()
                                result_committed = (not final_path.is_symlink() and
                                                    (final_stat.st_dev, final_stat.st_ino) == candidate_identity)
                                # The linked inode was validated before publication.
                                # A later inspection error cannot revoke that commit.
                                valid_result = result_committed
                                if result_committed and isinstance(progress, dict):
                                    terminal_status = ("PILOT_PARTIAL" if replay_options.pilot_work_count is not None else
                                                       "SUBSET_DIAGNOSTIC" if replay_options.through_parent_index is not None
                                                       else "DIAGNOSTIC_PASS")
                                    progress = progress | {"status": terminal_status}
                                if result_committed:
                                    valid_result = sha256(final_path) == candidate_sha256
                                    if not valid_result:
                                        publisher_error = "committed result content changed"
                            except OSError as error:
                                publisher_error = str(error)
    elapsed = time.monotonic() - started
    if not cancelled and elapsed >= args.timeout_seconds:
        timed_out = True
    if publisher is not None and output is not None and not result_committed:
        try:
            if isinstance(progress, dict):
                progress = progress | {"status": PENDING_STATUS}
                publish(output / "progress.json", progress)
        except OSError as error:
            publisher_error = str(error)
    elapsed = time.monotonic() - started
    owned_reaped = all(child.poll() is not None for child in (process, verifier, publisher)
                       if child is not None)
    receipt = {"status": "PASS" if result_committed and valid_result else
               "FAILED" if result_committed else
               "TIMEOUT" if timed_out else "CANCELLED" if cancelled else
               "PASS" if process.returncode == 0 and valid_result and owned_reaped else "FAILED",
               "result_committed": result_committed,
               "result_valid": valid_result if is_replay else None,
               "auxiliary_status": ("CLEANUP_FAILED" if not owned_reaped else
                                    "FAILED" if not valid_result else
                                    "TIMEOUT" if timed_out else "CANCELLED" if cancelled else
                                    "FAILED" if publisher_error is not None or
                                    (publisher is not None and publisher.returncode != 0) else "PASS")
               if result_committed else None,
               "cleanup_complete": owned_reaped,
               "elapsed_seconds": elapsed - cleanup_seconds,
               "cleanup_seconds": cleanup_seconds, "wall_seconds": elapsed,
               "timeout_seconds": args.timeout_seconds,
               "child_pid": process.pid, "child_returncode": process.returncode,
               "child_reaped": process.poll() is not None, "progress": progress,
               "verifier_pid": verifier.pid if verifier is not None else None,
               "verifier_returncode": verifier.returncode if verifier is not None else None,
               "verifier_reaped": verifier.poll() is not None if verifier is not None else None,
               "publisher_pid": publisher.pid if publisher is not None else None,
               "publisher_returncode": publisher.returncode if publisher is not None else None,
               "publisher_reaped": publisher.poll() is not None if publisher is not None else None}
    if verifier_error is not None:
        receipt["verifier_error"] = verifier_error
    if publisher_error is not None:
        receipt["publisher_error"] = publisher_error
    try:
        args.status.parent.mkdir(parents=True, exist_ok=True)
        publish(args.status, receipt)
    except OSError as error:
        receipt["auxiliary_status"] = "RECEIPT_FAILED"
        receipt["receipt_error"] = str(error)
        print(json.dumps(receipt, sort_keys=True), file=sys.stderr)
        return 0 if result_committed and valid_result else 1
    return 0 if receipt["status"] == "PASS" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="subcommand", required=True)
    replay_parser = commands.add_parser("replay", help="one cold diagnostic native session")
    replay_parser.add_argument("--trace", type=Path, required=True)
    replay_parser.add_argument("--library", type=Path, required=True)
    replay_parser.add_argument("--stateful-sequence-certificate", type=Path)
    replay_parser.add_argument("--stateful-evidence-root", type=Path)
    replay_parser.add_argument("--through-parent-index", type=int)
    replay_parser.add_argument("--pilot-work-count", type=int)
    replay_parser.add_argument("--expected-work-count", type=int)
    replay_parser.add_argument("--offer-policy", choices=("back-to-back",), required=True)
    replay_parser.add_argument("--record-events", type=int, choices=(0,), required=True)
    replay_parser.add_argument("--output", type=Path, required=True)
    replay_parser.add_argument("--watchdog-pending", action="store_true", help=argparse.SUPPRESS)
    watchdog_parser = commands.add_parser("watchdog", help="bound and reap one owned child")
    watchdog_parser.add_argument("--timeout-seconds", type=float, required=True)
    watchdog_parser.add_argument("--status", type=Path, required=True)
    watchdog_parser.add_argument("command", nargs=argparse.REMAINDER)
    verify_parser = commands.add_parser("_verify-candidate", help=argparse.SUPPRESS)
    verify_parser.add_argument("--trace", type=Path, required=True)
    verify_parser.add_argument("--library", type=Path, required=True)
    verify_parser.add_argument("--stateful-sequence-certificate", type=Path)
    verify_parser.add_argument("--stateful-evidence-root", type=Path)
    verify_parser.add_argument("--through-parent-index", type=int)
    verify_parser.add_argument("--pilot-work-count", type=int)
    verify_parser.add_argument("--expected-work-count", type=int)
    verify_parser.add_argument("--output", type=Path, required=True)
    publish_parser = commands.add_parser("_publish-candidate", help=argparse.SUPPRESS)
    publish_parser.add_argument("--output", type=Path, required=True)
    publish_parser.add_argument("--deadline", type=float, required=True)
    publish_parser.add_argument("--candidate-sha256", required=True)
    publish_parser.add_argument("--candidate-device", type=int, required=True)
    publish_parser.add_argument("--candidate-inode", type=int, required=True)
    args = parser.parse_args()
    output_existed = args.subcommand == "replay" and args.output.exists()
    try:
        if args.subcommand == "replay":
            return replay(args)
        if args.subcommand == "_publish-candidate":
            return publish_candidate(args)
        return watchdog(args) if args.subcommand == "watchdog" else verify_candidate(args)
    except (OSError, ValueError, RuntimeError, BuildFailure, sequence_binding.SequenceError) as error:
        print(f"sequence trace {args.subcommand} failed: {error}", file=sys.stderr)
        if (args.subcommand == "replay" and not output_existed and args.output.is_dir() and
                not (args.output / "failure.json").exists()):
            publish(args.output / "failure.json", {"status": "FAILED", "error": str(error)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
