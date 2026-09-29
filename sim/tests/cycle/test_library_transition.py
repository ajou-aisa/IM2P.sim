from __future__ import annotations

import gzip
import hashlib
import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from sim.cycle import library_transition as transition
from sim.cycle.certificate_contract import read_document


def write_json(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    return path


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_unreviewed_certificate_is_rejected(tmp_path: Path) -> None:
    # Given: a self-consistent looking document that no reviewer pinned.
    forged = write_json(tmp_path / "forged.json", {"schema": transition.SCHEMA, "version": 1, "status": "EXACT"})
    # When/Then: admission stops before trusting any recorded label.
    with pytest.raises(transition.TransitionError, match="unreviewed transition certificate"):
        transition.validate_transition(forged, forged, forged)


def test_no_transition_keeps_the_caller_library(tmp_path: Path) -> None:
    # Given: no transition artifact. When/Then: the base certificate must name the caller library itself.
    library = tmp_path / "lib"
    assert transition.certified_library(tmp_path / "cert.json", library, None) == (library, None)


@pytest.fixture
def genuine() -> tuple[Path, dict[str, Any]]:
    path = os.environ.get("IM2P_TRANSITION_CERTIFICATE")
    if path is None:
        pytest.skip("genuine library transition certificate not supplied")
    document: dict[str, Any] = deepcopy(dict(read_document(Path(path))))
    return Path(path), document


def paths(document: dict[str, Any]) -> tuple[Path, Path, Path]:
    old, current = document["old"], document["current"]
    return (Path(old["base_certificate"]["path"]), Path(old["library"]["path"]),
            Path(current["library"]["path"]))


def test_genuine_transition_admits_current_for_old_base(genuine: tuple[Path, dict[str, Any]]) -> None:
    path, document = genuine
    base, old, current = paths(document)
    admitted = transition.validate_transition(path, base, current)
    assert admitted.old_library == old and admitted.old_library_sha256 == read_document(base)["model_library_sha256"]
    assert transition.certified_library(base, current, path) == (old, digest(path))


def test_wrong_current_library_is_rejected(genuine: tuple[Path, dict[str, Any]]) -> None:
    path, document = genuine
    base, old, _ = paths(document)
    with pytest.raises(transition.TransitionError, match="caller library is not the transition CURRENT library"):
        transition.validate_transition(path, base, old)


def test_wrong_base_certificate_is_rejected(genuine: tuple[Path, dict[str, Any]], tmp_path: Path) -> None:
    path, document = genuine
    base, _, current = paths(document)
    other = tmp_path / "base.json"
    other.write_bytes(base.read_bytes() + b"\n")
    with pytest.raises(transition.TransitionError, match="base certificate differs"):
        transition.validate_transition(path, other, current)


def reviewed_copy(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, document: dict[str, Any]) -> Path:
    """Pin a tampered copy so the checks behind the reviewed-identity gate are exercised."""
    copy = write_json(tmp_path / "transition.json", document)
    pins = dict(transition.REVIEWED)
    pins[digest(copy)] = next(iter(transition.REVIEWED.values()))
    monkeypatch.setattr(transition, "REVIEWED", pins)
    return copy


def test_tampered_capture_is_rejected(genuine: tuple[Path, dict[str, Any]], tmp_path: Path,
                                      monkeypatch: pytest.MonkeyPatch) -> None:
    path, document = genuine
    base, _, current = paths(document)
    row = document["corpus_and_comparison"]["base240"]
    lines = gzip.decompress(Path(row["current_capture"]["path"]).read_bytes()).splitlines()
    first = json.loads(lines[0])
    first["result"]["total_cycles"] += 1
    lines[0] = json.dumps(first, sort_keys=True, separators=(",", ":")).encode()
    tampered = tmp_path / "base240.jsonl.gz"
    with gzip.open(tampered, "wb") as stream:
        stream.write(b"\n".join(lines) + b"\n")
    row["current_capture"] = {"path": str(tampered), "sha256": digest(tampered)}
    with pytest.raises(transition.TransitionError, match="OLD/CURRENT canonical outputs differ"):
        transition.validate_transition(reviewed_copy(monkeypatch, tmp_path, document), base, current)
    assert digest(path) in transition.REVIEWED


def test_tampered_rtl_receipt_is_rejected(genuine: tuple[Path, dict[str, Any]], tmp_path: Path,
                                          monkeypatch: pytest.MonkeyPatch) -> None:
    _, document = genuine
    base, _, current = paths(document)
    row = document["corpus_and_comparison"]["exact30"]
    receipt = dict(read_document(Path(row["rtl_anchor"]["current_receipt"]["path"])))
    receipt["exact"] = 29
    forged = write_json(tmp_path / "exact30.json", receipt)
    row["rtl_anchor"]["current_receipt"] = {"path": str(forged), "sha256": digest(forged)}
    with pytest.raises(transition.TransitionError, match="does not reproduce the retained RTL evidence"):
        transition.validate_transition(reviewed_copy(monkeypatch, tmp_path, document), base, current)


def test_changed_corpus_reference_is_rejected(genuine: tuple[Path, dict[str, Any]], tmp_path: Path,
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    _, document = genuine
    base, _, current = paths(document)
    row = document["corpus_and_comparison"]["drained1056"]
    row["corpus_authority"] = {**row["corpus_authority"], "sha256": "0" * 64}
    with pytest.raises(transition.TransitionError, match="changed or missing"):
        transition.validate_transition(reviewed_copy(monkeypatch, tmp_path, document), base, current)
