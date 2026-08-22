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
DIMS = (16, 32, 64)
MATCHED_WIDTHS = ((4, 4), (16, 16))
IDENTITY_RE = re.compile(r"a(?:4|8|16)-w(?:4|8|16)-d(?:16|32|64)")
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
        elif a4_ids != {f"a4-w8-d{dim}" for dim in DIMS} or a8_ids != {
            f"a8-w8-d{dim}" for dim in DIMS
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
            normalized_output = result.stdout.replace('"', "")
            obj_dir = artifact("verilator", identity, "obj_dir")
            clean_command = f"rm -rf {obj_dir}"
            create_command = f"mkdir -p {obj_dir}"
            if (
                clean_command not in normalized_output
                or create_command not in normalized_output
                or normalized_output.index(clean_command)
                > normalized_output.index(create_command)
            ):
                failures.append(
                    f"{target} must remove stale Verilator partitions before "
                    f"creating {obj_dir}"
                )
            if dim == 64:
                rtl_glob = (
                    f"{artifact('rtl', identity, f'SynthInt{bits}x{dim}')}/*.v"
                )
                if rtl_glob not in normalized_output:
                    failures.append(
                        f"{target} must compile every generated hierarchy module; "
                        f"missing {rtl_glob}"
                    )
            observed[identity] = result.stdout

            sim_target = f"sim-test-int{bits}x{dim}"
            sim = make_dry_run(
                sim_target, variables=("CARGO_TEST_FILTER=contract_filter",)
            )
            required = (
                f"IM2P_ACTIVATION_BITS={bits}",
                "IM2P_WEIGHT_BITS=8",
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
                expected_block_size = 64 if dim == 64 else 32
                block_definition = (
                    f"-DGGML_GEMMINI_BLOCK_SIZE={expected_block_size}"
                )
                if block_definition not in frontend.stdout:
                    missing.append(block_definition)
                weight_definition = "-DGGML_GEMMINI_WEIGHT_BITS=8"
                if weight_definition not in frontend.stdout:
                    missing.append(weight_definition)
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

    for activation_bits, weight_bits in MATCHED_WIDTHS:
        for dim in DIMS:
            identity = f"a{activation_bits}-w{weight_bits}-d{dim}"
            target = f"verilator-a{activation_bits}-w{weight_bits}-d{dim}"
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
            stem = f"SynthA{activation_bits}W{weight_bits}D{dim}"
            normalized_output = result.stdout.replace('"', "")
            matched_required = (
                f"TOP=mk{stem}",
                f"--top-module mk{stem}",
                artifact("rtl", identity, stem),
                artifact("verilator", identity, "obj_dir"),
            )
            missing = [
                value for value in matched_required if value not in normalized_output
            ]
            rtl_glob = f"{artifact('rtl', identity, stem)}/*.v"
            if dim == 64 and rtl_glob not in normalized_output:
                missing.append(rtl_glob)
            if missing:
                failures.append(f"{target} is missing {missing}")

            config = (
                f"IM2P_ACTIVATION_BITS={activation_bits}",
                f"IM2P_WEIGHT_BITS={weight_bits}",
                f"IM2P_DIM={dim}",
            )
            frontend = make_dry_run("gemmini-frontend-test", variables=config)
            frontend_required = (
                artifact("generated", identity, "gemmini_params.h"),
                artifact("bin", identity, "im2p_gemmini_frontend.o"),
                artifact("lib", identity, "libim2p_gemmini_frontend.a"),
                artifact("bin", identity, "im2p_gemmini_frontend_test"),
                f"-DGGML_GEMMINI_WEIGHT_BITS={weight_bits}",
            )
            if frontend.returncode != 0:
                failures.append(
                    f"frontend {identity} dry-run failed:\n{frontend.stdout}"
                )
            else:
                missing = [
                    value
                    for value in frontend_required
                    if value.startswith("-D") and value not in frontend.stdout
                ]
                missing.extend(
                    missing_paths(
                        frontend.stdout,
                        tuple(
                            value
                            for value in frontend_required
                            if not value.startswith("-D")
                        ),
                    )
                )
                if missing:
                    failures.append(f"frontend {identity} is missing {missing}")

            if dim == 16:
                real = make_dry_run("gemmini-frontend-real-test", variables=config)
                real_required = (
                    f"IM2P_ACTIVATION_BITS={activation_bits}",
                    f"IM2P_WEIGHT_BITS={weight_bits}",
                    f"IM2P_DIM={dim}",
                )
                real_paths = (
                    cargo_dir(identity),
                    artifact("results", identity),
                )
                if real.returncode != 0:
                    failures.append(
                        f"frontend real {identity} dry-run failed:\n{real.stdout}"
                    )
                else:
                    missing = [
                        value for value in real_required if value not in real.stdout
                    ]
                    missing.extend(missing_paths(real.stdout, real_paths))
                    if missing:
                        failures.append(
                            f"frontend real {identity} is missing {missing}"
                        )

            sim_target = f"sim-test-a{activation_bits}-w{weight_bits}-d{dim}"
            sim = make_dry_run(
                sim_target, variables=("CARGO_TEST_FILTER=contract_filter",)
            )
            required = (
                f"IM2P_ACTIVATION_BITS={activation_bits}",
                f"IM2P_WEIGHT_BITS={weight_bits}",
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

    frontend_weight_override = make_dry_run(
        "gemmini-frontend-test",
        variables=(
            "IM2P_ACTIVATION_BITS=4",
            "IM2P_WEIGHT_BITS=8",
            "IM2P_DIM=16",
            "GEMMINI_FRONTEND_WEIGHT_BITS=4",
        ),
    )
    override_identity = "a4-w4-d16"
    override_required_paths = (
        artifact("generated", override_identity, "gemmini_params.h"),
        artifact("bin", override_identity, "im2p_gemmini_frontend.o"),
        artifact("lib", override_identity, "libim2p_gemmini_frontend.a"),
    )
    if frontend_weight_override.returncode != 0:
        failures.append(
            "frontend weight override dry-run failed:\n"
            f"{frontend_weight_override.stdout}"
        )
    else:
        missing = missing_paths(
            frontend_weight_override.stdout, override_required_paths
        )
        if "-DGGML_GEMMINI_WEIGHT_BITS=4" not in frontend_weight_override.stdout:
            missing.append("-DGGML_GEMMINI_WEIGHT_BITS=4")
        override_ids = set(IDENTITY_RE.findall(frontend_weight_override.stdout))
        if override_ids != {override_identity}:
            missing.append(f"isolated identity {override_identity}")
        if missing:
            failures.append(f"frontend weight override is missing {missing}")

    invalid = make_dry_run("verilator", variables=("IM2P_ACTIVATION_BITS=5",))
    if invalid.returncode == 0:
        failures.append("IM2P_ACTIVATION_BITS=5 did not fail during Makefile parsing")
    elif "IM2P_ACTIVATION_BITS must be one of 4, 8, or 16" not in invalid.stdout:
        failures.append(
            f"invalid-width diagnostic is not actionable:\n{invalid.stdout}"
        )

    invalid_weight = make_dry_run("verilator", variables=("IM2P_WEIGHT_BITS=5",))
    if invalid_weight.returncode == 0:
        failures.append("IM2P_WEIGHT_BITS=5 did not fail during Makefile parsing")
    elif "IM2P_WEIGHT_BITS must be one of 4, 8, or 16" not in invalid_weight.stdout:
        failures.append(
            f"invalid weight-width diagnostic is not actionable:\n"
            f"{invalid_weight.stdout}"
        )

    invalid_frontend_weight = make_dry_run(
        "gemmini-frontend-test", variables=("GEMMINI_FRONTEND_WEIGHT_BITS=5",)
    )
    if invalid_frontend_weight.returncode == 0:
        failures.append(
            "GEMMINI_FRONTEND_WEIGHT_BITS=5 did not fail during Makefile parsing"
        )
    elif (
        "GEMMINI_FRONTEND_WEIGHT_BITS must be one of 4, 8, or 16"
        not in invalid_frontend_weight.stdout
    ):
        failures.append(
            "invalid frontend weight-width diagnostic is not actionable:\n"
            f"{invalid_frontend_weight.stdout}"
        )

    expected_artifact_count = (
        len(BITS) * len(DIMS) + len(MATCHED_WIDTHS) * len(DIMS)
    )
    if len(observed) == expected_artifact_count:
        path_sets = {
            identity: set(IDENTITY_RE.findall(output))
            for identity, output in observed.items()
        }
        if (
            len({next(iter(paths)) for paths in path_sets.values() if paths})
            != expected_artifact_count
        ):
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
