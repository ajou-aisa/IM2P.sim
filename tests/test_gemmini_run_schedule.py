from __future__ import annotations

import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def test_run_schedule() -> None:
    with tempfile.TemporaryDirectory(prefix="im2p-run-schedule-") as directory:
        binary = Path(directory) / "run-schedule-test"
        command = [*shlex.split(os.environ.get("CXX", "c++")), "-std=c++17",
                   "-Wall", "-Wextra", "-Werror", "-I", str(ROOT / "sim/common"),
                   "-I", str(ROOT / "sim/include"),
                   str(ROOT / "sim/common/gemmini_schedule.cpp"),
                   str(ROOT / "tests/gemmini_run_schedule_test.cpp"), "-o", str(binary)]
        subprocess.run(command, check=True)
        result = subprocess.run([str(binary), *sys.argv[1:]], check=True,
                                text=True, capture_output=True)
        expected = 1 if sys.argv[1:] == ["--invalid-runs"] else 6
        assert result.stdout.count("PASS") == expected, result.stdout
        print(result.stdout, end="")


if __name__ == "__main__":
    test_run_schedule()
