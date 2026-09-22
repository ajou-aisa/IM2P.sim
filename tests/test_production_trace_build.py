#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

# ─── How to run ───
# uv run tests/test_production_trace_build.py <host-build/compile_commands.json>
# ─────────────────

from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path


def main() -> int:
    # Given: the exact host frontend compile command against a source without optrace.
    compile_commands = Path(sys.argv[1])
    entries = json.loads(compile_commands.read_text(encoding="utf-8"))
    entry = next(item for item in entries if item["file"].endswith("/im2p_gemmini_frontend.cpp"))
    command = shlex.split(entry["command"])
    output_index = command.index("-o")
    del command[output_index : output_index + 2]
    command.remove("-c")
    command = [arg for arg in command if not arg.startswith("-DIM2P_PRODUCTION_TRACE_ENABLED=")]

    # When: the official host mode preprocesses the complete frontend.
    off = subprocess.run(
        [*command, "-DIM2P_PRODUCTION_TRACE_ENABLED=0", "-E", "-P"],
        cwd=entry["directory"], text=True, capture_output=True, check=False,
    )

    # Then: no production optrace type, member, session, or header survives.
    assert off.returncode == 0, off.stderr[-3000:]
    assert "optrace" not in off.stdout
    assert "trace_context" not in off.stdout
    assert "production_trace" not in off.stdout
    off_compile = subprocess.run(
        [*command, "-DIM2P_PRODUCTION_TRACE_ENABLED=0", "-fsyntax-only"],
        cwd=entry["directory"], text=True, capture_output=True, check=False,
    )
    assert off_compile.returncode == 0, off_compile.stderr[-3000:]

    # When: tracing is requested against that same missing producer API.
    on = subprocess.run(
        [*command, "-DIM2P_PRODUCTION_TRACE_ENABLED=1", "-fsyntax-only"],
        cwd=entry["directory"], text=True, capture_output=True, check=False,
    )

    # Then: compilation rejects the missing real header rather than linking a shim.
    assert on.returncode != 0
    assert "gemmini/optrace.hpp" in on.stderr, on.stderr[-3000:]
    print("PRODUCTION TRACE BUILD BOUNDARY PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
