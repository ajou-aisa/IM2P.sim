from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path

import pytest

from scripts.gemmini_resolve_profile import JsonValue
from sim.cycle import cycle_trace_certificate as certificate
from sim.cycle import stateful_sequence_certificate as certificates
from sim.cycle.certificate_contract import object_value, read_document
from sim.cycle.npu_trace_schema import Record
from sim.cycle.stateful_sequence_evidence import (
    EvidenceContext,
    StatefulCertificateError,
)
from sim.tests.cycle.test_npu_trace import records, write_trace


def trace(path: Path, rows: list[Record]) -> Path:
    write_trace(path, rows)
    return path


def identity(dim: int = 16) -> Record:
    return {"model": "GPT-2 124M", "precision": "A8W8", "dim": dim, "BK": 32}


def unused_context(root: Path) -> EvidenceContext:
    return EvidenceContext(*(root / name for name in ("e", "l", "s", "b", "r", "v")))


def test_identical_descriptor_sequence_is_equivalent(tmp_path: Path) -> None:
    # Given: two production traces with byte-identical NPU work descriptors.
    fresh = trace(tmp_path / "fresh.jsonl", records())
    certified = trace(tmp_path / "certified.jsonl", records())
    # When: the fresh trace is compared with the certified corpus.
    summary = certificate.summarize(fresh, certified)
    # Then: the ordered descriptor bindings match the certified trace alone.
    assert summary.work_ids == (0,)
    assert summary == certificate.summarize(certified)


@pytest.mark.parametrize("field,value", [("activation_stride_bytes", 4096), ("tile_k_count", 2),
                                         ("provenance", "residual"), ("weight_stride_bytes", 2)])
def test_changed_descriptor_is_rejected(tmp_path: Path, field: str, value: JsonValue) -> None:
    # Given: a fresh trace whose only work differs from the certified corpus.
    rows = records()
    rows[4][field] = value
    fresh, certified = trace(tmp_path / "fresh.jsonl", rows), trace(tmp_path / "certified.jsonl", records())
    # When/Then: equivalence fails closed instead of admitting a new workload.
    with pytest.raises((StatefulCertificateError, ValueError)):
        certificate.summarize(fresh, certified)


def test_other_profile_is_rejected(tmp_path: Path) -> None:
    # Given: an otherwise valid trace captured for another DIM.
    fresh = trace(tmp_path / "fresh.jsonl", records(8, 32))
    certified = trace(tmp_path / "certified.jsonl", records(8, 16))
    # When/Then: the hardware profile cannot be relabelled by equivalence.
    with pytest.raises(StatefulCertificateError, match="profile/contract"):
        certificate.summarize(fresh, certified)


def test_cycle_certificate_cannot_parent_another(tmp_path: Path) -> None:
    # Given: a candidate parent that is itself an equivalence certificate.
    parent = tmp_path / "parent.json"
    parent.write_text(json.dumps({"schema": certificate.SCHEMA, "profile": "a8w8-d16-hp1"}))
    fresh = trace(tmp_path / "fresh.jsonl", records())
    # When/Then: chaining is refused before any evidence is read.
    with pytest.raises(StatefulCertificateError, match="independently certified corpus"):
        certificate.expected(fresh, identity(), parent, unused_context(tmp_path), {}, {})


def test_parent_for_other_profile_is_rejected(tmp_path: Path) -> None:
    # Given: the certified GPT-2 A8W8 DIM16 corpus and a DIM32 capture.
    parent = tmp_path / "parent.json"
    parent.write_text(json.dumps({"schema": "stateful-full374-replay-v1", "profile": "a8w8-d16-hp1"}))
    fresh = trace(tmp_path / "fresh.jsonl", records(8, 32))
    # When/Then: no certified corpus exists for the requested profile.
    with pytest.raises(StatefulCertificateError, match="no certified corpus for a8w8-d32-hp1"):
        certificate.expected(fresh, identity(32), parent, unused_context(tmp_path), {}, {})


@pytest.fixture(scope="module")
def genuine() -> tuple[Path, Record]:
    path = os.environ.get("IM2P_CYCLE_TRACE_CERTIFICATE")
    if path is None:
        pytest.skip("genuine cycle trace certificate not supplied")
    return Path(path), certificate.verify_certificate(Path(path))


def test_genuine_certificate_dispatches_through_admission(genuine: tuple[Path, Record]) -> None:
    # Given: a certificate built from an actual inference capture.
    path, document = genuine
    context = certificate.context_from(object_value(document["evidence_context"], "context"))
    # When: the existing admission validator reads it.
    scoped = certificates.validate(path, context)
    certificates.admit(path, context)
    # Then: the parent's finite state domain is inherited unchanged.
    assert scoped.state_domain_revision == document["state_domain_revision"]
    assert all(case.startswith("cycle-trace:") for case in scoped.case_ids)


@pytest.mark.parametrize("field", ["trace_sha256", "work_ids", "geometry", "tile_shape", "ordered_runs",
                                   "row_mapping", "state_domain_revision", "initial_state",
                                   "provider_revision", "parent_certificate", "producer_sha256", "dim"])
def test_mutated_certificate_is_rejected(genuine: tuple[Path, Record], tmp_path: Path,
                                         monkeypatch: pytest.MonkeyPatch, field: str) -> None:
    # Given: the recomputed genuine document; the candidate stays untrusted.
    _, document = genuine
    monkeypatch.setattr(certificate, "_recompute", lambda value: deepcopy(document))
    candidate = deepcopy(document)
    candidate[field] = "changed"
    path = tmp_path / "candidate.json"
    path.write_text(json.dumps(candidate))
    # When/Then: any relabelled binding is rejected.
    with pytest.raises(StatefulCertificateError):
        certificate.verify_certificate(path)
    assert read_document(path)[field] == "changed"
