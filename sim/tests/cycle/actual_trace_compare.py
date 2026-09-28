# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
# How to run: PYTHONPATH=IM2P.sim python3 -B -m sim.tests.cycle.actual_trace_compare CAPTURE_DIR STIMULUS.json OUTPUT.json
"""Compare complete retained RTL and model streams of an actual-trace prefix; report first divergence."""
from __future__ import annotations

import gzip
import json
import sys
import time
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path

from sim.cycle.certificate_contract import array_value, object_value, read_document
from sim.cycle.npu_trace import work_binding
from sim.cycle.npu_trace_schema import Record, integer
from sim.cycle.reconstruct_graph import sha256
from sim.cycle.sequence_trace_cli import load_trace
from sim.cycle.stateful_sequence_evidence_tag5 import RAW_FIELDS
from sim.tests.cycle.compositional_sequence_bounded import compare_bounded_sequences
from sim.tests.cycle.compositional_sequence_v2_base import AbsoluteOfferError


@dataclass(slots=True)
class Observed:
    runs: list[Record] = field(default_factory=list[Record])
    works: list[Record] = field(default_factory=list[Record])
    tag_peak: int = 0
    row_peak: int = 0
    first_peak: Record | None = None


def compare(capture_dir: Path, stimulus_path: Path, output: Path) -> Record:
    if output.exists():
        raise FileExistsError(output)
    started = time.monotonic()
    stimulus = read_document(stimulus_path)
    capture = read_document(capture_dir / "capture.json")
    artifacts = object_value(capture["artifacts"], "capture artifacts")
    selected = [int(str(item)) for item in array_value(stimulus["selected_work_ids"], "selected works")]
    trace_ref = object_value(stimulus["trace"], "stimulus trace")
    profile, works, digest = load_trace(Path(str(trace_ref["path"])))
    observed = {side: Observed() for side in ("rtl", "model")}

    def raw(side: str) -> Record:
        return object_value(object_value(artifacts[side], side)["raw"], f"{side} raw")

    def scan(side: str) -> Iterator[bytes]:
        state = observed[side]
        with gzip.open(Path(str(raw(side)["path"])), "rb") as stream:
            for line in stream:
                if line.startswith((b"COMPOSITION_RUN ", b"MODEL_RUN ")):
                    run = json.loads(line.split(b" ", 1)[1])
                    if (run["instance_count"], run["reset_count"], run["period"], run["work_count"],
                            run["stimulus_sha256"], run["offer_mode"]) != (
                            1, 1, 5, len(selected), stimulus["stimulus_sha256"], "availability-driven"):
                        raise ValueError(f"{side} run identity/reset/policy differs")
                    state.runs.append(run)
                elif line.startswith((b"COMPOSITION_WORK ", b"MODEL_WORK ")):
                    row = json.loads(line.split(b" ", 1)[1])
                    ordinal = len(state.works)
                    work = works[selected[ordinal]]
                    if (row["ordinal"], row["work_id"], row["work_binding"]) != (
                            ordinal, work.identity, work_binding(work)):
                        raise ValueError(f"{side} actual work identity differs at ordinal {ordinal}")
                    if side == "rtl" and row["numeric_pass"] is not True:
                        raise ValueError(f"RTL numerical fixture failed at ordinal {ordinal}")
                    state.works.append(row)
                elif line.startswith((b"RTL_QUEUE_EDGE_V2 ", b"MODEL_QUEUE_EDGE_V2 ")):
                    edge = json.loads(line.split(b" ", 1)[1])
                    tag = max(integer(edge["old"], "tag_count"), integer(edge["next"], "tag_count"))
                    rows = max(integer(edge["old"], "row_count"), integer(edge["next"], "row_count"))
                    if tag > state.tag_peak or rows > state.row_peak:
                        state.tag_peak, state.row_peak = max(tag, state.tag_peak), max(rows, state.row_peak)
                        state.first_peak = {"tag_peak": state.tag_peak, "row_peak": state.row_peak,
                                            "cycle": edge["next"]["cycle"], "ordinal": len(state.works)}
                yield line

    report: Record
    try:
        paired = compare_bounded_sequences(scan("rtl"), scan("model"), tuple(selected), payload_stimulus=stimulus)
        for side, summary in (("rtl", paired.rtl), ("model", paired.model)):
            if (len(observed[side].runs) != 1 or len(observed[side].works) != len(selected) or
                    summary.sha256 != raw(side)["restored_sha256"]):
                raise ValueError(f"{side} full raw coverage/hash differs")
        rtl, model = observed["rtl"], observed["model"]
        for left, right in zip(rtl.works, model.works, strict=True):
            for name in RAW_FIELDS:
                if left[name] != right[name]:
                    raise ValueError(f"first endpoint divergence work={left['work_id']} field={name}: "
                                     f"rtl={left[name]} model={right[name]}")
        if (rtl.tag_peak, rtl.row_peak, rtl.first_peak) != (model.tag_peak, model.row_peak, model.first_peak):
            raise ValueError("RTL/model observed tag or row peaks differ")
        report = {
            "schema": "im2p-actual-trace-prefix-comparison-v1", "status": "EXACT",
            "scope": "FINITE_ACTUAL_TRACE_PREFIX_NOT_FORMAL_PROOF", "profile": profile,
            "trace_work_count": len(works), "work_ids": [item for item in selected],
            "native_only_work_ids": stimulus["native_only_work_ids"],
            "selected_events_and_queue": asdict(paired), "tag_peak": rtl.tag_peak, "row_peak": rtl.row_peak,
            "first_peak": rtl.first_peak, "endpoint_fields": [name for name in RAW_FIELDS],
            "endpoints": [row for row in rtl.works],
            "numerical_scope": "actual geometry/runs with synthetic all-one operands",
            "trace": {"path": str(trace_ref["path"]), "sha256": digest}}
    except (ValueError, KeyError, TypeError, AbsoluteOfferError) as error:
        report = {"schema": "im2p-actual-trace-prefix-comparison-v1",
                  "status": "FIRST_DIVERGENCE_OR_INVALID_EVIDENCE", "detail": str(error),
                  "model_timing_modified": False}
    report.update(stimulus={"path": str(stimulus_path), "sha256": sha256(stimulus_path)},
                  capture={"path": str(capture_dir / "capture.json"), "sha256": sha256(capture_dir / "capture.json")},
                  harness={"path": str(Path(__file__)), "sha256": sha256(Path(__file__))},
                  elapsed_seconds=time.monotonic() - started)
    with output.open("x") as stream:
        stream.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


if __name__ == "__main__":
    result = compare(*map(Path, sys.argv[1:4]))
    print("ACTUAL_COMPARISON " + json.dumps({key: result.get(key) for key in (
        "status", "detail", "tag_peak", "row_peak")}), flush=True)
    raise SystemExit(0 if result["status"] == "EXACT" else 1)
