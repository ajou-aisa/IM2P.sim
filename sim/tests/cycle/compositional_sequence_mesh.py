from __future__ import annotations

import gzip
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from sim.cycle.npu_trace_schema import Record, integer, object_value, unique_pairs
from sim.tests.cycle.compositional_sequence_v2_base import AbsoluteOfferError

FIELDS: Final = (
    "mesh_schema", "generation", "cycle", "ordinal", "work_id",
    "request_valid", "request_rows", "request_counter", "written_mask", "matmul_id",
    "control_valid", "control_first", "control_fire_mask", "side_valid_mask",
    "side_ready_mask", "attempt_valid", "request_ready", "request_fire",
    "stall_reason_mask", "tag_count", "row_count", "tag_read", "tag_write",
    "row_read", "row_write", "tag_valid", "row_valid",
    "owner_generation", "owner_ordinal", "owner_work",
)
BITS: Final = (
    "request_valid", "control_valid", "control_first", "attempt_valid",
    "request_ready", "request_fire", "tag_valid", "row_valid",
)
MASKS: Final = (
    "written_mask", "control_fire_mask", "side_valid_mask", "side_ready_mask", "stall_reason_mask",
)


@dataclass(frozen=True, slots=True)
class MeshComparison:
    work_id: int
    first_cycle: int
    last_cycle: int
    cycles: int
    tag_peak: int
    row_peak: int
    full_cycles: int
    full_stall_cycles: int
    first_full_cycle: int | None
    rtl_sha256: str
    model_sha256: str


def _record(line: bytes, side: str, ordinal: int, work_id: int, cycle: int) -> Record:
    location = f"{side} mesh work={work_id} cycle={cycle}"
    if not line.endswith(b"\n") or len(line) > 4096:
        raise AbsoluteOfferError(f"{location} malformed line")
    try:
        row = object_value(json.loads(line, object_pairs_hook=unique_pairs))
        if set(row) != set(FIELDS) | {"owner_source", "pointer_source"}:
            raise ValueError("field membership differs")
        for key in FIELDS:
            if type(row[key]) is not int or not 0 <= integer(row, key) < 1 << 64:
                raise ValueError(f"{key} is not uint64")
        if (row["mesh_schema"], row["generation"], row["cycle"], row["ordinal"], row["work_id"]) != (
                1, 1, cycle, ordinal, work_id):
            raise ValueError("schema/generation/epoch/identity differs")
        expected_sources = (("active-manifest-attribution", "physical-register") if side == "RTL"
                            else ("native-origin", "logical-fifo-accounting"))
        if (row["owner_source"], row["pointer_source"]) != expected_sources:
            raise ValueError("observation provenance differs")
        if any(integer(row, key) > 1 for key in BITS) or any(integer(row, key) > 7 for key in MASKS):
            raise ValueError("bit or mask outside domain")
        if integer(row, "matmul_id") >= 5:
            raise ValueError("matmul ID outside domain")
        for prefix in ("tag", "row"):
            count, head, tail = (integer(row, f"{prefix}_{key}") for key in ("count", "read", "write"))
            if count > 6 or head >= 6 or tail >= 6 or (head + count) % 6 != tail:
                raise ValueError(f"{prefix} capacity/pointer invariant differs")
            if integer(row, f"{prefix}_valid") != int(count != 0):
                raise ValueError(f"{prefix} valid differs")
        mask = integer(row, "stall_reason_mask")
        if bool(mask & 2) != (row["tag_count"] == 6) or bool(mask & 4) != (row["row_count"] == 6):
            raise ValueError("full queue reason differs")
        last_input = (row["request_valid"] == 1 and row["written_mask"] == 7 and
                      integer(row, "request_counter") + 1 == row["request_rows"])
        if bool(mask & 1) != (row["request_valid"] == 1 and not last_input):
            raise ValueError("resident request reason differs")
        if row["request_ready"] != int(mask == 0):
            raise ValueError("ready differs from old-state blockers")
        if row["request_fire"] and not (row["attempt_valid"] and row["request_ready"]):
            raise ValueError("request fire without valid/ready")
        owner = (row["owner_generation"], row["owner_ordinal"], row["owner_work"])
        expected_owner = (1, ordinal + 1, work_id) if row["request_valid"] else (0, 0, 0)
        if owner != expected_owner:
            raise ValueError("mesh ownership differs")
    except (KeyError, TypeError, ValueError) as error:
        raise AbsoluteOfferError(f"{location}: {error}") from error
    return row


def compare_mesh_states(rtl_path: Path, model_path: Path, *, ordinal: int, work_id: int,
                        first_cycle: int, last_cycle: int) -> MeshComparison:
    """Compare every pre-edge mesh observation, including the unprocessed ready edge."""
    if not 0 <= first_cycle <= last_cycle or ordinal < 0 or work_id < 0:
        raise AbsoluteOfferError("mesh comparison bounds invalid")
    rtl_digest, model_digest = hashlib.sha256(), hashlib.sha256()
    tag_peak = row_peak = full_cycles = full_stalls = 0
    first_full = None
    with (
        (gzip.open(rtl_path, "rb") if rtl_path.suffix == ".gz" else rtl_path.open("rb")) as rtl_stream,
        (gzip.open(model_path, "rb") if model_path.suffix == ".gz" else model_path.open("rb")) as model_stream,
    ):
        for cycle in range(first_cycle, last_cycle + 1):
            left, right = rtl_stream.readline(4097), model_stream.readline(4097)
            rtl_digest.update(left)
            model_digest.update(right)
            rtl = _record(left, "RTL", ordinal, work_id, cycle)
            model = _record(right, "MODEL", ordinal, work_id, cycle)
            for field in FIELDS:
                if rtl[field] != model[field]:
                    raise AbsoluteOfferError(
                        f"first mesh divergence cycle={cycle} field={field}: "
                        f"rtl={rtl[field]} model={model[field]}")
            tag_peak = max(tag_peak, integer(rtl, "tag_count"))
            row_peak = max(row_peak, integer(rtl, "row_count"))
            if rtl["tag_count"] == 6:
                full_cycles += 1
                if first_full is None:
                    first_full = cycle
            operands_ready = all(
                not integer(rtl, "control_fire_mask") & (1 << side) or
                not integer(rtl, "side_ready_mask") & (1 << side) or
                integer(rtl, "side_valid_mask") & (1 << side)
                for side in range(3))
            full_stalls += int(rtl["stall_reason_mask"] == 2 and rtl["control_first"] == 1 and
                               rtl["attempt_valid"] == 1 and operands_ready)
        if rtl_stream.read(1) or model_stream.read(1):
            raise AbsoluteOfferError("mesh observation extends beyond declared resource edge")
    return MeshComparison(work_id, first_cycle, last_cycle, last_cycle - first_cycle + 1,
                          tag_peak, row_peak, full_cycles, full_stalls, first_full,
                          rtl_digest.hexdigest(), model_digest.hexdigest())
