#!/usr/bin/env python3
"""Bounded all-nine production real-library cache scheduler."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import TypedDict

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.real_lib_manifest import ArtifactRow, read_manifest_summary

IDENTITIES = tuple((bits, bits, dim) for bits in (4, 8, 16) for dim in (16, 32, 64))


class MatrixRow(TypedDict):
    id: str
    activation_bits: int
    weight_bits: int
    dim: int
    returncode: int
    manifest: str
    fingerprint: str
    manifest_sha256: str
    artifacts: list[ArtifactRow]


def run_one(
    args: argparse.Namespace,
    bits: int,
    weight_bits: int,
    dim: int,
) -> MatrixRow:
    identity = f"a{bits}-w{weight_bits}-d{dim}"
    environment = os.environ.copy()
    environment.pop("MAKEFLAGS", None)
    make = environment.get("MAKE", "make").split()[0]
    command = [
        make, "--no-print-directory", "-j1", f"BUILD_DIR={args.build_dir}",
        f"IM2P_ACTIVATION_BITS={bits}", f"IM2P_WEIGHT_BITS={weight_bits}",
        f"IM2P_DIM={dim}", f"GEMMINI_FRONTEND_ACTIVATION_BITS={bits}",
        f"GEMMINI_FRONTEND_WEIGHT_BITS={weight_bits}", f"GEMMINI_FRONTEND_DIM={dim}",
        f"GEMMINI_FRONTEND_BLOCK_SIZE={args.block_size}",
        f"GEMMINI_ROOT={args.gemmini_root}", f"GEMMINI_PARAMS_ROOT={args.params_root}",
        f"CXX={args.cxx}", f"AR={args.ar}", f"BSC={args.bsc}",
        f"BSC_VERILOG={args.bsc_verilog}",
        f"BSC_EXTRA_FLAGS={args.bsc_extra_flags}",
        f"VERILATOR={args.verilator}",
        f"RUSTC={args.rustc}", f"CARGO={args.cargo}",
        "gemmini-frontend-real-lib",
    ]
    result = subprocess.run(
        command, cwd=ROOT, env=environment, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
    )
    sys.stdout.write(result.stdout)
    manifest = (
        args.build_dir / "selected" / identity / "current" / "real-lib.json"
    )
    row: MatrixRow = {
        "id": identity, "activation_bits": bits, "weight_bits": weight_bits,
        "dim": dim, "returncode": result.returncode, "manifest": str(manifest.resolve()),
        "fingerprint": "", "manifest_sha256": "", "artifacts": [],
    }
    if result.returncode == 0:
        data = manifest.read_bytes()
        fingerprint, artifacts = read_manifest_summary(manifest)
        row.update({
            "fingerprint": fingerprint,
            "manifest_sha256": hashlib.sha256(data).hexdigest(),
            "artifacts": artifacts,
        })
    return row


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--build-dir", type=Path, default=Path("build"))
    parser.add_argument("--block-size", type=int, default=32)
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument("--gemmini-root", default=str(ROOT.parent / "llama.cpp-gemmini"))
    parser.add_argument("--params-root", default=str(ROOT.parent / "RISC-V-DynDNN-gemmini-include/include"))
    parser.add_argument("--cxx", default="c++")
    parser.add_argument("--ar", default="ar")
    parser.add_argument("--bsc", default="bsc")
    parser.add_argument("--bsc-verilog", default="")
    parser.add_argument("--bsc-extra-flags", default="")
    parser.add_argument("--verilator", default="verilator")
    parser.add_argument("--rustc", default="rustc")
    parser.add_argument("--cargo", default="cargo")
    parser.add_argument("--print-identities", action="store_true")
    args = parser.parse_args()
    if args.print_identities:
        print(json.dumps([f"a{a}-w{w}-d{d}" for a, w, d in IDENTITIES]))
        return 0
    if not args.bsc_verilog or not args.bsc_extra_flags:
        parser.error("--bsc-verilog and --bsc-extra-flags are required")
    if args.jobs < 1:
        parser.error("--jobs must be at least 1")
    args.build_dir = args.build_dir.resolve()
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as executor:
        futures = [executor.submit(run_one, args, *identity) for identity in IDENTITIES]
        rows = [future.result() for future in futures]
    rows.sort(key=lambda row: row["id"])
    summary = {
        "schema": "im2p-real-lib-all-v1",
        "block_size": args.block_size,
        "jobs": args.jobs,
        "artifacts": rows,
        "ok": all(row["returncode"] == 0 for row in rows),
    }
    summary_path = args.build_dir / "manifests" / "real-lib-all.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = summary_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(summary, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, summary_path)
    print(f"IM2P_REAL_LIB_ALL artifacts=9 ok={str(summary['ok']).lower()} summary={summary_path}")
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
