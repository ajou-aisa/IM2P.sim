#!/usr/bin/env python3
"""Resolve the Gemmini dependency root without a user-specific default path."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final

ROOT: Final = Path(__file__).resolve().parents[1]
CHIPYARD: Final = Path("deps/chipyard-1.13.0")
LEGACY_ROOT: Final = Path.home() / "aisa-lab/build/im2p-gemmini"


def resolve_gemmini_work_root(root: Path = ROOT) -> Path:
    override = os.environ.get("IM2P_GEMMINI_WORK_ROOT")
    if override:
        return Path(override).expanduser().resolve()

    repository = root.expanduser().resolve()
    if (repository / CHIPYARD).is_dir():
        return repository
    if (LEGACY_ROOT / CHIPYARD).is_dir():
        return LEGACY_ROOT.resolve()
    return repository


if __name__ == "__main__":
    print(resolve_gemmini_work_root())
