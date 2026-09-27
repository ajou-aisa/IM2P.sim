from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from contextlib import closing
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

import pytest

from sim.cycle import execution_cli, scheduler
from sim.cycle.execution_ir import ExecutionError, ir_record
from sim.cycle.execution_sequence_admission import ProductionStatefulAdmission
from sim.cycle.execution_sequence_provider import StatefulSequenceProvider
from sim.cycle.execution_services import services_record
from sim.cycle.npu_trace_schema import Record, object_value
from sim.cycle.scheduler import (
    Scenario,
    ScheduleInputs,
    TimingProvider,
    validate_environment,
)
from sim.cycle.scheduler_sqlite import SqliteScheduleInputs, schedule_sqlite
from sim.tests.cycle import test_execution_schedule_verifier as verifier_fixtures
from sim.tests.cycle.test_stateful_schedule import (
    _npu_node,
    _SourceProbeProvider,
    _write_store,
)


class _CliProbe(_SourceProbeProvider):
    """CPU-only verifier seam; this does not establish native admission."""

    def __init__(self) -> None:
        super().__init__()
        self._closed = False

    def close(self) -> None:
        self._closed = True


@pytest.mark.parametrize("storage", ["json", "sqlite"])
@pytest.mark.parametrize("node_id", ["cpu", "application:sample:0"])
def test_fresh_diagnostic_cli_verifier_replays_all_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, storage: str, node_id: str,
) -> None:
    # Given: an existing source-bound diagnostic schedule with CPU/application rows.
    args, old, _ = verifier_fixtures.ScheduleVerifierTests().fixture(tmp_path)
    args.clock_selection = None
    args.profile = "a8w8-d16-hp1"
    args.frequency_hz = 1_000_000_000
    args.stateful_diagnostic = True
    args.bundle = tmp_path / f"bundle.{storage}"
    args.schedule = tmp_path / f"schedule.{storage}"
    scenario = Scenario(args.frequency_hz, "STATEFUL_DIAGNOSTIC", None, args.profile)
    inputs = ScheduleInputs(old.ir, old.services, _CliProbe())
    if storage == "sqlite":
        _write_store(args.bundle, inputs, "PRODUCER_DECLARED")
        schedule_sqlite(args.bundle, args.schedule, SqliteScheduleInputs(inputs.npu_provider, scenario))
    else:
        args.bundle.write_text(json.dumps({"schema": "im2p-execution-bundle", "version": 1,
            "ir": ir_record(inputs.ir), "services": services_record(inputs.services),
            "dataset_sha256": inputs.ir.source_sha256}))
        args.schedule.write_text(json.dumps(execution_cli.schedule_json(args, inputs.npu_provider, scenario)))
    created: list[_CliProbe] = []

    def fresh_provider(_args: execution_cli.Arguments) -> _CliProbe:
        bound = _CliProbe()
        created.append(bound)
        return bound

    monkeypatch.setattr(execution_cli, "provider", fresh_provider)
    assert execution_cli.verify_schedule(args)["status"] == "PASS"
    if storage == "sqlite":
        with closing(sqlite3.connect(args.schedule)) as database:
            row = json.loads(database.execute("SELECT body FROM results WHERE identity=?", (node_id,)).fetchone()[0])
            row["result_ready_ns"]["numerator"] += 1
            database.execute("UPDATE results SET body=? WHERE identity=?", (json.dumps(row), node_id))
            database.commit()
    else:
        document = json.loads(args.schedule.read_text())
        row = next(row for row in document["nodes"] if row["node_id"] == node_id)
        row["result_ready_ns"]["numerator"] += 1
        args.schedule.write_text(json.dumps(document))

    # When/Then: fresh replay rejects the altered row and closes both provider lifetimes.
    with pytest.raises(ExecutionError, match="node/endpoint"):
        execution_cli.verify_schedule(args)
    assert len(created) == 2 and created[0] is not created[1]
    assert all(bound._closed for bound in created)


@pytest.mark.parametrize("action", ["schedule", "verify-schedule"])
def test_official_cli_exposes_disjoint_stateful_selector(action: str) -> None:
    # Given/When: users inspect the official command, in a fresh Python process.
    result = subprocess.run([sys.executable, "-B", "-m", "sim.cycle.execution_cli", action, "--help"],
                            capture_output=True, text=True, timeout=30, check=False)
    # Then: both commands expose the same explicit mode and certificate interface.
    assert result.returncode == 0
    for flag in ("--stateful-sequence-certificate", "--stateful-evidence-root", "--stateful-diagnostic"):
        assert flag in result.stdout


@pytest.mark.parametrize("phase", ["during-schedule", "before-publication"])
def test_json_source_change_cannot_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str,
) -> None:
    # Given: a diagnostic consumer source copied into a task-owned temporary directory.
    args, old, _ = verifier_fixtures.ScheduleVerifierTests().fixture(tmp_path)
    probe = _CliProbe()
    inputs = ScheduleInputs(old.ir, old.services, probe)
    scenario = Scenario(1_000_000_000, "STATEFUL_DIAGNOSTIC", None, "a8w8-d16-hp1")
    copy = tmp_path / "scheduler.py"
    copy.write_bytes(Path(scheduler.__file__).read_bytes())
    monkeypatch.setattr(scheduler, "__file__", str(copy))

    def drift() -> None:
        copy.write_bytes(copy.read_bytes() + b"\n")

    if phase == "during-schedule":
        monkeypatch.setattr(probe, "verify_complete", drift)
    # When/Then: drift during execution or immediately before link rejects the publication.
    with pytest.raises(ExecutionError, match="source binding"):
        record = scheduler.schedule(inputs, scenario).record()
        monkeypatch.setattr(probe, "verify_complete", drift)
        execution_cli.publish(args.schedule, record, probe)
    assert not args.schedule.exists()


@pytest.mark.parametrize("failure", [ExecutionError("native fault"), sqlite3.OperationalError("disk full"), KeyboardInterrupt()])
def test_cli_closes_stateful_provider_on_failed_schedule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: BaseException,
) -> None:
    # Given: a provider already owned by the official CLI before an execution failure.
    bundle, output = tmp_path / "input.json", tmp_path / "schedule.json"
    bundle.write_text("{}")
    probe = _CliProbe()
    monkeypatch.setattr(execution_cli, "provider", lambda _args: probe)
    monkeypatch.setattr(sys, "argv", ["execution_cli", "schedule", "--bundle", str(bundle), "--output", str(output),
        "--cycle-library", str(tmp_path / "unused"), "--stateful-diagnostic", "--profile", "a8w8-d16-hp1",
        "--frequency-hz", "1000000000"])

    def fail(_args: execution_cli.Arguments, _bound: TimingProvider, _scenario: Scenario) -> Record:
        raise failure

    monkeypatch.setattr(execution_cli, "schedule_json", fail)
    # When: the selected execution fails, including cancellation outside the error handler.
    if isinstance(failure, KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt):
            execution_cli.main()
    else:
        assert execution_cli.main() == 1
    # Then: no final artifact or native lifetime survives.
    assert probe._closed and not output.exists()


def test_diagnostic_identity_cannot_be_promoted_by_scope_string() -> None:
    # Given: a concrete diagnostic provider with a genuine diagnostic admission type.
    probe = _CliProbe()
    # When/Then: a production scenario is rejected before clock or native access.
    with pytest.raises(ExecutionError, match="diagnostic scenario"):
        validate_environment("BOUND_DATASET", probe, Scenario(1_000_000_000, "RECONSTRUCTED"))


def test_earliest_observes_shared_native_resource_without_calling_native() -> None:
    # Given: one native session drained through cycle 100; no native handle exists in this seam.
    probe = _CliProbe()
    probe.previous_resource_cycle = 100
    engine = scheduler.ServiceExecutor(probe, Scenario(1_000_000_000, "STATEFUL_DIAGNOSTIC"))
    # When/Then: candidate inspection obeys the shared frontier using only committed Python state.
    assert engine.earliest(_npu_node("candidate", 0), Fraction(0)) == 100
    assert probe.previous_resource_cycle == 100


class _ProductionProbe(StatefulSequenceProvider):
    def __init__(self) -> None:
        diagnostic = _CliProbe().admission
        self._admission = ProductionStatefulAdmission(
            diagnostic.scoped, diagnostic.trace_sha256, diagnostic.source_identity,
            diagnostic.binding_sha256, diagnostic.binding_abi_sha256, diagnostic.cli_sha256,
            diagnostic.provider_sha256, diagnostic.admission_sha256, diagnostic.validator_sha256,
            diagnostic.profile, (),
        )
        self.requests = ()

    def verify_complete(self) -> None:
        return


def test_typed_production_branch_retains_real_clock_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a CPU-only production-admission routing seam, not a certificate or native positive.
    args, old, _ = verifier_fixtures.ScheduleVerifierTests().fixture(tmp_path)
    provider = _ProductionProbe()
    inputs = ScheduleInputs(old.ir, old.services, provider)
    scenario = Scenario(1_000_000_000, "RECONSTRUCTED", args.clock_selection, "a8w8-d16-hp1")
    with pytest.raises(ValueError):
        scheduler.schedule(inputs, scenario)
    seen: list[tuple[Path, str]] = []

    def clock(path: Path, profile: str) -> SimpleNamespace:
        seen.append((path, profile))
        return SimpleNamespace(frequency_hz=1_000_000_000)

    monkeypatch.setattr("scripts.evaluation_clock.load_selection", clock)
    # When: the existing clock validator supplies the selected frequency.
    result = scheduler.schedule(inputs, scenario).record()
    # Then: only the typed production branch emits v2 reconstruction with the bound clock frequency.
    assert seen == [(args.clock_selection, scenario.profile)]
    assert result["version"] == 2 and result["validated_service_reconstruction"] is True
    assert object_value(result["service_binding"])["scope"] == "CURRENT_STATEFUL_SEQUENCE"
    assert object_value(result["service_binding"])["npu_frequency_hz"] == 1_000_000_000
