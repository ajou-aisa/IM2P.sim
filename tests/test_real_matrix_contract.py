"""Regression tests for deterministic real-matrix cache and log contracts."""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FINGERPRINT = ROOT / "scripts/real_matrix_fingerprint.py"
VALIDATOR = ROOT / "scripts/validate_real_matrix_log.py"


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["python3", *args],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )


def fingerprint(bits: int, dim: int, extra: Path) -> str:
    result = run(
        str(FINGERPRINT),
        "--bits",
        str(bits),
        "--dim",
        str(dim),
        "--gemmini-root",
        str(ROOT.parent / "llama.cpp-gemmini"),
        "--params-root",
        str(ROOT.parent / "RISC-V-DynDNN-gemmini-include/include"),
        "--extra-input",
        str(extra),
    )
    if result.returncode != 0:
        raise AssertionError(result.stdout)
    value = result.stdout.strip()
    assert len(value) == 64 and all(char in "0123456789abcdef" for char in value)
    return value


def execution_line(bits: int, dim: int, mode: str) -> str:
    route = "q8_h1" if bits == 8 else "q8_h0"
    stripes = 3 if mode == "stripe" else 0
    return (
        f"REAL_EXECUTION bits={bits} dim={dim} route={route} mode={mode} PASS "
        f"M=1 N=1 K=2 stripes={stripes} activation_reads=1 weight_reads=1 "
        f"output_writes=1 completed={stripes} published={stripes}\n"
    )


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="im2p-real-matrix-contract-") as tmp:
        temp = Path(tmp)
        fixture = temp / "fingerprint-input.txt"
        fixture.write_text("alpha\n", encoding="utf-8")
        baseline = fingerprint(4, 16, fixture)
        os.utime(fixture, None)
        assert fingerprint(4, 16, fixture) == baseline, "mtime changed fingerprint"
        fixture.write_text("beta\n", encoding="utf-8")
        assert fingerprint(4, 16, fixture) != baseline, "content change was ignored"
        assert fingerprint(8, 16, fixture) != fingerprint(4, 16, fixture)
        assert fingerprint(4, 32, fixture) != fingerprint(4, 16, fixture)
        assert fingerprint(4, 64, fixture) != fingerprint(4, 32, fixture)

        expected = [
            execution_line(8, dim, mode)
            for dim in (16, 32, 64)
            for mode in ("full", "stripe")
        ]
        valid = temp / "valid.log"
        valid.write_text("".join(expected), encoding="utf-8")
        assert run(str(VALIDATOR), str(valid)).returncode == 0

        misleading = expected.copy()
        misleading[0] = misleading[0].replace("activation_reads=1", "activation_reads=0")
        wrong_route = expected.copy()
        wrong_route[0] = wrong_route[0].replace("route=q8_h1", "route=q8_h0")
        malformed_logs = {
            "duplicate.log": expected + [expected[0]],
            "missing.log": expected[:-1],
            "extra.log": expected + [execution_line(4, 64, "full")],
            "malformed.log": expected + ["REAL_EXECUTION malformed\n"],
            "misleading.log": misleading,
            "wrong-route.log": wrong_route,
        }
        for name, lines in malformed_logs.items():
            path = temp / name
            path.write_text("".join(lines), encoding="utf-8")
            result = run(str(VALIDATOR), str(path))
            assert result.returncode != 0, f"{name} was accepted"

    print("REAL MATRIX CONTRACT PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
