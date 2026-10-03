from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from dataclasses import replace
from fractions import Fraction
from pathlib import Path
from types import ModuleType

import pytest

from sim.cycle import execution_cli, scheduler, scheduler_sqlite
from sim.cycle import stateful_sequence_certificate as certificate
from sim.cycle.certificate_contract import read_document
from sim.cycle.execution_ir import (
    Dependency,
    ExecutionError,
    ExecutionIR,
    Kind,
    Node,
    NodeId,
    ResourceId,
    ServiceId,
)
from sim.cycle.execution_sequence_provider import (
    DiagnosticStatefulProvider,
    StatefulAdmission,
    StatefulProviderError,
    StatefulWindow,
)
from sim.cycle.execution_services import (
    CpuService,
    NpuService,
    NpuWork,
    PhaseTable,
    Services,
    WorkerService,
    parse_services,
    services_record,
)
from sim.cycle.execution_stream import ExecutionStore
from sim.cycle.npu_trace import InputSnapshot
from sim.cycle.npu_trace_schema import object_value, text
from sim.cycle.reconstruct_graph import sha256
from sim.cycle.scheduler import Scenario, ScheduleInputs, schedule
from sim.cycle.scheduler_sqlite import (
    SqliteScheduleInputs,
    schedule_sqlite,
    verify_schedule_sqlite,
)
from sim.cycle.sequence_binding import SourceIdentity
from sim.cycle.stateful_sequence_certificate import ScopedEvidence
from sim.cycle.stateful_sequence_evidence import EvidenceContext

E = Path("/Users/zerogod/aisa-lab/build/im2p-gemmini/stateful-sequence-v2-20260924T033303Z")
ARCHIVE = E.parent
CERTIFICATE = E / "integration/todo16-context-path-review-20260926T123941Z/scoped-certificate.json"
TRACE = E / "build/llama-cpu-functional/tests/cycle-sim-pipeline-5NsPwJrl6ilqZM0w/npu-cycle-trace.jsonl"
REPEATED_TRACE = E / "rtl/task-13-producer-20260924T125443Z/runs/a4w4-d16-hp1/npu-cycle-trace.jsonl"
LIBRARY = E / "build/cycle-a8d16/libim2p_cycle_model.dylib"
CONTEXT = EvidenceContext(
    E, E / "build/cycle-a8d16/libim2p_cycle_model.a", LIBRARY,
    ARCHIVE / "production-drained-sequence-20260923T064750Z/certificate/base-v4/current-certificate.json",
    ARCHIVE / "production-drained-sequence-20260923T064750Z/certificate/run-aware-current/official/production-run-aware-certificate.json",
    ARCHIVE / "production-drained-sequence-resume-20260923T100511Z/service-v1-package-current/service-certificate.json",
)


@pytest.fixture(scope="module")
def audited_native_context() -> tuple[Path, EvidenceContext]:
    path = Path(os.environ.get("IM2P_STATEFUL_CERTIFICATE", str(CERTIFICATE)))
    document = read_document(path)
    parents = object_value(document["parents"])
    return path, EvidenceContext(
        E, Path(text(object_value(document["library"]), "path")), LIBRARY,
        Path(text(object_value(parents["base"]), "path")),
        Path(text(object_value(parents["run_aware"]), "path")),
        Path(text(object_value(parents["service"]), "path")),
        Path(text(object_value(document["evidence_input"]), "path")) if "evidence_input" in document else None,
    )


def _npu_node(identity: str, order: int) -> Node:
    return Node(NodeId(identity), Kind.NPU, identity, "prefill", order, (), ServiceId(identity),
                (ResourceId("npu:0"),))


def _write_store(path: Path, inputs: ScheduleInputs, scope: str = "SYNTHETIC") -> None:
    with closing(sqlite3.connect(path)) as database:
        store = ExecutionStore(database)
        for node in inputs.ir.nodes:
            identity = node.service
            assert identity is not None
            service = (Services({}, {identity: inputs.services.npu[identity]}) if node.kind == Kind.NPU else
                       Services({identity: inputs.services.cpu[identity]}, {}))
            store.add(node, services_record(service))
        store.validate()
        database.execute("INSERT INTO metadata VALUES(?,?)", ("manifest", json.dumps({
            "schema": "im2p-execution-sqlite", "version": 1, "status": "PASS",
            "scope": scope, "node_count": store.count,
        })))
        database.commit()


def test_legacy_v1_json_sqlite_fields_and_acceptance_stay_stable(tmp_path: Path) -> None:
    # Given: two old isolated services sharing the same NPU resource.
    works = {ServiceId(name): NpuWork(ServiceId(name), name, "a8w8-d16-hp1") for name in ("npu:0", "npu:1")}
    table = PhaseTable(1, {(work.profile, work.request_sha256, 0): NpuService(5, 8, "legacy")
                           for work in works.values()})
    inputs = ScheduleInputs(ExecutionIR((_npu_node("npu:0", 0), _npu_node("npu:1", 1)),
                                        "SYNTHETIC", "legacy"), Services({}, works), table)
    scenario = Scenario(1_000_000_000, "SYNTHETIC")
    _write_store(tmp_path / "input.sqlite", inputs)

    # When: both public schedulers run the same v1 services.
    json_result = schedule(inputs, scenario).record()
    sqlite_result = schedule_sqlite(tmp_path / "input.sqlite", tmp_path / "schedule.sqlite",
                                    SqliteScheduleInputs(table, scenario))
    with closing(sqlite3.connect(tmp_path / "schedule.sqlite")) as database:
        rows = [json.loads(body) for (body,) in database.execute("SELECT body FROM results ORDER BY ordinal")]

    # Then: v1 keeps its exact node keys, acceptance semantics, and manifest version.
    assert json_result["version"] == sqlite_result["version"] == 1
    assert json_result["nodes"] == rows
    assert [row["accepted_ns"] for row in rows] == [
        {"numerator": 0, "denominator": 1}, {"numerator": 8, "denominator": 1},
    ]
    assert set(rows[0]) == {"node_id", "accepted_ns", "result_ready_ns", "resource_ready_ns",
                            "accepted_cycle", "evidence_id", "worker_intervals"}
    assert json_result["service_binding"] == sqlite_result["service_binding"] == {"scope": "SYNTHETIC_ONLY"}
    assert json_result["paper_latency_ready"] is sqlite_result["paper_latency_ready"] is False
    assert not {"scheduler_sha256", "scheduler_sqlite_sha256", "execution_cli_sha256"} & set(json_result)
    assert not {"scheduler_sha256", "scheduler_sqlite_sha256", "execution_cli_sha256"} & set(sqlite_result)
    assert Fraction(rows[1]["result_ready_ns"]["numerator"]) == 13


class _SourceProbeProvider(DiagnosticStatefulProvider):
    def __init__(self) -> None:
        self._admission = StatefulAdmission(
            ScopedEvidence("certificate", "library", ()), "trace",
            SourceIdentity("a8w8-d16-hp1", "library", ()),
            "binding", "binding-abi", "cli", "provider", "admission", "validator",
            "a8w8-d16-hp1", (),
        )
        self.requests: tuple[NpuWork, ...] = ()

    def verify_complete(self) -> None:
        return


def _source_probe(tmp_path: Path) -> tuple[Path, Path, SqliteScheduleInputs]:
    worker = WorkerService(ResourceId("cpu:0"), Fraction(1), 0, "thread_cpu_clock", "nanosecond", 1)
    service = CpuService("FULL_CPU", "THREAD_CPU_NS_GANG", (worker,))
    node = Node(NodeId("cpu"), Kind.CPU, "cpu", "prefill", 0, (), ServiceId("cpu"), (ResourceId("cpu:0"),))
    provider = _SourceProbeProvider()
    inputs = ScheduleInputs(ExecutionIR((node,), "BOUND_DATASET", "source"),
                            Services({ServiceId("cpu"): service}, {}), provider)
    source, output = tmp_path / "input.sqlite", tmp_path / "schedule.sqlite"
    _write_store(source, inputs, "PRODUCER_DECLARED")
    return source, output, SqliteScheduleInputs(provider,
                                               Scenario(1_000_000_000, "STATEFUL_DIAGNOSTIC", None, "a8w8-d16-hp1"))


def test_v2_sqlite_manifest_binds_its_own_scheduler_source(tmp_path: Path) -> None:
    # Given: a bound diagnostic SQLite input served through the real scheduler.
    source, output, inputs = _source_probe(tmp_path)
    # When: the result is published through the SQLite path.
    summary = schedule_sqlite(source, output, inputs)
    # Then: its own current consumer source is part of the version-2 manifest.
    assert summary["version"] == 2
    assert summary["scheduler_sqlite_sha256"] == sha256(Path(scheduler_sqlite.__file__))
    with closing(sqlite3.connect(output)) as database:
        manifest = json.loads(database.execute("SELECT body FROM metadata WHERE key='manifest'").fetchone()[0])
    assert manifest["scheduler_sqlite_sha256"] == summary["scheduler_sqlite_sha256"]


def test_v2_sqlite_replay_rejects_changed_own_scheduler_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a published version-2 schedule and a task-owned copy of its SQLite scheduler source.
    source, output, inputs = _source_probe(tmp_path)
    schedule_sqlite(source, output, inputs)
    copy = tmp_path / "scheduler_sqlite.py"
    copy.write_bytes(Path(scheduler_sqlite.__file__).read_bytes() + b"\n# changed before replay\n")
    monkeypatch.setattr(scheduler_sqlite, "__file__", str(copy))
    # When/Then: fresh replay refuses the changed consumer source.
    with pytest.raises(ExecutionError, match="source binding"):
        verify_schedule_sqlite(source, output, SqliteScheduleInputs(_SourceProbeProvider(), inputs.scenario))


@pytest.mark.parametrize("module", [scheduler, scheduler_sqlite, execution_cli],
                         ids=["scheduler", "scheduler_sqlite", "execution_cli"])
def test_v2_sqlite_rejects_consumer_source_change_before_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, module: ModuleType,
) -> None:
    # Given: a real SQLite stage and a task-owned copy of one bound consumer source.
    source, output, inputs = _source_probe(tmp_path)
    assert module.__file__ is not None
    copy = tmp_path / Path(module.__file__).name
    copy.write_bytes(Path(module.__file__).read_bytes())
    monkeypatch.setattr(module, "__file__", str(copy))
    verify_inputs = scheduler_sqlite.verify_input_snapshots

    def change_source_after_run(snapshots: tuple[InputSnapshot, ...], context: str) -> None:
        copy.write_bytes(copy.read_bytes() + b"\n# changed after scheduling\n")
        verify_inputs(snapshots, context)

    monkeypatch.setattr(scheduler_sqlite, "verify_input_snapshots", change_source_after_run)
    # When: those real copied bytes change after scheduling and before output link.
    with pytest.raises(ExecutionError, match="source binding"):
        schedule_sqlite(source, output, inputs)
    # Then: no normal SQLite schedule is published from a stale manifest.
    assert not output.exists()


def _two_work_inputs(provider: DiagnosticStatefulProvider) -> ScheduleInputs:
    works = {work.identity: work for work in provider.requests}
    assert len(works) == 2
    cpu = Node(NodeId("cpu"), Kind.CPU, "cpu", "prefill", 1, (), ServiceId("cpu"), (ResourceId("cpu:0"),))
    application = Node(NodeId("application"), Kind.APPLICATION_CPU, "sample", "prefill", 2,
                       (Dependency(NodeId("cpu")),), ServiceId("application"), (ResourceId("cpu:0"),))
    nodes = (_npu_node(str(provider.requests[0].identity), 0), cpu, application,
             _npu_node(str(provider.requests[1].identity), 3))
    worker = WorkerService(ResourceId("cpu:0"), Fraction(3), 0, "thread_cpu_clock", "nanosecond", 3)
    cpu_services = {ServiceId("cpu"): CpuService("FULL_CPU", "THREAD_CPU_NS_GANG", (worker,)),
                    ServiceId("application"): CpuService("APPLICATION_CPU", "THREAD_CPU_NS_GANG", (worker,))}
    return ScheduleInputs(ExecutionIR(nodes, "BOUND_DATASET", provider.admission.trace_sha256),
                           parse_services(services_record(Services(cpu_services, works))), provider)


@pytest.fixture
def scoped_current_native(monkeypatch: pytest.MonkeyPatch) -> None:
    def scoped(path: Path, context: EvidenceContext) -> ScopedEvidence:
        return ScopedEvidence(sha256(path), sha256(context.library), ())

    monkeypatch.setattr(certificate, "validate", scoped)


def _repeated_work_inputs(provider: DiagnosticStatefulProvider) -> ScheduleInputs:
    works = {work.identity: work for work in provider.requests}
    nodes = tuple(_npu_node(str(work.identity), index) for index, work in enumerate(provider.requests))
    return ScheduleInputs(ExecutionIR(nodes, "BOUND_DATASET", provider.admission.trace_sha256),
                          parse_services(services_record(Services({}, works))), provider)


@pytest.mark.parametrize("sqlite", [False, True], ids=["json", "sqlite"])
def test_stale_same_binding_cache_cannot_publish_stateful_schedule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scoped_current_native: None, sqlite: bool,
) -> None:
    # Given: equal producer bindings can have different real native timing at different legal epochs.
    with DiagnosticStatefulProvider(LIBRARY, REPEATED_TRACE, CERTIFICATE, CONTEXT) as reference:
        first = reference.execute(reference.requests[0], 0)
        _ = reference.execute(reference.requests[1], reference.native_cursor)
        third = reference.execute(reference.requests[2], reference.native_cursor)
        assert first.work.request_sha256 == third.work.request_sha256
        assert first.accepted_cycle != third.accepted_cycle
        assert first.result_ready_cycle - first.accepted_cycle != third.result_ready_cycle - third.accepted_cycle
        assert reference._session.counters().logical_work_count == 3
        assert reference._session.row_pressure().max_row_occupancy < 6

    with DiagnosticStatefulProvider(LIBRARY, REPEATED_TRACE, CERTIFICATE, CONTEXT) as provider:
        inputs = _repeated_work_inputs(provider)
        scenario = Scenario(1_000_000_000, "STATEFUL_DIAGNOSTIC", None, "a4w4-d16-hp1")
        source, output = tmp_path / "input.sqlite", tmp_path / "schedule.sqlite"
        if sqlite:
            _write_store(source, inputs, "PRODUCER_DECLARED")
        native_execute = provider.execute
        cache: dict[str, StatefulWindow] = {}

        def cached_execute(work: NpuWork, offered_cycle: int) -> StatefulWindow:
            if work.identity == provider.requests[-1].identity:
                old = cache[work.request_sha256]
                assert old.work.request_sha256 == work.request_sha256
                assert offered_cycle > old.resource_ready_cycle
                rows = provider._session.row_pressure()
                assert rows.row_count == 0 and rows.max_row_occupancy < 6
                assert provider._session.domain_snapshot().ready_violation_mask == 0
                shift = offered_cycle - old.offered_cycle
                forged = replace(old, work=work, offered_cycle=offered_cycle,
                                 accepted_cycle=old.accepted_cycle + shift,
                                 result_ready_cycle=old.result_ready_cycle + shift,
                                 final_scale_release_cycle=old.final_scale_release_cycle + shift,
                                 resource_ready_cycle=old.resource_ready_cycle + shift)
                provider.invocations += 1
                provider.completed = provider.completed | {work.identity}
                provider.previous_resource_cycle = forged.resource_ready_cycle
                provider.scratchpad_half = forged.next_scratchpad_half
                provider.accumulator_half = forged.next_accumulator_half
                return forged
            window = native_execute(work, offered_cycle)
            cache[work.request_sha256] = window
            return window

        monkeypatch.setattr(provider, "execute", cached_execute)
        # When: a stale cached final work bypasses one native transition on the public JSON or SQLite surface.
        with pytest.raises(StatefulProviderError, match="completion"):
            if sqlite:
                schedule_sqlite(source, output, SqliteScheduleInputs(provider, scenario))
            else:
                schedule(inputs, scenario)
        # Then: superficial ID/count changes cannot replace native cumulative work accounting.
        native_count = provider._session.counters().logical_work_count
        assert provider.faulted and provider.invocations == len(provider.requests)
        assert provider.completed == frozenset(work.identity for work in provider.requests)
        assert native_count < provider.invocations
        if sqlite:
            assert not output.exists()
        print(json.dumps({"surface": "sqlite" if sqlite else "json", "native_count": native_count,
                          "declared_count": provider.invocations, "faulted": provider.faulted,
                          "sqlite_output_exists": output.exists() if sqlite else None}, sort_keys=True))


def test_real_source_bound_two_work_surface(tmp_path: Path, audited_native_context: tuple[Path, EvidenceContext]) -> None:
    # Given: two real producer works and a scoped native stateful session.
    scenario = Scenario(1_000_000_000, "STATEFUL_DIAGNOSTIC", None, "a8w8-d16-hp1")
    certified, context = audited_native_context
    with DiagnosticStatefulProvider(LIBRARY, TRACE, certified, context) as provider:
        inputs = _two_work_inputs(provider)
        _write_store(tmp_path / "input.sqlite", inputs, "PRODUCER_DECLARED")
        pointer = provider._session._handle.value

        # When: the public JSON scheduler selects the work on one live native handle.
        record = schedule(inputs, scenario).record()
        assert provider._session._handle.value == pointer
        assert provider.invocations == 2

    with DiagnosticStatefulProvider(LIBRARY, TRACE, certified, context) as sqlite_provider:
        summary = schedule_sqlite(tmp_path / "input.sqlite", tmp_path / "schedule.sqlite",
                                  SqliteScheduleInputs(sqlite_provider, scenario))
        assert sqlite_provider.invocations == 2
    with closing(sqlite3.connect(tmp_path / "schedule.sqlite")) as database:
        rows = [json.loads(body) for (body,) in database.execute("SELECT body FROM results ORDER BY ordinal")]
    with DiagnosticStatefulProvider(LIBRARY, TRACE, certified, context) as verifier:
        assert verify_schedule_sqlite(tmp_path / "input.sqlite", tmp_path / "schedule.sqlite",
                                      SqliteScheduleInputs(verifier, scenario))

    # Then: request availability, electrical offer, acceptance, and release stay distinct.
    assert record["version"] == summary["version"] == 2
    assert record["nodes"] == rows
    npu_rows = [row for row in rows if row["node_id"].startswith("npu:")]
    assert npu_rows[1]["request_available_ns"] == {"numerator": 0, "denominator": 1}
    assert npu_rows[1]["port_offer_ns"]["numerator"] >= npu_rows[0]["resource_ready_ns"]["numerator"]
    assert all(row["port_offer_ns"]["numerator"] <= row["accepted_ns"]["numerator"] <
               row["result_ready_ns"]["numerator"] <= row["final_scale_release_ns"]["numerator"] <=
               row["resource_ready_ns"]["numerator"] for row in npu_rows)
    assert rows[1]["accepted_ns"]["numerator"] == 0
    assert rows[2]["result_ready_ns"]["numerator"] < npu_rows[0]["resource_ready_ns"]["numerator"]
    assert record["scheduler_sha256"] == summary["scheduler_sha256"]
    assert record["execution_cli_sha256"] == summary["execution_cli_sha256"]
    assert record["service_binding"] == summary["service_binding"]
    assert record["paper_latency_ready"] is summary["paper_latency_ready"] is False
    assert record["validated_service_reconstruction"] is summary["validated_service_reconstruction"] is False
    print(json.dumps({"work_count": len(npu_rows), "native_pointer_preserved": True,
                      "second_request_available": npu_rows[1]["request_available_ns"],
                      "second_port_offer": npu_rows[1]["port_offer_ns"],
                      "second_accepted": npu_rows[1]["accepted_ns"],
                      "paper_latency_ready": record["paper_latency_ready"]}, sort_keys=True))


def test_diagnostic_provider_cannot_enter_production_reconstruction(audited_native_context: tuple[Path, EvidenceContext]) -> None:
    # Given: valid diagnostic native evidence but no production certificate.
    certified, context = audited_native_context
    with DiagnosticStatefulProvider(LIBRARY, TRACE, certified, context) as provider:
        inputs = _two_work_inputs(provider)
        # When/Then: the production scenario refuses it before invoking native execute.
        with pytest.raises(ExecutionError, match="diagnostic scenario"):
            schedule(inputs, Scenario(1_000_000_000, "RECONSTRUCTED", TRACE, "a8w8-d16-hp1"))
        assert provider.invocations == 0


def test_missing_trace_work_rejects_before_native_execution(audited_native_context: tuple[Path, EvidenceContext]) -> None:
    # Given: a source-bound session with one trace work omitted from the IR.
    certified, context = audited_native_context
    with DiagnosticStatefulProvider(LIBRARY, TRACE, certified, context) as provider:
        inputs = _two_work_inputs(provider)
        omitted = provider.requests[1].identity
        partial = ScheduleInputs(ExecutionIR(tuple(node for node in inputs.ir.nodes if node.service != omitted),
                                             "BOUND_DATASET", inputs.ir.source_sha256),
                                 Services(inputs.services.cpu, {provider.requests[0].identity: provider.requests[0]}),
                                 provider)
        # When/Then: the shared gate rejects coverage before mutating the session.
        with pytest.raises(ExecutionError, match="bound trace work"):
            schedule(partial, Scenario(1_000_000_000, "STATEFUL_DIAGNOSTIC", None, "a8w8-d16-hp1"))
        assert provider.invocations == provider.native_cursor == 0


def test_sqlite_forged_application_endpoint_rejects_fresh_verification(
    tmp_path: Path, audited_native_context: tuple[Path, EvidenceContext],
) -> None:
    # Given: an actual stateful SQLite schedule containing CPU and application work.
    scenario = Scenario(1_000_000_000, "STATEFUL_DIAGNOSTIC", None, "a8w8-d16-hp1")
    certified, context = audited_native_context
    with DiagnosticStatefulProvider(LIBRARY, TRACE, certified, context) as provider:
        _write_store(tmp_path / "input.sqlite", _two_work_inputs(provider), "PRODUCER_DECLARED")
        schedule_sqlite(tmp_path / "input.sqlite", tmp_path / "schedule.sqlite",
                        SqliteScheduleInputs(provider, scenario))

    # When: the persisted application endpoint is changed without touching the source IR.
    with closing(sqlite3.connect(tmp_path / "schedule.sqlite")) as database:
        row = database.execute("SELECT body FROM results WHERE identity='application'").fetchone()
        assert row is not None
        body = json.loads(row[0])
        body["result_ready_ns"]["numerator"] += 1
        database.execute("UPDATE results SET body=? WHERE identity='application'", (json.dumps(body),))
        database.commit()

    # Then: a new native replay compares the application row and rejects publication.
    with (DiagnosticStatefulProvider(LIBRARY, TRACE, certified, context) as verifier,
          pytest.raises(ExecutionError, match="node/endpoint mismatch")):
        verify_schedule_sqlite(tmp_path / "input.sqlite", tmp_path / "schedule.sqlite",
                               SqliteScheduleInputs(verifier, scenario))
