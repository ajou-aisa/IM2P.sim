#!/usr/bin/env python3
"""Executable contract test for width/DIM-isolated simulator builds."""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BITS = (4, 8, 16)
DIMS = (16, 32)
IDENTITY_RE = re.compile(r"a(?:4|8|16)-w8-d(?:16|32)")
BUILD_DIR_TEXT = os.environ.get("IM2P_BUILD_CONTRACT_BUILD_DIR", "build")
BUILD_DIR = Path(BUILD_DIR_TEXT)


def artifact(*parts: str) -> str:
    return (BUILD_DIR.joinpath(*parts)).as_posix()


def cargo_dir(identity: str) -> str:
    path = BUILD_DIR / "cargo" / identity
    if not path.is_absolute():
        path = ROOT / path
    return str(path)


def canonicalize_existing_prefix(path: str | Path, base: Path = ROOT) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = base / candidate
    candidate = Path(os.path.abspath(candidate))
    missing: list[str] = []
    existing = candidate
    while not os.path.lexists(existing):
        if existing.parent == existing:
            break
        missing.append(existing.name)
        existing = existing.parent
    canonical = Path(os.path.realpath(existing))
    for part in reversed(missing):
        canonical /= part
    return canonical


def paths_equivalent(left: str | Path, right: str | Path) -> bool:
    return canonicalize_existing_prefix(left) == canonicalize_existing_prefix(right)


def command_path_operands(output: str):
    for line in output.replace("\\\n", " ").splitlines():
        try:
            tokens = shlex.split(line)
        except ValueError:
            continue
        for token in tokens:
            if "=" in token:
                _, token = token.split("=", 1)
            if "/" in token:
                yield token


def output_has_path(output: str, expected: str | Path) -> bool:
    return any(
        paths_equivalent(candidate, expected)
        for candidate in command_path_operands(output)
    )


def missing_paths(output: str, expected: tuple[str, ...]) -> list[str]:
    return [path for path in expected if not output_has_path(output, path)]


def make_dry_run(
    *targets: str, variables: tuple[str, ...] = ()
) -> subprocess.CompletedProcess[str]:
    build_variable = (
        () if BUILD_DIR_TEXT == "build" else (f"BUILD_DIR={BUILD_DIR_TEXT}",)
    )
    return subprocess.run(
        [
            "make",
            "--no-print-directory",
            "-n",
            "-B",
            *targets,
            *variables,
            *build_variable,
        ],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )


def main() -> int:
    failures: list[str] = []
    observed: dict[str, str] = {}

    generic_a4 = make_dry_run("verilator", variables=("IM2P_ACTIVATION_BITS=4",))
    generic_a8 = make_dry_run("verilator", variables=("IM2P_ACTIVATION_BITS=8",))
    if generic_a4.returncode != 0 or generic_a8.returncode != 0:
        failures.append("generic A4/A8 Verilator dry-runs must both resolve")
    else:
        a4_ids = set(IDENTITY_RE.findall(generic_a4.stdout))
        a8_ids = set(IDENTITY_RE.findall(generic_a8.stdout))
        if not a4_ids and not a8_ids:
            failures.append(
                "A4 and A8 collide: generic Verilator paths have no width identity"
            )
        elif a4_ids != {"a4-w8-d16", "a4-w8-d32"} or a8_ids != {
            "a8-w8-d16",
            "a8-w8-d32",
        }:
            failures.append(
                f"generic width paths are not isolated: A4={a4_ids}, A8={a8_ids}"
            )

    for bits in BITS:
        for dim in DIMS:
            identity = f"a{bits}-w8-d{dim}"
            target = f"verilator-int{bits}x{dim}"
            result = make_dry_run(target)
            if result.returncode != 0:
                failures.append(f"{target} is unavailable:\n{result.stdout}")
                continue
            identities = set(IDENTITY_RE.findall(result.stdout))
            if identities != {identity}:
                failures.append(
                    f"{target} must use only {identity}; observed {sorted(identities)}"
                )
            observed[identity] = result.stdout

            sim_target = f"sim-test-int{bits}x{dim}"
            sim = make_dry_run(
                sim_target, variables=("CARGO_TEST_FILTER=contract_filter",)
            )
            required = (
                f"IM2P_ACTIVATION_BITS={bits}",
                f"IM2P_DIM={dim}",
                "--features test-hooks",
                "contract_filter -- --nocapture",
            )
            required_paths = (
                cargo_dir(identity),
                artifact("results", identity),
            )
            if sim.returncode != 0:
                failures.append(f"{sim_target} is unavailable:\n{sim.stdout}")
            else:
                missing = [value for value in required if value not in sim.stdout]
                missing.extend(missing_paths(sim.stdout, required_paths))
                if missing:
                    failures.append(f"{sim_target} is missing {missing}")

            config = (f"IM2P_ACTIVATION_BITS={bits}", f"IM2P_DIM={dim}")
            frontend = make_dry_run("gemmini-frontend-test", variables=config)
            frontend_required = (
                artifact("generated", identity, "gemmini_params.h"),
                artifact("bin", identity, "im2p_gemmini_frontend.o"),
                artifact("lib", identity, "libim2p_gemmini_frontend.a"),
                artifact("bin", identity, "im2p_gemmini_frontend_test"),
            )
            if frontend.returncode != 0:
                failures.append(
                    f"frontend {identity} dry-run failed:\n{frontend.stdout}"
                )
            else:
                missing = missing_paths(frontend.stdout, frontend_required)
                if missing:
                    failures.append(f"frontend {identity} is missing {missing}")

            c_api = make_dry_run("c-api-test", variables=config)
            c_api_required = (
                artifact("c-api", identity, "c_api_smoke.o"),
                artifact("c-api", identity, "im2p_c_api_smoke"),
                artifact("cargo", identity),
            )
            if c_api.returncode != 0:
                failures.append(f"C API {identity} dry-run failed:\n{c_api.stdout}")
            else:
                missing = missing_paths(c_api.stdout, c_api_required)
                if missing:
                    failures.append(f"C API {identity} is missing {missing}")

    invalid = make_dry_run("verilator", variables=("IM2P_ACTIVATION_BITS=5",))
    if invalid.returncode == 0:
        failures.append("IM2P_ACTIVATION_BITS=5 did not fail during Makefile parsing")
    elif "IM2P_ACTIVATION_BITS must be one of 4, 8, or 16" not in invalid.stdout:
        failures.append(
            f"invalid-width diagnostic is not actionable:\n{invalid.stdout}"
        )

    if len(observed) == len(BITS) * len(DIMS):
        path_sets = {
            identity: set(IDENTITY_RE.findall(output))
            for identity, output in observed.items()
        }
        if len({next(iter(paths)) for paths in path_sets.values() if paths}) != 6:
            failures.append(f"artifact identities collide: {path_sets}")

    if failures:
        print("BUILD CONTRACT FAIL")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("BUILD CONTRACT PASS")
    for identity in sorted(observed):
        print(f"- {identity}: isolated RTL, Verilator, Cargo, and result paths")
    return 0


if __name__ == "__main__":
    sys.exit(main())
