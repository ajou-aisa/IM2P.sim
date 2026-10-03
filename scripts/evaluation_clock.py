#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
# How to run: python3 -B scripts/evaluation_clock.py --help
from __future__ import annotations

import argparse
from fractions import Fraction
import json
from pathlib import Path
import platform
import shutil
import sys
from typing import Final

if __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.evaluation_clock_contract import (
    ClockError, ClockSelection, Json, Record, file_ref,
    load_observation, read_record, record, require, text,
)
from scripts.gemmini_board import BoardError, load_board_manifest

ROOT: Final = Path(__file__).resolve().parents[1]
IDENTITY: Final = ("profile", "top", "top_scope", "source", "hardware_contract_sha256", "tool",
                   "technology", "memory_implementation", "memory_interface", "timing_stage",
                   "array_count", "independent_macs_per_pe_per_cycle", "peak_basis", "execution_kind")


def select_clock(paths: list[Path], target: str | None, basis: str | None) -> Record:
    require(bool(paths), "nonempty finite observation set required")
    observations = [load_observation(path) for path in paths]
    first = observations[0].data
    require(all(all(item.data[key] == first[key] for key in IDENTITY) for item in observations),
            "mixed source/profile/top/tool/technology/memory/peak/stage identity")
    frequencies = sorted(item.frequency_hz for item in observations)
    require(len(frequencies) == len(set(frequencies)), "duplicate tested frequency")
    expected_target = None
    if target is not None:
        try:
            expected_target = Fraction(target)
        except (ValueError, ZeroDivisionError) as error:
            raise ClockError("target peak must be finite decimal or rational") from error
        require(expected_target > 0 and bool(basis and basis.strip()), "positive target and explicit GPU basis required")
    valid = [item for item in observations if not item.rejection]
    output: Record = {
        "schema": "im2p-operating-clock", "version": 1, "status": "NOT_READY",
        "profile": first["profile"], "selected_frequency_hz": None, "selected": None,
        "selection_reason": "MISSING_GPU_TARGET" if expected_target is None else "NO_PASSING_CLOCK",
        "target_peak_tops": target, "target_basis": basis, "achieved_peak_tops": None, "gap_tops": None,
        "peak_convention": "2 * array_count * DIM^2 * independent_macs_per_pe_per_cycle * frequency_hz / 10^12",
        "tested_frequency_hz": list(frequencies),
        "tested_bounds_hz": [min(frequencies), max(frequencies)],
        "search_steps_hz": [b - a for a, b in zip(frequencies, frequencies[1:])],
        "search_scope": "FINITE_TESTED_SET_NOT_PHYSICAL_FMAX",
        "observations": [item.evidence for item in observations],
        "rejected": [{"frequency_hz": item.frequency_hz, "reasons": list(item.rejection)}
                     for item in observations if item.rejection],
    }
    if not valid or expected_target is None:
        return output
    reachable = any(item.peak_tops >= expected_target for item in valid)
    selected = (min(valid, key=lambda item: (abs(item.peak_tops - expected_target), item.frequency_hz))
                if reachable else max(valid, key=lambda item: item.frequency_hz))
    output.update({
        "status": "DIAGNOSTIC_ONLY" if first["execution_kind"] == "TOOL_EXECUTION" else "SYNTHETIC_ONLY",
        "selected_frequency_hz": selected.frequency_hz, "selected": selected.data,
        "selection_reason": "NEAREST_VERIFIED_GPU_TARGET" if reachable else "HIGHEST_TESTED_PASSING",
        "achieved_peak_tops": str(selected.peak_tops), "gap_tops": str(selected.peak_tops - expected_target),
    })
    return output


def load_selection(path: Path, profile: str) -> ClockSelection:
    from scripts.evaluation_ooc_report import load_operating_clock
    return load_operating_clock(path, profile)


def preflight(board: Path | None, target: str | None, basis: str | None) -> Record:
    catalog = read_record(ROOT / "config/gemmini_hp1_profiles.json")
    rows = catalog["profiles"]
    require(isinstance(rows, list), "profile catalog array required")
    if not isinstance(rows, list):
        raise ClockError("profile catalog array required")
    gaps: list[Json] = []
    resolved: Record | None = None
    if board is None:
        gaps.append("resolved_board_and_constraints")
    else:
        parsed = load_board_manifest(board)
        resolved = {"part": parsed.part, "top": parsed.top, "memory_interface": parsed.memory_interface,
                    "clock_mhz": parsed.clock_mhz, "manifest": file_ref(board),
                    "constraints": [file_ref(path) for path in parsed.xdc]}
    if target is None or not basis:
        gaps.append("gpu_target_peak_and_dense_sparse_basis")
    if target is not None:
        try:
            require(Fraction(target) > 0, "target peak must be positive")
        except (ValueError, ZeroDivisionError) as error:
            raise ClockError("invalid target peak") from error
    tool = shutil.which("vivado")
    if tool is None:
        gaps.append("vivado_executable")
    if platform.system() != "Linux":
        gaps.append("linux_vivado_execution_host")
    return {
        "schema": "im2p-clock-preflight", "version": 1,
        "status": "NOT_READY" if gaps else "CONFIGURED_NOT_RUN",
        "profiles": [text(record(row), "profile") for row in rows],
        "configuration_gaps": gaps, "board": resolved, "vivado": tool,
        "target_peak_tops": target, "target_basis": basis,
        "profile_catalog": file_ref(ROOT / "config/gemmini_hp1_profiles.json"),
        "production_top_source": file_ref(ROOT / "src/gemmini/control/src/main/scala/im2p/gemmini/UpstreamWsHp1Top.scala"),
        "official_builder": "scripts/gemmini_build.py --top integrated --stage synth|route",
        "synthesis": "NOT_RUN", "selected_frequency_hz": None, "physical_programming": "NOT_RUN",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Finite, artifact-bound PoTal operating-clock selection")
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("preflight", help="Report actual tool/target gaps without synthesis")
    check.add_argument("--board", type=Path)
    select = commands.add_parser("select", help="Select only among independently observed passing clocks")
    select.add_argument("--observations", type=Path, nargs="+", required=True)
    for command in (check, select):
        command.add_argument("--target-peak-tops")
        command.add_argument("--target-basis")
        command.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        require(not args.out.exists(), "fresh output required")
        if args.command == "preflight":
            result = preflight(args.board, args.target_peak_tops, args.target_basis)
        else:
            result = select_clock(args.observations, args.target_peak_tops, args.target_basis)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n")
        print(json.dumps({"status": result["status"], "output": str(args.out)}))
        return 0 if result["status"] in ("PASS", "CONFIGURED_NOT_RUN") else 2
    except (ClockError, BoardError, OSError, json.JSONDecodeError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
