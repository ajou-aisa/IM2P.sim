#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
#
# ─── How to run ───
# 1. Install uv (if not installed):
#      curl -LsSf https://astral.sh/uv/install.sh | sh
# 2. Run directly (no venv, no pip install needed):
#      uv run scripts/gemmini_board.py --manifest BOARD.json
# 3. Or make executable and run:
#      chmod +x scripts/gemmini_board.py && ./scripts/gemmini_board.py --help
# ─────────────────

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import TypeAlias

JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]


@dataclass(frozen=True, slots=True)
class BoardError(Exception):
    message: str

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True, slots=True)
class BoardManifest:
    board_id: str
    part: str
    top: str
    clock_port: str
    clock_mhz: float
    xdc: tuple[Path, ...]
    pin_constraints: Path
    memory_interface: str
    deployment_artifact: str


def nonempty_string(value: JsonValue | None, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise BoardError(f"resolved board {label} is required")
    return value


def confined_xdc(manifest: Path, name: str) -> Path:
    pure = PurePosixPath(name)
    if pure.is_absolute() or ".." in pure.parts or pure.as_posix() != name:
        raise BoardError(f"unsafe XDC path: {name}")
    base = manifest.parent.resolve()
    candidate = manifest.parent.joinpath(*pure.parts)
    if candidate.is_symlink() or not candidate.is_file():
        raise BoardError(f"regular XDC file required: {name}")
    resolved = candidate.resolve()
    if not resolved.is_relative_to(base) or resolved.suffix.lower() != ".xdc":
        raise BoardError(f"XDC must stay beside board manifest: {name}")
    return resolved


def load_board_manifest(path: Path) -> BoardManifest:
    if path.is_symlink() or not path.is_file():
        raise BoardError(f"regular board manifest required: {path}")
    raw: JsonValue = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise BoardError("board manifest schema_version must be 1")
    clock = raw.get("clock")
    if not isinstance(clock, dict):
        raise BoardError("board clock must be an object")
    board_id = nonempty_string(raw.get("board_id"), "board_id")
    part = nonempty_string(raw.get("part"), "part")
    top = nonempty_string(raw.get("top"), "top")
    memory = nonempty_string(raw.get("memory_interface"), "memory_interface")
    artifact = nonempty_string(raw.get("deployment_artifact"), "deployment_artifact")
    clock_port = nonempty_string(clock.get("port"), "clock.port")
    frequency = clock.get("frequency_mhz")
    if not isinstance(frequency, (int, float)) or isinstance(frequency, bool) or frequency <= 0:
        raise BoardError("resolved board clock frequency must be positive")
    raw_names = raw.get("xdc")
    if not isinstance(raw_names, list) or not raw_names:
        raise BoardError("resolved board requires XDC inputs")
    names = [nonempty_string(name, "xdc entry") for name in raw_names]
    if len(names) != len(set(names)):
        raise BoardError("board XDC inputs must be unique")
    pin_name = nonempty_string(raw.get("pin_constraints"), "pin_constraints")
    if pin_name not in names:
        raise BoardError("pin_constraints must name one listed XDC")
    xdc = tuple(confined_xdc(path, name) for name in names)
    if artifact not in {"bit", "xclbin", "pdi"}:
        raise BoardError("unsupported board deployment artifact")
    return BoardManifest(board_id, part, top, clock_port, float(frequency), xdc,
                         xdc[names.index(pin_name)], memory, artifact)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate resolved Gemmini HP1 board inputs")
    parser.add_argument("--manifest", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        board = load_board_manifest(arguments.manifest)
    except (BoardError, OSError, json.JSONDecodeError) as error:
        print(f"GEMMINI_BOARD_INVALID: {error}", file=sys.stderr)
        return 1
    payload = asdict(board)
    payload.update(status="PASS", xdc=[str(path) for path in board.xdc],
                   pin_constraints=str(board.pin_constraints))
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
