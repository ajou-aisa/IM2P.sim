from __future__ import annotations

import errno
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from typing import Literal, TextIO, assert_never

import pytest

from sim.cycle import stateful_sequence_certificate as certificate
from sim.cycle.npu_trace_schema import Record

EVIDENCE_ROOT = Path(os.environ.get(
    "IM2P_TODO16_EVIDENCE_ROOT",
    str(Path(__file__).resolve().parents[6] / "build/im2p-gemmini"
        / "stateful-sequence-v2-20260924T033303Z"),
))
CURRENT_INPUT = EVIDENCE_ROOT / "integration/todo16-current-evidence-20260926T154224Z/reviewed-input.json"


def _context(evidence_root: Path) -> certificate.EvidenceContext:
    archive_root = EVIDENCE_ROOT.parent
    return certificate.EvidenceContext(
        evidence_root=evidence_root,
        library=evidence_root / "build/cycle-a8d16/libim2p_cycle_model.a",
        shared_library=evidence_root / "build/cycle-a8d16/libim2p_cycle_model.dylib",
        base_parent=archive_root / "production-drained-sequence-20260923T064750Z"
                    / "certificate/base-v4/current-certificate.json",
        run_aware_parent=archive_root / "production-drained-sequence-20260923T064750Z"
                         / "certificate/run-aware-current/official/production-run-aware-certificate.json",
        service_parent=archive_root / "production-drained-sequence-resume-20260923T100511Z"
                       / "service-v1-package-current/service-certificate.json",
    )


CONTEXT = _context(EVIDENCE_ROOT)


def test_stateful_certificate_error_survives_generator_contextmanager() -> None:
    original = certificate.StatefulCertificateError(
        "source closure", "changed: sim/cycle/sequence_c_api.cpp",
    )

    @contextmanager
    def pass_through() -> Iterator[None]:
        yield

    with pytest.raises(certificate.StatefulCertificateError) as captured, pass_through():
        raise original
    assert captured.value is original
    assert captured.value.boundary == "source closure"
    assert str(captured.value) == "source closure: changed: sim/cycle/sequence_c_api.cpp"


def test_stateful_certificate_accepts_explicit_relocated_context(tmp_path: Path) -> None:
    # Given: the reviewed evidence reached through a task-owned alias.
    alias = tmp_path / "evidence-alias"
    alias.symlink_to(EVIDENCE_ROOT, target_is_directory=True)
    context = _context(alias)
    path = tmp_path / "scoped.json"

    # When: a scoped certificate is built and validated with that context.
    certificate.build(path, context)
    result = certificate.validate(path, context)

    # Then: the caller's paths bind the artifact without granting admission.
    assert result.validation_scope == "SCOPED_EVIDENCE"
    assert result.production_admitted is False
    assert json.loads(path.read_text())["library"]["path"] == str(context.library)


@pytest.mark.parametrize(("field", "error_type", "boundary"), [
    ("evidence_root", certificate.StatefulCertificateError, "artifact"),
    ("library", certificate.StatefulCertificateError, "artifact"),
    ("shared_library", certificate.StatefulCertificateError, "artifact"),
    ("base_parent", FileNotFoundError, "missing"),
])
def test_stateful_validator_rejects_missing_context_path(
    scoped_path: Path, tmp_path: Path, field: str, error_type: type[Exception], boundary: str,
) -> None:
    bad = replace(CONTEXT, **{field: tmp_path / "missing"})
    with pytest.raises(error_type, match=boundary):
        certificate.validate(scoped_path, bad)


def test_stateful_validator_uses_caller_root_over_candidate_path(
    scoped_path: Path, tmp_path: Path,
) -> None:
    alias = tmp_path / "evidence-alias"
    alias.symlink_to(EVIDENCE_ROOT, target_is_directory=True)
    with pytest.raises(certificate.StatefulCertificateError, match="independent review"):
        certificate.validate(scoped_path, replace(CONTEXT, evidence_root=alias))


def test_stateful_validator_rejects_wrong_existing_parent_and_library(
    scoped_path: Path, tmp_path: Path,
) -> None:
    library_alias = tmp_path / "wrong-library-path.a"
    library_alias.symlink_to(CONTEXT.library)
    with pytest.raises(certificate.StatefulCertificateError, match="library/ABI2"):
        certificate.validate(scoped_path, replace(CONTEXT, library=library_alias))
    with pytest.raises(certificate.StatefulCertificateError, match="parent certificate"):
        certificate.validate(scoped_path, replace(CONTEXT, base_parent=CONTEXT.run_aware_parent))


@pytest.fixture(scope="module")
def scoped_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("stateful-certificate") / "scoped.json"
    certificate.build(path, CONTEXT)
    return path


def test_stateful_validator_rejects_exact_v2_role(tmp_path: Path) -> None:
    # Given: a legacy exact-v2 document with a misleading production label.
    path = tmp_path / "exact-v2.json"
    path.write_text(json.dumps({"schema": "im2p-single-gemm-cycle-certificate",
                                "version": 2, "artifact_role": "PRODUCTION",
                                "validation_scope": "PRODUCTION"}))

    # When: it crosses the distinct stateful certificate boundary.
    # Then: its role cannot grant stateful admission.
    with pytest.raises(certificate.StatefulCertificateError, match="schema or artifact role"):
        certificate.validate(path, CONTEXT)


def test_historic_v1_pins_remain_stale_on_current_native_archive(tmp_path: Path) -> None:
    output = tmp_path / "historic-v1.json"
    with pytest.raises(certificate.StatefulCertificateError, match="artifact: changed or missing"):
        certificate.build(output, CONTEXT)
    assert not output.exists()


def test_stateful_validator_accepts_only_scoped_evidence(scoped_path: Path) -> None:
    # Given: a generated certificate over all six reviewed producer cases.
    # When: the stateful validator checks the bound evidence.
    result = certificate.validate(scoped_path, CONTEXT)
    # Then: its typed result remains scoped, with production admission false.
    assert result.validation_scope == "SCOPED_EVIDENCE"
    assert result.production_admitted is False
    assert result.certificate_sha256 == certificate.sha256(scoped_path)
    assert result.case_ids == certificate.EXPECTED_IDS


Mutation = Literal[
    "missing_case", "reordered_case", "shrunk_summary", "summary_only", "offer", "run", "event",
    "queue", "rtl_hash", "library", "parent", "full_claim", "role", "scope",
    "fixture", "source_hash", "comparison",
]


@pytest.mark.parametrize(("mutation", "boundary"), [
    ("missing_case", "case set"),
    ("reordered_case", "case set"),
    ("shrunk_summary", "case set"),
    ("summary_only", "completeness"),
    ("offer", "work/run/offer"),
    ("run", "work/run/offer"),
    ("event", "selected-event/queue/boundary"),
    ("queue", "selected-event/queue/boundary"),
    ("rtl_hash", "RTL artifact"),
    ("library", "library/ABI2"),
    ("parent", "parent certificate"),
    ("full_claim", "State.array domain"),
    ("role", "schema or artifact role"),
    ("scope", "schema or artifact role"),
    ("fixture", "schema or artifact role"),
    ("source_hash", "source closure"),
    ("comparison", "comparison summary"),
])
def test_stateful_validator_rejects_mutated_boundary(
    scoped_path: Path, tmp_path: Path, mutation: Mutation, boundary: str,
) -> None:
    # Given: a valid scoped artifact with one independent binding altered.
    document = json.loads(scoped_path.read_text())
    match mutation:
        case "missing_case":
            document["cases"].pop()
        case "reordered_case":
            document["cases"][0], document["cases"][1] = document["cases"][1], document["cases"][0]
        case "shrunk_summary":
            document["cases"].pop()
            document["completeness"]["profiles_expected"] = 5
        case "summary_only":
            document["completeness"]["profiles_expected"] = 5
        case "offer":
            document["cases"][0]["declared"]["offer_cycles"][0] += 1
        case "run":
            document["cases"][0]["work_inputs_sha256"] = "0" * 64
        case "event":
            document["cases"][0]["raw_digests"]["selected_event"] = "0" * 64
        case "queue":
            document["cases"][0]["raw_digests"]["queue_v2"] = "0" * 64
        case "rtl_hash":
            document["cases"][0]["artifacts"]["rtl_object"]["sha256"] = "0" * 64
        case "library":
            document["library"]["sha256"] = "0" * 64
        case "parent":
            document["parents"]["run_aware"]["status"] = "CURRENT"
        case "full_claim":
            document["state_refinement"]["max_tag_occupancy_observed"] = 5
            document["state_refinement"]["full6_supported"] = True
        case "role":
            document["artifact_role"] = "FIXTURE_ONLY"
        case "scope":
            document["validation_scope"] = "PRODUCTION"
            document["production_admitted"] = True
        case "fixture":
            document["schema"] = "im2p-drained-service-certificate"
        case "source_hash":
            document["source_sha256"]["sim/cycle/control_engine.cpp"] = "0" * 64
        case "comparison":
            document["cases"][0]["comparison"]["numeric_pass_count"] = 0
        case unreachable:
            assert_never(unreachable)
    path = tmp_path / f"{mutation}.json"
    path.write_text(json.dumps(document))

    # When: that edited document is offered to the stateful validator.
    # Then: the named boundary rejects it before any production admission.
    with pytest.raises(certificate.StatefulCertificateError, match=boundary):
        certificate.validate(path, CONTEXT)


@pytest.mark.parametrize("helper", [
    "sim/tests/cycle/compositional_sequence_v2_stimulus.py",
    "sim/cycle/stateful_sequence_evidence.py",
])
def test_stateful_validator_rejects_copied_helper_source_edit(
    scoped_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, helper: str,
) -> None:
    # Given: a task-owned copy of the bound source closure with one helper changed.
    source_root = tmp_path / "source"
    for name in certificate.source_hashes(CONTEXT, certificate.pinned(CONTEXT)):
        target = source_root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(certificate.ROOT / name, target)
    helper_path = source_root / helper
    assert helper_path.is_file()
    helper_path.write_bytes(helper_path.read_bytes() + b"\n")
    monkeypatch.setitem(certificate.source_hashes.__globals__, "ROOT", source_root)

    # When: the certificate is checked against the copied current source tree.
    # Then: the source closure rejects the modified helper.
    with pytest.raises(certificate.StatefulCertificateError, match="source closure: changed"):
        certificate.validate(scoped_path, CONTEXT)


def test_production_admission_remains_not_ready(scoped_path: Path) -> None:
    # Given: the same valid scoped artifact with unresolved State.array evidence.
    # When: production admission is requested through the typed API.
    # Then: it fails closed with the unresolved full-domain reason.
    with pytest.raises(certificate.NotReadyError, match="State.array old-full6/6 unresolved"):
        certificate.admit(scoped_path, CONTEXT)


def test_current_reviewed_evidence_builds_distinct_scoped_v2_cli(tmp_path: Path) -> None:
    # Given: the six current, independently reviewed case directories and guarded state audit.
    output = tmp_path / "current-scoped.json"
    common = [sys.executable, "-B", "-m", "sim.cycle.stateful_sequence_certificate",
              "--evidence-root", str(EVIDENCE_ROOT), "--library", str(CONTEXT.library),
              "--shared-library", str(CONTEXT.shared_library),
              "--base-parent", str(CONTEXT.base_parent),
              "--run-aware-parent", str(CONTEXT.run_aware_parent),
              "--service-parent", str(CONTEXT.service_parent),
              "--current-evidence-input", str(CURRENT_INPUT)]
    # When: the official CLI builds and validates the current version-2 scoped artifact.
    built = subprocess.run([*common, "build", str(output)], cwd=Path(__file__).resolve().parents[3],
                           capture_output=True, text=True, timeout=120, check=False)
    assert built.returncode == 0, built.stderr
    validated = subprocess.run([*common, "validate", str(output)], cwd=Path(__file__).resolve().parents[3],
                               capture_output=True, text=True, timeout=120, check=False)
    # Then: the current library and six cases are bound without promoting stale parents.
    assert validated.returncode == 0, validated.stderr
    document = json.loads(output.read_text())
    assert (document["version"], document["validation_scope"], document["production_admitted"]) == \
           (2, "SCOPED_EVIDENCE", False)
    assert len(document["cases"]) == 6
    assert document["library"]["sha256"] == certificate.sha256(CONTEXT.library)
    assert document["shared_library"]["sha256"] == certificate.sha256(CONTEXT.shared_library)
    assert document["library"]["sha256"] != document["shared_library"]["sha256"]
    for name in ("execution_sequence_provider.py", "execution_sequence_admission.py",
                 "sequence_binding_abi.py", "cli.py"):
        source = Path(__file__).resolve().parents[3] / "sim/cycle" / name
        assert document["source_sha256"][f"sim/cycle/{name}"] == certificate.sha256(source)
    assert "prior_guarded_audit" in document["reviewed_evidence"]
    assert all(parent["status"] == "STALE" for parent in document["parents"].values())
    for label, mutation, boundary in (
        ("false-production", {"validation_scope": "PRODUCTION", "production_admitted": True},
         "schema or artifact role"),
        ("fixture-only", {"artifact_role": "FIXTURE_ONLY"}, "schema or artifact role"),
    ):
        altered = {**document, **mutation}
        path = tmp_path / f"{label}.json"
        path.write_text(json.dumps(altered))
        with pytest.raises(certificate.StatefulCertificateError, match=boundary):
            certificate.validate(path, replace(CONTEXT, current_evidence_input=CURRENT_INPUT))


def test_current_parent_gate_preserves_stale_diagnostic() -> None:
    context = replace(CONTEXT, current_evidence_input=CURRENT_INPUT)
    bound = certificate.parents(context, certificate.sha256(context.shared_library))
    assert {name: certificate.object_value(row, name)["status"] for name, row in bound.items()} == {
        "base": "STALE", "run_aware": "STALE", "service": "STALE",
    }


def test_current_service_parent_uses_json_timing_without_c_abi_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sim.cycle import cli, service_certificate

    # Given: the real service JSON timing contract at a unit-only parent transport seam.
    expected = json.loads(service_certificate.CORPUS.read_text())["timing"] | {"read_ready_period": 5}
    library = tmp_path / "library"
    library.write_bytes(b"unit-only library identity")
    report = tmp_path / "report.json"
    trace = tmp_path / "trace.jsonl"
    report.write_text(json.dumps({"producer_artifacts": {"trace": {"path": str(trace)}}}))
    document: Record = {"version": 2, "artifact_role": "PRODUCTION_GENERATED_SEQUENCE",
                        "status": "PASS", "cases": [{"report": {"path": str(report)}}]}
    context = replace(CONTEXT, shared_library=library)
    observed: Record = {}

    def service_boundary(
        path: Path, bound_library: Path, bound_trace: Path, *,
        base_certificate: Path, run_certificate: Path, timing: Record,
        initial_scratchpad_half: int, initial_accumulator_half: int,
    ) -> None:
        assert (path, bound_library, bound_trace) == (tmp_path / "parent.json", library, trace)
        assert (base_certificate, run_certificate) == (context.base_parent, context.run_aware_parent)
        assert (initial_scratchpad_half, initial_accumulator_half) == (0, 0)
        cli.object_fields(timing, set(expected), "timing")
        observed.update(timing)

    monkeypatch.setattr(service_certificate, "validate_service_certificate", service_boundary)
    monkeypatch.setattr(certificate.current_evidence, "reviewed_parent",
                        lambda *_args: {"parent": "service"})
    # When: current-parent admission crosses from C configuration to the JSON service API.
    result = certificate.validate_current_parent("service", tmp_path / "parent.json", document, context)
    # Then: physical timing values survive exactly, without revision/reserved ABI fields.
    assert observed == expected
    assert result == {"parent": "service"}


@pytest.mark.parametrize("forged_name", ["base", "run_aware", "service"])
def test_current_parent_gate_rejects_hash_only_forgery(
    tmp_path: Path, forged_name: str,
) -> None:
    current_hash = certificate.sha256(CONTEXT.shared_library)
    copies: dict[str, Path] = {}
    for name, path, key in (
        ("base", CONTEXT.base_parent, "model_library_sha256"),
        ("run_aware", CONTEXT.run_aware_parent, "library_sha256"),
        ("service", CONTEXT.service_parent, "library_sha256"),
    ):
        document = json.loads(path.read_text())
        if name == forged_name:
            document[key] = current_hash
        copy = tmp_path / f"forged-{name}.json"
        copy.write_text(json.dumps(document))
        copies[name] = copy
    context = replace(CONTEXT, base_parent=copies["base"],
                      run_aware_parent=copies["run_aware"],
                      service_parent=copies["service"],
                      current_evidence_input=CURRENT_INPUT)
    with pytest.raises(certificate.StatefulCertificateError, match=f"parent certificate: {forged_name}"):
        certificate.parents(context, current_hash)


def test_current_build_never_publishes_partial_certificate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = replace(CONTEXT, current_evidence_input=CURRENT_INPUT)
    output = tmp_path / "scoped.json"
    monkeypatch.setattr(certificate, "expected_current", lambda _context: {"version": 2})

    def fail_after_partial(_document: Record, stream: TextIO, *, indent: int, sort_keys: bool) -> None:
        stream.write('{"partial":')
        raise OSError(errno.ENOSPC, "injected write failure")

    monkeypatch.setattr(certificate.json, "dump", fail_after_partial)
    with pytest.raises(OSError, match="injected write failure"):
        certificate.build(output, context)
    assert not output.exists()


def test_current_build_rechecks_source_before_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "validator.py"
    source.write_text("original\n")
    document = {"source_sha256": {source.name: certificate.sha256(source)}}
    output = tmp_path / "scoped.json"
    context = replace(CONTEXT, current_evidence_input=CURRENT_INPUT)
    monkeypatch.setattr(certificate, "ROOT", tmp_path)
    monkeypatch.setattr(certificate, "expected_current", lambda _context: document)
    original_dump = certificate.json.dump

    def mutate_after_write(value: Record, stream: TextIO, *, indent: int, sort_keys: bool) -> None:
        original_dump(value, stream, indent=indent, sort_keys=sort_keys)
        source.write_text("changed\n")

    monkeypatch.setattr(certificate.json, "dump", mutate_after_write)
    with pytest.raises(certificate.StatefulCertificateError, match="source closure: changed"):
        certificate.build(output, context)
    assert not output.exists()


def test_current_source_closure_includes_parent_validator_imports() -> None:
    assert {
        "sim/cycle/certificate_contract.py",
        "sim/cycle/run_aware_certificate.py",
        "sim/cycle/service_certificate.py",
        "sim/cycle/production_sequence_certificate.py",
        "sim/cycle/production_sequence_evidence.py",
        "sim/cycle/cli.py",
    } <= certificate.current_evidence._python_closure()


@pytest.mark.parametrize("mutation,boundary", [
    ("shrink", "case set"),
    ("stale_review", "artifact"),
    ("fixture_only", "independent review"),
])
def test_current_input_rejects_unreviewed_case_or_authority(
    tmp_path: Path, mutation: str, boundary: str,
) -> None:
    manifest = json.loads(CURRENT_INPUT.read_text())
    if mutation == "shrink":
        manifest["cases"].pop()
    elif mutation == "stale_review":
        manifest["aggregate_review"]["sha256"] = "0" * 64
    else:
        manifest["state_review"] = manifest["aggregate_review"]
    input_path = tmp_path / "mutated-input.json"
    input_path.write_text(json.dumps(manifest))
    context = replace(CONTEXT, current_evidence_input=input_path)
    output = tmp_path / "scoped.json"
    with pytest.raises(certificate.StatefulCertificateError, match=boundary):
        certificate.build(output, context)
    assert not output.exists()


@contextmanager
def _copied_current_case() -> Iterator[tuple[certificate.EvidenceContext, Path]]:
    with tempfile.TemporaryDirectory(prefix="todo16-case-", dir=EVIDENCE_ROOT / "integration") as temporary:
        copied = Path(temporary)
        manifest = json.loads(CURRENT_INPUT.read_text())
        original = EVIDENCE_ROOT / manifest["cases"][1]["case_dir"]
        (copied / "target").mkdir()
        for source in (original / "target").iterdir():
            destination = copied / "target" / source.name
            if source.name in {"report.json", "rtl-diagnostics.log"}:
                shutil.copyfile(source, destination)
            else:
                destination.symlink_to(source)
        receipt = json.loads((original / "case.json").read_text())
        receipt["command"][receipt["command"].index("--out") + 1] = str(copied / "target")
        (copied / "case.json").write_text(json.dumps(receipt))
        manifest["cases"][1]["case_dir"] = copied.relative_to(EVIDENCE_ROOT).as_posix()
        input_path = copied / "input.json"
        input_path.write_text(json.dumps(manifest))
        yield replace(CONTEXT, current_evidence_input=input_path), copied


def test_current_validator_rejects_changed_diagnostics(tmp_path: Path) -> None:
    # Given: real current evidence with task-owned stderr and a valid scoped certificate.
    with _copied_current_case() as (context, copied):
        output = tmp_path / "scoped.json"
        certificate.build(output, context)
        diagnostics = copied / "target/rtl-diagnostics.log"
        diagnostics.write_bytes(diagnostics.read_bytes() + b"changed stderr\n")
        # When/Then: changing only raw stderr rejects the certificate.
        with pytest.raises(certificate.StatefulCertificateError, match="artifact: changed or missing"):
            certificate.validate(output, context)


@pytest.mark.parametrize("artifact", ["report.json", "rtl-diagnostics.log"])
def test_current_build_rejects_late_case_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, artifact: str,
) -> None:
    # Given: actual current evidence; mutation occurs only after staging the certificate.
    with _copied_current_case() as (context, copied):
        output = tmp_path / "scoped.json"
        original_dump = certificate.json.dump

        def mutate_after_write(value: Record, stream: TextIO, *, indent: int, sort_keys: bool) -> None:
            original_dump(value, stream, indent=indent, sort_keys=sort_keys)
            target = copied / "target" / artifact
            target.write_bytes(target.read_bytes() + b"\n")

        monkeypatch.setattr(certificate.json, "dump", mutate_after_write)
        # When/Then: drift aborts publication and cleans the staging directory.
        with pytest.raises(certificate.StatefulCertificateError, match="artifact: changed or missing"):
            certificate.build(output, context)
        assert list(tmp_path.iterdir()) == []


def test_current_parent_rejects_complete_forged_base_review(tmp_path: Path) -> None:
    # Given: all historical rows plus a fully populated, hash-consistent forged review.
    with tempfile.TemporaryDirectory(prefix="todo16-parent-", dir=EVIDENCE_ROOT / "integration") as temporary:
        copied = Path(temporary)
        parent = json.loads(CONTEXT.base_parent.read_text())
        current_hash = certificate.sha256(CONTEXT.shared_library)
        parent["model_library_sha256"] = current_hash
        forged = tmp_path / "forged-base.json"
        forged.write_text(json.dumps(parent))
        sources = {}
        for profile in certificate.PROFILES:
            sources.update(certificate.current_evidence.source_identity(CONTEXT.library, profile).source_sha256)
        rows = []
        for row in parent["cases"]:
            log = Path(row["rtl_log"])
            artifacts = {name: certificate.sha256(path) for name, path in (
                ("rtl_log", log), ("rtl_events", Path(row["rtl_events"])),
                ("model_request", log.parent / "model-request.json"),
                ("model_result", log.parent / "model-result.json"))}
            rows.append({"row_sha256": sha256(json.dumps(row, sort_keys=True,
                separators=(",", ":")).encode()).hexdigest(), "artifacts": artifacts})
        review = {"schema": "im2p-stateful-parent-recert-review-v1", "verdict": "confirmed", "parent": "base",
            "certificate_sha256": certificate.sha256(forged), "library_sha256": current_hash,
            "native_source_sha256": sources, "profiles": list(certificate.PROFILES),
            "timing": dict(certificate.TIMING), "expected_case_count": 240,
            "fresh_model_comparison_count": 240, "verified_rtl_artifact_count": 960,
            "case_evidence_sha256": sha256(json.dumps(rows, sort_keys=True,
                separators=(",", ":")).encode()).hexdigest()}
        review_path = copied / "review.json"
        review_path.write_text(json.dumps(review))
        manifest = json.loads(CURRENT_INPUT.read_text())
        manifest["parent_reviews"] = {"base": {"path": review_path.relative_to(EVIDENCE_ROOT).as_posix(),
            "sha256": certificate.sha256(review_path)}}
        input_path = copied / "input.json"
        input_path.write_text(json.dumps(manifest))
        context = replace(CONTEXT, base_parent=forged, current_evidence_input=input_path)
        # When/Then: currentness must depend on executable evidence, not the review's count claim.
        with pytest.raises(certificate.StatefulCertificateError, match="parent certificate: base"):
            certificate.parents(context, current_hash)


@pytest.mark.parametrize("mutation", ["none", "request", "retained_event", "rtl_event"])
def test_current_base_replays_native_model_and_rejects_artifact_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    mutation: Literal["none", "request", "retained_event", "rtl_event"],
) -> None:
    row = json.loads(CONTEXT.base_parent.read_text())["cases"][0]
    original = Path(row["rtl_log"]).parent
    for name in ("run.log", "events.csv", "model-request.json", "model-result.json"):
        shutil.copyfile(original / name, tmp_path / name)
    row.update(rtl_log=str(tmp_path / "run.log"), rtl_events=str(tmp_path / "events.csv"))
    helper = certificate.current_evidence
    estimate = helper.cli.estimate
    calls: list[Path] = []

    def replay(library: Path, request: Record) -> Record:
        calls.append(library)
        return estimate(library, request)

    monkeypatch.setattr(helper.cli, "estimate", replay)
    match mutation:
        case "request":
            path = tmp_path / "model-request.json"
            request = json.loads(path.read_text())
            request["request"]["m"] += 1
            path.write_text(json.dumps(request))
        case "retained_event":
            path = tmp_path / "model-result.json"
            model = json.loads(path.read_text())
            model["events"][0]["cycle"] += 1
            path.write_text(json.dumps(model))
        case "rtl_event":
            path = tmp_path / "events.csv"
            lines = path.read_text().splitlines()
            index = next(i for i, line in enumerate(lines) if ",array_input," in line)
            fields = lines[index].split(",")
            fields[1] = str(int(fields[1]) + 1)
            lines[index] = ",".join(fields)
            path.write_text("\n".join(lines) + "\n")
        case "none":
            helper._base_model(CONTEXT.shared_library, row)
            assert calls == [CONTEXT.shared_library]
            return
        case unreachable:
            assert_never(unreachable)
    with pytest.raises(certificate.StatefulCertificateError, match="base current model"):
        helper._base_model(CONTEXT.shared_library, row)
    assert calls == ([] if mutation == "request" else [CONTEXT.shared_library])
