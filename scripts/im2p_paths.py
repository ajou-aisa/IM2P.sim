#!/usr/bin/env python3
"""Portable repository-relative path resolution for IM2P Gemmini dependencies."""

from __future__ import annotations

import argparse
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Final

ROOT: Final = Path(__file__).resolve().parents[1]
CHIPYARD_RELATIVE: Final = Path("deps/chipyard-1.13.0")
LEGACY_RELATIVE: Final = Path("aisa-lab/build/im2p-gemmini")


def resolve_gemmini_work_root(
    root: Path = ROOT,
    environment: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """Resolve one work root without encoding a user's workspace location.

    Precedence is explicit override, repository-local dependency checkout, then
    the historical external checkout when it actually exists. Fresh clones
    default to the repository root so all generated paths remain relocatable.
    """
    env = os.environ if environment is None else environment
    override = env.get("IM2P_GEMMINI_WORK_ROOT")
    if override:
        return Path(override).expanduser().resolve()

    repository = root.expanduser().resolve()
    if (repository / CHIPYARD_RELATIVE).is_dir():
        return repository

    legacy = (Path.home() if home is None else home).expanduser() / LEGACY_RELATIVE
    if (legacy / CHIPYARD_RELATIVE).is_dir():
        return legacy.resolve()

    return repository


def chipyard_root(
    root: Path = ROOT,
    environment: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    return resolve_gemmini_work_root(root, environment, home) / CHIPYARD_RELATIVE


def main(arguments: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gemmini-work-root", action="store_true")
    parser.add_argument("--chipyard-root", action="store_true")
    args = parser.parse_args(arguments)
    if args.gemmini_work_root == args.chipyard_root:
        parser.error("choose exactly one path to print")
    print(resolve_gemmini_work_root() if args.gemmini_work_root else chipyard_root())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
