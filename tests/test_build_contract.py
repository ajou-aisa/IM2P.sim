#!/usr/bin/env python3
"""Executable contract test for width/DIM-isolated simulator builds."""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.static_check import obsolete_exsia_claims

BITS = (4, 8, 16)
DIMS = (16, 32, 64)
MATCHED_WIDTHS = ((4, 4), (16, 16))
REAL_MATRIX_PAIRS = tuple((bits, bits, dim) for bits in BITS for dim in DIMS)
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
            token = token.strip(";|()")
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

    with tempfile.TemporaryDirectory(prefix="im2p-frontend-deps-") as temp_dir:
        build_dir = Path(temp_dir) / "build"
        identity = "a8-w8-d16"
        frontend_object = build_dir / "bin" / identity / "im2p_gemmini_frontend.o"
        variables = (
            f"BUILD_DIR={build_dir}",
            "IM2P_ACTIVATION_BITS=8",
            "IM2P_WEIGHT_BITS=8",
            "IM2P_DIM=16",
        )
        built = subprocess.run(
            ["make", "--no-print-directory", str(frontend_object), *variables],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        args_header = ROOT.parent / "llama.cpp-gemmini" / "ggml" / "src" / "ggml-gemmini" / "ggml-gemmini-args.h"
        dependency_file = frontend_object.with_suffix(".d")
        if built.returncode != 0:
            failures.append(f"frontend dependency bootstrap failed:\n{built.stdout}")
        elif not dependency_file.is_file():
            failures.append("frontend compile must emit a compiler dependency file")
        else:
            rebuilt = subprocess.run(
                [
                    "make", "--no-print-directory", "-q", "-W", str(args_header),
                    str(frontend_object), *variables,
                ],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            if rebuilt.returncode != 1:
                failures.append(
                    "frontend object must become out-of-date when an included llama header changes; "
                    f"make -q returned {rebuilt.returncode}:\n{rebuilt.stdout}"
                )

    makefile_text = (ROOT / "Makefile").read_text(encoding="utf-8")
    matrix_match = re.search(r"^REAL_MATRIX_PAIRS\s*:=\s*(.+)$", makefile_text, re.MULTILINE)
    expected_matrix = " ".join(
        f"{activation_bits}:{weight_bits}:{dim}"
        for activation_bits, weight_bits, dim in REAL_MATRIX_PAIRS
    )
    if matrix_match is None or matrix_match.group(1).strip() != expected_matrix:
        failures.append(
            "REAL_MATRIX_PAIRS must contain exactly the nine matched "
            f"activation/weight/DIM identities: {expected_matrix}"
        )

    expected_public_targets = {
        *(f"sim-test-a4-w4-d{dim}" for dim in DIMS),
        *(f"sim-test-a8-w8-d{dim}" for dim in DIMS),
        *(f"sim-test-a16-w16-d{dim}" for dim in DIMS),
    }
    phony_match = re.search(
        r"^\.PHONY:(.*?)(?:\n\n|\Z)", makefile_text, re.MULTILINE | re.DOTALL
    )
    declared_public_targets = set()
    if phony_match is not None:
        declared_public_targets = {
            target
            for target in phony_match.group(1).replace("\\\n", " ").split()
            if re.fullmatch(
                r"sim-test-a(?:4|8|16)-w(?:4|8|16)-d(?:16|32|64)",
                target,
            )
        }
    if declared_public_targets != expected_public_targets:
        failures.append(
            "public exact sim targets must be exactly the nine matched identities: "
            f"missing={sorted(expected_public_targets - declared_public_targets)} "
            f"extra={sorted(declared_public_targets - expected_public_targets)}"
        )

    obsolete_claims = (
        "Production ExSIA route는 A8/Q8만 지원한다",
        "Production ExSIA는 A8/Q8만 허용한다",
        "production ExSIA 경로는 A8/Q8만 지원한다",
        "RMD scale integration은 TODO",
        "Matched ExSIA RMD scale integration",
    )
    exact_prior_mutation = (
        "The real matrix only exercises A8/Q8 and A4/Q4 and A16/Q16 "
        "ExSIA remain rejected/TODO."
    )
    detected_mutation = obsolete_exsia_claims(exact_prior_mutation)
    if len(detected_mutation) != 2:
        failures.append(
            "normalized semantic checker did not reject the exact prior mutation: "
            f"{detected_mutation}"
        )
    copied_docs_negative_fixture = (
        "Production ExSIA is A8/Q8 only; A4/Q4 and A16/Q16 are TODO rejection routes."
    )
    copied_fixture_findings = set(obsolete_exsia_claims(copied_docs_negative_fixture))
    copied_fixture_expected = {
        "production ExSIA is A8/Q8-only",
        "matched A4/Q4 and A16/Q16 ExSIA remain rejected/TODO",
    }
    if copied_fixture_findings != copied_fixture_expected:
        failures.append(
            "copied obsolete docs sentence was not rejected by the full build contract: "
            f"{sorted(copied_fixture_findings)}"
        )
    for relative in ("README.md", "frontend/README.md", "docs/ARCHITECTURE.md", "docs/VERIFICATION.md"):
        text = (ROOT / relative).read_text(encoding="utf-8")
        if not all(value in text for value in ("matched ExSIA", "A4/Q4", "A8/Q8", "A16/Q16")):
            failures.append(f"{relative} lacks the supported matched ExSIA contract")
        returned = [claim for claim in obsolete_claims if claim in text]
        returned.extend(obsolete_exsia_claims(text))
        if returned:
            failures.append(f"{relative} restored obsolete A8-only/TODO claims: {returned}")

    syntax = make_dry_run("gemmini-frontend-real-syntax-test")
    syntax_required = (
        "IM2P_ACTIVATION_BITS=\"$bits\" IM2P_WEIGHT_BITS=\"$bits\" IM2P_DIM=16",
        "GEMMINI_FRONTEND_WEIGHT_BITS=\"$bits\" GEMMINI_FRONTEND_DIM=16",
        "-fsyntax-only",
        "frontend/tests/test_frontend_real.cpp",
    )
    if syntax.returncode != 0:
        failures.append(f"real frontend syntax dry-run failed:\n{syntax.stdout}")
    else:
        missing = [value for value in syntax_required if value not in syntax.stdout]
        if missing:
            failures.append(f"real frontend syntax target is missing {missing}")

    for activation_bits in BITS:
        for weight_bits in BITS:
            if activation_bits == weight_bits:
                continue
            mixed = make_dry_run(
                "gemmini-frontend-real-test",
                variables=(
                    f"IM2P_ACTIVATION_BITS={activation_bits}",
                    f"IM2P_WEIGHT_BITS={weight_bits}",
                    f"GEMMINI_FRONTEND_ACTIVATION_BITS={activation_bits}",
                    f"GEMMINI_FRONTEND_WEIGHT_BITS={weight_bits}",
                    "IM2P_DIM=16",
                    "GEMMINI_FRONTEND_DIM=16",
                ),
            )
            if mixed.returncode == 0:
                failures.append(
                    f"mixed artifact A{activation_bits}/W{weight_bits} must fail before execution"
                )
            elif "matched activation/weight widths" not in mixed.stdout:
                failures.append(
                    f"mixed artifact A{activation_bits}/W{weight_bits} lacks exact identity diagnostic"
                )

    generic_a8 = make_dry_run(
        "verilator", variables=("IM2P_ACTIVATION_BITS=8", "IM2P_WEIGHT_BITS=8")
    )
    if generic_a8.returncode != 0:
        failures.append("generic matched A8/Q8 Verilator dry-run must resolve")
    elif set(IDENTITY_RE.findall(generic_a8.stdout)) != {
        f"a8-w8-d{dim}" for dim in DIMS
    }:
        failures.append("generic A8/Q8 paths are not isolated")

    for bits in (8,):
        for dim in DIMS:
            identity = f"a{bits}-w8-d{dim}"
            target = f"verilator-a{bits}-w8-d{dim}"
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
            stem = f"SynthA{bits}W8D{dim}"
            canonical_required = (
                f"TOP=mk{stem}",
                f"--top-module mk{stem}",
                f"--prefix Vmk{stem}",
                artifact("rtl", identity, stem),
            )
            missing_canonical = [
                value for value in canonical_required if value not in normalized_output
            ]
            if missing_canonical:
                failures.append(f"{target} is missing {missing_canonical}")
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
                rtl_glob = f"{artifact('rtl', identity, stem)}/*.v"
                if rtl_glob not in normalized_output:
                    failures.append(
                        f"{target} must compile every generated hierarchy module; "
                        f"missing {rtl_glob}"
                    )
            observed[identity] = result.stdout

            sim_target = f"sim-test-a{bits}-w8-d{dim}"
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

            config = (
                f"IM2P_ACTIVATION_BITS={bits}",
                f"IM2P_WEIGHT_BITS={bits}",
                f"IM2P_DIM={dim}",
            )
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
                block_definition = "-DGGML_GEMMINI_BLOCK_SIZE=32"
                if block_definition not in frontend.stdout:
                    missing.append(block_definition)
                weight_definition = "-DGGML_GEMMINI_WEIGHT_BITS=8"
                if weight_definition not in frontend.stdout:
                    missing.append(weight_definition)
                selector_token = "q8_hp1_extent_contract"
                if selector_token not in frontend.stdout:
                    missing.append(selector_token)
                if missing:
                    failures.append(f"frontend {identity} is missing {missing}")

            c_api = make_dry_run("c-api-test", variables=config)
            c_api_required = (
                artifact("c-api", identity, "c_api_runtime.o"),
                artifact("c-api", identity, "im2p_c_api_runtime"),
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
                if "q8_hp1_extent_contract" in frontend.stdout:
                    missing.append("!q8_hp1_extent_contract")
                if missing:
                    failures.append(f"frontend {identity} is missing {missing}")

            if dim == 16:
                real = make_dry_run("gemmini-frontend-real-test", variables=config)
                real_required = (
                    f"-DIM2P_GEMMINI_FRONTEND_ACTIVATION_BITS={activation_bits}",
                    f"-DGGML_GEMMINI_ACTIVATION_BITS={activation_bits}",
                    f"-DGGML_GEMMINI_WEIGHT_BITS={weight_bits}",
                )
                real_paths = (
                    artifact(
                        "selected", identity, "current", "libim2p_sim.a"
                    ),
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
                    if "cargo build" in real.stdout:
                        failures.append(
                            f"frontend real {identity} must consume the verified "
                            "cached simulator archive without rebuilding Cargo"
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
            "IM2P_WEIGHT_BITS=4",
            "IM2P_DIM=16",
            "GEMMINI_FRONTEND_ACTIVATION_BITS=4",
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

    expected_artifact_count = len(DIMS) + len(MATCHED_WIDTHS) * len(DIMS)
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
