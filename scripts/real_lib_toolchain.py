"""Toolchain identity and source fingerprint inputs for real-library caches."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from scripts.real_lib_manifest import (
    ArtifactRow,
    BuildConfig,
    CacheError,
    ToolIdentity,
    sha256,
)

ROOT = Path(__file__).resolve().parents[1]


def _input_rows(paths: tuple[Path, ...]) -> list[ArtifactRow]:
    rows: list[ArtifactRow] = []
    for path in paths:
        concrete = path.resolve(strict=True)
        rows.append({
            "path": concrete.name,
            "sha256": sha256(concrete),
            "size": concrete.stat().st_size,
        })
    return rows


def tool_identity(
    command: str,
    inputs: tuple[Path, ...] = (),
) -> ToolIdentity:
    try:
        words = shlex.split(command)
    except ValueError as error:
        raise CacheError(f"invalid tool command: {command}") from error
    if not words:
        raise CacheError("empty tool command")
    executable = shutil.which(words[0])
    if executable is None:
        raise CacheError(f"tool is not executable: {words[0]}")
    launcher = Path(executable)
    concrete = Path(executable).resolve()
    version = ""
    for flag in ("--version", "-version"):
        result = subprocess.run(
            [str(launcher), *words[1:], flag], text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            version = result.stdout.strip()
            break
    return {
        "command": command,
        "executable": str(concrete),
        "executable_sha256": sha256(concrete),
        "version": version,
        "inputs": _input_rows(inputs),
    }


def verilator_runtime_inputs(command: str) -> tuple[Path, ...]:
    words = shlex.split(command)
    executable = shutil.which(words[0]) if words else None
    if executable is None:
        raise CacheError("Verilator executable is unavailable")

    result = subprocess.run(
        [str(Path(executable).resolve()), *words[1:], "-V"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if result.returncode != 0:
        raise CacheError("verilator -V failed")

    runtime_names = (
        "verilated.cpp",
        "verilated_threads.cpp",
        "verilated.h",
        "verilated_threads.h",
    )

    roots = [
        Path(value.strip())
        for line in result.stdout.splitlines()
        if "=" in line
        for key, value in (line.split("=", 1),)
        if key.strip() == "VERILATOR_ROOT"
    ]

    root = next(
        (
            candidate
            for candidate in roots
            if all(
                (candidate / "include" / name).is_file()
                for name in runtime_names
            )
        ),
        None,
    )

    if root is None:
        raise CacheError(
            "no usable VERILATOR_ROOT found in verilator -V output"
        )

    return tuple(
        root / "include" / name
        for name in runtime_names
    )

def collect_toolchain(
    args: argparse.Namespace,
) -> tuple[dict[str, ToolIdentity], BuildConfig]:
    bsc_inputs = tuple(
        Path(args.bsc_verilog) / name for name in ("RegFile.v", "FIFO2.v", "BRAM1.v")
    )
    cargo_home = Path(
        os.environ.get("CARGO_HOME", str(Path.home() / ".cargo"))
    )
    cargo_inputs = tuple(
        path
        for path in (cargo_home / "config", cargo_home / "config.toml")
        if path.is_file()
    )
    tools: dict[str, ToolIdentity] = {
        "cxx": tool_identity(args.cxx),
        "ar": tool_identity(args.ar),
        "bsc": tool_identity(args.bsc, bsc_inputs),
        "verilator": tool_identity(
            args.verilator, verilator_runtime_inputs(args.verilator)
        ),
        "rustc": tool_identity(args.rustc),
        "cargo": tool_identity(args.cargo, cargo_inputs),
        "make": tool_identity(os.environ.get("MAKE", "make").split()[0]),
    }
    builder = Path(args.builder).resolve(strict=True) if args.builder else None
    config: BuildConfig = {
        "bsc_verilog": str(Path(args.bsc_verilog).resolve(strict=True)),
        "bsc_extra_flags": args.bsc_extra_flags,
        "cargo_encoded_rustflags": os.environ.get("CARGO_ENCODED_RUSTFLAGS", ""),
        "rustflags": os.environ.get("RUSTFLAGS", ""),
        "sdkroot": os.environ.get("SDKROOT", ""),
        "deployment_target": os.environ.get("MACOSX_DEPLOYMENT_TARGET", ""),
        "builder": str(builder) if builder else "",
        "builder_sha256": sha256(builder) if builder else "",
    }
    for name in (
        "ARFLAGS", "CC", "CFLAGS", "CPPFLAGS", "CXX", "CXXFLAGS",
        "LDFLAGS", "RUSTC_WRAPPER", "RUSTDOCFLAGS",
    ):
        config[f"env.{name}"] = os.environ.get(name, "")
    for name, value in os.environ.items():
        if name.startswith(
            ("CARGO_BUILD_", "CARGO_PROFILE_", "CARGO_TARGET_", "CXXSTDLIB_")
        ) or name in ("CXXSTDLIB", "HOST_CXXSTDLIB", "TARGET_CXXSTDLIB"):
            config[f"env.{name}"] = value
    config["cargo_home"] = str(cargo_home.resolve())
    return tools, config


def identity_fingerprint(
    args: argparse.Namespace,
    tools: dict[str, ToolIdentity],
    build_config: BuildConfig,
) -> str:
    command = [
        sys.executable, str(ROOT / "scripts/real_matrix_fingerprint.py"),
        "--bits", str(args.bits), "--weight-bits", str(args.weight_bits),
        "--dim", str(args.dim), "--block-size", str(args.block_size),
        "--gemmini-root", str(args.gemmini_root),
        "--params-root", str(args.params_root),
    ]
    if args.extra_input:
        command.extend(("--extra-input", str(args.extra_input)))
    host = {
        "platform": platform.system(),
        "release": platform.release(),
        "arch": platform.machine(),
    }
    for name, value in sorted(host.items()):
        command.extend(("--config", f"host.{name}={value}"))
    for name, value in sorted(tools.items()):
        encoded = json.dumps(value, sort_keys=True)
        command.extend(("--config", f"tool.{name}={encoded}"))
    for name, value in sorted(build_config.items()):
        command.extend(("--config", f"build.{name}={value}"))
    result = subprocess.run(
        command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        check=False,
    )
    if result.returncode:
        raise CacheError(result.stdout.strip())
    return result.stdout.strip()
