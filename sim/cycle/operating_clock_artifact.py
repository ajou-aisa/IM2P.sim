"""Normalize a real validated clock; never turn a requested frequency into one."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts.evaluation_clock import load_selection
from scripts.evaluation_clock_contract import (
    ClockError,
    bound_file,
    read_record,
    record,
    require,
    sha256,
    text,
)
from sim.cycle.stateful_measurement_contract import (
    ArtifactReference,
    OperatingClockArtifact,
)


def load_clock_artifact(path: Path, profile: str) -> OperatingClockArtifact:
    """Retain the existing fixed-policy, post-route and source-artifact gate."""
    selection = load_selection(path, profile)
    document = read_record(path)
    selected = record(document["selected"])
    require(selected.get("execution_kind") == "TOOL_EXECUTION" and
            selected.get("timing_stage") == "POST_ROUTE",
            "real post-route tool execution required")
    source = bound_file(selected["source"], path.parent)
    source_path = Path(text(source, "path"))
    source_document = read_record(source_path)
    rtl = {
        name: text(bound_file(reference, source_path.parent), "sha256")
        for name, reference in record(source_document["artifacts"]).items()
        if Path(text(record(reference), "path")).suffix in (".sv", ".v", ".svh", ".vh")
    }
    require(bool(rtl), "complete RTL artifact hashes required")
    constraints = selected["constraints"]
    if not isinstance(constraints, list) or len(constraints) != 1:
        raise ClockError("one verified fixed-policy constraint artifact required")
    constraint = bound_file(constraints[0], path.parent)
    implementation = bound_file(selected["netlist"], path.parent)
    require(sha256(path) == selection.sha256 and sha256(source_path) == text(source, "sha256"),
            "clock selection/source changed during normalization")
    # The aggregate binds every named RTL file, not an arbitrary top-only hash.
    rtl_hash = hashlib.sha256(json.dumps(rtl, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return OperatingClockArtifact(
        profile, selection.frequency_hz, ArtifactReference(str(path.resolve()), selection.sha256),
        rtl_hash, text(constraint, "sha256"), text(implementation, "sha256"),
        "POST_ROUTE_PASS", selection.hardware_contract_sha256,
    )
