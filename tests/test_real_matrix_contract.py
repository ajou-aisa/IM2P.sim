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
        "--weight-bits",
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


def routes(bits: int) -> tuple[str, ...]:
    if bits == 8:
        return ("q8_h1",)
    return (f"q{bits}_h0", f"q{bits}_h1", f"q{bits}_hp1")


def canonical_rows(dim: int, mode: str) -> str:
    if mode == "full":
        return "none"
    fixture_rows = dim + 3
    stripe_rows = (fixture_rows + 2) // 3
    return ",".join(
        str(min(stripe_rows, fixture_rows - row))
        for row in range(0, fixture_rows, stripe_rows)
    )


def execution_line(bits: int, dim: int, route: str, mode: str) -> str:
    stripes = 3 if mode == "stripe" else 0
    return (
        f"REAL_EXECUTION activation_bits={bits} weight_bits={bits} dim={dim} "
        f"route={route} mode={mode} PASS M={dim + 3} N=1 K=2 stripes={stripes} "
        f"activation_reads=1 weight_reads=1 output_writes=1 completed={stripes} "
        f"published={stripes} published_rows={dim + 3 if stripes else 0} "
        f"published_row_sequence={canonical_rows(dim, mode)} "
        "output_works=1 fragments=2\n"
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
            execution_line(bits, dim, route, mode)
            for bits in (4, 8, 16)
            for dim in (16, 32, 64)
            for route in routes(bits)
            for mode in ("full", "stripe")
        ]
        valid = temp / "valid.log"
        valid.write_text("".join(expected), encoding="utf-8")
        assert run(str(VALIDATOR), str(valid)).returncode == 0

        misleading = expected.copy()
        misleading[0] = misleading[0].replace("activation_reads=1", "activation_reads=0")
        wrong_route = expected.copy()
        wrong_route[0] = wrong_route[0].replace("route=q4_h0", "route=q8_h0")
        missing_output_works = expected.copy()
        missing_output_works[0] = missing_output_works[0].replace("output_works=1 ", "")
        duplicate_fragments = expected.copy()
        duplicate_fragments[0] = duplicate_fragments[0].replace(
            "fragments=2", "fragments=2 fragments=3"
        )
        negative_output_works = expected.copy()
        negative_output_works[0] = negative_output_works[0].replace(
            "output_works=1", "output_works=-1"
        )
        missing_rows = expected.copy()
        missing_rows[1] = missing_rows[1].replace(
            f"published_row_sequence={canonical_rows(16, 'stripe')} ", ""
        )
        wrong_rows = expected.copy()
        wrong_rows[1] = wrong_rows[1].replace(
            f"published_row_sequence={canonical_rows(16, 'stripe')}",
            "published_row_sequence=7,6,6",
        )
        wrong_row_sum = expected.copy()
        wrong_row_sum[1] = wrong_row_sum[1].replace(
            "published_rows=19", "published_rows=18"
        )
        malformed_logs = {
            "duplicate.log": expected + [expected[0]],
            "missing.log": expected[:-1],
            "extra.log": expected + [execution_line(4, 64, "q4_h0", "full")],
            "malformed.log": expected + ["REAL_EXECUTION malformed\n"],
            "misleading.log": misleading,
            "wrong-route.log": wrong_route,
            "missing-output-works.log": missing_output_works,
            "duplicate-fragments.log": duplicate_fragments,
            "negative-output-works.log": negative_output_works,
            "missing-rows.log": missing_rows,
            "wrong-rows.log": wrong_rows,
            "wrong-row-sum.log": wrong_row_sum,
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
