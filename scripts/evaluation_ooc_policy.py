from __future__ import annotations

from fractions import Fraction
from pathlib import Path
import re
from typing import Final

from scripts.evaluation_clock_contract import ClockError, Record, read_record, require

ROOT: Final = Path(__file__).resolve().parents[1]
POLICY_PATH: Final = ROOT / "config/evaluation_ooc_policy.json"
PART: Final = "xcu250-figd2104-2L-e"
PROFILES: Final = tuple(f"a{bits}w{bits}-d{dim}-hp1" for bits in (4, 8) for dim in (16, 32, 64))


def policy() -> Record:
    value = read_record(POLICY_PATH)
    require(value["part"] == PART and value["target_peak_tops"] == 33
            and value["array_count"] == value["independent_macs_per_pe_per_cycle"] == 1
            and value["a4_throughput_multiplier"] == 1 and value["timing_stage"] == "POST_ROUTE"
            and value["clock_port"] == "CLK" and value["part_fallback"] is False,
            "confirmed OOC policy changed; explicit policy review required")
    return value


def dimensions(profile: str) -> tuple[int, int]:
    match = re.fullmatch(r"a([48])w\1-d(16|32|64)-hp1", profile)
    if match is None:
        raise ClockError("unsupported OOC profile")
    return int(match[1]), int(match[2])


def target_frequency(profile: str) -> Fraction:
    _, dim = dimensions(profile)
    return Fraction(33 * 10**12, 2 * dim * dim)


def frequencies(values: list[int]) -> tuple[int, ...]:
    require(1 <= len(values) <= 64 and all(type(item) is int and item > 0 for item in values)
            and len(values) == len(set(values)), "finite 1..64 unique positive frequencies required")
    return tuple(sorted(values))
