"""Bound clock observations, separate from value-free NPU timing."""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import re
from typing import Final, TypeAlias

Json: TypeAlias = str | int | float | bool | None | list["Json"] | dict[str, "Json"]
Record: TypeAlias = dict[str, Json]
FIELDS: Final = frozenset((
    "schema", "version", "execution_kind", "profile", "top", "top_scope", "source",
    "netlist", "constraints", "tool_report", "hardware_contract_sha256", "tool",
    "technology", "memory_implementation", "memory_interface", "timing_stage",
    "frequency_hz", "array_count", "independent_macs_per_pe_per_cycle", "peak_basis",
    "tool_exit_code", "setup_slack_ns", "hold_slack_ns", "timed_paths",
    "unconstrained_paths", "fit", "resource_usage", "resource_limits",
))
MEASUREMENTS: Final = ("top", "frequency_hz", "tool_exit_code", "setup_slack_ns", "hold_slack_ns",
                      "timed_paths", "unconstrained_paths", "fit", "resource_usage", "resource_limits")


class ClockError(ValueError):
    def __init__(self, detail: str) -> None:
        super().__init__("evaluation-clock: " + detail)


def require(condition: bool, detail: str) -> None:
    if not condition:
        raise ClockError(detail)


def record(value: Json) -> Record:
    if not isinstance(value, dict):
        raise ClockError("JSON object required")
    return value


def unique_pairs(pairs: list[tuple[str, Json]]) -> Record:
    result: Record = {}
    for key, value in pairs:
        require(key not in result, "duplicate JSON key: " + key)
        result[key] = value
    return result


def read_record(path: Path) -> Record:
    return record(json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_pairs))


def text(row: Record, key: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ClockError(key + ": nonempty string required")
    return value


def integer(row: Record, key: str, minimum: int = 0) -> int:
    value = row.get(key)
    if type(value) is not int or value < minimum:
        raise ClockError(key + ": integer outside domain")
    return value


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_ref(path: Path) -> Record:
    require(path.is_file() and not path.is_symlink(), "regular artifact required: " + str(path))
    return {"path": str(path.resolve()), "sha256": sha256(path)}


def bound_file(value: Json, base: Path) -> Record:
    ref = record(value)
    require(set(ref) == {"path", "sha256"}, "artifact reference fields")
    path = Path(text(ref, "path"))
    resolved = file_ref(path if path.is_absolute() else base / path)
    require(resolved["sha256"] == text(ref, "sha256"), "artifact digest mismatch: " + str(path))
    return resolved


def rational(row: Record, key: str) -> Fraction:
    try:
        return Fraction(text(row, key))
    except (ValueError, ZeroDivisionError) as error:
        raise ClockError(key + ": finite decimal or rational required") from error


@dataclass(frozen=True, slots=True)
class Observation:
    data: Record
    evidence: Record
    frequency_hz: int
    peak_tops: Fraction
    rejection: tuple[str, ...]


def load_observation(path: Path) -> Observation:
    row = read_record(path)
    require(set(row) == FIELDS, "observation missing/unknown fields")
    require(row["schema"] == "im2p-clock-observation" and type(row["version"]) is int
            and row["version"] == 1, "observation schema/version")
    profile = re.fullmatch(r"a([48])w\1-d(16|32|64)-hp1", text(row, "profile"))
    require(profile is not None, "unsupported profile")
    if profile is None:
        raise ClockError("unsupported profile")
    width, dim = map(int, profile.groups())
    require(row["top_scope"] == "INTEGRATED" and row["top"] == f"IM2PGemminiWSHP1A{width}W{width}D{dim}",
            "complete integrated production top required")
    require(row["execution_kind"] in ("TOOL_EXECUTION", "SYNTHETIC"), "execution kind")
    require(row["timing_stage"] in ("POST_SYNTH_ESTIMATE", "POST_ROUTE"), "timing stage")
    require(re.fullmatch(r"[0-9a-f]{64}", text(row, "hardware_contract_sha256")) is not None,
            "hardware contract hash required")
    tool = record(row["tool"])
    require(set(tool) == {"name", "version"}, "tool identity fields")
    text(tool, "name"); text(tool, "version")
    for key in ("technology", "memory_implementation", "memory_interface"):
        text(row, key)
    for key in ("source", "netlist", "tool_report", "peak_basis"):
        row[key] = bound_file(row[key], path.parent)
    report = read_record(Path(text(record(row["tool_report"]), "path")))
    require(set(report) == {"schema", "version", *MEASUREMENTS}
            and report["schema"] == "im2p-clock-tool-report" and type(report["version"]) is int
            and report["version"] == 1, "tool report schema/fields")
    require(all(report[key] == row[key] for key in MEASUREMENTS), "observation differs from tool report")
    constraints = row["constraints"]
    require(isinstance(constraints, list) and bool(constraints), "constraint artifacts required")
    if not isinstance(constraints, list):
        raise ClockError("constraint artifacts required")
    row["constraints"] = [bound_file(item, path.parent) for item in constraints]
    require(len({text(record(item), "path") for item in row["constraints"]}) == len(constraints),
            "duplicate constraints")
    frequency = integer(row, "frequency_hz", 1)
    arrays = integer(row, "array_count", 1)
    macs = integer(row, "independent_macs_per_pe_per_cycle", 1)
    require(type(row["fit"]) is bool, "fit must be boolean")
    usage, limits = record(row["resource_usage"]), record(row["resource_limits"])
    require(bool(usage) and set(usage) == set(limits), "resource usage/limit coverage required")
    exceeds_fit = any(integer(usage, key) > integer(limits, key, 1) for key in usage)
    reject = tuple(name for name, failed in (
        ("POST_ROUTE_REQUIRED", row["timing_stage"] != "POST_ROUTE"),
        ("TOOL_FAILED", integer(row, "tool_exit_code") != 0),
        ("NEGATIVE_SETUP_SLACK", rational(row, "setup_slack_ns") < 0),
        ("NEGATIVE_HOLD_SLACK", rational(row, "hold_slack_ns") < 0),
        ("NO_TIMED_PATHS", integer(row, "timed_paths") == 0),
        ("UNCONSTRAINED_PATHS", integer(row, "unconstrained_paths") != 0),
        ("NO_FIT", not row["fit"] or exceeds_fit),
    ) if failed)
    return Observation(row, file_ref(path), frequency, Fraction(2 * arrays * dim * dim * macs * frequency, 10**12), reject)


@dataclass(frozen=True, slots=True)
class ClockSelection:
    frequency_hz: int
    profile: str
    stage: str
    sha256: str
    hardware_contract_sha256: str
