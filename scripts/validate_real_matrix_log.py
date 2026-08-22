"""Require an exact set of real frontend execution identities in a log."""

from __future__ import annotations

import argparse
import collections
import re
from pathlib import Path

EXECUTION_RE = re.compile(
    r"^REAL_EXECUTION bits=(\d+) dim=(\d+) mode=(\S+) PASS(?:\s|$)"
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("log", type=Path)
    parser.add_argument("--bits", type=int, choices=(4, 8, 16))
    parser.add_argument("--dim", type=int, choices=(16, 32))
    args = parser.parse_args()
    if (args.bits is None) != (args.dim is None):
        parser.error("--bits and --dim must be provided together")

    if args.bits is None:
        expected = {
            (bits, dim, mode)
            for bits in (4, 8, 16)
            for dim in (16, 32)
            for mode in ("full", "stripe")
        }
    else:
        expected = {(args.bits, args.dim, mode) for mode in ("full", "stripe")}

    observed: collections.Counter[tuple[int, int, str]] = collections.Counter()
    malformed: list[str] = []
    for line_number, line in enumerate(
        args.log.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.startswith("REAL_EXECUTION"):
            continue
        match = EXECUTION_RE.match(line)
        if match is None:
            malformed.append(f"line {line_number}: {line}")
            continue
        observed[(int(match.group(1)), int(match.group(2)), match.group(3))] += 1

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

    print("REAL MATRIX LOG PASS identities=" + str(len(expected)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
