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

import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Final

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
        assert json.loads((output / "result.json").read_text())["status"] == "PASS"


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
            if stage in ("rtl", "export"):
                generator = plan["profiles"][0]["commands"][0]["arguments"]
                assert generator[1] == "-J-Xmx6G"
                assert "midas_target_utils" in generator[3]
                assert "scalaVersion := \"2.13.12\"" in generator[3]
                assert "--a-bits 8 --w-bits 8 --dim 16" in generator[4]
                assert "--scratchpad-bank-rows 4096" in generator[4]
                assert "--accumulator-rows 1024" in generator[4]
            if stage == "test":
                test_command = plan["profiles"][0]["commands"][0]["arguments"]
                assert "-Dim2p.testProfile=a8w8-d16-hp1" in test_command
                assert "-Dim2p.scratchpadBankRows=4096" in test_command
                assert "-Dim2p.accumulatorRows=1024" in test_command
                assert "midas_target_utils" in test_command[-2]
                assert test_command[-1] == "testOnly im2p.gemmini.StandaloneTopSpec"
            if stage == "export":
                commands = plan["profiles"][0]["commands"]
                assert commands[0]["arguments"][0] == "sbt"
                assert commands[1]["arguments"][0] == "verilator"
                assert "create" in plan["handoff"]["arguments"]
            if stage == "host-test":
                commands = plan["profiles"][0]["commands"]
                assert commands[0]["arguments"][0] == "sbt"
                assert commands[1]["arguments"][0] == "verilator"
                assert commands[-2]["arguments"][0] == "verilator"
                assert commands[-1]["arguments"][0].endswith("VIM2PGemminiHP1RtlTest")
                audit = next(command for command in commands if "--role" in command["arguments"])
                assert audit["arguments"][-4:] == [
                    "--role", "HOST_COMMON", "--out", str(base / stage / "host-audit.json")
                ]


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
        assert plan["profiles"][0]["commands"][0]["arguments"][-1] == (
            "testOnly im2p.gemmini.StandaloneTopSpec"
        )
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
            "    pinned = pathlib.Path(os.environ['IM2P_GEMMINI_WORK_ROOT'], 'deps', "
            "'firtool-1.62.0-macos-x64', 'org.chipsalliance', 'llvm-firtool', "
            "'macos-x64', 'bin')\n"
            "    assert pathlib.Path(os.environ['CHISEL_FIRTOOL_PATH']) == pinned\n"
            "args = shlex.split(sys.argv[-1])\n"
            "out = pathlib.Path(args[args.index('--out') + 1])\n"
            "out.mkdir(parents=True)\n"
            "(out / 'StandaloneTop.sv').write_text('module StandaloneTop; endmodule\\n')\n",
            encoding="utf-8",
        )
        verilator = tools / "verilator"
        verilator.write_text(
            "#!/usr/bin/env python3\n"
            "import pathlib, sys\n"
            "filelist = pathlib.Path(sys.argv[sys.argv.index('-F') + 1])\n"
            "assert filelist.read_text() == 'rtl/StandaloneTop.sv\\n'\n",
            encoding="utf-8",
        )
        sbt.chmod(0o755)
        verilator.chmod(0o755)
        firtool = (
            base / "work-root" / "deps" / "firtool-1.62.0-macos-x64"
            / "org.chipsalliance" / "llvm-firtool" / "macos-x64" / "bin" / "firtool"
        )
        firtool.parent.mkdir(parents=True)
        firtool.write_text("pinned firtool fixture\n", encoding="utf-8")
        firtool.chmod(0o755)
        output = base / "rtl-build"
        environment = dict(os.environ)
        environment.pop("JAVA_HOME", None)
        environment["PATH"] = f"{tools}{os.pathsep}{environment['PATH']}"
        environment["IM2P_GEMMINI_WORK_ROOT"] = str(base / "work-root")

        # When: actual orchestration runs instead of dry-run planning.
        completed = subprocess.run(
            [sys.executable, str(BUILD), *base_single_arguments(output), "--stage", "rtl"],
            cwd=ROOT, env=environment, text=True, capture_output=True, check=False,
        )

        # Then: both commands pass and filelist remains relocatable.
        assert completed.returncode == 0, completed.stderr
        result = json.loads((output / "result.json").read_text(encoding="utf-8"))
        assert result["status"] == "PASS"
        assert (output / "filelist.f").read_text(encoding="utf-8") == "rtl/StandaloneTop.sv\n"
        lock = json.loads((output / "tool-lock.json").read_text(encoding="utf-8"))
        assert lock["tools"]["java"]["path"].endswith("/bin/java")
        assert lock["tools"]["firtool"]["sha256"]
        assert lock["host"]["system"]
        assert (output / "stage-rtl.json").is_file()


def test_matrix_export_reuses_rtl_in_one_relocatable_handoff() -> None:
    # Given: a completed six-profile RTL output and only Verilator available.
    with tempfile.TemporaryDirectory(prefix="im2p-gemmini-export-matrix-") as temporary:
        base = Path(temporary)
        output = base / "matrix"
        tools = base / "tools"
        tools.mkdir()
        verilator = tools / "verilator"
        verilator.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        verilator.chmod(0o755)
        for profile in EXPECTED_PROFILES:
            rtl = output / profile / "rtl" / "StandaloneTop.sv"
            rtl.parent.mkdir(parents=True)
            rtl.write_text(f"module {profile.replace('-', '_')}; endmodule\n", encoding="utf-8")
        environment = dict(os.environ)
        environment["PATH"] = f"{tools}{os.pathsep}/usr/bin:/bin"

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
        assert completed.returncode == 0, completed.stderr
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
        filelist = (output / "export/filelist.f").read_text(encoding="utf-8").splitlines()
        assert len(filelist) == len(EXPECTED_PROFILES)
        assert all(not Path(name).is_absolute() for name in filelist)
        assert all((output / "export" / name).is_file() for name in filelist)


def main() -> int:
    tests = (
        test_resolver_emits_six_exact_profiles,
        test_resolver_rejects_mixed_widths,
        test_resolver_rejects_non_ws_memory_contract,
        test_matrix_dry_run_is_sequential_and_side_effect_free,
        test_matrix_rejects_non_integer_dim,
        test_plan_writes_resolved_profile,
        test_existing_output_is_rejected,
        test_board_free_stages_validate_in_dry_run,
        test_darwin_hardware_stage_is_deferred_before_output,
        test_bash3_wrapper_forwards_arguments,
        test_test_entrypoint_selects_real_rtl_stage,
        test_rtl_stage_uses_detected_java_and_emitted_relative_filelist,
        test_matrix_export_reuses_rtl_in_one_relocatable_handoff,
    )
    for test in tests:
        test()
    print(f"GEMMINI BUILD CLI PASS ({len(tests)} tests)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
