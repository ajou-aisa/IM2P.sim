#!/usr/bin/env python3
"""Content-addressed, atomically published cache for real frontend libraries."""

from __future__ import annotations

import argparse
import fcntl
import os
import platform
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.im2p_config import profile_config
from scripts.real_lib_manifest import (
    SCHEMA,
    CacheError,
    IdentityData,
    ToolIdentity,
    artifact_rows,
    atomic_json,
    verify_manifest,
)
from scripts.real_lib_materialize import (
    SELECTED_ARTIFACTS,
    materialize,
    secure_directory,
)
from scripts.real_lib_toolchain import collect_toolchain, identity_fingerprint


def artifact_relatives(identity: str) -> tuple[Path, ...]:
    return (
        Path("lib") / identity / "libim2p_gemmini_frontend.a",
        Path("cargo") / identity / "release" / "libim2p_sim.a",
    )


def run_builder(
    args: argparse.Namespace,
    stage_build: Path,
    identity: str,
    tools: dict[str, ToolIdentity],
) -> int:
    environment = os.environ.copy()
    environment.update({
        "CARGO_BUILD_JOBS": "1",
        "IM2P_CACHE_STAGE_BUILD_DIR": str(stage_build),
        "IM2P_CACHE_ARTIFACT_ID": identity,
        "IM2P_ACTIVATION_BITS": str(args.bits),
        "IM2P_WEIGHT_BITS": str(args.weight_bits),
        "IM2P_DIM": str(args.dim),
        "GEMMINI_FRONTEND_BLOCK_SIZE": str(args.block_size),
        "IM2P_VERILATOR_EXECUTABLE": tools["verilator"]["executable"],
    })
    environment.pop("MAKEFLAGS", None)
    if args.builder:
        command = [args.builder]
    else:
        make = os.environ.get("MAKE", "make").split()[0]
        command = [
            make, "--no-print-directory", "-j1", f"BUILD_DIR={stage_build}",
            f"IM2P_ACTIVATION_BITS={args.bits}", f"IM2P_WEIGHT_BITS={args.weight_bits}",
            f"IM2P_DIM={args.dim}", f"GEMMINI_FRONTEND_ACTIVATION_BITS={args.bits}",
            f"GEMMINI_FRONTEND_WEIGHT_BITS={args.weight_bits}",
            f"GEMMINI_FRONTEND_DIM={args.dim}",
            f"GEMMINI_FRONTEND_BLOCK_SIZE={args.block_size}",
            f"GEMMINI_ROOT={args.gemmini_root}", f"GEMMINI_PARAMS_ROOT={args.params_root}",
            f"CXX={args.cxx}", f"AR={args.ar}", f"BSC={args.bsc}",
            f"BSC_VERILOG={args.bsc_verilog}",
            f"BSC_EXTRA_FLAGS={args.bsc_extra_flags}",
            f"VERILATOR={args.verilator}",
            f"RUSTC={args.rustc}", f"CARGO={args.cargo}",
            "_gemmini-frontend-real-lib-build",
        ]
    return subprocess.run(command, cwd=ROOT, env=environment).returncode


def ensure(args: argparse.Namespace) -> int:
    if args.bits != args.weight_bits:
        raise CacheError(
            "real library cache requires matched activation/weight widths"
        )
    build_dir = args.build_dir.resolve()
    build_dir.mkdir(parents=True, exist_ok=True)
    if not build_dir.is_dir():
        raise CacheError(f"build root is not a directory: {build_dir}")
    identity = f"a{args.bits}-w{args.weight_bits}-d{args.dim}"
    tools, build_config = collect_toolchain(args)
    fingerprint = identity_fingerprint(args, tools, build_config)
    identity_data: IdentityData = {
        **profile_config(args.bits, args.weight_bits, args.dim),
        "id": identity, "block_size": args.block_size,
        "platform": platform.system(), "platform_release": platform.release(),
        "arch": platform.machine(),
    }
    cache_root = secure_directory(build_dir, Path("cache") / "real-lib")
    entries = secure_directory(build_dir, Path("cache") / "real-lib" / "entries")
    staging = secure_directory(build_dir, Path("cache") / "real-lib" / "staging")
    locks = secure_directory(build_dir, Path("cache") / "real-lib" / "locks")
    entry = entries / fingerprint
    # Serialize every generation of one public identity, not just one content
    # key, so two source revisions cannot race while materializing its stable paths.
    lock_path = locks / f"{identity}.lock"
    reason = "missing"
    with lock_path.open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        cache_manifest = entry / "real-lib.json"
        if os.path.lexists(entry):
            if entry.is_symlink() or not entry.is_dir():
                raise CacheError(f"cache entry is not a private directory: {entry}")
            relatives = artifact_relatives(identity)
            expected_cache_artifacts = tuple(
                (Path("artifacts") / relative).as_posix()
                for relative in relatives
            )
            cache_rows = []
            valid, _ = verify_manifest(
                cache_manifest,
                expected_fingerprint=fingerprint,
                expected_identity_data=identity_data,
                expected_toolchains=tools,
                expected_build_config=build_config,
                expected_artifacts=expected_cache_artifacts,
                verified_rows=cache_rows,
            )
            if valid:
                manifest = materialize(
                    entry, build_dir, identity_data, tools, build_config,
                    fingerprint, relatives, cache_rows,
                )
                print(f"IM2P_REAL_LIB_CACHE id={identity} state=hit fingerprint={fingerprint} manifest={manifest}")
                return 0
            reason = "tamper"
            shutil.rmtree(entry)
        transaction = staging / f".{fingerprint}.{os.getpid()}.{uuid.uuid4().hex}"
        stage_build = transaction / "build"
        publish = transaction / "publish"
        try:
            stage_build.mkdir(parents=True)
            status = run_builder(args, stage_build, identity, tools)
            if status:
                return status
            relatives = artifact_relatives(identity)
            for relative in relatives:
                source = stage_build / relative
                destination = publish / "artifacts" / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                if source.is_file() and not source.is_symlink():
                    shutil.copy2(source, destination)
                else:
                    raise CacheError(f"builder omitted artifact: {relative}")
            published_rows = artifact_rows(
                publish, tuple(Path("artifacts") / item for item in relatives)
            )
            cache_data = {
                "schema": SCHEMA, "fingerprint": fingerprint,
                "identity": identity_data, "toolchains": tools,
                "build_config": build_config,
                "artifact_root": ".",
                "artifacts": published_rows,
            }
            atomic_json(publish / "real-lib.json", cache_data)
            expected_cache_artifacts = tuple(
                (Path("artifacts") / relative).as_posix()
                for relative in relatives
            )
            valid, detail = verify_manifest(
                publish / "real-lib.json",
                expected_fingerprint=fingerprint,
                expected_identity_data=identity_data,
                expected_toolchains=tools,
                expected_build_config=build_config,
                expected_artifacts=expected_cache_artifacts,
            )
            if not valid:
                raise CacheError(
                    f"staged cache verification failed: {detail}"
                )
            publish.rename(entry)
            manifest = materialize(
                entry, build_dir, identity_data, tools, build_config,
                fingerprint, relatives, published_rows,
            )
            print(f"IM2P_REAL_LIB_CACHE id={identity} state=rebuild reason={reason} fingerprint={fingerprint} manifest={manifest}")
            return 0
        finally:
            if transaction.exists():
                shutil.rmtree(transaction)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    sub = result.add_subparsers(dest="command", required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("--manifest", type=Path, required=True)
    for field in ("identity", "platform", "arch"):
        verify.add_argument(f"--expected-{field}")
    verify.add_argument("--expected-block-size", type=int)
    verify.add_argument("--expected-platform-release")
    verify.add_argument("--artifact-kind", choices=("selected",))
    ensure_parser = sub.add_parser("ensure")
    for name in ("bits", "weight-bits"):
        ensure_parser.add_argument(f"--{name}", type=int, choices=(4, 8, 16), required=True)
    ensure_parser.add_argument("--dim", type=int, choices=(16, 32, 64), required=True)
    ensure_parser.add_argument("--block-size", type=int, required=True)
    ensure_parser.add_argument("--build-dir", type=Path, required=True)
    ensure_parser.add_argument("--gemmini-root", type=Path, required=True)
    ensure_parser.add_argument("--params-root", type=Path, required=True)
    for name in ("cxx", "ar", "bsc", "verilator", "rustc", "cargo"):
        ensure_parser.add_argument(f"--{name}", required=True)
    ensure_parser.add_argument("--bsc-verilog", required=True)
    ensure_parser.add_argument("--bsc-extra-flags", required=True)
    ensure_parser.add_argument("--extra-input", type=Path)
    ensure_parser.add_argument("--builder")
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "verify":
            valid, detail = verify_manifest(
                args.manifest.resolve(), expected_identity=args.expected_identity,
                expected_block_size=args.expected_block_size,
                expected_platform=args.expected_platform,
                expected_platform_release=args.expected_platform_release,
                expected_arch=args.expected_arch,
                expected_artifacts=(
                    SELECTED_ARTIFACTS if args.artifact_kind == "selected" else None
                ),
            )
            if not valid:
                print(f"REAL_LIB_CACHE_VERIFY FAIL manifest={args.manifest} detail={detail}", file=sys.stderr)
                return 1
            print(f"REAL_LIB_CACHE_VERIFY PASS manifest={args.manifest}")
            return 0
        return ensure(args)
    except (OSError, CacheError) as error:
        print(f"REAL_LIB_CACHE ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
