"""Require exact matched artifact, route, and mode executions in a real matrix log."""

from __future__ import annotations

import argparse
import collections
import re
from pathlib import Path

LEGACY_EXECUTION_RE = re.compile(
    r"^REAL_EXECUTION bits=(\d+) dim=(\d+) route=(\S+) mode=(\S+) PASS(?:\s|$)"
)
MATCHED_EXECUTION_RE = re.compile(
    r"^REAL_EXECUTION activation_bits=(\d+) weight_bits=(\d+) dim=(\d+) "
    r"route=(\S+) mode=(\S+) PASS(?:\s|$)"
)
STAT_RE = re.compile(r"\b([a-z_]+)=(\d+)\b")
SIGNED_STAT_RE = re.compile(r"\b(output_works|fragments)=(-?\d+)\b")
ROW_SEQUENCE_RE = re.compile(r"\bpublished_row_sequence=(none|\d+(?:,\d+)*)\b")


def expected_routes(bits: int) -> tuple[str, ...]:
    if bits == 8:
        return ("q8_h1",)
    prefix = f"q{bits}"
    return (f"{prefix}_h0", f"{prefix}_h1", f"{prefix}_hp1")


def expected_for_pair(bits: int, dim: int) -> set[tuple[int, int, int, str, str]]:
    return {
        (bits, bits, dim, route, mode)
        for route in expected_routes(bits)
        for mode in ("full", "stripe")
    }


def expected_published_rows(dim: int, mode: str) -> tuple[int, ...]:
    if mode == "full":
        return ()
    fixture_rows = dim + 3
    rows_per_stripe = (fixture_rows + 2) // 3
    return tuple(
        min(rows_per_stripe, fixture_rows - row_begin)
        for row_begin in range(0, fixture_rows, rows_per_stripe)
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("log", type=Path)
    parser.add_argument("--bits", type=int, choices=(4, 8, 16))
    parser.add_argument("--dim", type=int, choices=(16, 32, 64))
    args = parser.parse_args()
    if (args.bits is None) != (args.dim is None):
        parser.error("--bits and --dim must be provided together")

    if args.bits is None:
        expected = set().union(
            *(expected_for_pair(bits, dim) for bits in (4, 8, 16) for dim in (16, 32, 64))
        )
    else:
        expected = expected_for_pair(args.bits, args.dim)

    observed: collections.Counter[tuple[int, int, int, str, str]] = collections.Counter()
    malformed: list[str] = []
    for line_number, line in enumerate(
        args.log.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.startswith("REAL_EXECUTION"):
            continue
        matched = MATCHED_EXECUTION_RE.match(line)
        legacy = LEGACY_EXECUTION_RE.match(line) if matched is None else None
        if matched is not None:
            identity = (
                int(matched.group(1)),
                int(matched.group(2)),
                int(matched.group(3)),
                matched.group(4),
                matched.group(5),
            )
        elif legacy is not None:
            identity = (
                int(legacy.group(1)),
                8,
                int(legacy.group(2)),
                legacy.group(3),
                legacy.group(4),
            )
        else:
            malformed.append(f"line {line_number}: {line}")
            continue
        stats = {name: int(value) for name, value in STAT_RE.findall(line)}
        required = (
            "activation_reads",
            "weight_reads",
            "output_writes",
            "completed",
            "published",
            "published_rows",
        )
        if any(name not in stats for name in required):
            malformed.append(f"line {line_number}: missing stats: {line}")
            continue
        rtl_stats = SIGNED_STAT_RE.findall(line)
        for name in ("output_works", "fragments"):
            values = [int(value) for found_name, value in rtl_stats if found_name == name]
            if len(values) != 1 or values[0] < 0:
                malformed.append(
                    f"line {line_number}: {name} must occur exactly once and be nonnegative: {line}"
                )
                break
        else:
            row_sequences = ROW_SEQUENCE_RE.findall(line)
            if len(row_sequences) != 1:
                malformed.append(
                    f"line {line_number}: published_row_sequence must occur exactly once: {line}"
                )
                continue
            actual_rows = (
                ()
                if row_sequences[0] == "none"
                else tuple(int(value) for value in row_sequences[0].split(","))
            )
            canonical_rows = expected_published_rows(identity[2], identity[4])
            if actual_rows != canonical_rows:
                malformed.append(
                    f"line {line_number}: published rows {actual_rows} != canonical {canonical_rows}: {line}"
                )
                continue
        if malformed and malformed[-1].startswith(f"line {line_number}:"):
            continue
        if any(stats[name] == 0 for name in required[:3]):
            malformed.append(f"line {line_number}: zero provider activity: {line}")
            continue
        if identity[4] == "full" and (
            stats["published"] != 0 or stats["published_rows"] != 0
        ):
            malformed.append(f"line {line_number}: FULL published lifecycle: {line}")
            continue
        if identity[4] == "stripe" and (
            stats["completed"] != 3
            or stats["published"] != 3
            or stats["published_rows"]
            != sum(expected_published_rows(identity[2], identity[4]))
        ):
            malformed.append(f"line {line_number}: wrong stripe stats: {line}")
            continue
        observed[identity] += 1

    duplicates = sorted(identity for identity, count in observed.items() if count != 1)
    actual = set(observed)
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if malformed or duplicates or missing or extra:
        print("REAL MATRIX LOG FAIL")
        if malformed:
            print(f"- malformed={malformed}")
        if duplicates:
            print(f"- duplicate={duplicates}")
        if missing:
            print(f"- missing={missing}")
        if extra:
            print(f"- extra={extra}")
        return 1

    artifact_modes = {(a, w, dim, mode) for a, w, dim, _, mode in actual}
    print(
        "REAL MATRIX LOG PASS routes="
        + str(len(expected))
        + " artifact_modes="
        + str(len(artifact_modes))
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
