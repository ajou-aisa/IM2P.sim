"""Recheck finite profile captures against immutable reviewed proof bytes."""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from sim.cycle.certificate_contract import array_value, object_value, read_document
from sim.cycle.npu_trace_schema import Record, integer
from sim.cycle.reconstruct_graph import sha256
from sim.cycle.stateful_sequence_evidence import ROOT, reference, require
from sim.tests.cycle.compositional_sequence_bounded import compare_bounded_sequences
from sim.tests.cycle.compositional_sequence_v2_payload import payload_geometry
from sim.tests.cycle.compositional_sequence_v2_stream import _queue_edge


def artifact(value: Record) -> Record:
    require(set(value) == {"path", "sha256"}, "extension artifact", "path/hash required")
    return reference(Path(str(value["path"])), str(value["sha256"]))


def check_proof(proof: Record, profile: str, count: int) -> Record:
    require(proof["schema"] == "im2p-stateful-profile-exact-v1" and
            proof["status"] == "EXACT" and proof["profile"] == profile and
            proof["work_count"] == count and proof["work_ids"] == list(range(count)) and
            proof["initial_reset_count"] == 1 and proof["midrun_resets"] == proof["fallbacks"] == 0 and
            proof["offer_policy"] == "back-to-back-npu-only-independent-readiness",
            "extension proof", "profile, finite work order or session policy differs")
    artifacts = {name: artifact(object_value(proof[name], name)) for name in
                 ("input", "raw", "probe", "absolute_proof", "harness")}
    stimulus = read_document(Path(str(artifacts["input"]["path"])))
    artifacts["absolute_input"] = artifact(object_value(stimulus["absolute_input"], "absolute input"))
    original = read_document(Path(str(artifacts["absolute_input"]["path"])))
    absolute = read_document(Path(str(artifacts["absolute_proof"]["path"])))
    require(absolute["profile"] == original["profile"] == profile and
            absolute["status"] == "PASS_ABSOLUTE_MODEL_RTL" and
            absolute["work_count"] == count and original["fixture_kind"] == "GENUINE_COMPLETE_PARENT" and
            absolute["stimulus_file_sha256"] == artifacts["absolute_input"]["sha256"],
            "extension absolute proof", "independent profile hardware run required")
    for name, digest in object_value(original["source_sha256"], "probe sources").items():
        require(sha256(ROOT / name) == digest, "extension probe source", name)
    for name, value in object_value(stimulus["producer_artifacts"], "producer").items():
        artifacts[f"producer/{name}"] = artifact(object_value(value, name))
    raw = Path(str(artifacts["raw"]["path"]))
    compared = compare_bounded_sequences(raw, raw, tuple(range(count)), payload_stimulus=stimulus)
    require(json.dumps(asdict(compared), sort_keys=True) ==
            json.dumps(proof["selected_events_and_queue"], sort_keys=True),
            "extension stream", "event hash/multiplicity or queue state differs")
    geometry = payload_geometry(stimulus)
    peaks = {"RTL": [0, 0], "MODEL": [0, 0]}
    with raw.open("rb") as stream:
        for line in stream:
            for side, values in peaks.items():
                prefix = f"{side}_QUEUE_EDGE_V2".encode()
                if line.startswith(prefix + b" "):
                    generation, old, new = _queue_edge(line, prefix, geometry)
                    require(generation == 1, "extension state", "midrun reset")
                    for index, field in enumerate(("tag_count", "row_count")):
                        values[index] = max(values[index], integer(old, field), integer(new, field))
    require(peaks["RTL"] == peaks["MODEL"], "extension peaks", "native/RTL state differs")
    records = object_value(proof["works"], "works")
    for rtl, model in zip(array_value(records["rtl"], "RTL works"),
                          array_value(records["model"], "model works"), strict=True):
        left, right = object_value(rtl, "RTL work"), object_value(model, "model work")
        for field in array_value(proof["work_comparison_fields"], "comparison fields"):
            require(left[str(field)] == right[str(field)], "extension boundary", str(field))
        require(left["numeric_pass"] is True and
                left["physical_fragment_count"] == right["fragment_count"],
                "extension fragment", "numeric or fragment mismatch")
    return {"artifacts": {name: value for name, value in artifacts.items()},
            "tag_peak": peaks["RTL"][0], "row_peak": peaks["RTL"][1],
            "library_sha256": absolute["cycle_library_sha256"],
            "hardware_contract": original["hardware_contract"]}


def check_milestone(document: Record, profile: str, proof: Record) -> None:
    require(document["schema"] == "stateful-profile-milestone-parity-v1" and
            document["status"] == "EXACT" and document["profile"] == profile and
            document["work_count"] == proof["work_count"] and
            document["initial_resets_per_session"] == 1 and
            document["midrun_resets"] == document["fallbacks"] == 0,
            "extension milestone", "finite profile parity required")
    for name in ("trace", "library", "harness"):
        artifact(object_value(document[name], name))
    edge = object_value(document["cycle_stepping"], "edge")
    boundary = object_value(document["advance_to_boundary"], "boundary")
    require({key: value for key, value in edge.items() if key != "calls"} ==
            {key: value for key, value in boundary.items() if key != "calls"} and
            edge["final_cycle"] == proof["final_resource_cycle"] and integer(edge, "event_count") > 0,
            "extension milestone", "cycles/state/events/reports/counters differ")
