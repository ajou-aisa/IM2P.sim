# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
# How to run: PYTHONPATH=IM2P.sim python3 -B -m sim.tests.cycle.actual_trace_stimulus TRACE LIFECYCLE OUTPUT.json CASE_ID
"""Seal an actual-inference trace as an RTL/model probe stimulus; oversize works stay native-only."""
from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Final

from scripts.gemmini_replay_contract import hardware_contract
from sim.cycle.npu_trace import work_binding
from sim.cycle.npu_trace_schema import INPUT_KEYS, Record, Work
from sim.cycle.reconstruct_graph import sha256
from sim.cycle.sequence_trace_cli import load_trace
from sim.cycle.stateful_sequence_evidence import require
from sim.tests.cycle.compositional_sequence_v2_base import stimulus_digest

PROBE_MAX_WORKS: Final = 4096
PROBE_MAX_EXTENT: Final = 8192
PROBE_MAX_RUNS: Final = 256
SCHEMA: Final = "im2p-actual-prefix-availability-diagnostic-v1"


def probe_admissible(work: Work) -> bool:
    return max(work.inputs[:3]) <= PROBE_MAX_EXTENT and len(work.runs) <= PROBE_MAX_RUNS


def workspace_slots(lifecycle: Path) -> dict[int, int]:
    rows = [json.loads(line) for line in lifecycle.read_text().splitlines()]
    dense: dict[int, int] = {}
    for row in rows:
        if row["kind"] == "PIPELINE_OWNER" and row["transition"] == "ACQUIRE":
            require(dense.setdefault(row["work_id"], row["workspace_slot"]) == row["workspace_slot"],
                    "stimulus", "dense work has inconsistent workspace slot")
    slots = dict(dense)
    for row in rows:
        if row["kind"] == "PIPELINE_PARENT":
            for binding in row["residual_bindings"]:
                slots[binding["work_id"]] = dense[binding["dense_work_id"]]
    return slots


def seal(trace: Path, lifecycle: Path, output: Path, case_id: str) -> Record:
    require(not output.exists() and not output.with_suffix(".txt").exists(), "stimulus", f"exists: {output}")
    profile, works, trace_digest = load_trace(trace)
    lifecycle_digest = sha256(lifecycle)
    count = 0
    while count < min(len(works), PROBE_MAX_WORKS) and probe_admissible(works[count]):
        count += 1
    chosen, tail = works[:count], works[count:]
    require(bool(chosen) and all(not probe_admissible(work) for work in tail), "stimulus",
            "probe-admissible works must form one prefix followed only by oversize native-only works")
    slots = workspace_slots(lifecycle)
    require(all(slots.get(work.identity) in (0, 1) for work in chosen), "stimulus", "workspace ownership missing")
    projection: Record = {
        "schema": SCHEMA, "case_id": case_id, "profile": profile,
        "hardware_contract": hardware_contract(profile),
        "trace": {"path": str(trace), "sha256": trace_digest},
        "lifecycle": {"path": str(lifecycle), "sha256": lifecycle_digest},
        "trace_work_count": len(works), "selected_work_ids": [work.identity for work in chosen],
        "native_only_work_ids": [work.identity for work in tail],
        "instance_count": 1, "reset_count": 1,
        "execution_scope": "DIAGNOSTIC_PREFIX_NOT_COMPLETE_APPLICATION",
        "offer_policy": "back-to-back-npu-only-independent-readiness",
        "availability": [0 for _ in chosen], "slots": [slots[work.identity] for work in chosen],
        "works": [{"work_id": work.identity, "input": dict(zip(INPUT_KEYS, work.inputs, strict=True)),
                   "trace_record": asdict(work)} for work in chosen],
    }
    projection["stimulus_sha256"] = stimulus_digest(projection)
    lines = [f"IM2P_COMPOSITIONAL_SEQUENCE_V2 {profile}", "PERIOD 5", f"WORKS {count}",
             f"DIGEST {projection['stimulus_sha256']}"]
    for ordinal, work in enumerate(chosen):
        fields = (ordinal, work.identity, "R" if work.scope == "residual_compact" else "D",
                  slots[work.identity], 0, work.parent_id, work.call_id,
                  work.stripe_id if work.stripe_id is not None else 0, work.row_begin,
                  work.parent_rows, *work.inputs, work.original_k or 0, work_binding(work), len(work.runs))
        lines.append("W " + " ".join(map(str, fields)))
        for run in work.runs:
            lines.append("R " + " ".join(map(str, (run.original_block_id, run.original_k_mask,
                                                   run.compact_k_begin, run.compact_k_count))))
        lines.append(f"A {ordinal} 0 0")
    require(sha256(trace) == trace_digest and sha256(lifecycle) == lifecycle_digest, "stimulus",
            "producer inputs changed while sealing")
    with output.open("x") as stream:
        stream.write(json.dumps(projection, indent=2, sort_keys=True) + "\n")
    with output.with_suffix(".txt").open("x") as stream:
        stream.write("\n".join(lines) + "\n")
    return projection


if __name__ == "__main__":
    trace_path, lifecycle_path, output_path = map(Path, sys.argv[1:4])
    sealed = seal(trace_path, lifecycle_path, output_path, sys.argv[4])
    print("ACTUAL_STIMULUS_SEALED " + json.dumps({key: sealed[key] for key in (
        "profile", "trace_work_count", "native_only_work_ids", "stimulus_sha256")}), flush=True)
