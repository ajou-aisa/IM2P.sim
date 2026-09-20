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
from scripts.gemmini_replay_contract import (
    CONTRACT_KEY, ContractError, canonical_json, hardware_contract,
    verify_runtime_source_proof,
)
from scripts.im2p_paths import resolve_gemmini_work_root
from scripts.real_lib_manifest import (
    SCHEMA,
    CacheError,
    IdentityData,
    ToolIdentity,
    artifact_rows,
    atomic_json,
    verify_manifest,
    sha256,
)
from scripts.real_lib_materialize import (
    SELECTED_ARTIFACTS,
    materialize,
    secure_directory,
)
from scripts.real_lib_toolchain import (
    collect_toolchain,
    identity_fingerprint,
    integrated_build_environment,
)


def artifact_relatives(implementation: str, identity: str) -> tuple[Path, ...]:
    return (
        Path("lib") / implementation / identity / "libim2p_gemmini_frontend.a",
        Path("cargo") / implementation / identity / "release" / "libim2p_sim.a",
    )


def run_builder(
    args: argparse.Namespace,
    stage_build: Path,
    identity: str,
    tools: dict[str, ToolIdentity],
) -> int:
    environment = integrated_build_environment(args)
    environment.update({
        "CARGO_BUILD_JOBS": "1",
        "IM2P_CACHE_STAGE_BUILD_DIR": str(stage_build),
        "IM2P_CACHE_ARTIFACT_ID": identity,
        "IM2P_SIM_IMPLEMENTATION": args.implementation,
        "IM2P_ACTIVATION_BITS": str(args.bits),
        "IM2P_WEIGHT_BITS": str(args.weight_bits),
        "IM2P_DIM": str(args.dim),
        "GEMMINI_FRONTEND_BLOCK_SIZE": str(args.block_size),
        "IM2P_VERILATOR_EXECUTABLE": tools["verilator"]["executable"],
    })
    environment.pop("MAKEFLAGS", None)
    if args.implementation == "GEMMINI_HP1" and not args.builder:
        profile = f"a{args.bits}w{args.weight_bits}-d{args.dim}-hp1"
        generated = stage_build / "gemmini-hp1" / profile
        contract = ROOT / "config" / "gemmini_host_memory_contracts" / f"{profile}.json"
        generator = [
            sys.executable, str(ROOT / "scripts" / "gemmini_build.py"),
            "--a-bits", str(args.bits), "--w-bits", str(args.weight_bits),
            "--dim", str(args.dim), "--scu", "hp1-left-shift",
            "--top", "integrated", "--memory-contract", str(contract),
            "--stage", "rtl", "--out", str(generated),
        ]
        if subprocess.run(generator, cwd=ROOT, env=environment).returncode:
            return 1
        top = f"IM2PGemminiWSHP1A{args.bits}W{args.weight_bits}D{args.dim}"
        object_dir = stage_build / "verilator" / "GEMMINI_HP1" / identity / "obj_dir"
        object_dir.mkdir(parents=True, exist_ok=True)
        model = [
            tools["verilator"]["executable"], "--cc", "--timing", "-Wall", "-Wno-fatal",
            "--top-module", top, "--prefix", "VIM2PGemminiWSHP1Sim",
            "--Mdir", str(object_dir), "-F", str(generated / "filelist.f"),
        ]
        if subprocess.run(model, cwd=generated, env=environment).returncode:
            return 1
        shutil.copyfile(generated / "im2p_gemmini_hardware.h",
                        object_dir / "im2p_gemmini_hardware.h")
        environment.update({
            "IM2P_GEMMINI_HP1_OBJ_DIR": str(object_dir),
            "IM2P_GEMMINI_HP1_TOP": top,
            "IM2P_GEMMINI_HP1_PREFIX": "VIM2PGemminiWSHP1Sim",
        })
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
            f"IM2P_SIM_IMPLEMENTATION={args.implementation}",
            f"RUSTC={args.rustc}", f"CARGO={args.cargo}",
            "_gemmini-frontend-real-lib-build",
        ]
    return subprocess.run(command, cwd=ROOT, env=environment).returncode


def ensure(args: argparse.Namespace) -> int:
    if args.runtime_source_proof and (not args.builder or args.implementation != "GEMMINI_HP1"):
        raise CacheError("runtime source proof requires a custom GEMMINI_HP1 builder")
    if args.bits != args.weight_bits:
        raise CacheError(
            "real library cache requires matched activation/weight widths"
        )
    build_dir = args.build_dir.resolve()
    build_dir.mkdir(parents=True, exist_ok=True)
    if not build_dir.is_dir():
        raise CacheError(f"build root is not a directory: {build_dir}")
    if args.implementation == "GEMMINI_HP1" and (
        args.bits not in (4, 8) or args.block_size != 32
    ):
        raise CacheError("GEMMINI_HP1 requires A/W 4 or 8 and block size 32")
    if args.implementation == "LEGACY_BSV" and (
        not args.bsc_verilog or not args.bsc_extra_flags
    ):
        raise CacheError("LEGACY_BSV requires BSC Verilog runtime inputs")
    identity = f"a{args.bits}-w{args.weight_bits}-d{args.dim}"
    tools, build_config = collect_toolchain(args)
    contract = None
    runtime_digest = None
    profile = f"a{args.bits}w{args.weight_bits}-d{args.dim}-hp1"
    if args.implementation == "GEMMINI_HP1" and (not args.builder or args.runtime_source_proof):
        contract = hardware_contract(profile)
        if args.builder:
            runtime_digest = verify_runtime_source_proof(args.runtime_source_proof, profile, contract)
            build_config["runtime_source_proof_sha256"] = sha256(args.runtime_source_proof)
        build_config[CONTRACT_KEY] = canonical_json(contract)
        build_config["runtime_execution_kind"] = "VERIFIED_REUSE" if args.builder else "FRESH_BUILD"
    fingerprint = identity_fingerprint(args, tools, build_config)
    identity_data: IdentityData = {
        **profile_config(args.bits, args.weight_bits, args.dim),
        "id": identity, "implementation": args.implementation,
        "block_size": args.block_size,
        "platform": platform.system(), "platform_release": platform.release(),
        "arch": platform.machine(),
    }
    cache_path = Path("cache") / "real-lib" / args.implementation
    cache_root = secure_directory(build_dir, cache_path)
    entries = secure_directory(build_dir, cache_path / "entries")
    staging = secure_directory(build_dir, cache_path / "staging")
    locks = secure_directory(build_dir, cache_path / "locks")
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
            relatives = artifact_relatives(args.implementation, identity)
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
            if contract is not None and canonical_json(hardware_contract(profile)) != build_config[CONTRACT_KEY]:
                raise CacheError("hardware contract sources changed while building")
            relatives = artifact_relatives(args.implementation, identity)
            if runtime_digest is not None and sha256(stage_build / relatives[1]) != runtime_digest:
                raise CacheError("builder runtime differs from verified source-proof archive")
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
    for field in ("identity", "implementation", "platform", "arch"):
        verify.add_argument(f"--expected-{field}")
    verify.add_argument("--expected-block-size", type=int)
    verify.add_argument("--expected-platform-release")
    verify.add_argument("--artifact-kind", choices=("selected",))
    ensure_parser = sub.add_parser("ensure")
    ensure_parser.add_argument(
        "--implementation", choices=("LEGACY_BSV", "GEMMINI_HP1"),
        default="LEGACY_BSV",
    )
    for name in ("bits", "weight-bits"):
        ensure_parser.add_argument(f"--{name}", type=int, choices=(4, 8, 16), required=True)
    ensure_parser.add_argument("--dim", type=int, choices=(16, 32, 64), required=True)
    ensure_parser.add_argument("--block-size", type=int, required=True)
    ensure_parser.add_argument("--build-dir", type=Path, required=True)
    ensure_parser.add_argument("--gemmini-root", type=Path, required=True)
    ensure_parser.add_argument("--params-root", type=Path, required=True)
    for name in ("cxx", "ar", "verilator", "rustc", "cargo"):
        ensure_parser.add_argument(f"--{name}", required=True)
    ensure_parser.add_argument("--bsc", default="bsc")
    ensure_parser.add_argument("--bsc-verilog", default="")
    ensure_parser.add_argument("--bsc-extra-flags", default="")
    ensure_parser.add_argument("--extra-input", type=Path)
    ensure_parser.add_argument("--builder")
    ensure_parser.add_argument("--runtime-source-proof", type=Path,
                              help="original source/artifact proof for an HP1 runtime reused by --builder")
    ensure_parser.add_argument(
        "--gemmini-work-root", type=Path,
        default=resolve_gemmini_work_root(ROOT),
    )
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "verify":
            valid, detail = verify_manifest(
                args.manifest.resolve(), expected_identity=args.expected_identity,
                expected_implementation=args.expected_implementation,
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
    except (OSError, CacheError, ContractError) as error:
        print(f"REAL_LIB_CACHE ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
