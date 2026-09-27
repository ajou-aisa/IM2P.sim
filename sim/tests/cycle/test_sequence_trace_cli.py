from __future__ import annotations

import argparse
import errno
import json
import os
import select
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from sim.cycle import sequence_binding, sequence_trace_cli
from sim.cycle.npu_trace import validate_trace
from sim.cycle.npu_trace_schema import NpuTraceError

ROOT = Path(__file__).resolve().parents[3]
E = Path("/Users/zerogod/aisa-lab/build/im2p-gemmini/stateful-sequence-v2-20260924T033303Z")
TRACE = E / "rtl/task-13-producer-20260924T125443Z/runs/a8w8-d16-hp1/npu-cycle-trace.jsonl"
CompletedBoundary = tuple[sequence_binding.Status, sequence_binding.DomainSnapshot,
                          sequence_binding.RowPressure, sequence_binding.Report]


@pytest.fixture(scope="module")
def library(tmp_path_factory: pytest.TempPathFactory) -> Path:
    build = tmp_path_factory.mktemp("sequence-trace-cli-build")
    subprocess.run(["cmake", "-S", str(ROOT / "sim/cycle"), "-B", str(build),
                    "-DIM2P_CYCLE_BUILD_TESTS=OFF"],
                   check=True, capture_output=True, text=True, timeout=30)
    subprocess.run(["cmake", "--build", str(build), "--target", "im2p_cycle_model_shared", "-j2"],
                   check=True, capture_output=True, text=True, timeout=120)
    return next(build.glob("libim2p_cycle_model.*"))


def test_existing_exact_v2_validator_accepts_complete_producer_fixture() -> None:
    # Given: the source producer fixture, including both completed parent windows.
    # When: the existing strict parser validates every record through RUN_END.
    summary = validate_trace(TRACE)
    # Then: twelve real works and the expected profile survive exact validation.
    assert (summary["profile"], summary["npu_work_count"]) == ("a8w8-d16-hp1", 12)


@pytest.fixture
def completed_boundary() -> CompletedBoundary:
    # Portable validation-only scalars, not a new native/RTL pressure witness.
    binding = sequence_trace_cli.sequence_binding
    status = binding.Status(generation=1, cursor=20)
    domain = binding.DomainSnapshot(generation=1, cursor=20, max_tag_occupancy=4)
    rows = binding.RowPressure(generation=1, cursor=20, row_count=0, max_row_occupancy=3)
    report = binding.Report(generation=1, logical_work_id=3, offered_cycle=0,
                            accepted_cycle=0, result_ready_cycle=10,
                            final_scale_release_cycle=19, resource_ready_cycle=20)
    report.counters.logical_work_count = 1
    return status, domain, rows, report


def test_completed_domain_failure_names_reviewed_tag_limit(completed_boundary: CompletedBoundary) -> None:
    status, domain, rows, report = completed_boundary
    assert sequence_trace_cli.completed_domain_failure(status, domain, rows, report, 3, 0) is None
    domain.max_tag_occupancy = 5
    failure = sequence_trace_cli.completed_domain_failure(status, domain, rows, report, 3, 0)
    assert failure is not None
    assert failure["classification"] == "OUTSIDE_REVIEWED_STATE_DOMAIN"
    assert failure["failed_predicates"] == ["tag_peak_le_four"]
    observed = failure["observed"]
    assert isinstance(observed, dict) and observed["domain"]["max_tag_occupancy"] == 5
    assert failure["production_admitted"] is False


@pytest.mark.parametrize(("field", "value", "predicate"), (
    ("max_row_occupancy", 6, "row_peak_lt_six"),
    ("generation", 2, "row_generation"),
    ("cursor", 21, "row_cursor"),
    ("row_count", 1, "row_count_matches"),
))
def test_completed_domain_rejects_row_pressure_mismatch(
    completed_boundary: CompletedBoundary, field: str, value: int, predicate: str,
) -> None:
    status, domain, rows, report = completed_boundary
    setattr(rows, field, value)
    failure = sequence_trace_cli.completed_domain_failure(status, domain, rows, report, 3, 0)
    assert failure is not None
    failed = failure["failed_predicates"]
    assert isinstance(failed, list) and predicate in failed
    assert failure["classification"] == ("OUTSIDE_REVIEWED_STATE_DOMAIN" if field == "max_row_occupancy"
                                          else "NATIVE_COMPLETED_REPORT_MISMATCH")


def test_replay_pilot_uses_one_cold_native_generation(tmp_path: Path, library: Path) -> None:
    # Given: a complete v2 producer trace and current native library.
    output = tmp_path / "pilot"
    command = [sys.executable, "-B", "-m", "sim.cycle.sequence_trace_cli", "replay",
               "--trace", str(TRACE), "--library", str(library),
               "--through-parent-index", "1", "--pilot-work-count", "2",
               "--expected-work-count", "2",
               "--offer-policy", "back-to-back", "--record-events", "0",
               "--output", str(output)]
    # When: two producer-ordered works run in one new child process.
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=60, check=False)
    # Then: the emitted work IDs are native completions in generation one.
    assert result.returncode == 0, result.stderr
    progress = json.loads((output / "progress.json").read_text())
    rows = [json.loads(line) for line in (output / "works.jsonl").read_text().splitlines()]
    summary = json.loads((output / "result.json").read_text())
    assert progress["completed_work_ids"] == [0, 1]
    assert [row["work_id"] for row in rows] == [0, 1]
    assert all(row["generation"] == 1 for row in rows)
    assert all(set(row["counters"]) == set(sequence_trace_cli.RESULT_FIELDS) and
               row["result_ready_cycle"] <= row["final_scale_release_cycle"] <=
               row["resource_ready_cycle"] and
               row["event_count"] == row["counters"]["event_count"] > 0 and
               row["counters"]["load_request_count"] == row["counters"]["load_response_count"]
               for row in rows)
    assert (summary["schema"], summary["version"]) == (sequence_trace_cli.RESULT_SCHEMA, 2)
    assert summary["final_domain_snapshot_hex"] == rows[-1]["domain_snapshot_hex"]
    assert summary["final_row_pressure_hex"] == rows[-1]["row_pressure_hex"]
    assert summary["cumulative_counters"]["logical_work_count"] == 2
    assert summary["status"] == "PILOT_PARTIAL"
    assert summary["production_admitted"] is False
    assert {"sim/cycle/sequence_binding_abi.py", "sim/cycle/cli.py",
            "sim/cycle/reconstruct_graph.py"} <= summary["source_sha256"].keys()
    options = argparse.Namespace(trace=TRACE, library=library, through_parent_index=1,
                                 pilot_work_count=2, expected_work_count=2)
    assert sequence_trace_cli.valid_replay_result(output, options, progress)
    for field, bad in (
        ("schema", "unknown"), ("version", True), ("selected_work_count", 1),
        ("completed_work_ids", [1, 0]), ("status", "DIAGNOSTIC_PASS"),
        ("trace_sha256", "0" * 64), ("library_sha256", "0" * 64),
        ("state_domain_revision", "GUARDED_A8D16_TAG5_ROW_LT6_REVIEWED_V3"),
        ("final_domain_snapshot_hex", rows[0]["domain_snapshot_hex"]),
        ("cumulative_counters", {**summary["cumulative_counters"], "event_count": 0}),
        ("source_sha256", {**summary["source_sha256"], "sim/cycle/cli.py": "0" * 64}),
    ):
        (output / "result.json").write_text(json.dumps(summary | {field: bad}))
        assert not sequence_trace_cli.valid_replay_result(output, options, progress), field
    (output / "result.json").write_text(json.dumps(summary))
    peak = sequence_binding.DomainSnapshot.from_buffer_copy(bytes.fromhex(rows[0]["domain_snapshot_hex"]))
    peak.max_tag_occupancy = 6
    overshoot = sequence_binding.DomainSnapshot.from_buffer_copy(bytes.fromhex(rows[0]["domain_snapshot_hex"]))
    overshoot.cursor += 1
    invalid_abi = sequence_binding.DomainSnapshot.from_buffer_copy(bytes.fromhex(rows[0]["domain_snapshot_hex"]))
    invalid_abi.abi_version += 1
    pressure = sequence_binding.RowPressure.from_buffer_copy(bytes.fromhex(rows[0]["row_pressure_hex"]))
    pressure.max_row_occupancy = 6
    for field, bad in (
        ("final_scale_release_cycle", rows[0]["result_ready_cycle"] - 1),
        ("next_scratchpad_half", 2),
        ("call_id", rows[0]["call_id"] + 1),
        ("stripe_id", -1),
        ("request_available_cycle", rows[0]["offered_cycle"] + 1),
        ("port_offer_cycle", rows[0]["offered_cycle"] + 1),
        ("request_available_cycle", rows[0]["offered_cycle"] + 1),
        ("port_offer_cycle", rows[0]["offered_cycle"] + 1),
        ("domain_snapshot_hex", bytes(peak).hex()),
        ("domain_snapshot_hex", bytes(overshoot).hex()),
        ("domain_snapshot_hex", bytes(invalid_abi).hex()),
        ("row_pressure_hex", bytes(pressure).hex()),
        ("counters", {**rows[0]["counters"], "load_response_count":
                       rows[0]["counters"]["load_response_count"] + 1}),
    ):
        changed_rows = [rows[0] | {field: bad}, *rows[1:]]
        (output / "works.jsonl").write_text("\n".join(map(json.dumps, changed_rows)) + "\n")
        assert not sequence_trace_cli.valid_replay_result(output, options, progress), field
    reordered_ids = [1, 0]
    reordered_rows = [row | {"work_id": work_id, "parent_id": 1 - work_id}
                      for row, work_id in zip(rows, reordered_ids, strict=True)]
    (output / "works.jsonl").write_text("\n".join(map(json.dumps, reordered_rows)) + "\n")
    (output / "result.json").write_text(json.dumps(summary | {
        "completed_work_ids": reordered_ids, "last_work_id": 0}))
    reordered_progress = progress | {"completed_work_ids": reordered_ids, "last_work_id": 0}
    assert not sequence_trace_cli.valid_replay_result(output, options, reordered_progress)


def test_replay_domain_failure_separates_native_report_from_validated_works(
    tmp_path: Path, library: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Portable loader/boundary unit seam over real native work, not producer/pressure evidence.
    trace = tmp_path / "unit-trace"
    trace.write_text("unit-only loaded work descriptors")
    works = tuple(sequence_trace_cli.Work(
        phase=0, operation_id=0, parent_id=index, node_id=0, call_id=index, identity=index,
        layer="unit", operation="MUL_MAT", provenance="dense", scope="unit", parent_rows=1,
        row_begin=0, stripe_id=None, inputs=(1, 1, 1, 1, 1, 1, 1, 1, 4, 1), original_k=None,
        runs=(), row_map=(), residual_work_revision=None) for index in range(2))
    monkeypatch.setattr(sequence_trace_cli, "load_trace",
                        lambda _path: ("a8w8-d16-hp1", works, sequence_trace_cli.sha256(trace)))
    original_snapshot = sequence_binding.SequenceSession.domain_snapshot

    def outside_reviewed(session: sequence_binding.SequenceSession) -> sequence_binding.DomainSnapshot:
        snapshot = original_snapshot(session)
        if snapshot.cursor and session.status().accepted_work_id == 1:
            snapshot.max_tag_occupancy = 5
        return snapshot

    monkeypatch.setattr(sequence_binding.SequenceSession, "domain_snapshot", outside_reviewed)
    output = tmp_path / "outside-domain"
    monkeypatch.setattr(sys, "argv", ["sequence_trace_cli", "replay",
        "--trace", str(trace), "--library", str(library), "--pilot-work-count", "2",
        "--expected-work-count", "2", "--offer-policy", "back-to-back", "--record-events", "0",
        "--output", str(output)])
    assert sequence_trace_cli.main() == 1
    failure = json.loads((output / "failure.json").read_text())
    progress = json.loads((output / "progress.json").read_text())
    assert failure["classification"] == "OUTSIDE_REVIEWED_STATE_DOMAIN"
    assert failure["failed_predicates"] == ["tag_peak_le_four"]
    assert failure["native_completed_report_work_id"] == 1
    assert failure["validated_completed_work_ids"] == progress["completed_work_ids"] == [0]
    assert progress["native_completed_report_work_id"] == 1 and progress["status"] == "FAILED"
    assert failure["production_admitted"] is False
    assert "capacity six is not validation permission" in failure["domain_scope"]["implemented"]
    assert len((output / "works.jsonl").read_text().splitlines()) == 1
    assert not (output / "result.json").exists() and not (output / "pending-result.json").exists()


def test_watchdog_replay_stages_candidate_without_normal_result(tmp_path: Path, library: Path) -> None:
    output = tmp_path / "pending"
    args = argparse.Namespace(trace=TRACE, library=library, through_parent_index=1,
                              pilot_work_count=2, expected_work_count=2,
                              offer_policy="back-to-back", record_events=0,
                              output=output, watchdog_pending=True)
    assert sequence_trace_cli.replay(args) == 0
    assert (output / "pending-result.json").exists()
    assert not (output / "result.json").exists()
    progress = json.loads((output / "progress.json").read_text())
    assert progress["status"] == "PENDING_VALIDATION"
    assert sequence_trace_cli.valid_replay_result(output, args, progress, pending=True)
    (output / "pending-result.json").write_text("{}\n")
    assert sequence_trace_cli.verify_candidate(args) == 1
    assert not (output / "result.json").exists()


def test_watchdog_publishes_terminal_progress_after_verified_result(
    tmp_path: Path, library: Path,
) -> None:
    output, status = tmp_path / "verified", tmp_path / "watchdog.json"
    replay = [sys.executable, "-B", "-m", "sim.cycle.sequence_trace_cli", "replay",
              "--trace", str(TRACE), "--library", str(library),
              "--through-parent-index", "1", "--pilot-work-count", "2",
              "--expected-work-count", "2", "--offer-policy", "back-to-back",
              "--record-events", "0", "--output", str(output)]
    command = [sys.executable, "-B", "-m", "sim.cycle.sequence_trace_cli", "watchdog",
               "--timeout-seconds", "30", "--status", str(status), "--", *replay]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=60, check=False)
    assert result.returncode == 0, result.stderr
    receipt = json.loads(status.read_text())
    summary = json.loads((output / "result.json").read_text())
    progress = json.loads((output / "progress.json").read_text())
    assert receipt["status"] == "PASS" and receipt["verifier_reaped"]
    assert progress["status"] == summary["status"] == "PILOT_PARTIAL"
    assert progress["completed_work_ids"] == summary["completed_work_ids"] == [0, 1]


def test_watchdog_bounds_slow_verifier_without_transient_result(
    tmp_path: Path, library: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    output, status = tmp_path / "pending", tmp_path / "watchdog.json"
    replay = [sys.executable, "-B", "-m", "sim.cycle.sequence_trace_cli", "replay",
              "--trace", str(TRACE), "--library", str(library),
              "--through-parent-index", "1", "--pilot-work-count", "2",
              "--expected-work-count", "2", "--offer-policy", "back-to-back",
              "--record-events", "0", "--output", str(output)]
    real_popen = subprocess.Popen
    verifier_started: list[bool] = []

    def launch(argv: list[str], *, start_new_session: bool,
               stdout: int | None = None, stderr: int | None = None) -> subprocess.Popen[bytes]:
        if "_verify-candidate" in argv:
            verifier_started.append(True)
            argv = [sys.executable, "-B", "-c", "import time; time.sleep(30)"]
        return real_popen(argv, start_new_session=start_new_session, stdout=stdout, stderr=stderr)

    monkeypatch.setattr(subprocess, "Popen", launch)
    seen_normal: list[bool] = []
    stop = threading.Event()

    def observe() -> None:
        while not stop.is_set():
            if (output / "result.json").exists():
                seen_normal.append(True)
                break
            time.sleep(0.005)

    observer = threading.Thread(target=observe)
    observer.start()
    started = time.monotonic()
    try:
        code = sequence_trace_cli.watchdog(argparse.Namespace(
            command=replay, timeout_seconds=3.0, status=status))
    finally:
        stop.set()
        observer.join(timeout=1)
    receipt = json.loads(status.read_text())
    assert code == 1 and verifier_started
    assert receipt["status"] == "TIMEOUT"
    assert receipt["child_returncode"] == 0 and receipt["child_reaped"]
    assert receipt["verifier_reaped"] and receipt["verifier_returncode"] != 0
    assert 3.0 <= receipt["elapsed_seconds"] < 3.5 and time.monotonic() - started < 3.5
    assert not seen_normal and not (output / "result.json").exists()
    assert (output / "pending-result.json").exists()


def test_watchdog_link_enospc_leaves_only_pending_result(
    tmp_path: Path, library: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    output, status = tmp_path / "pending", tmp_path / "watchdog.json"
    replay = [sys.executable, "-B", "-m", "sim.cycle.sequence_trace_cli", "replay",
              "--trace", str(TRACE), "--library", str(library),
              "--through-parent-index", "1", "--pilot-work-count", "2",
              "--expected-work-count", "2", "--offer-policy", "back-to-back",
              "--record-events", "0", "--output", str(output)]

    def no_space(_source: Path, _target: Path) -> None:
        raise OSError(errno.ENOSPC, "No space left on device")

    real_popen = subprocess.Popen

    def launch(argv: list[str], *, start_new_session: bool,
               stdout: int | None = None, stderr: int | None = None) -> subprocess.Popen[bytes]:
        if "_publish-candidate" in argv:
            script = ("import os,sys,errno; from sim.cycle import sequence_trace_cli as cli; "
                      "os.link=lambda a,b: (_ for _ in ()).throw("
                      "OSError(errno.ENOSPC, 'No space left on device')); "
                      f"sys.argv={argv[3:]!r}; raise SystemExit(cli.main())")
            argv = [sys.executable, "-B", "-c", script]
        return real_popen(argv, start_new_session=start_new_session, stdout=stdout, stderr=stderr)

    monkeypatch.setattr(subprocess, "Popen", launch)
    monkeypatch.setattr(sequence_trace_cli.os, "link", no_space)
    assert sequence_trace_cli.watchdog(argparse.Namespace(
        command=replay, timeout_seconds=30.0, status=status)) == 1
    receipt = json.loads(status.read_text())
    assert receipt["status"] == "FAILED" and receipt["verifier_reaped"]
    assert receipt["publisher_reaped"] and receipt["publisher_returncode"] == 1
    assert (output / "pending-result.json").exists()
    assert not (output / "result.json").exists()


@pytest.mark.parametrize("delay_after_link", (False, True))
def test_watchdog_bounds_final_link_publication(
    tmp_path: Path, library: Path, monkeypatch: pytest.MonkeyPatch,
    delay_after_link: bool,
) -> None:
    # Given: real two-work replay with a filesystem link delayed by two seconds.
    output, status = tmp_path / "pending", tmp_path / "watchdog.json"
    replay = [sys.executable, "-B", "-m", "sim.cycle.sequence_trace_cli", "replay",
              "--trace", str(TRACE), "--library", str(library),
              "--through-parent-index", "1", "--pilot-work-count", "2",
              "--expected-work-count", "2", "--offer-policy", "back-to-back",
              "--record-events", "0", "--output", str(output)]
    real_link, real_popen = os.link, subprocess.Popen
    marker = tmp_path / "link-started"

    def slow_link(source: Path, target: Path) -> None:
        marker.write_text(str(os.getpid()))
        if delay_after_link:
            real_link(source, target)
        time.sleep(2)
        if not delay_after_link:
            real_link(source, target)

    def launch(argv: list[str], *, start_new_session: bool,
               stdout: int | None = None, stderr: int | None = None) -> subprocess.Popen[bytes]:
        if "_publish-candidate" in argv:
            operation = ("original(a,b), time.sleep(2)" if delay_after_link else
                         "time.sleep(2), original(a,b)")
            script = ("import os,sys,time; from pathlib import Path; "
                      "from sim.cycle import sequence_trace_cli as cli; original=os.link; "
                      f"marker=Path({str(marker)!r}); "
                      "os.link=lambda a,b: (marker.write_text(str(os.getpid())), "
                      f"{operation})[-1]; "
                      f"sys.argv={argv[3:]!r}; raise SystemExit(cli.main())")
            argv = [sys.executable, "-B", "-c", script]
        return real_popen(argv, start_new_session=start_new_session, stdout=stdout, stderr=stderr)

    monkeypatch.setattr(os, "link", slow_link)
    monkeypatch.setattr(subprocess, "Popen", launch)
    observations: list[bool] = []
    stop = threading.Event()

    def observe() -> None:
        while not stop.is_set():
            observations.append((output / "result.json").exists())
            time.sleep(0.005)

    observer = threading.Thread(target=observe)
    observer.start()
    # When: the same 2.5-second deadline covers replay, verification and publication.
    started = time.monotonic()
    try:
        code = sequence_trace_cli.watchdog(argparse.Namespace(
            command=replay, timeout_seconds=2.5, status=status))
    finally:
        observations.append((output / "result.json").exists())
        stop.set()
        observer.join(timeout=1)
    receipt = json.loads(status.read_text())
    # Then: precommit timeout stays private; a committed result is never revoked.
    assert marker.exists()
    assert code == (0 if delay_after_link else 1), receipt
    assert receipt["status"] == ("PASS" if delay_after_link else "TIMEOUT"), receipt
    assert receipt["child_returncode"] == receipt["verifier_returncode"] == 0
    assert receipt["child_reaped"] and receipt["verifier_reaped"]
    assert receipt["publisher_reaped"] and receipt["publisher_returncode"] != 0
    assert time.monotonic() - started < 3.5
    assert receipt["elapsed_seconds"] >= 2.5
    assert receipt["cleanup_seconds"] < 0.5
    assert (output / "pending-result.json").exists()
    assert (output / "result.json").exists() == delay_after_link
    assert receipt["result_committed"] == delay_after_link
    assert receipt["cleanup_complete"]
    assert receipt["auxiliary_status"] == ("TIMEOUT" if delay_after_link else None)
    if delay_after_link:
        assert True in observations
        assert all(observations[observations.index(True):])
    else:
        assert not any(observations)


@pytest.mark.parametrize("fault", ("serialization", "occupied", "replaced", "mutated", "after-link",
                                  "postcommit-corrupt", "digest-read-failure"))
def test_watchdog_publication_faults_respect_commit_boundary(
    tmp_path: Path, library: Path, monkeypatch: pytest.MonkeyPatch, fault: str,
) -> None:
    output, status = tmp_path / "pending", tmp_path / "watchdog.json"
    replay = [sys.executable, "-B", "-m", "sim.cycle.sequence_trace_cli", "replay",
              "--trace", str(TRACE), "--library", str(library),
              "--pilot-work-count", "2", "--expected-work-count", "2",
              "--offer-policy", "back-to-back", "--record-events", "0", "--output", str(output)]
    real_popen = subprocess.Popen

    def launch(argv: list[str], *, start_new_session: bool,
               stdout: int | None = None, stderr: int | None = None) -> subprocess.Popen[bytes]:
        if "_publish-candidate" in argv:
            pending = output / "pending-result.json"
            if fault == "occupied":
                (output / "result.json").write_text("foreign owner\n")
            elif fault == "replaced":
                replacement = output / "replacement.json"
                replacement.write_bytes(pending.read_bytes())
                replacement.replace(pending)
            elif fault == "mutated":
                pending.write_text(pending.read_text() + " ")
            elif fault != "digest-read-failure":
                operation = (
                    "original=json.dumps; json.dumps=lambda *a,**k: (time.sleep(30), original(*a,**k))[1]; "
                    if fault == "serialization" else
                    "original=os.link; os.link=lambda a,b: (original(a,b), Path(b).write_text('corrupt'))[1]; "
                    if fault == "postcommit-corrupt" else
                    "original=os.link; os.link=lambda a,b: (original(a,b), "
                    "(_ for _ in ()).throw(OSError('auxiliary fault')))[1]; ")
                script = ("import json,os,sys,time; from pathlib import Path; "
                          "from sim.cycle import sequence_trace_cli as cli; "
                          + operation + f"sys.argv={argv[3:]!r}; raise SystemExit(cli.main())")
                argv = [sys.executable, "-B", "-c", script]
        return real_popen(argv, start_new_session=start_new_session, stdout=stdout, stderr=stderr)

    monkeypatch.setattr(subprocess, "Popen", launch)
    if fault == "digest-read-failure":
        original_sha256 = sequence_trace_cli.sha256

        def observed_sha256(path: Path) -> str:
            if path.name == "result.json":
                raise OSError("injected result inspection failure")
            return original_sha256(path)

        monkeypatch.setattr(sequence_trace_cli, "sha256", observed_sha256)
    code = sequence_trace_cli.watchdog(argparse.Namespace(
        command=replay, timeout_seconds=3.0 if fault == "serialization" else 30.0, status=status))
    receipt = json.loads(status.read_text())
    committed = fault in ("after-link", "postcommit-corrupt", "digest-read-failure")
    valid = fault in ("after-link", "digest-read-failure")
    assert code == (0 if valid else 1), receipt
    assert receipt["result_committed"] == committed
    assert receipt["result_valid"] == valid
    assert receipt["cleanup_complete"] and receipt["publisher_reaped"]
    assert receipt["child_returncode"] == receipt["verifier_returncode"] == 0
    if committed:
        assert receipt["status"] == ("PASS" if valid else "FAILED")
        assert receipt["auxiliary_status"] == "FAILED"
        assert (output / "result.json").samefile(output / "pending-result.json")
        if valid:
            assert receipt["progress"]["status"] == "PILOT_PARTIAL"
        if fault == "postcommit-corrupt":
            assert (output / "result.json").read_text() == "corrupt"
    elif fault == "occupied":
        assert (output / "result.json").read_text() == "foreign owner\n"
        assert receipt["status"] == "FAILED"
    else:
        assert not (output / "result.json").exists()
        assert receipt["status"] == ("TIMEOUT" if fault == "serialization" else "FAILED")


def test_watchdog_rejects_existing_replay_output(tmp_path: Path) -> None:
    output, status = tmp_path / "existing", tmp_path / "watchdog.json"
    output.mkdir()
    (output / "owner.txt").write_text("unchanged")
    replay = [sys.executable, "-B", "-m", "sim.cycle.sequence_trace_cli", "replay",
              "--output", str(output)]
    with pytest.raises(NpuTraceError, match="replay output must be fresh"):
        sequence_trace_cli.watchdog(argparse.Namespace(
            command=replay, timeout_seconds=5.0, status=status))
    assert (output / "owner.txt").read_text() == "unchanged"
    assert not status.exists()


def test_watchdog_receipt_failure_preserves_completed_publication(
    tmp_path: Path, library: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: a real replay whose auxiliary receipt cannot be written.
    output, status = tmp_path / "published", tmp_path / "watchdog.json"
    replay = [sys.executable, "-B", "-m", "sim.cycle.sequence_trace_cli", "replay",
              "--trace", str(TRACE), "--library", str(library),
              "--pilot-work-count", "2", "--expected-work-count", "2",
              "--offer-policy", "back-to-back", "--record-events", "0", "--output", str(output)]
    original_publish = sequence_trace_cli.publish

    def fail_receipt(path: Path, value: dict[str, object]) -> None:
        if path == status:
            raise OSError(errno.ENOSPC, "No space left on device")
        original_publish(path, value)

    monkeypatch.setattr(sequence_trace_cli, "publish", fail_receipt)
    # When: verification and atomic result publication precede the receipt failure.
    code = sequence_trace_cli.watchdog(argparse.Namespace(
        command=replay, timeout_seconds=30.0, status=status))
    # Then: publication remains complete; the auxiliary failure is reported separately.
    assert code == 0
    assert not status.exists()
    assert (output / "result.json").samefile(output / "pending-result.json")
    receipt = json.loads(capsys.readouterr().err)
    assert receipt["status"] == "PASS" and receipt["result_committed"]
    assert receipt["result_valid"] and receipt["cleanup_complete"]
    assert receipt["auxiliary_status"] == "RECEIPT_FAILED"
    assert "No space left on device" in receipt["receipt_error"]


def test_watchdog_cleanup_failure_is_auxiliary_after_publication(
    tmp_path: Path, library: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a real publisher signals its commit and remains alive until cleanup.
    output, status = tmp_path / "published", tmp_path / "watchdog.json"
    replay = [sys.executable, "-B", "-m", "sim.cycle.sequence_trace_cli", "replay",
              "--trace", str(TRACE), "--library", str(library),
              "--pilot-work-count", "2", "--expected-work-count", "2",
              "--offer-policy", "back-to-back", "--record-events", "0", "--output", str(output)]
    reader, writer = os.pipe()
    real_popen = subprocess.Popen
    publishers: list[subprocess.Popen[bytes]] = []

    def launch(argv: list[str], *, start_new_session: bool,
               stdout: int | None = None, stderr: int | None = None) -> subprocess.Popen[bytes]:
        if "_publish-candidate" not in argv:
            return real_popen(argv, start_new_session=start_new_session, stdout=stdout, stderr=stderr)
        script = ("import os,sys,signal; from sim.cycle import sequence_trace_cli as cli; "
                  "original=os.link; os.link=lambda a,b: (original(a,b), "
                  f"os.write({writer},b'1'), signal.pause())[-1]; "
                  f"sys.argv={argv[3:]!r}; raise SystemExit(cli.main())")
        process = real_popen([sys.executable, "-B", "-c", script],
                             start_new_session=start_new_session, pass_fds=(writer,),
                             stdout=stdout, stderr=stderr)
        publishers.append(process)
        original_wait = process.wait
        interrupted = False

        def interrupt_after_commit(timeout: float | None = None) -> int:
            nonlocal interrupted
            if not interrupted:
                assert select.select([reader], [], [], 10)[0]
                assert os.read(reader, 1) == b"1"
                interrupted = True
                raise KeyboardInterrupt
            return original_wait(timeout=timeout)

        monkeypatch.setattr(process, "wait", interrupt_after_commit)
        return process

    def cleanup_failure(_process: subprocess.Popen[bytes]) -> float:
        raise PermissionError(errno.EPERM, "injected owned-child cleanup failure")

    monkeypatch.setattr(subprocess, "Popen", launch)
    monkeypatch.setattr(sequence_trace_cli, "reap_owned", cleanup_failure)
    try:
        # When: cancellation and a cleanup error occur strictly after the commit signal.
        code = sequence_trace_cli.watchdog(argparse.Namespace(
            command=replay, timeout_seconds=30.0, status=status))
        # Then: the valid result remains complete, with cleanup failure explicitly separate.
        receipt = json.loads(status.read_text())
        assert code == 0 and receipt["status"] == "PASS"
        assert receipt["result_committed"] and receipt["result_valid"]
        assert receipt["auxiliary_status"] == "CLEANUP_FAILED"
        assert not receipt["cleanup_complete"] and not receipt["publisher_reaped"]
        assert "injected owned-child cleanup failure" in receipt["publisher_error"]
        assert (output / "result.json").samefile(output / "pending-result.json")
    finally:
        for process in publishers:
            process.kill()
            process.wait(timeout=5)
        os.close(reader)
        os.close(writer)


def test_watchdog_rejects_publisher_zero_exit_without_publication(
    tmp_path: Path, library: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    output, status = tmp_path / "pending", tmp_path / "watchdog.json"
    replay = [sys.executable, "-B", "-m", "sim.cycle.sequence_trace_cli", "replay",
              "--trace", str(TRACE), "--library", str(library),
              "--pilot-work-count", "2", "--expected-work-count", "2",
              "--offer-policy", "back-to-back", "--record-events", "0", "--output", str(output)]
    real_popen = subprocess.Popen

    def launch(argv: list[str], *, start_new_session: bool,
               stdout: int | None = None, stderr: int | None = None) -> subprocess.Popen[bytes]:
        if "_publish-candidate" in argv:
            argv = [sys.executable, "-B", "-c", "raise SystemExit(0)"]
        return real_popen(argv, start_new_session=start_new_session, stdout=stdout, stderr=stderr)

    monkeypatch.setattr(subprocess, "Popen", launch)
    assert sequence_trace_cli.watchdog(argparse.Namespace(
        command=replay, timeout_seconds=30.0, status=status)) == 1
    receipt = json.loads(status.read_text())
    assert receipt["status"] == "FAILED" and receipt["publisher_returncode"] == 0
    assert receipt["publisher_reaped"]
    assert (output / "pending-result.json").exists()
    assert not (output / "result.json").exists()


def test_watchdog_reaps_only_its_timed_out_child(tmp_path: Path) -> None:
    # Given: a child that cannot complete inside a one-second total budget.
    status = tmp_path / "watchdog.json"
    command = [sys.executable, "-B", "-m", "sim.cycle.sequence_trace_cli", "watchdog",
               "--timeout-seconds", "1", "--status", str(status), "--",
               sys.executable, "-B", "-c", "import time; time.sleep(30)"]
    # When: the separate watchdog owns that child to its deadline.
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=10, check=False)
    # Then: the child is reaped and no PASS receipt is emitted.
    assert result.returncode != 0
    receipt = json.loads(status.read_text())
    assert receipt["status"] == "TIMEOUT"
    assert receipt["child_reaped"] is True


def test_watchdog_rejects_zero_exit_without_replay_result(tmp_path: Path) -> None:
    status = tmp_path / "watchdog.json"
    output = tmp_path / "missing-result"
    command = [sys.executable, "-B", "-m", "sim.cycle.sequence_trace_cli", "watchdog",
               "--timeout-seconds", "5", "--status", str(status), "--",
               sys.executable, "-B", "-m", "sim.cycle.sequence_trace_cli", "replay",
               "--help", "--output", str(output)]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=10, check=False)
    assert result.returncode != 0
    assert json.loads(status.read_text())["status"] == "FAILED"


def test_watchdog_rejects_zero_exit_with_equals_output_and_no_result(tmp_path: Path) -> None:
    # Given: a replay-shaped child that exits successfully before publishing any result.
    status = tmp_path / "watchdog.json"
    output = tmp_path / "missing-result"
    command = [sys.executable, "-B", "-m", "sim.cycle.sequence_trace_cli", "watchdog",
               "--timeout-seconds", "5", "--status", str(status), "--",
               sys.executable, "-B", "-m", "sim.cycle.sequence_trace_cli", "replay",
               "--help", f"--output={output}"]
    # When: the child is invoked through the real watchdog command surface.
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=10, check=False)
    # Then: zero exit without result remains a failure for the equals spelling too.
    assert result.returncode != 0
    receipt = json.loads(status.read_text())
    assert receipt["status"] == "FAILED" and receipt["progress"] is None
    assert not (output / "result.json").exists()


def test_source_copy_mutations_fail_publication_check(tmp_path: Path) -> None:
    # Given: an isolated copy of the CLI's actual source closure.
    for name in sequence_trace_cli.SOURCE_NAMES:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    captured = sequence_trace_cli.source_hashes(tmp_path)
    # When/Then: changing each direct dependency rejects the captured closure.
    for name in ("sim/cycle/sequence_binding_abi.py", "sim/cycle/cli.py",
                 "sim/cycle/reconstruct_graph.py"):
        target = tmp_path / name
        original = target.read_bytes()
        target.write_bytes(original + b"\n")
        with pytest.raises(NpuTraceError, match="Python source changed"):
            sequence_trace_cli.verify_source_hashes(tmp_path, captured)
        target.write_bytes(original)


def test_watchdog_rejects_embedded_replay_tokens(tmp_path: Path) -> None:
    status = tmp_path / "watchdog.json"
    output = tmp_path / "forged-result"
    script = ("from pathlib import Path; import sys; "
              "p=Path(sys.argv[-1].partition('=')[2]); p.mkdir(); "
              "(p/'result.json').write_text('{}\\n')")
    command = [sys.executable, "-B", "-m", "sim.cycle.sequence_trace_cli", "watchdog",
               "--timeout-seconds", "5", "--status", str(status), "--",
               sys.executable, "-B", "-c", script,
               "-m", "sim.cycle.sequence_trace_cli", "replay", f"--output={output}"]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=10, check=False)
    assert result.returncode != 0
    assert not output.exists()
    assert not status.exists() or json.loads(status.read_text())["status"] != "PASS"
