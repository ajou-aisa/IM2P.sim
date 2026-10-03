"""Compile and verify the value-free planner without any RTL/Verilator dependency."""
from __future__ import annotations

import os
from pathlib import Path
import shlex
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def test_schedule_and_packing() -> None:
    with tempfile.TemporaryDirectory(prefix="im2p-pure-schedule-") as directory:
        binary = Path(directory) / "schedule-test"
        command = [*shlex.split(os.environ.get("CXX", "c++")), "-std=c++17",
                   "-Wall", "-Wextra", "-Werror", "-I", str(ROOT / "sim/common"),
                   str(ROOT / "sim/common/gemmini_schedule.cpp"),
                   str(ROOT / "tests/gemmini_schedule_test.cpp"), "-o", str(binary)]
        subprocess.run(command, check=True)
        result = subprocess.run([str(binary)], check=True, text=True, capture_output=True)
        assert result.stdout.count("PASS") == 14, result.stdout
        print(result.stdout, end="")


if __name__ == "__main__":
    test_schedule_and_packing()
