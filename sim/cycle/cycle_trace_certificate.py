"""Admit a fresh inference trace only through exact equivalence to a certified corpus.

The native cycle model is value-free: its behaviour is fixed by the ordered NPU
work descriptors, the profile and the offer policy. A new production trace is
therefore admissible under an existing certificate exactly when its ordered
descriptor sequence equals the one that certificate already admits. Nothing here
widens a state domain, relabels a profile or trusts a caller-supplied PASS label.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Final, assert_never

from sim.cycle import stateful_sequence_certificate
from sim.cycle.certificate_contract import array_value, object_value, read_document
from sim.cycle.npu_trace import work_binding
from sim.cycle.npu_trace_integrity import read_records, start_trace
from sim.cycle.npu_trace_schema import Record
from sim.cycle.npu_trace_schema import Work as TraceWork
from sim.cycle.reconstruct_graph import sha256
from sim.cycle.stateful_domain import InitialState
from sim.cycle.stateful_sequence_certificate import ScopedEvidence
from sim.cycle.stateful_sequence_evidence import (
    ROOT,
    EvidenceContext,
    StatefulCertificateError,
    reference,
    require,
)

SCHEMA: Final = "im2p-cycle-trace-certificate-v1"
EQUIVALENCE: Final = "EXACT_ORDERED_NPU_WORK_DESCRIPTORS"
PARENT_SCHEMAS: Final = ("stateful-full374-replay-v1", "stateful-profile-domain-v1",
                         "stateful-profile-extension-v1")
PROVIDER_SOURCES: Final = (
    "sim/cycle/cycle_trace_certificate.py", "sim/cycle/execution_sequence_provider.py",
    "sim/cycle/execution_sequence_admission.py", "sim/cycle/stateful_sequence_certificate.py",
    "sim/cycle/stateful_domain.py", "sim/cycle/stateful_domain_admission.py",
    "sim/cycle/sequence_binding.py", "sim/cycle/sequence_binding_abi.py",
    "sim/cycle/npu_trace.py", "sim/cycle/npu_trace_integrity.py", "sim/cycle/npu_trace_schema.py",
)
IDENTITY_FIELDS: Final = ("model", "precision", "dim", "BK")
INPUT_KEYS: Final = ("m", "n", "k", "tile_i_count", "tile_j_count", "tile_k_count", "activation_stride_bytes",
                     "weight_stride_bytes", "output_stride_bytes", "scale_stride_elements")
# One parse per (trace bytes, certified bytes, this source); every use rehashes all three.
_summaries: dict[tuple[str, str, str], TraceSummary] = {}


@dataclass(frozen=True, slots=True)
class TraceSummary:
    profile: str
    contract_sha256: str
    work_ids: tuple[int, ...]
    geometry: Record
    tile_shape: Record
    ordered_runs: Record
    row_mapping: Record
    work_binding_sha256: str


def _descriptor(work: TraceWork) -> Record:
    return {"work_id": work.identity, "parent_id": work.parent_id, "provenance": work.provenance,
            "residual_work_revision": work.residual_work_revision, "inputs": list(work.inputs),
            "original_k": work.original_k,
            "runs": [[run.original_block_id, run.original_k_mask, run.compact_k_begin, run.compact_k_count]
                     for run in work.runs],
            "row_map": [[row.source_row, row.lane_id] for row in work.row_map],
            "binding": work_binding(work)}


def _line(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode() + b"\n"


def _works(trace: Path) -> tuple[str, str, Iterator[TraceWork]]:
    records = read_records(trace)
    state = start_trace(records)

    def stream() -> Iterator[TraceWork]:
        for record in records:
            if (work := state.consume(record)) is not None:
                yield work
        _ = state.summary()

    return state.run.profile, state.run.contract_hash, stream()


def summarize(trace: Path, certified: Path | None = None) -> TraceSummary:
    key = (sha256(trace), "" if certified is None else sha256(certified), sha256(Path(__file__)))
    if (cached := _summaries.get(key)) is not None:
        return cached
    profile, contract, works = _works(trace)
    reference_works: Iterator[TraceWork] | None = None
    if certified is not None:
        certified_profile, certified_contract, reference_works = _works(certified)
        require(profile == certified_profile and contract == certified_contract, "trace equivalence",
                f"profile/contract {profile}/{contract[:12]} differs from certified "
                f"{certified_profile}/{certified_contract[:12]}")
    digests = {name: hashlib.sha256() for name in ("descriptors", "tiles", "runs", "rows", "bindings")}
    ids: list[int] = []
    classes = {"dense_main": 0, "residual": 0}
    run_count = multi_run = mapped = 0
    for index, work in enumerate(works):
        row = _descriptor(work)
        if reference_works is not None:
            other = next(reference_works, None)
            require(other is not None, "trace equivalence", f"work {index} exceeds certified corpus length")
            require(other is not None and _descriptor(other) == row, "trace equivalence",
                    f"work {index} descriptor differs from certified corpus")
        require(work.provenance in classes, "trace work class", f"unsupported provenance {work.provenance}")
        ids.append(work.identity)
        classes[work.provenance] += 1
        run_count += len(work.runs)
        multi_run += len(work.runs) > 1
        mapped += len(work.row_map)
        digests["descriptors"].update(_line(row))
        digests["tiles"].update(_line(list(work.inputs[3:6])))
        digests["runs"].update(_line(row["runs"]))
        digests["rows"].update(_line(row["row_map"]))
        digests["bindings"].update(_line([work.identity, work.parent_id, row["binding"]]))
    if reference_works is not None:
        require(next(reference_works, None) is None, "trace equivalence",
                f"certified corpus has more than {len(ids)} works")
    require(bool(ids), "trace", "no NPU work")
    summary = TraceSummary(
        profile, contract, tuple(ids),
        {"profile": profile, "hardware_contract_sha256": contract, "work_count": len(ids),
         "dense_main_works": classes["dense_main"], "residual_works": classes["residual"],
         "input_keys": list(INPUT_KEYS), "descriptors_sha256": digests["descriptors"].hexdigest()},
        {"fields": list(INPUT_KEYS[3:6]), "sha256": digests["tiles"].hexdigest()},
        {"run_count": run_count, "multi_run_works": multi_run, "sha256": digests["runs"].hexdigest()},
        {"mapped_rows": mapped, "sha256": digests["rows"].hexdigest()}, digests["bindings"].hexdigest())
    if key == (sha256(trace), "" if certified is None else sha256(certified), sha256(Path(__file__))):
        _summaries.clear()
        _summaries[key] = summary
    return summary


def context_record(context: EvidenceContext) -> Record:
    return {
        "evidence_root": str(context.evidence_root), "library": str(context.library),
        "shared_library": str(context.shared_library), "base_parent": str(context.base_parent),
        "run_aware_parent": str(context.run_aware_parent), "service_parent": str(context.service_parent),
        "current_evidence_input": None if context.current_evidence_input is None else str(context.current_evidence_input),
        "domain_delta_input": None if context.domain_delta_input is None else str(context.domain_delta_input),
        "tag6_evidence_input": None if context.tag6_evidence_input is None else str(context.tag6_evidence_input),
    }


def context_from(value: Record) -> EvidenceContext:
    require(set(value) == {"evidence_root", "library", "shared_library", "base_parent", "run_aware_parent",
                           "service_parent", "current_evidence_input", "domain_delta_input",
                           "tag6_evidence_input"}, "evidence context", "exact context fields required")

    def optional(name: str) -> Path | None:
        item = value[name]
        return None if item is None else Path(str(item))

    return EvidenceContext(*(Path(str(value[name])) for name in (
        "evidence_root", "library", "shared_library", "base_parent", "run_aware_parent", "service_parent")),
        current_evidence_input=optional("current_evidence_input"),
        domain_delta_input=optional("domain_delta_input"), tag6_evidence_input=optional("tag6_evidence_input"))


def provider_revision() -> Record:
    sources: Record = {name: sha256(ROOT / name) for name in PROVIDER_SOURCES}
    return {"sources": sources, "sha256": hashlib.sha256(_line(sources)).hexdigest()}


def expected(trace: Path, identity: Record, parent: Path, context: EvidenceContext,
             producer: Record, manifest: Record) -> Record:
    require(trace.is_absolute() and parent.is_absolute(), "certificate inputs", "absolute paths required")
    require(set(identity) == set(IDENTITY_FIELDS) and identity["BK"] == 32 and
            identity["precision"] in ("A4W4", "A8W8") and identity["dim"] in (16, 32, 64),
            "identity", "model, A4W4/A8W8, DIM16/32/64 and BK32 required")
    parent_document = read_document(parent)
    require(parent_document.get("schema") in PARENT_SCHEMAS, "parent certificate",
            "only an independently certified corpus can be a parent")
    bits = str(identity["precision"])[1]
    profile = f"a{bits}w{bits}-d{identity['dim']}-hp1"
    require(parent_document.get("profile") == profile, "profile",
            f"no certified corpus for {profile} in parent {parent_document.get('profile')}")
    certified = object_value(parent_document.get("trace"), "certified trace")
    certified_ref = reference(Path(str(certified["path"])), str(certified["sha256"]))
    summary = summarize(trace, Path(str(certified_ref["path"])))
    require(summary.profile == profile and list(summary.work_ids) == parent_document.get("work_ids"),
            "trace equivalence", "trace profile or certified work order differs")
    scoped = stateful_sequence_certificate.validate(parent, context)
    stateful_sequence_certificate.admit(parent, context)
    producer_ref = reference(Path(str(producer["path"])), str(producer["sha256"]))
    manifest_ref = reference(Path(str(manifest["path"])), str(manifest["sha256"]))
    manifest_value = read_document(Path(str(manifest_ref["path"])))
    require(all(manifest_value.get(key) == identity[key] for key in IDENTITY_FIELDS),
            "evaluation manifest", "identity differs from manifest")
    trace_ref = reference(trace, sha256(trace))
    return {
        "schema": SCHEMA, "version": 1, "artifact_role": "CYCLE_TRACE_ADMISSION",
        "validation_scope": "SCOPED_EVIDENCE", "production_admitted": False,
        **identity, "profile": profile,
        "trace": trace_ref, "trace_sha256": trace_ref["sha256"], "trace_work_count": len(summary.work_ids),
        "work_ids": list(summary.work_ids), "producer": producer_ref, "producer_sha256": producer_ref["sha256"],
        "evaluation_manifest": manifest_ref,
        "geometry": summary.geometry, "tile_shape": summary.tile_shape, "ordered_runs": summary.ordered_runs,
        "row_mapping": summary.row_mapping, "work_binding_sha256": summary.work_binding_sha256,
        "state_domain_revision": scoped.state_domain_revision, "initial_state": asdict(InitialState()),
        "provider_revision": provider_revision(),
        "parent_certificate": {**reference(parent, sha256(parent)), "schema": parent_document["schema"],
                               "case_ids": list(scoped.case_ids)},
        "certified_trace": certified_ref, "evidence_context": context_record(context),
        "equivalence": EQUIVALENCE, "offer_policy": "back-to-back-npu-only-independent-readiness",
        "scope": "fresh production trace equal to an independently certified finite corpus",
        "cycle_count_is_not_latency_ms": True,
        "E2E_RECONSTRUCTION_READY": "NOT_READY", "PAPER_CAMPAIGN_COMPLETE": "NOT_RUN",
    }


def _recompute(document: Record) -> Record:
    parent = object_value(document.get("parent_certificate"), "parent certificate")
    trace = object_value(document.get("trace"), "trace")
    return expected(Path(str(trace["path"])), {key: document.get(key) for key in IDENTITY_FIELDS},
                    Path(str(parent["path"])),
                    context_from(object_value(document.get("evidence_context"), "evidence context")),
                    object_value(document.get("producer"), "producer"),
                    object_value(document.get("evaluation_manifest"), "evaluation manifest"))


def verify_certificate(path: Path, context: EvidenceContext | None = None) -> Record:
    document = read_document(path)
    require(document.get("schema") == SCHEMA, "cycle trace schema", "cycle trace certificate required")
    if context is not None:
        require(document.get("evidence_context") == context_record(context), "evidence context",
                "caller context differs from certificate")
    require(json.dumps(document, sort_keys=True) == json.dumps(_recompute(document), sort_keys=True),
            "cycle trace certificate", "trace, producer, parent, domain, source or provider binding differs")
    return document


def validate(path: Path, context: EvidenceContext) -> ScopedEvidence:
    document = verify_certificate(path, context)
    parent = object_value(document["parent_certificate"], "parent certificate")
    cases = tuple(f"cycle-trace:{case}" for case in array_value(parent["case_ids"], "parent cases"))
    return ScopedEvidence(sha256(path), sha256(context.library), cases,
                          state_domain_revision=str(document["state_domain_revision"]))


def build(output: Path, trace: Path, identity: Record, parent: Path, context: EvidenceContext,
          producer: Record, manifest: Record) -> Path:
    if os.path.lexists(output):
        raise FileExistsError(output)
    document = expected(trace, identity, parent, context, producer, manifest)
    with tempfile.TemporaryDirectory(prefix=f".{output.name}.", dir=output.parent) as directory:
        staged = Path(directory) / "certificate.json"
        with staged.open("x") as stream:
            json.dump(document, stream, indent=2, sort_keys=True)
            stream.write("\n")
        os.link(staged, output)
    _ = verify_certificate(output, context)
    return output


def parent_context(parent: Path, evidence_root: Path) -> EvidenceContext:
    document = read_document(parent)
    parents = object_value(document.get("parents"), "parents")
    evidence = (Path(str(object_value(document.get("evidence_input"), "evidence input")["path"]))
                if document.get("schema") == "stateful-full374-replay-v1" else None)
    return EvidenceContext(evidence_root, Path(str(object_value(document.get("library"), "library")["path"])),
                           Path(str(object_value(document.get("shared_library"), "shared library")["path"])),
                           *(Path(str(object_value(parents[key], key)["path"]))
                             for key in ("base", "run_aware", "service")),
                           tag6_evidence_input=evidence)


def main() -> int:
    parser = argparse.ArgumentParser(description="Exact-equivalence cycle trace certificate")
    commands = parser.add_subparsers(dest="command", required=True)
    make = commands.add_parser("build")
    make.add_argument("--trace", type=Path, required=True)
    make.add_argument("--parent-certificate", type=Path, required=True)
    make.add_argument("--evidence-root", type=Path, required=True)
    make.add_argument("--producer", type=Path, required=True)
    make.add_argument("--evaluation-manifest", type=Path, required=True)
    make.add_argument("output", type=Path)
    check = commands.add_parser("verify")
    check.add_argument("path", type=Path)
    args = parser.parse_args()
    try:
        match args.command:
            case "build":
                parent = args.parent_certificate.resolve(strict=True)
                manifest_path = args.evaluation_manifest.resolve(strict=True)
                manifest = read_document(manifest_path)
                producer = args.producer.resolve(strict=True)
                path = build(args.output.resolve(), args.trace.resolve(strict=True),
                             {key: manifest.get(key) for key in IDENTITY_FIELDS}, parent,
                             parent_context(parent, args.evidence_root.resolve(strict=True)),
                             {"path": str(producer), "sha256": sha256(producer)},
                             {"path": str(manifest_path), "sha256": sha256(manifest_path)})
                print(json.dumps({"certificate": str(path), "sha256": sha256(path), "status": "VERIFIED"}))
            case "verify":
                document = verify_certificate(args.path.resolve(strict=True))
                print(json.dumps({"certificate": str(args.path), "status": "VERIFIED",
                                  "state_domain_revision": document["state_domain_revision"],
                                  "trace_sha256": document["trace_sha256"]}))
            case unreachable:
                assert_never(unreachable)
    except (StatefulCertificateError, OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({"status": "REJECTED", "error": str(error)}), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
