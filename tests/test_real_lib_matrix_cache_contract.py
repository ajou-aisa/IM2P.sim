#!/usr/bin/env python3
"""Aggregate scheduling and nine-hit contracts for real-library caches."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MATRIX = ROOT / "scripts/real_lib_matrix.py"


def run(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *args], cwd=ROOT, env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
    )


def main() -> int:
    identities = json.loads(run(str(MATRIX), "--print-identities").stdout)
    expected = [
        f"a{bits}-w{bits}-d{dim}"
        for bits in (4, 8, 16)
        for dim in (16, 32, 64)
    ]
    assert identities == expected and len(set(identities)) == 9

    with tempfile.TemporaryDirectory(prefix="im2p-real-lib-matrix-") as raw:
        temp = Path(raw)
        build = temp / "build"
        invocation_log = temp / "invocations.jsonl"
        fake_make = temp / "make.py"
        fake_make.write_text(
            """#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
values = dict(arg.split('=', 1) for arg in sys.argv[1:] if '=' in arg)
identity = f"a{values['IM2P_ACTIVATION_BITS']}-w{values['IM2P_WEIGHT_BITS']}-d{values['IM2P_DIM']}"
build = Path(values['BUILD_DIR'])
selected = build / 'selected' / identity
generation = selected / 'generations' / identity
generation.mkdir(parents=True, exist_ok=True)
manifest = generation / 'real-lib.json'
manifest.write_text(json.dumps({'fingerprint': identity, 'artifacts': []}))
current = selected / 'current'
if not current.exists():
    current.symlink_to(Path('generations') / identity, target_is_directory=True)
row = json.dumps({'id': identity, 'args': sys.argv[1:]}) + '\\n'
fd = os.open(os.environ['IM2P_MATRIX_INVOCATIONS'], os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
os.write(fd, row.encode()); os.close(fd)
builds = Path(os.environ['IM2P_MATRIX_BUILDS'])
observed = builds.read_text().splitlines() if builds.exists() else []
if identity not in observed:
    fd = os.open(builds, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    os.write(fd, (identity + '\\n').encode()); os.close(fd)
    print(f'IM2P_REAL_LIB_CACHE id={identity} state=rebuild')
else:
    print(f'IM2P_REAL_LIB_CACHE id={identity} state=hit')
""",
            encoding="utf-8",
        )
        fake_make.chmod(0o755)
        matrix_env = os.environ.copy()
        matrix_env.update({
            "MAKE": str(fake_make),
            "MAKEFLAGS": "-j99 --jobserver-auth=fake",
            "IM2P_MATRIX_INVOCATIONS": str(invocation_log),
            "IM2P_MATRIX_BUILDS": str(temp / "builds"),
        })
        bsc_verilog = temp / "bsc-verilog"
        bsc_verilog.mkdir()
        matrix_args = (
            str(MATRIX), "--build-dir", str(build), "--jobs", "3",
            "--bsc-verilog", str(bsc_verilog),
            "--bsc-extra-flags", "-steps 123",
        )
        matrix = run(*matrix_args, env=matrix_env)
        assert matrix.returncode == 0, matrix.stdout
        summary = json.loads(
            (build / "manifests/real-lib-all.json").read_text()
        )
        assert summary["ok"] and summary["jobs"] == 3
        assert [row["id"] for row in summary["artifacts"]] == sorted(expected)
        invocations = [
            json.loads(line) for line in invocation_log.read_text().splitlines()
        ]
        assert sorted(row["id"] for row in invocations) == sorted(expected)
        assert all("-j1" in row["args"] for row in invocations)
        assert all("-j99" not in row["args"] for row in invocations)

        matrix_hit = run(*matrix_args, env=matrix_env)
        assert matrix_hit.returncode == 0, matrix_hit.stdout
        assert matrix_hit.stdout.count("state=hit") == 9
        assert len((temp / "builds").read_text().splitlines()) == 9

    with tempfile.TemporaryDirectory(prefix="im2p-real-lib-dry-parent-") as parent:
        dry_root = Path(parent) / "absent"
        for target in ("gemmini-frontend-real-lib", "gemmini-frontend-real-lib-all"):
            dry = subprocess.run(
                ["make", "--no-print-directory", "-n", target,
                 f"BUILD_DIR={dry_root}"], cwd=ROOT, text=True,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
            )
            assert dry.returncode == 0, dry.stdout
            assert not dry_root.exists(), f"make -n {target} created the build root"

    print("REAL LIB MATRIX CACHE CONTRACT PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
