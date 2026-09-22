#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
# noqa: SIZE_OK - end-to-end CLI contract matrix

# ─── How to run ───
# 1. Install uv (if not installed):
#      curl -LsSf https://astral.sh/uv/install.sh | sh
# 2. Run directly (no venv, no pip install needed):
#      uv run tests/test_gemmini_build_cli.py
# 3. Or make executable and run:
#      chmod +x tests/test_gemmini_build_cli.py && ./tests/test_gemmini_build_cli.py
# ─────────────────

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Final
from unittest.mock import patch

from scripts.gemmini_build import (
    BuildFailure,
    FailureReason,
    _parse_request,
    _validate_request,
    run,
)
from scripts.gemmini_export import ExportError, VerifyResult

ROOT: Final = Path(__file__).resolve().parents[1]
RESOLVER: Final = ROOT / "scripts" / "gemmini_resolve_profile.py"
BUILD: Final = ROOT / "scripts" / "gemmini_build.py"
TEST: Final = ROOT / "scripts" / "gemmini_test.py"
WRAPPER: Final = ROOT / "scripts" / "gemmini_build.sh"
CONTRACTS: Final = ROOT / "config" / "gemmini_host_memory_contracts"
EXPECTED_PROFILES: Final = tuple(
    f"a{bits}w{bits}-d{dim}-hp1" for bits in (4, 8) for dim in (16, 32, 64)
)


@dataclass(frozen=True, slots=True)
class RunResult:
    returncode: int
    stdout: str
    stderr: str


def run_script(script: Path, arguments: list[str]) -> RunResult:
    completed = subprocess.run(
        [sys.executable, str(script), *arguments],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    return RunResult(completed.returncode, completed.stdout, completed.stderr)


def base_single_arguments(out: Path) -> list[str]:
    return [
        "--a-bits", "8", "--w-bits", "8", "--dim", "16",
        "--scu", "hp1-left-shift",
        "--memory-contract", str(CONTRACTS / "a8w8-d16-hp1.json"),
        "--stage", "plan", "--out", str(out),
    ]


def clean_llama_source(source: Path) -> None:
    source.mkdir()
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    (source / "CMakeLists.txt").write_text("project(llama)\n")
    reference = source / "ggml/src/ggml-gemmini/residual/rmd/rmd-reference.cpp"
    reference.parent.mkdir(parents=True)
    reference.write_text("// fixture\n")
    subprocess.run(["git", "-C", str(source), "add", "."], check=True)
    subprocess.run(["git", "-C", str(source), "-c", "user.name=Test", "-c",
                    "user.email=test@example.invalid", "commit", "-qm", "fixture"], check=True)


def test_resolver_emits_six_exact_profiles() -> None:
    # Given: six supported paired width and DIM selections.
    with tempfile.TemporaryDirectory(prefix="im2p-gemmini-resolve-") as temporary:
        temporary_path = Path(temporary)
        observed: list[str] = []
        for bits in (4, 8):
            for dim in (16, 32, 64):
                output = temporary_path / f"a{bits}-d{dim}.json"

                # When: each selection resolves against its matching contract.
                result = run_script(RESOLVER, [
                    "--a-bits", str(bits), "--w-bits", str(bits),
                    "--dim", str(dim), "--scu", "hp1-left-shift",
                    "--memory-contract",
                    str(CONTRACTS / f"a{bits}w{bits}-d{dim}-hp1.json"),
                    "--out", str(output),
                ])

                # Then: manifest identity and byte/row geometry are exact.
                assert result.returncode == 0, result.stderr
                document = json.loads(output.read_text(encoding="utf-8"))
                observed.append(document["profile"])
                row_bytes = dim * bits // 8
                assert document["schema_version"] == 1
                assert document["implementation"] == "gemmini-hp1"
                assert document["accumulator_bits"] == 32
                assert document["block_size"] == 32
                assert document["scu"] == "hp1-left-shift"
                assert document["memory"]["scratchpad_row_bytes"] == row_bytes
                assert document["memory"]["bank_rows"] == 262_144 // (4 * row_bytes)
                assert document["memory"]["scratchpad_total_bytes"] == 262_144
                assert document["memory"]["accumulator_total_bytes"] == 65_536
        assert tuple(observed) == EXPECTED_PROFILES


def test_resolver_rejects_mixed_widths() -> None:
    # Given: an unsupported A4/W8 selection.
    with tempfile.TemporaryDirectory(prefix="im2p-gemmini-invalid-") as temporary:
        output = Path(temporary) / "resolved.json"

        # When: resolver parses the selection.
        result = run_script(RESOLVER, [
            "--a-bits", "4", "--w-bits", "8", "--dim", "16",
            "--scu", "hp1-left-shift",
            "--memory-contract", str(CONTRACTS / "a8w8-d16-hp1.json"),
            "--out", str(output),
        ])

        # Then: validation fails without an artifact.
        assert result.returncode != 0
        assert json.loads(result.stderr)["reason"] == "VALIDATION"
        assert not output.exists()


def test_resolver_rejects_non_ws_memory_contract() -> None:
    # Given: matching geometry whose double-buffer policy is false.
    with tempfile.TemporaryDirectory(prefix="im2p-gemmini-bad-memory-") as temporary:
        base = Path(temporary)
        contract = json.loads(
            (CONTRACTS / "a8w8-d16-hp1.json").read_text(encoding="utf-8"),
        )
        contract["ws_double_buffered"] = False
        contract_path = base / "contract.json"
        contract_path.write_text(json.dumps(contract), encoding="utf-8")
        output = base / "resolved.json"

        # When: resolver parses the modified contract.
        result = run_script(RESOLVER, [
            "--a-bits", "8", "--w-bits", "8", "--dim", "16",
            "--scu", "hp1-left-shift", "--memory-contract", str(contract_path),
            "--out", str(output),
        ])

        # Then: contract validation fails rather than emitting a false WS claim.
        assert result.returncode != 0
        assert json.loads(result.stderr)["reason"] == "VALIDATION"
        assert not output.exists()


def test_matrix_dry_run_is_sequential_and_side_effect_free() -> None:
    # Given: full six-profile plan matrix with a fresh output path.
    with tempfile.TemporaryDirectory(prefix="im2p-gemmini-dry-") as temporary:
        output = Path(temporary) / "matrix"

        # When: build runs in dry-run mode.
        result = run_script(BUILD, [
            "--matrix", "a4w4,a8w8", "--dims", "16,32,64",
            "--scu", "hp1-left-shift",
            "--memory-contract-dir", str(CONTRACTS),
            "--stage", "plan", "--out", str(output), "--dry-run",
        ])

        # Then: ordered plan is emitted and no output directory is created.
        assert result.returncode == 0, result.stderr
        plan = json.loads(result.stdout)
        assert tuple(case["profile"] for case in plan["profiles"]) == EXPECTED_PROFILES
        assert plan["execution"] == "sequential"
        assert plan["status"] == "DRY_RUN"
        assert all(case["status"] == "NOT_RUN" for case in plan["profiles"])
        assert not output.exists()


def test_matrix_rejects_non_integer_dim() -> None:
    # Given: matrix DIM list containing non-integer input.
    with tempfile.TemporaryDirectory(prefix="im2p-gemmini-bad-dim-") as temporary:
        output = Path(temporary) / "matrix"

        # When: build parses matrix selection.
        result = run_script(BUILD, [
            "--matrix", "a4w4", "--dims", "16,x", "--scu", "hp1-left-shift",
            "--memory-contract-dir", str(CONTRACTS),
            "--stage", "plan", "--out", str(output), "--dry-run",
        ])

        # Then: typed validation error is returned without output.
        assert result.returncode != 0
        assert json.loads(result.stderr)["reason"] == "VALIDATION"
        assert not output.exists()


def test_plan_writes_resolved_profile() -> None:
    # Given: one valid profile and a fresh output directory.
    with tempfile.TemporaryDirectory(prefix="im2p-gemmini-plan-") as temporary:
        output = Path(temporary) / "single"

        # When: plan stage executes.
        result = run_script(BUILD, base_single_arguments(output))

        # Then: resolved profile and truthful PASS result exist.
        assert result.returncode == 0, result.stderr
        assert json.loads((output / "resolved-profile.json").read_text())["profile"] == "a8w8-d16-hp1"
        resolved = json.loads((output / "resolved-profile.json").read_text())
        assert (output / "resolved-hardware.properties").is_file()
        assert (output / "im2p_gemmini_hardware.h").is_file()
        host_params = (output / "host-params" / "gemmini_params.h").read_text()
        assert "#define DIM 16" in host_params
        assert "#define BANK_NUM 4" in host_params
        assert "#define BANK_ROWS 4096" in host_params
        assert "#define ACC_ROWS 1024" in host_params
        assert resolved["fixed_latencies"] == {"scratchpad_read_delay": 4, "accumulator_latency": 2}
        assert resolved["rmd_enabled"] is True
        assert resolved["rmd_datapath"] == "NORMAL_HP1_SCALED"
        assert resolved["rmd_raw"] is False
        assert resolved["rmd_numerical_revision"] == "rmd-hp1-scu-sat32-radix-v1"
        assert resolved["host_integer_block_multiply"] is False
        assert resolved["work_kinds"] == ["DENSE_HP1_FINAL"]
        assert resolved["diagnostic_work_kinds"] == ["RMD_RAW"]
        assert json.loads((output / "result.json").read_text())["status"] == "PASS"
        assert "llama_source" not in resolved


def test_explicit_llama_root_records_exact_source_and_host_commands() -> None:
    # Given: a clean source worktree.
    with tempfile.TemporaryDirectory(prefix="im2p-gemmini-source-") as temporary:
        source = Path(temporary) / "llama"
        clean_llama_source(source)
        output = Path(temporary) / "plan"
        head = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()

        # When: the official plan uses that source explicitly.
        result = run_script(BUILD, [*base_single_arguments(output), "--llama-root", str(source)])

        # Then: the resolved manifest records the real source root and actual commit.
        assert result.returncode == 0, result.stderr
        resolved = json.loads((output / "resolved-profile.json").read_text())
        assert resolved["llama_source"] == {"root": str(source.resolve()), "head": head}
        result_doc = json.loads((output / "result.json").read_text())
        assert result_doc["profiles"][0]["llama_source"] == resolved["llama_source"]

        host = run_script(BUILD, [
            *base_single_arguments(Path(temporary) / "host"), "--stage", "host-test",
            "--llama-root", str(source), "--dry-run",
        ])
        assert host.returncode == 0, host.stderr
        commands = json.loads(host.stdout)["profiles"][0]["commands"]
        configure = next(command for command in commands if command["arguments"][:2] == ["cmake", "-S"])
        assert f"-DIM2P_LLAMA_ROOT={source.resolve()}" in configure["arguments"]
        build_steps = [command["arguments"] for command in commands
                       if command["arguments"][:2] == ["cmake", "--build"]]
        assert build_steps == [
            ["cmake", "--build", str(Path(temporary) / "host" / "host-build")],
            ["cmake", "--build", str(Path(temporary) / "host" / "host-build"),
             "--target", "gemmini_hp1_run_aware_rtl"],
        ]
        rtl_test = next(command["arguments"] for command in commands
                        if command["arguments"][:2] == ["verilator", "--cc"])
        assert str(Path(temporary) / "host" / "host-build" / "gemmini-utils" /
                   "libggml-gemmini-utils.a") in rtl_test[rtl_test.index("-LDFLAGS") + 1]
        assert any(str(source.resolve() / "ggml/src/ggml-gemmini/residual/rmd/rmd-reference.cpp")
                   in command["arguments"] for command in commands)


def test_source_package_records_manifest_identity_and_rejects_failed_verification() -> None:
    # Given: a source-only llama tree in the export package layout.
    with tempfile.TemporaryDirectory(prefix="im2p-gemmini-package-") as temporary:
        base = Path(temporary)
        package = base / "package"
        source = package / "dependency/source/llama_cpp_gemmini"
        reference = source / "ggml/src/ggml-gemmini/residual/rmd/rmd-reference.cpp"
        reference.parent.mkdir(parents=True)
        reference.write_text("// candidate\n", encoding="utf-8")
        manifest = package / "source-manifest.json"
        lock = package / "dependency-lock.json"
        source_name = "dependency/source/llama_cpp_gemmini/ggml/src/ggml-gemmini/residual/rmd/rmd-reference.cpp"
        manifest.write_text(json.dumps({"schema_version": 1, "files": {source_name: {
            "bytes": reference.stat().st_size,
            "sha256": hashlib.sha256(reference.read_bytes()).hexdigest(),
        }}}), encoding="utf-8")
        head = "1" * 40
        lock.write_text(json.dumps({"repositories": {"llama_cpp_gemmini": {"head": head}}}), encoding="utf-8")
        output = base / "plan"

        # When: the official plan selects the verified package source tree.
        with patch("scripts.gemmini_build.verify_export", return_value=VerifyResult("PASS", 2), create=True):
            result = run(_parse_request([*base_single_arguments(output), "--llama-root", str(source)]))
            host = run(_parse_request([
                *base_single_arguments(base / "host"), "--stage", "host-test", "--dry-run",
                "--llama-root", str(source),
            ]))

        # Then: the package identity is recorded without calling its base commit the source HEAD.
        assert result["status"] == "PASS"
        identity = json.loads((output / "resolved-profile.json").read_text())["llama_source"]
        assert identity == {
            "root": str(source.resolve()), "base_head": head,
            "source_manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
            "dependency_lock_sha256": hashlib.sha256(lock.read_bytes()).hexdigest(),
        }
        assert json.loads((output / "result.json").read_text())["profiles"][0]["llama_source"] == identity
        host_doc = json.loads(json.dumps(host))
        assert host_doc["profiles"][0]["llama_source"] == identity
        configure = next(command for command in host_doc["profiles"][0]["commands"]
                         if command["arguments"][:2] == ["cmake", "-S"])
        assert f"-DIM2P_LLAMA_ROOT={source.resolve()}" in configure["arguments"]
        assert not (base / "host").exists()

        # When: package verification detects a mismatch before a second build.
        rejected = base / "rejected"
        with patch("scripts.gemmini_build.verify_export", side_effect=ExportError("source mismatch"), create=True):
            try:
                run(_parse_request([*base_single_arguments(rejected), "--llama-root", str(source)]))
            except BuildFailure as error:
                assert error.reason is FailureReason.VALIDATION
            else:
                raise AssertionError("unverified package accepted")

        # Then: no artifact is created for the unverified source.
        assert not rejected.exists()


def test_explicit_llama_root_rejects_wrong_and_dirty_sources_before_output() -> None:
    # Given: another repository and a newly dirty temporary source worktree.
    with tempfile.TemporaryDirectory(prefix="im2p-gemmini-invalid-source-") as temporary:
        base = Path(temporary)
        source = base / "llama"
        clean_llama_source(source)
        (source / "untracked.txt").write_text("dirty\n")
        for label, root in (("wrong", ROOT), ("dirty", source)):
            output = base / label

            # When: an explicit untrusted source is selected.
            result = run_script(BUILD, [*base_single_arguments(output), "--llama-root", str(root)])

            # Then: validation fails before creating an artifact.
            assert result.returncode != 0
            assert json.loads(result.stderr)["reason"] == "VALIDATION"
            assert not output.exists()


def test_explicit_llama_root_rejects_ancestor_output_before_write() -> None:
    # Given: export output is an existing parent of a clean explicit source.
    with tempfile.TemporaryDirectory(prefix="im2p-gemmini-source-overlap-") as temporary:
        output = Path(temporary)
        source = output / "llama"
        clean_llama_source(source)
        request = _parse_request([
            *base_single_arguments(output), "--stage", "export", "--llama-root", str(source),
        ])

        # When: the request is validated before any export artifact is written.
        try:
            _validate_request(request)
        except BuildFailure as error:
            assert error.reason is FailureReason.VALIDATION
        else:
            raise AssertionError("ancestor output accepted")

        # Then: the source and parent output retain only the original fixture files.
        assert not (output / "resolved-profile.json").exists()
        assert not (output / "export").exists()
        assert not subprocess.check_output(["git", "-C", str(source), "status", "--porcelain"], text=True)


def test_explicit_llama_root_rejects_nested_output_before_write() -> None:
    # Given: a plan output path nested inside a clean explicit source.
    with tempfile.TemporaryDirectory(prefix="im2p-gemmini-source-nested-") as temporary:
        source = Path(temporary) / "llama"
        clean_llama_source(source)
        output = source / "plan"
        request = _parse_request([*base_single_arguments(output), "--llama-root", str(source)])

        # When: the request is validated before planning.
        try:
            _validate_request(request)
        except BuildFailure as error:
            assert error.reason is FailureReason.VALIDATION
        else:
            raise AssertionError("nested output accepted")

        # Then: no artifact appears inside the source.
        assert not output.exists()
        assert not subprocess.check_output(["git", "-C", str(source), "status", "--porcelain"], text=True)


def test_existing_output_is_rejected() -> None:
    # Given: output directory already exists.
    with tempfile.TemporaryDirectory(prefix="im2p-gemmini-existing-") as temporary:
        output = Path(temporary)

        # When: plan stage receives it.
        result = run_script(BUILD, base_single_arguments(output))

        # Then: no existing content is reused or overwritten.
        assert result.returncode != 0
        assert json.loads(result.stderr)["reason"] == "OUTPUT_EXISTS"


def test_board_free_stages_validate_in_dry_run() -> None:
    # Given: every Mac-capable stage without a board.
    with tempfile.TemporaryDirectory(prefix="im2p-gemmini-board-free-") as temporary:
        base = Path(temporary)
        for stage in ("rtl", "test", "host-test", "export"):

            # When: stage is planned without tool execution.
            result = run_script(
                BUILD,
                [*base_single_arguments(base / stage), "--stage", stage, "--dry-run"],
            )

            # Then: board absence is accepted.
            assert result.returncode == 0, result.stderr
            plan = json.loads(result.stdout)
            assert plan["stage"] == stage
            profile = plan["profiles"][0]
            assert profile["controller_kind"] == "UPSTREAM_GEMMINI_WS"
            assert profile["backing_memory"] == "INTEGRATED"
            assert profile["cycle_scope"] == (
                "logical_work_accept_to_final_backing_write_completion"
            )
            assert profile["host_artifact_role"] == "HOST_COMMON_ORCHESTRATION"
            assert profile["host_audit_role"] == "PHYSICAL_HOST"
            assert profile["rmd_enabled"] is True
            assert profile["rmd_datapath"] == "NORMAL_HP1_SCALED"
            assert profile["rmd_raw"] is False
            assert profile["host_integer_block_multiply"] is False
            assert profile["work_kinds"] == ["DENSE_HP1_FINAL"]
            assert profile["diagnostic_work_kinds"] == ["RMD_RAW"]
            assert profile["selected_top"] == "IM2PGemminiWSHP1A8W8D16"
            if stage in ("rtl", "export"):
                commands = profile["commands"]
                overlay = commands[0]["arguments"]
                generator = commands[1]["arguments"]
                assert overlay[1].endswith("scripts/gemmini_vendor.py")
                assert overlay[-2] == "--overlay"
                assert overlay[-1].endswith("upstream-overlay")
                assert commands[1]["cwd"].endswith("src/gemmini")
                assert generator[1] == "-J-Xmx6G"
                assert generator[3].startswith("-Dim2p.gemmini.overlay=")
                assert "upstream-overlay" in generator[3]
                assert generator[4].startswith("runMain im2p.gemmini.ElaborateUpstreamWsHp1 ")
                assert "--a-bits 8 --w-bits 8 --dim 16" in generator[4]
                assert "--resolved-hardware" in generator[4]
                assert not any(argument.startswith("set ") for argument in generator)
            if stage == "test":
                commands = profile["commands"]
                assert commands[0]["arguments"][1].endswith("scripts/gemmini_vendor.py")
                assert commands[1]["cwd"].endswith("src/gemmini")
                test_command = commands[1]["arguments"]
                assert "-Dim2p.testProfile=a8w8-d16-hp1" in test_command
                assert "-Dim2p.scratchpadBankRows=4096" in test_command
                assert "-Dim2p.accumulatorRows=1024" in test_command
                assert any(argument.startswith("-Dim2p.resolvedHardware=") for argument in test_command)
                assert test_command[-2].startswith("-Dim2p.gemmini.overlay=")
                assert test_command[-1] == "test"
            if stage == "export":
                commands = profile["commands"]
                assert commands[1]["arguments"][0] == "sbt"
                assert commands[2]["arguments"][0] == "verilator"
                assert "create" in plan["handoff"]["arguments"]
            if stage in ("host-test", "export"):
                commands = profile["commands"]
                assert commands[1]["arguments"][0] == "sbt"
                assert commands[2]["arguments"][0] == "verilator"
                configure = next(
                    command for command in commands
                    if command["arguments"][:2] == ["cmake", "-S"]
                )
                assert "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON" in configure["arguments"]
                assert commands[-2]["arguments"][0] == "verilator"
                assert "--top-module" in commands[-2]["arguments"]
                assert "IM2PGemminiWSHP1A8W8D16" in commands[-2]["arguments"]
                assert "VIM2PGemminiWSHP1RtlTest" in commands[-2]["arguments"]
                rtl_sources = {Path(argument).name for argument in commands[-2]["arguments"]}
                assert {
                    "test_ws_rtl.cpp", "rmd_rtl_fixture.cpp", "bound_rmd_rtl_fixture.cpp",
                    "rmd-reference.cpp",
                } <= rtl_sources
                assert "-DGGML_GEMMINI_ENABLE_RMD=1" in commands[-2]["arguments"][
                    commands[-2]["arguments"].index("-CFLAGS") + 1
                ]
                flags = commands[-2]["arguments"][commands[-2]["arguments"].index("-CFLAGS") + 1]
                assert f"-I{ROOT / 'sim/ffi'}" in flags
                assert "-DIM2P_ACTIVATION_BITS=8" in flags
                assert commands[-1]["arguments"][0].endswith("VIM2PGemminiWSHP1RtlTest")
                audit = next(command for command in commands if "--role" in command["arguments"])
                assert audit["arguments"][audit["arguments"].index("--role") + 1] == (
                    "PHYSICAL_HOST"
                )
                assert audit["arguments"][audit["arguments"].index("--artifact") + 1].endswith(
                    "gemmini_hp1_host_orchestration"
                )
                assert "--command-log" in audit["arguments"]
                assert audit["arguments"][-2:] == [
                    "--out", str(base / stage / "host-audit.json")
                ]


def test_removed_standalone_top_is_rejected() -> None:
    with tempfile.TemporaryDirectory(prefix="im2p-gemmini-removed-top-") as temporary:
        output = Path(temporary) / "standalone"
        result = run_script(
            BUILD,
            [*base_single_arguments(output), "--stage", "rtl", "--top", "standalone", "--dry-run"],
        )
        assert result.returncode != 0
        assert not output.exists()


def test_darwin_hardware_stage_is_deferred_before_output() -> None:
    # Given: route requested on this Darwin host.
    with tempfile.TemporaryDirectory(prefix="im2p-gemmini-route-") as temporary:
        output = Path(temporary) / "route"

        # When: build validates platform boundary.
        result = run_script(BUILD, [*base_single_arguments(output), "--stage", "route"])

        # Then: platform is deferred before output or Vivado invocation.
        assert result.returncode != 0
        assert json.loads(result.stderr)["reason"] == "DEFERRED_PLATFORM"
        assert not output.exists()


def test_bash3_wrapper_forwards_arguments() -> None:
    # Given: wrapper and a dry-run selection.
    with tempfile.TemporaryDirectory(prefix="im2p-gemmini-wrapper-") as temporary:
        output = Path(temporary) / "wrapper"

        # When: Bash invokes the thin wrapper.
        completed = subprocess.run(
            ["bash", str(WRAPPER), *base_single_arguments(output), "--dry-run"],
            cwd=ROOT, text=True, capture_output=True, check=False,
        )

        # Then: Python CLI result is forwarded unchanged.
        assert completed.returncode == 0, completed.stderr
        assert json.loads(completed.stdout)["profiles"][0]["profile"] == "a8w8-d16-hp1"
        assert not output.exists()


def test_test_entrypoint_selects_real_rtl_stage() -> None:
    with tempfile.TemporaryDirectory(prefix="im2p-gemmini-test-entry-") as temporary:
        output = Path(temporary) / "test"
        result = run_script(TEST, [*base_single_arguments(output)[:-4], "--out", str(output), "--dry-run"])
        assert result.returncode == 0, result.stderr
        plan = json.loads(result.stdout)
        assert plan["stage"] == "test"
        commands = plan["profiles"][0]["commands"]
        assert commands[0]["arguments"][1].endswith("scripts/gemmini_vendor.py")
        assert commands[1]["arguments"][-1] == "test"
        assert not output.exists()


def test_rtl_stage_uses_detected_java_and_emitted_relative_filelist() -> None:
    # Given: tool doubles that require project-local JAVA_HOME and real emitted RTL.
    with tempfile.TemporaryDirectory(prefix="im2p-gemmini-tools-") as temporary:
        base = Path(temporary)
        tools = base / "tools"
        tools.mkdir()
        sbt = tools / "sbt"
        sbt.write_text(
            "#!/usr/bin/env python3\n"
            "import os, pathlib, shlex, sys\n"
            "if sys.platform == 'darwin':\n"
            "    assert pathlib.Path(os.environ['JAVA_HOME'], 'bin', 'java').is_file()\n"
            "    work_root = pathlib.Path(os.environ.get('IM2P_GEMMINI_WORK_ROOT', "
            "pathlib.Path.home() / 'aisa-lab/build/im2p-gemmini'))\n"
            "    pinned = pathlib.Path(work_root, 'deps', "
            "'firtool-1.62.0-macos-x64', 'org.chipsalliance', 'llvm-firtool', "
            "'macos-x64', 'bin')\n"
            "    assert pathlib.Path(os.environ['CHISEL_FIRTOOL_PATH']) == pinned\n"
            "assert pathlib.Path.cwd().name == 'gemmini'\n"
            "overlay_arg = next(arg for arg in sys.argv if arg.startswith('-Dim2p.gemmini.overlay='))\n"
            "assert pathlib.Path(overlay_arg.split('=', 1)[1]).is_dir()\n"
            "args = shlex.split(sys.argv[-1])\n"
            "out = pathlib.Path(args[args.index('--out') + 1])\n"
            "overlay = out.parent / 'upstream-overlay'\n"
            "assert len(tuple(overlay.rglob('*.scala'))) == 4\n"
            "out.mkdir(parents=True)\n"
            "(out / 'IM2PGemminiWSHP1A8W8D16.sv').write_text("
            "'module IM2PGemminiWSHP1A8W8D16; endmodule\\n')\n",
            encoding="utf-8",
        )
        verilator = tools / "verilator"
        verilator.write_text(
            "#!/usr/bin/env python3\n"
            "import pathlib, sys\n"
            "filelist = pathlib.Path(sys.argv[sys.argv.index('-F') + 1])\n"
            "assert filelist.read_text() == 'rtl/IM2PGemminiWSHP1A8W8D16.sv\\n'\n",
            encoding="utf-8",
        )
        sbt.chmod(0o755)
        verilator.chmod(0o755)
        output = base / "rtl-build"
        incompatible_firtool = tools / "incompatible-firtool"
        incompatible_firtool.mkdir()
        incompatible = incompatible_firtool / "firtool"
        incompatible.write_text("#!/bin/sh\necho incompatible firtool\n", encoding="utf-8")
        incompatible.chmod(0o755)
        environment = dict(os.environ)
        environment.pop("JAVA_HOME", None)
        environment["CHISEL_FIRTOOL_PATH"] = str(incompatible_firtool)
        environment["PATH"] = f"{tools}{os.pathsep}{environment['PATH']}"

        # When: actual orchestration runs instead of dry-run planning.
        completed = subprocess.run(
            [sys.executable, str(BUILD), *base_single_arguments(output), "--stage", "rtl"],
            cwd=ROOT, env=environment, text=True, capture_output=True, check=False,
        )

        # Then: both commands pass and filelist remains relocatable.
        diagnostics = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted((output / "logs").glob("*.log"))
        )
        assert completed.returncode == 0, completed.stderr + diagnostics
        result = json.loads((output / "result.json").read_text(encoding="utf-8"))
        assert result["status"] == "PASS"
        assert (output / "filelist.f").read_text(encoding="utf-8") == (
            "rtl/IM2PGemminiWSHP1A8W8D16.sv\n"
        )
        resolved = json.loads((output / "resolved-profile.json").read_text(encoding="utf-8"))
        assert resolved["controller_kind"] == "UPSTREAM_GEMMINI_WS"
        assert resolved["backing_memory"] == "INTEGRATED"
        assert resolved["cycle_scope"] == (
            "logical_work_accept_to_final_backing_write_completion"
        )
        assert resolved["selected_top"] == "IM2PGemminiWSHP1A8W8D16"
        lock = json.loads((output / "tool-lock.json").read_text(encoding="utf-8"))
        assert lock["tools"]["java"]["path"].endswith("/bin/java")
        assert lock["tools"]["firtool"]["sha256"]
        assert lock["host"]["system"]
        assert (output / "stage-rtl.json").is_file()


def test_export_does_not_reuse_a_different_top() -> None:
    # Given: existing RTL belongs to the old standalone diagnostic.
    with tempfile.TemporaryDirectory(prefix="im2p-gemmini-export-stale-") as temporary:
        output = Path(temporary) / "build"
        rtl = output / "rtl" / "IM2PGemminiHP1A8W8D16.sv"
        rtl.parent.mkdir(parents=True)
        rtl.write_text("module IM2PGemminiHP1A8W8D16; endmodule\n", encoding="utf-8")

        # When: the default integrated export is planned.
        result = run_script(
            BUILD,
            [*base_single_arguments(output), "--stage", "export", "--dry-run"],
        )

        # Then: integrated elaboration is retained instead of reusing stale RTL.
        assert result.returncode == 0, result.stderr
        commands = json.loads(result.stdout)["profiles"][0]["commands"]
        assert commands[0]["arguments"][1].endswith("scripts/gemmini_vendor.py")
        assert commands[1]["arguments"][-1].startswith(
            "runMain im2p.gemmini.ElaborateUpstreamWsHp1 "
        )
        assert commands[2]["arguments"][0] == "verilator"
        assert not (output / "upstream-overlay").exists()


def test_matrix_export_reuses_rtl_in_one_relocatable_handoff() -> None:
    # Given: a completed six-profile RTL output and only Verilator available.
    with tempfile.TemporaryDirectory(prefix="im2p-gemmini-export-matrix-") as temporary:
        base = Path(temporary)
        output = base / "matrix"
        tools = base / "tools"
        tools.mkdir()
        verilator = tools / "verilator"
        verilator.write_text(
            "#!/usr/bin/env python3\n"
            "import pathlib, sys\n"
            "if '--Mdir' in sys.argv:\n"
            "    root = pathlib.Path(sys.argv[sys.argv.index('--Mdir') + 1])\n"
            "    name = sys.argv[sys.argv.index('--prefix') + 1]\n"
            "    top = sys.argv[sys.argv.index('--top-module') + 1]\n"
            "    label = top.removeprefix('IM2PGemminiWSHP1')\n"
            "    bits, dim = int(label[1]), int(label.rsplit('D', 1)[1])\n"
            "    lanes = 9 if bits == 4 else 5\n"
            "    root.mkdir(parents=True, exist_ok=True)\n"
            "    binary = root / name\n"
            "    binary.write_text('#!/bin/sh\\necho \\\'integrated upstream WS HP1 RTL passed "
            "' + label + ' loops=1 load_execute_overlap=1\\\'\\n' + "
            "f'echo \\\'WS_RMD_RUNS_DIAGNOSTIC {label} run_callbacks=1 compact_exact=27 lanes={lanes} high_carry=1 "
            "compose_exact=54 merge_exact=54 negative_tests=7 missing_reject=1 duplicate_reject=1 "
            "missing_runs_reject=1 high_exponent_run=1 sparse_k=1 odd_k=1 stripes=3 slots=0,1,0\\\'\\n' + "
            "f'echo \\\'WS_RMD_BOUND_RUNS_DIAGNOSTIC bits={bits} DIM={dim} full_exact=27 pipeline_exact=27 "
            "dense_calls=4 runs_calls=1 stripes=3 slots=0,1,0 rollback=2 public_entry=1\\\'\\n' + "
            "'echo FLOW_UNIT_TEST_ONLY\\n')\n"
            "    binary.chmod(0o755)\n",
            encoding="utf-8",
        )
        verilator.chmod(0o755)
        for profile in EXPECTED_PROFILES:
            bits = profile[1]
            dim = profile.split("-d", maxsplit=1)[1].split("-", maxsplit=1)[0]
            top = f"IM2PGemminiWSHP1A{bits}W{bits}D{dim}"
            rtl = output / profile / "rtl" / f"{top}.sv"
            rtl.parent.mkdir(parents=True)
            rtl.write_text(f"module {top}; endmodule\n", encoding="utf-8")
        environment = dict(os.environ)
        environment["PATH"] = f"{tools}{os.pathsep}{environment['PATH']}"

        # When: export targets that existing matrix output.
        completed = subprocess.run(
            [
                sys.executable, str(BUILD),
                "--matrix", "a4w4,a8w8", "--dims", "16,32,64",
                "--scu", "hp1-left-shift", "--memory-contract-dir", str(CONTRACTS),
                "--stage", "export", "--out", str(output),
            ],
            cwd=ROOT, env=environment, text=True, capture_output=True, check=False,
        )

        # Then: RTL is reused and one verified board-free archive contains all profiles.
        diagnostics = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(output.rglob("*.log"))
        )
        assert completed.returncode == 0, completed.stderr + diagnostics
        result = json.loads((output / "result.json").read_text(encoding="utf-8"))
        assert result["status"] == "PASS"
        assert all(profile["status"] == "PASS" for profile in result["profiles"])
        assert (output / "export.tar.gz").is_file()
        handoff = json.loads((output / "export/result.json").read_text(encoding="utf-8"))
        assert handoff["board_included"] is False
        assert tuple(profile["profile"] for profile in handoff["profiles"]) == EXPECTED_PROFILES
        assert all(profile["status"] == "PASS" for profile in handoff["profiles"])
        assert all(not Path(profile["root"]).is_absolute() for profile in handoff["profiles"])
        assert (output / "export/SHA256SUMS").is_file()
        assert not (output / "export/filelist.f").exists()
        filelists = [Path(str(profile["filelist"])) for profile in handoff["profiles"]]
        assert len(filelists) == len(EXPECTED_PROFILES)
        assert all(not path.is_absolute() for path in filelists)
        assert all((output / "export" / path).is_file() for path in filelists)


def main() -> int:
    tests = (
        test_resolver_emits_six_exact_profiles,
        test_resolver_rejects_mixed_widths,
        test_resolver_rejects_non_ws_memory_contract,
        test_matrix_dry_run_is_sequential_and_side_effect_free,
        test_matrix_rejects_non_integer_dim,
        test_plan_writes_resolved_profile,
        test_explicit_llama_root_records_exact_source_and_host_commands,
        test_explicit_llama_root_rejects_wrong_and_dirty_sources_before_output,
        test_explicit_llama_root_rejects_ancestor_output_before_write,
        test_explicit_llama_root_rejects_nested_output_before_write,
        test_existing_output_is_rejected,
        test_board_free_stages_validate_in_dry_run,
        test_removed_standalone_top_is_rejected,
        test_darwin_hardware_stage_is_deferred_before_output,
        test_bash3_wrapper_forwards_arguments,
        test_test_entrypoint_selects_real_rtl_stage,
        test_rtl_stage_uses_detected_java_and_emitted_relative_filelist,
        test_export_does_not_reuse_a_different_top,
        test_matrix_export_reuses_rtl_in_one_relocatable_handoff,
    )
    for test in tests:
        test()
    print(f"GEMMINI BUILD CLI PASS ({len(tests)} tests)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
