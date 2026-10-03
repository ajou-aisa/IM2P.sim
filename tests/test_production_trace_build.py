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
import shutil
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> int:
    # Given: the exact host frontend compile command and its selected producer API.
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

    # Then: no production emission or session access survives; shared ABI forwards are allowed.
    assert off.returncode == 0, off.stderr[-3000:]
    for access in ("source->optrace_context", "trace_context->session", "production_trace::"):
        assert access not in off.stdout, access
    off_compile = subprocess.run(
        [*command, "-DIM2P_PRODUCTION_TRACE_ENABLED=0", "-fsyntax-only"],
        cwd=entry["directory"], text=True, capture_output=True, check=False,
    )
    assert off_compile.returncode == 0, off_compile.stderr[-3000:]

    # When: tracing is requested against the same producer snapshot.
    on = subprocess.run(
        [*command, "-DIM2P_PRODUCTION_TRACE_ENABLED=1", "-E", "-P"],
        cwd=entry["directory"], text=True, capture_output=True, check=False,
    )

    # Then: a missing API rejects; an available API restores real dispatch capture.
    if on.returncode:
        assert "gemmini/optrace.hpp" in on.stderr, on.stderr[-3000:]
    else:
        assert "trace_context->session->accepted" in on.stdout
        assert "production_trace::stripe" in on.stdout
        on_compile = subprocess.run(
            [*command, "-DIM2P_PRODUCTION_TRACE_ENABLED=1", "-fsyntax-only"],
            cwd=entry["directory"], text=True, capture_output=True, check=False,
        )
        assert on_compile.returncode == 0, on_compile.stderr[-3000:]
        include = next(arg for arg in command if arg.startswith("-I")
                       and arg.endswith("/ggml-gemmini-utils/include"))
        with tempfile.TemporaryDirectory(prefix="im2p-trace-header-reject-") as temporary:
            copied = Path(temporary) / "include"
            shutil.copytree(Path(include[2:]), copied, ignore=shutil.ignore_patterns("optrace.hpp"))
            missing = subprocess.run(
                [*("-I" + str(copied) if arg == include else arg for arg in command),
                 "-DIM2P_PRODUCTION_TRACE_ENABLED=1", "-fsyntax-only"],
                cwd=entry["directory"], text=True, capture_output=True, check=False,
            )
            assert missing.returncode != 0
            assert "gemmini/optrace.hpp" in missing.stderr, missing.stderr[-3000:]
    print("PRODUCTION TRACE BUILD BOUNDARY PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
