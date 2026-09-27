"""Three-certificate admission for the independently replayed original374 trace."""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Final

from sim.cycle.certificate_contract import object_value, read_document
from sim.cycle.npu_trace_schema import Record
from sim.cycle.reconstruct_graph import sha256
from sim.cycle.stateful_sequence_evidence import (
    ROOT,
    EvidenceContext,
    reference,
    require,
)
from sim.cycle.stateful_sequence_evidence_tag6 import (
    validate_document as validate_domain,
)
from sim.cycle.stateful_sequence_replay_state import inspect_records

SCHEMA: Final = "stateful-full374-replay-v1"
STATE_SCHEMA: Final = "stateful-tag6-transitions-v1"
STATE_PATH: Final = "full374/stateful-tag6-transitions-v1.json"
RECORDS_SHA256: Final = "8b916cc3534274ea0588a2837fa8dffe98f6e59d70dd29fccc739b7fc3202acf"
PINS: Final = {
    "domain": ("state-domain/stateful-tag6-domain-v1.json",
               "5002f88b33c6d7a5587a53995332c03748c1d52f7f93ed815eb90bd1b5fbb04d"),
    "first": ("full374/second/report.json",
              "20c5219d0e340834ea4056ac6946cfe67a30b6cfba43b1efe290897538940419"),
    "fresh": ("full374/fresh/report.json",
              "f8913594f38df8526b0a6aff875cfa92dbcaf5d23bd8da862e34bc767a8b4b5d"),
}
CONSUMER_SOURCES: Final = (
    "sim/cycle/stateful_sequence_replay_certificate.py", "sim/cycle/stateful_sequence_replay_state.py",
    "sim/cycle/stateful_sequence_certificate.py", "sim/cycle/execution_sequence_admission.py",
    "sim/cycle/execution_sequence_provider.py", "sim/cycle/execution_cli.py",
    "sim/cycle/sequence_trace_cli.py", "sim/cycle/scheduler.py", "sim/cycle/scheduler_sqlite.py",
)


def _verified(context: EvidenceContext) -> tuple[Record, Record, Record]:
    references: Record = {}
    documents: dict[str, Record] = {}
    for name, (relative, digest) in PINS.items():
        path = context.evidence_root / relative
        references[name] = reference(path, digest)
        documents[name] = read_document(path)
    domain = validate_domain(context.evidence_root / PINS["domain"][0], context)
    for name in ("first", "fresh"):
        run = documents[name]
        require(run["status"] == "PASS" and run["work_ids"] == list(range(374)) and
                run["work_count"] == run["trace_work_count"] == 374 and run["same_handle"] is True and
                run["reset_count"] == 1 and run["midrun_resets"] == run["fallbacks"] == 0 and
                run["domain_certificate"] == references["domain"] and run["trace"] == domain["trace"],
                "full374 proof", f"{name} scope/lifetime differs")
        records = object_value(run["records"], "replay records")
        require(records["sha256"] == RECORDS_SHA256, "full374 proof", "deterministic payload differs")
        references[f"{name}_records"] = reference(Path(str(records["path"])), RECORDS_SHA256)
        harness = object_value(run["harness"], "replay harness")
        references["harness"] = reference(Path(str(harness["path"])), str(harness["sha256"]))
    require(documents["fresh"]["fresh_record_parity"] is True and
            documents["fresh"]["fresh_reference"] == references["first"],
            "full374 proof", "independent fresh comparison absent")
    records = object_value(references["first_records"], "first records")
    transition = inspect_records(Path(str(records["path"])))
    require(transition["final_cursor"] == 501848295 and transition["tag_peak"] == 6 and
            transition["row_peak"] == 5 and transition["ready_mask"] == 0,
            "full374 proof", "finite original state domain differs")
    return domain, references, transition


def _state(domain: Record, references: Record, transition: Record) -> Record:
    return {
        "schema": STATE_SCHEMA, "version": 1, "status": "PASS",
        "domain_certificate": references["domain"], "trace": domain["trace"],
        "proofs": references, "transition": transition,
        "validator_sha256": sha256(Path(__file__).with_name("stateful_sequence_replay_state.py")),
        "formal_proof": False, "scope": "finite original374 native state transitions",
    }


def expected(context: EvidenceContext) -> Record:
    domain, references, transition = _verified(context)
    state_path = context.evidence_root / STATE_PATH
    state = read_document(state_path)
    require(json.dumps(state, sort_keys=True) == json.dumps(_state(domain, references, transition), sort_keys=True),
            "state-transition certificate", "missing, stale or altered transition proof")
    return {
        "schema": SCHEMA, "version": 1, "status": "PASS",
        "state_domain_revision": domain["state_domain_revision"], "profile": domain["profile"],
        "trace": domain["trace"], "trace_work_count": 374, "work_ids": list(range(374)),
        "library": domain["library"], "shared_library": domain["shared_library"],
        "parents": domain["parents"], "evidence_input": domain["evidence_input"],
        "reference_memory": domain["reference_memory"], "domain_certificate": references["domain"],
        "state_transition_certificate": reference(state_path, sha256(state_path)),
        "replay_proofs": references, "transition": transition,
        "offer_policy": domain["offer_policy"], "domain": domain["domain"],
        "source_sha256": {name: sha256(ROOT / name) for name in CONSUMER_SOURCES},
        "production_admitted": False, "production_eligibility": "CERTIFIED_ORIGINAL374_ONLY",
        "formal_proof": False, "E2E_RECONSTRUCTION_READY": "NOT_READY", "PAPER_CAMPAIGN_COMPLETE": "NOT_RUN",
    }


def validate_document(path: Path, context: EvidenceContext) -> Record:
    document = read_document(path)
    require(document.get("schema") == SCHEMA, "full374 schema", "three-certificate replay required")
    require(json.dumps(document, sort_keys=True) == json.dumps(expected(context), sort_keys=True),
            "full374 certificate", "domain, transition, replay or consumer source differs")
    return document


def _publish(path: Path, document: Record) -> None:
    if os.path.lexists(path):
        raise FileExistsError(path)
    with tempfile.TemporaryDirectory(prefix=f".{path.name}.", dir=path.parent) as directory:
        staged = Path(directory) / "certificate.json"
        with staged.open("x") as stream:
            json.dump(document, stream, indent=2, sort_keys=True)
            stream.write("\n")
        os.link(staged, path)


def build(output: Path, context: EvidenceContext) -> Path:
    domain, references, transition = _verified(context)
    _publish(context.evidence_root / STATE_PATH, _state(domain, references, transition))
    document = expected(context)
    _publish(output, document)
    validate_document(output, context)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="Certify independently replayed original374 state")
    parser.add_argument("--context", type=Path, required=True)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    value = read_document(args.context)
    context = EvidenceContext(
        *(Path(str(value[key])) for key in
          ("evidence_root", "library", "shared_library", "base_parent", "run_aware_parent", "service_parent")),
        tag6_evidence_input=Path(str(value["tag6_evidence_input"])),
    )
    build(args.output, context)
    print(f"FULL374_CERTIFICATES_PASS {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
