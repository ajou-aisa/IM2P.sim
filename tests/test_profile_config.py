#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
# Run: uv run tests/test_profile_config.py
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GENERATOR = ROOT / "scripts/im2p_config.py"


def main() -> None:
    assert GENERATOR.is_file(), "production configuration generator is missing"
    subprocess.run([sys.executable, str(GENERATOR), "--help"], check=True, capture_output=True)
    subprocess.run([sys.executable, str(GENERATOR), "--check"], check=True)
    for precision, width, rows in ((4, 32, (1024, 512, 256)), (8, 32, (1024, 512, 256)), (16, 64, (512, 256, 128))):
        for dim, expected_rows in zip((16, 32, 64), rows):
            args = [str(precision), str(precision), str(dim)]
            result = subprocess.run([sys.executable, str(GENERATOR), "--profile", *args], check=True, text=True, capture_output=True)
            profile = json.loads(result.stdout)
            assert profile["partial_bits"] == width
            assert profile["accumulator_bits"] == width
            assert profile["accumulator_rows"] == expected_rows
            assert profile["row_address_bits"] == (expected_rows - 1).bit_length()
            assert profile["row_count_bits"] == dim.bit_length()
            assert expected_rows * dim * width == 65536 * 8
            assert expected_rows >= dim
            assert profile["memory_backend"] == "column-banked-sync-bram"
            assert profile["memory_read_latency"] == 1
            source = f'''#include "{ROOT / "sim/ffi/im2p_config.h"}"
static_assert(IM2P_ACCUMULATOR_BITS == {width});
static_assert(IM2P_PARTIAL_BITS == {width});
static_assert(IM2P_ACCUMULATOR_ROWS == {expected_rows});
static_assert(IM2P_ROW_ADDRESS_BITS == {(expected_rows - 1).bit_length()});
static_assert(IM2P_ROW_COUNT_BITS == {dim.bit_length()});
static_assert(IM2P_ACCUMULATOR_ROWS * IM2P_DIM * IM2P_ACCUMULATOR_BITS == 65536 * 8);
'''
            subprocess.run([os.environ.get("CXX", "c++"), "-std=c++17", "-fsyntax-only", "-x", "c++", "-", f"-DIM2P_ACTIVATION_BITS={precision}", f"-DIM2P_WEIGHT_BITS={precision}", f"-DIM2P_DIM={dim}"], input=source, text=True, check=True)
            rust = subprocess.run([sys.executable, str(GENERATOR), "--rust", *args], check=True, text=True, capture_output=True).stdout
            assert f"pub const IM2P_ACCUMULATOR_ROWS: usize = {expected_rows};" in rust
    for args in ((4, 8, 16), (8, 8, 8), (32, 32, 16)):
        result = subprocess.run([sys.executable, str(GENERATOR), "--profile", *map(str, args)], text=True, capture_output=True)
        assert result.returncode != 0, f"unsupported profile accepted: {args}"
    with tempfile.TemporaryDirectory(prefix="im2p-profile-") as directory:
        copied_root = Path(directory)
        for relative in ("scripts/im2p_config.py", "config/im2p_profiles.json", "src/common/Config.bsv", "sim/ffi/im2p_config.h"):
            destination = copied_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, destination)
        generated = copied_root / "src/common/Config.bsv"
        generated.write_text(generated.read_text().replace("typedef 32 A8AccumulatorWidth;", "typedef 64 A8AccumulatorWidth;"))
        for arguments in (("--check",), ("--profile", "8", "8", "16")):
            result = subprocess.run([sys.executable, str(copied_root / "scripts/im2p_config.py"), *arguments], text=True, capture_output=True)
            assert result.returncode != 0 and "stale generated configuration" in result.stderr
    print("PROFILE CONFIG: PASS (nine profiles, C++ header, Rust constants, stale/mismatch rejection)")


if __name__ == "__main__":
    main()
