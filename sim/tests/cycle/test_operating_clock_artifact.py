from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path

import pytest

from scripts.evaluation_clock_contract import (
    ClockError,
    ClockSelection,
    file_ref,
    sha256,
)
from sim.cycle import operating_clock_artifact as clock
from sim.cycle.stateful_measurement_contract import (
    ArtifactReference,
    ClockRequirement,
    HostRequirement,
    ValidatedStatefulMeasurement,
)


@pytest.mark.parametrize("status", ["NOT_READY", "SYNTHETIC_ONLY", "DIAGNOSTIC_ONLY"])
def test_requested_or_synthetic_frequency_is_not_a_clock(tmp_path: Path, status: str) -> None:
    # Given: an explicit frequency without a genuine accepted post-route chain.
    path = tmp_path / "clock.json"
    path.write_text(json.dumps({
        "schema": "im2p-operating-clock", "version": 2, "status": status,
        "profile": "a8w8-d32-hp1", "selected_frequency_hz": 100_000_000,
    }))
    # When/Then: normalization cannot bypass the existing operating-clock gate.
    with pytest.raises(ClockError):
        clock.load_clock_artifact(path, "a8w8-d32-hp1")


def test_missing_clock_cannot_be_substituted_by_a_default(tmp_path: Path) -> None:
    # Given/When/Then: absent hardware evidence is an error, not a nominal frequency.
    with pytest.raises(FileNotFoundError):
        clock.load_clock_artifact(tmp_path / "absent.json", "a8w8-d32-hp1")


def test_profile_mismatch_rejects_before_consuming_artifacts(tmp_path: Path) -> None:
    # Given: a document claims a different profile.
    path = tmp_path / "clock.json"
    path.write_text(json.dumps({
        "schema": "im2p-operating-clock", "version": 2,
        "status": "PASS", "profile": "a8w8-d16-hp1",
    }))
    # When/Then: the caller's profile remains mandatory.
    with pytest.raises(ClockError):
        clock.load_clock_artifact(path, "a8w8-d32-hp1")


@pytest.fixture
def normalized_input(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """TEST_ONLY normalization seam; this is not a validated operating clock."""
    rtl, header, wrapper, constraint, implementation = (
        tmp_path / name for name in ("core.sv", "params.svh", "wrapper.sv", "clock.xdc", "route.dcp"))
    for path in (rtl, header, wrapper, constraint, implementation):
        path.write_text("TEST_ONLY " + path.name)
    source = tmp_path / "source.json"
    source.write_text(json.dumps({"artifacts": {
        "rtl/core.sv": file_ref(rtl), "rtl/params.svh": file_ref(header), "wrapper": file_ref(wrapper),
    }}))
    selection = tmp_path / "selection.json"
    selection.write_text(json.dumps({"selected": {
        "execution_kind": "TOOL_EXECUTION", "timing_stage": "POST_ROUTE",
        "source": file_ref(source), "constraints": [file_ref(constraint)],
        "netlist": file_ref(implementation),
    }}))

    def verified_seam(path: Path, profile: str) -> ClockSelection:
        assert path == selection and profile == "a8w8-d32-hp1"
        return ClockSelection(123_000_000, profile, "POST_ROUTE", sha256(path), "a" * 64)

    monkeypatch.setattr(clock, "load_selection", verified_seam)
    return selection


def test_normalization_preserves_verified_frequency_and_all_hashes(normalized_input: Path) -> None:
    # Given: the narrow verifier seam; real artifact hashes remain live.
    root = normalized_input.parent
    expected_rtl = hashlib.sha256(json.dumps(
        {"rtl/core.sv": sha256(root / "core.sv"), "rtl/params.svh": sha256(root / "params.svh"),
         "wrapper": sha256(root / "wrapper.sv")}, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    # When: the verified observation is normalized.
    artifact = clock.load_clock_artifact(normalized_input, "a8w8-d32-hp1")
    # Then: frequency, RTL, constraints and implementation keep their distinct identities.
    assert artifact.frequency_hz == 123_000_000
    assert (artifact.rtl_hash, artifact.constraint_hash, artifact.implementation_hash) == (
        expected_rtl, sha256(root / "clock.xdc"), sha256(root / "route.dcp"),
    )
    assert artifact.source == ArtifactReference(str(normalized_input), sha256(normalized_input))


@pytest.mark.parametrize("name", ["core.sv", "params.svh", "wrapper.sv", "clock.xdc", "route.dcp"])
def test_changed_bound_artifact_rejects(normalized_input: Path, name: str) -> None:
    # Given: one actual artifact changed after the observation bound it.
    (normalized_input.parent / name).write_text("changed")
    # When/Then: even the verifier seam cannot hide a broken leaf binding.
    with pytest.raises(ClockError):
        clock.load_clock_artifact(normalized_input, "a8w8-d32-hp1")


def test_measurement_schema_carries_requirements_without_publication_authority() -> None:
    # Given: a schema value, deliberately not a production-admission result.
    certificate = ArtifactReference("/fixture/certificate", "a" * 64)
    trace = ArtifactReference("/fixture/trace", "b" * 64)
    measurement = ValidatedStatefulMeasurement(
        certificate, "finite-test-domain", trace, "a8w8-d32-hp1",
        ClockRequirement("a8w8-d32-hp1", "c" * 64), HostRequirement(),
    )
    # When: a consumer serializes the preparation record.
    record = asdict(measurement)
    # Then: required evidence remains explicit, with no manufactured frequency or E2E authority.
    assert record["clock_requirement"] == {
        "profile": "a8w8-d32-hp1", "hardware_contract_sha256": "c" * 64,
        "required_timing_status": "POST_ROUTE_PASS",
    }
    assert (record["e2e_reconstruction_ready"], record["paper_campaign_complete"]) == ("NOT_READY", "NOT_RUN")
    assert record["host_requirement"]["status"] == "NOT_PROVIDED"
