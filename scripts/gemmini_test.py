#!/usr/bin/env python3

from __future__ import annotations

import sys
from typing import Sequence

from gemmini_build import main as build_main


def main(arguments: Sequence[str] | None = None) -> int:
    selected = list(sys.argv[1:] if arguments is None else arguments)
    if any(argument == "--stage" or argument.startswith("--stage=") for argument in selected):
        raise SystemExit("gemmini_test.py selects --stage test")
    return build_main([*selected, "--stage", "test"])


if __name__ == "__main__":
    raise SystemExit(main())
