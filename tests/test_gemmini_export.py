#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
#
# ─── How to run ───
# 1. Install uv (if not installed):
#      curl -LsSf https://astral.sh/uv/install.sh | sh
# 2. Run directly (no venv, no pip install needed):
#      uv run tests/test_gemmini_export.py
# 3. Or make executable and run:
#      chmod +x tests/test_gemmini_export.py && ./tests/test_gemmini_export.py
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
from typing import TypedDict
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.gemmini_export import (
    RMD_HOST_SOURCES,
    RMD_LLAMA_SOURCES,
    ExportError,
    ExportRequest,
    approved_head,
    create_export,
    extract_export,
    require_clean_tracked_state,
    require_runtime_log,
    verify_export,
)


class CommandFixture(TypedDict):
    arguments: list[str]
    cwd: str


def write(path: Path, data: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, bytes):
        path.write_bytes(data)
    else:
        path.write_text(data, encoding="utf-8")


def refresh_sums(root: Path) -> None:
    rows = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name != "SHA256SUMS":
            rows.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(root).as_posix()}\n")
    (root / "SHA256SUMS").write_text("".join(rows), encoding="utf-8")


def source_fixture(root: Path) -> None:
    write(root / "src/gemmini/src/main/scala/im2p/gemmini/SCU.scala", "class SCU\n")
    write(root / "src/gemmini/UPSTREAM.lock.json", "{}\n")
    write(root / "src/gemmini/upstream/LICENSE", "upstream license\n")
    write(root / "config/gemmini_hp1_profiles.json", "{}\n")
    write(root / "config/gemmini_host_memory_contracts/a4w4-d16-hp1.json", "{}\n")
    write(root / "fpga/gemmini_hp1/flow/vivado_flow.tcl", "# flow\n")
    write(root / "fpga/gemmini_hp1/boards/board.schema.json", "{}\n")
    write(root / "fpga/gemmini_hp1/host/uart.cpp", "int host_transport;\n")
    write(root / "fpga/gemmini_hp1/host/uart.hpp", "extern int host_transport;\n")
    write(root / "scripts/gemmini_build.py", "# build\n")
    write(root / "models/forbidden.gguf", b"model")
    write(root / "src/gemmini/target/forbidden.class", b"class")
    write(root / "src/gemmini/test_run_dir/forbidden.sv", "module generated_test; endmodule\n")
    write(root / "src/gemmini/.bloop/forbidden.json", "{}\n")
    (root / "src/gemmini/external-link").symlink_to(Path("/tmp"))




def integrated_source_fixture(root: Path, rmd: bool = False) -> None:
    source_fixture(root)
    write(root / "src/gemmini/patches/0001-overlay.patch", "diff --git a/a b/a\n")
    write(root / "src/gemmini/upstream/src/main/scala/gemmini/MeshWithDelays.scala", "class MeshWithDelays\n")
    write(root / "src/gemmini/control/src/main/scala/im2p/gemmini/UpstreamWsHp1Top.scala", "class UpstreamWsHp1Top\n")
    write(root / "src/gemmini/control/src/main/scala/im2p/gemmini/UpstreamWsMemory.scala", "class UpstreamWsMemory\n")
    write(root / "src/gemmini/control/src/main/scala/im2p/gemmini/ScaleBackingLoader.scala", "class ScaleBackingLoader\n")
    write(root / "src/gemmini/src/main/scala/im2p/gemmini/BackingMemoryPort.scala", "class BackingMemoryPort\n")
    write(root / "src/gemmini/src/main/scala/im2p/gemmini/SCU.scala", "class SCU\n")
    write(root / "fpga/gemmini_hp1/host/test_ws_rtl.cpp", "int main() { return 0; }\n")
    if rmd:
        for name in RMD_HOST_SOURCES:
            write(root / "fpga/gemmini_hp1/host" / name, "// RMD source fixture\n")
        for name in RMD_LLAMA_SOURCES:
            write(root.parent / "llama.cpp-gemmini" / name, "// llama source fixture\n")
        write(root.parent / "llama.cpp-gemmini/ggml/include/ggml.h", "// header fixture\n")


def integrated_build_fixture(root: Path, rmd: bool = False) -> None:
    profile = root / "a8w8-d16-hp1"
    top = "IM2PGemminiWSHP1A8W8D16"
    write(profile / "resolved-profile.json", json.dumps({
        "profile": "a8w8-d16-hp1",
        "selected_top": top,
        "controller_kind": "UPSTREAM_GEMMINI_WS",
        "backing_memory": "INTEGRATED",
        "cycle_scope": "logical_work_accept_to_final_backing_write_completion",
        "host_artifact_role": "HOST_COMMON_ORCHESTRATION",
        "host_audit_role": "PHYSICAL_HOST",
        **({"rmd_raw": True, "rmd_numerical_revision": "rmd-raw-k32-cpu-compose-v1",
            "work_kinds": ["DENSE_HP1_FINAL", "RMD_RAW"]} if rmd else {}),
    }) + "\n")
    write(profile / "rtl" / f"{top}.sv", f"module {top}; endmodule\n")
    write(profile / "filelist.f", f"rtl/{top}.sv\n")
    write(profile / "upstream-overlay/src/main/scala/gemmini/LoadController.scala", "class LoadController\n")
    artifact = profile / "host-build/gemmini_hp1_host_orchestration"
    write(artifact, b"host common")
    command_log = profile / "host-build/CMakeFiles/gemmini_hp1_host_orchestration.dir/link.txt"
    write(command_log, "c++ host.o -o gemmini_hp1_host_orchestration\n")
    audit = profile / "host-audit.json"
    write(audit, json.dumps({
        "status": "PASS", "reason": None, "role": "PHYSICAL_HOST",
        "simulator_markers": [], "physical_operations": 0,
        "artifact": str(artifact), "artifact_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
    }) + "\n")
    cwd = str(root)
    commands: list[CommandFixture] = [
        {"arguments": ["verilator", "--top-module", top, str(root / "source/test_ws_rtl.cpp")], "cwd": cwd},
        {"arguments": [sys.executable, "gemmini_audit_host.py", "--artifact", str(artifact),
                       "--role", "PHYSICAL_HOST", "--command-log", str(command_log),
                       "--out", str(audit)], "cwd": cwd},
        {"arguments": [str(profile / "rtl-test-obj/VIM2PGemminiWSHP1RtlTest")], "cwd": cwd},
    ]
    if rmd:
        archives = [profile / "host-build" / name for name in (
            "libgemmini_hp1_host_common.a", "libgemmini_hp1_ggml_numeric.a",
        )]
        for archive in archives:
            write(archive, b"host archive fixture")
        commands[0]["arguments"].extend([
            str(root / "source/rmd_rtl_fixture.cpp"),
            str(root / "source/bound_rmd_rtl_fixture.cpp"),
            "-LDFLAGS", " ".join(str(archive) for archive in archives),
        ])
    write(profile / "rtl-test-obj/VIM2PGemminiWSHP1RtlTest", b"rtl test")
    logs = [profile / f"logs/{index:02d}.log" for index in range(1, 4)]
    for log in logs:
        write(log, "command passed\n")
    write(logs[-1], "integrated upstream WS HP1 RTL passed A8W8D16 loops=11 load_execute_overlap=7\n")
    if rmd:
        with logs[-1].open("a", encoding="utf-8") as stream:
            _ = stream.write(
                "WS_RMD A8W8D16 rtl_callbacks=12 raw_exact=36 lanes=5 high_carry=1 "
                "compose_exact=54 merge_exact=54 negative_tests=6 missing_reject=1 "
                "duplicate_reject=1 overflow_reject=1 sparse_k=1 odd_k=1 stripes=3 slots=0,1,0\n"
                "WS_RMD_BOUND bits=8 DIM=16 full_exact=27 pipeline_exact=27 dense_calls=6 "
                "raw_calls=18 stripes=3 slots=0,1,0 rollback=2 public_entry=1\n"
            )
    results = [{**command, "returncode": 0, "reason": None, "log": str(log)}
               for command, log in zip(commands, logs)]
    write(root / "stage-host-test.json", json.dumps({
        "schema_version": 1, "stage": "host-test", "status": "PASS", "execution": "sequential",
        "profiles": [{
            "profile": "a8w8-d16-hp1", "selected_top": top,
            "controller_kind": "UPSTREAM_GEMMINI_WS", "backing_memory": "INTEGRATED",
            "cycle_scope": "logical_work_accept_to_final_backing_write_completion",
            "host_artifact_role": "HOST_COMMON_ORCHESTRATION", "host_audit_role": "PHYSICAL_HOST",
            "resolved_profile": str(profile / "resolved-profile.json"),
            "status": "PASS", "reason": None, "commands": commands, "command_results": results,
        }],
    }) + "\n")


def test_export_round_trip() -> None:
    # Given: source plus generated RTL contain safe files and forbidden artifacts.
    with tempfile.TemporaryDirectory(prefix="gemmini-export-test-") as directory:
        base = Path(directory)
        source, build, output = base / "source", base / "build", base / "package"
        integrated_source_fixture(source)
        integrated_build_fixture(build)
        write(build / "a8w8-d16-hp1/forbidden.gguf", b"model")
        write(build / "a8w8-d16-hp1/rtl/forbidden.v", bytes.fromhex("cafebabf") + b"payload")
        (build / "a8w8-d16-hp1/rtl-link").symlink_to(Path("/tmp"))
        board_dir = base / "board"
        write(board_dir / "pins.xdc", "set_property PACKAGE_PIN A1 [get_ports core_clk]\n")
        board = board_dir / "board.json"
        board.write_text(json.dumps({"schema_version": 1, "board_id": "test-board", "part": "xc-test",
            "top": "IM2PGemminiWSHP1A8W8D16", "clock": {"port": "core_clk", "frequency_mhz": 100},
            "xdc": ["pins.xdc"], "pin_constraints": "pins.xdc", "memory_interface": "test-memory",
            "deployment_artifact": "bit"}), encoding="utf-8")

        # When: package is created and extracted at a different path.
        result = create_export(ExportRequest(source, build, output, board))
        relocated = base / "relocated"
        extracted = extract_export(result.archive, relocated)

        # Then: hashes and relative RTL closure survive relocation; unsafe data stays out.
        assert result.relocation_verified and verify_export(output).status == "PASS"
        assert extracted.status == "PASS"
        profile = json.loads((output / "profile-manifest.json").read_text(encoding="utf-8"))["profiles"][0]
        filelist_path = output / profile["filelist"]
        filelist = filelist_path.read_text(encoding="utf-8").splitlines()
        assert filelist and all(not Path(line).is_absolute() for line in filelist)
        assert all((filelist_path.parent / line).is_file() for line in filelist)
        names = {path.relative_to(output).as_posix() for path in output.rglob("*")}
        assert not any("forbidden" in name or "external-link" in name or "rtl-link" in name for name in names)
        assert "source/fpga/gemmini_hp1/host/uart.cpp" in names
        assert "source/fpga/gemmini_hp1/host/uart.hpp" in names
        assert "generated/a8w8-d16-hp1/resolved-profile.json" in names
        assert (output / "SHA256SUMS").is_file()
        resolved_board = json.loads((output / "board/resolved-board.json").read_text(encoding="utf-8"))
        assert resolved_board["schema_version"] == 1
        assert resolved_board["xdc"] == ["board/constraints/00-pins.xdc"]
        source_only = base / "source-only"
        create_export(ExportRequest(source, None, source_only, None))
        source_result = json.loads((source_only / "result.json").read_text(encoding="utf-8"))
        assert source_result["export_kind"] == "SOURCE_ONLY" and source_result["profiles"] == []


def test_removed_standalone_export_is_rejected() -> None:
    with tempfile.TemporaryDirectory(prefix="gemmini-retired-export-") as directory:
        base = Path(directory)
        source, build = base / "source", base / "build"
        integrated_source_fixture(source)
        integrated_build_fixture(build)
        manifest = build / "a8w8-d16-hp1/resolved-profile.json"
        data = json.loads(manifest.read_text(encoding="utf-8"))
        data.update(controller_kind="STANDALONE_DIAGNOSTIC", backing_memory="LOCAL_DIAGNOSTIC",
                    cycle_scope="fragment_accept_to_final_accumulator_write_completion",
                    selected_top="IM2PGemminiHP1A8W8D16")
        manifest.write_text(json.dumps(data), encoding="utf-8")
        try:
            create_export(ExportRequest(source, build, base / "package", None))
        except ExportError as error:
            assert "unsupported controller kind" in str(error)
        else:
            raise AssertionError("retired standalone export accepted")


def test_integrated_export_preserves_verified_profile_closure() -> None:
    # Given: an integrated resolved profile and its production source closure.
    with tempfile.TemporaryDirectory(prefix="gemmini-integrated-export-") as directory:
        base = Path(directory)
        source, build, output = base / "source", base / "build", base / "package"
        integrated_source_fixture(source)
        integrated_build_fixture(build)

        # When: the production integrated package is created and relocated.
        result = create_export(ExportRequest(source, build, output, None))
        extracted = extract_export(result.archive, base / "relocated")

        # Then: the resolved metadata and complete integrated closure are retained.
        manifest = json.loads((output / "profile-manifest.json").read_text(encoding="utf-8"))
        package_result = json.loads((output / "result.json").read_text(encoding="utf-8"))
        profile = manifest["profiles"][0]
        assert result.relocation_verified and extracted.status == "PASS"
        assert profile == package_result["profiles"][0]
        assert profile["selected_top"] == "IM2PGemminiWSHP1A8W8D16"
        assert profile["controller_kind"] == "UPSTREAM_GEMMINI_WS"
        assert profile["backing_memory"] == "INTEGRATED"
        assert profile["host_artifact_role"] == "HOST_COMMON_ORCHESTRATION"
        assert profile["host_audit_role"] == "PHYSICAL_HOST"
        assert profile["cycle_scope"] == "logical_work_accept_to_final_backing_write_completion"
        assert profile["runtime_status"] == profile["no_sim_host_artifact"] == "PASS"
        assert profile["host_test_result"] == "generated/stage-host-test.json"
        assert profile["host_audit"] == "generated/a8w8-d16-hp1/host-audit.json"
        assert profile["runtime_log"] == "generated/a8w8-d16-hp1/logs/03.log"
        assert profile["host_artifact_sha256"] == hashlib.sha256(b"host common").hexdigest()
        assert profile["rtl_test_artifact_sha256"] == hashlib.sha256(b"rtl test").hexdigest()
        assert profile["host_command_log"].endswith(
            "host-build/CMakeFiles/gemmini_hp1_host_orchestration.dir/link.txt"
        )
        assert profile["host_command_log_sha256"] == hashlib.sha256(
            b"c++ host.o -o gemmini_hp1_host_orchestration\n",
        ).hexdigest()
        assert profile["filelist"] == "generated/a8w8-d16-hp1/filelist.f"
        assert package_result["route"] == package_result["bitstream"] == "NOT_RUN"
        assert not (output / "filelist.f").exists()
        profile_filelist = output / profile["filelist"]
        assert profile_filelist.read_text(encoding="utf-8").splitlines() == [
            "rtl/IM2PGemminiWSHP1A8W8D16.sv",
        ]
        assert (output / "source/src/gemmini/patches/0001-overlay.patch").is_file()
        assert (output / "source/src/gemmini/upstream/src/main/scala/gemmini/MeshWithDelays.scala").is_file()
        assert (output / "source/src/gemmini/control/src/main/scala/im2p/gemmini/ScaleBackingLoader.scala").is_file()
        assert (output / "source/fpga/gemmini_hp1/host/test_ws_rtl.cpp").is_file()
        assert (output / "source/src/gemmini/UPSTREAM.lock.json").is_file()
        assert (output / "generated/a8w8-d16-hp1/upstream-overlay/src/main/scala/gemmini/LoadController.scala").is_file()


def test_rmd_profile_requires_rmd_runtime_evidence() -> None:
    # Given: dense runtime passed but the selected profile advertises RMD raw.
    profile = {"profile": "a8w8-d16-hp1", "rmd_raw": True}
    dense = "integrated upstream WS HP1 RTL passed A8W8D16 loops=11 load_execute_overlap=7\n"

    # When: export evaluates that profile's runtime log.
    try:
        require_runtime_log(profile, dense)
    except ExportError as error:
        # Then: dense PASS cannot authorize an RMD capability claim.
        assert "RMD" in str(error)
    else:
        raise AssertionError("RMD export accepted dense-only execution")


def test_dense_profile_retains_dense_runtime_contract() -> None:
    # Given: old resolved profiles omitted RMD capability.
    dense = "integrated upstream WS HP1 RTL passed A8W8D16 loops=11 load_execute_overlap=7\n"

    # When: export validates the original dense contract.
    require_runtime_log({"profile": "a8w8-d16-hp1"}, dense)

    # Then: an explicit disabled capability behaves identically.
    require_runtime_log({"profile": "a8w8-d16-hp1", "rmd_raw": False}, dense)


def test_rmd_export_retains_runtime_and_source_closure() -> None:
    with tempfile.TemporaryDirectory(prefix="gemmini-rmd-export-") as directory:
        base = Path(directory)
        source, build, output = base / "source", base / "build", base / "package"
        integrated_source_fixture(source, rmd=True)
        integrated_build_fixture(build, rmd=True)
        with patch.dict(os.environ, {"IM2P_WORKSPACE_ROOT": str(base)}):
            result = create_export(ExportRequest(source, build, output, None))
        assert extract_export(result.archive, base / "relocated").status == "PASS"
        manifest = json.loads((output / "profile-manifest.json").read_text(encoding="utf-8"))
        assert manifest["profiles"][0]["rmd_runtime_status"] == "PASS"
        assert (output / "dependency/source/llama_cpp_gemmini/ggml/src/ggml.c").is_file()
        assert not tuple(output.rglob("*.a"))
        runtime = output / "generated/a8w8-d16-hp1/logs/03.log"
        write(runtime, runtime.read_text(encoding="utf-8").replace("public_entry=1", "public_entry=0"))
        refresh_sums(output)
        try:
            _ = verify_export(output)
        except ExportError as error:
            assert "RMD public dispatch" in str(error)
        else:
            raise AssertionError("RMD export accepted incomplete public dispatch after relocation")


def test_integrated_export_rejects_incomplete_or_mixed_top_metadata() -> None:
    # Given: a complete integrated build fixture.
    with tempfile.TemporaryDirectory(prefix="gemmini-integrated-export-invalid-") as directory:
        base = Path(directory)
        source, build = base / "source", base / "build"
        integrated_source_fixture(source)
        integrated_build_fixture(build)
        manifest = build / "a8w8-d16-hp1" / "resolved-profile.json"

        # When: required top metadata is removed or changed to the diagnostic top.
        invalid_documents = (
            {"profile": "a8w8-d16-hp1"},
            {**json.loads(manifest.read_text(encoding="utf-8")), "selected_top": "IM2PGemminiHP1A8W8D16"},
        )

        # Then: neither ambiguity nor a diagnostic top can become a production export.
        for index, document in enumerate(invalid_documents):
            manifest.write_text(json.dumps(document) + "\n", encoding="utf-8")
            try:
                create_export(ExportRequest(source, build, base / f"package-{index}", None))
            except ExportError as error:
                assert str(error)
            else:
                raise AssertionError("invalid integrated profile was exported")
        integrated_build_fixture(build)
        write(build / "a4w4-d16-hp1" / "resolved-profile.json", json.dumps({
            "profile": "a4w4-d16-hp1",
            "selected_top": "IM2PGemminiHP1A4W4D16",
            "controller_kind": "STANDALONE_DIAGNOSTIC",
            "backing_memory": "LOCAL_DIAGNOSTIC",
            "cycle_scope": "fragment_accept_to_final_accumulator_write_completion",
        }) + "\n")
        try:
            create_export(ExportRequest(source, build, base / "package-mixed", None))
        except ExportError as error:
            assert str(error)
        else:
            raise AssertionError("mixed integrated and diagnostic profiles were exported")


def test_integrated_export_requires_passing_runtime_and_no_sim_evidence() -> None:
    # Given: valid integrated sources, RTL, host runtime result, and host audit.
    with tempfile.TemporaryDirectory(prefix="gemmini-integrated-export-gates-") as directory:
        base = Path(directory)
        source, build = base / "source", base / "build"
        integrated_source_fixture(source)
        integrated_build_fixture(build)
        stage = build / "stage-host-test.json"

        # Then: missing runtime evidence cannot inherit PASS from generated RTL.
        stage.unlink()
        try:
            create_export(ExportRequest(source, build, base / "missing-runtime", None))
        except ExportError as error:
            assert "host-test" in str(error)
        else:
            raise AssertionError("integrated export accepted missing runtime evidence")

        # Then: a failed command blocks integrated export even if summary says PASS.
        integrated_build_fixture(build)
        document = json.loads(stage.read_text(encoding="utf-8"))
        document["profiles"][0]["command_results"][-1]["returncode"] = 1
        stage.write_text(json.dumps(document) + "\n", encoding="utf-8")
        try:
            create_export(ExportRequest(source, build, base / "failed-runtime", None))
        except ExportError as error:
            assert "command" in str(error)
        else:
            raise AssertionError("integrated export accepted failed runtime command")

        integrated_build_fixture(build)
        document = json.loads(stage.read_text(encoding="utf-8"))
        document["profiles"][0]["command_results"][-1]["returncode"] = False
        stage.write_text(json.dumps(document) + "\n", encoding="utf-8")
        try:
            create_export(ExportRequest(source, build, base / "boolean-returncode", None))
        except ExportError as error:
            assert "command" in str(error)
        else:
            raise AssertionError("integrated export accepted boolean return code")

        integrated_build_fixture(build)
        document = json.loads(stage.read_text(encoding="utf-8"))
        spoofed = "/evil/VIM2PGemminiWSHP1RtlTest"
        document["profiles"][0]["commands"][-1]["arguments"][0] = spoofed
        document["profiles"][0]["command_results"][-1]["arguments"][0] = spoofed
        stage.write_text(json.dumps(document) + "\n", encoding="utf-8")
        try:
            create_export(ExportRequest(source, build, base / "spoofed-runtime", None))
        except ExportError as error:
            assert "runtime artifact" in str(error)
        else:
            raise AssertionError("integrated export accepted spoofed runtime path")

        integrated_build_fixture(build)
        command_log = build / "a8w8-d16-hp1/host-build/CMakeFiles/gemmini_hp1_host_orchestration.dir/link.txt"
        command_log.unlink()
        try:
            create_export(ExportRequest(source, build, base / "missing-command-log", None))
        except ExportError as error:
            assert "command log" in str(error)
        else:
            raise AssertionError("integrated export accepted missing host command log")

        integrated_build_fixture(build)
        document = json.loads(stage.read_text(encoding="utf-8"))
        malformed = ["verilator", "test_ws_rtl.cpp", "--top-module"]
        document["profiles"][0]["commands"][0]["arguments"] = malformed
        document["profiles"][0]["command_results"][0]["arguments"] = malformed
        stage.write_text(json.dumps(document) + "\n", encoding="utf-8")
        try:
            create_export(ExportRequest(source, build, base / "malformed-option", None))
        except ExportError as error:
            assert "option" in str(error)
        else:
            raise AssertionError("integrated export accepted malformed command option")

        integrated_build_fixture(build)
        runtime_log = build / "a8w8-d16-hp1/logs/03.log"
        runtime_log.write_text("runtime returned zero without success marker\n", encoding="utf-8")
        try:
            create_export(ExportRequest(source, build, base / "empty-runtime-log", None))
        except ExportError as error:
            assert "runtime log" in str(error)
        else:
            raise AssertionError("integrated export accepted unproven runtime log")

        integrated_build_fixture(build)
        audit = build / "a8w8-d16-hp1/host-audit.json"
        audit_document = json.loads(audit.read_text(encoding="utf-8"))
        audit_document["artifact_sha256"] = "0" * 64
        audit.write_text(json.dumps(audit_document) + "\n", encoding="utf-8")
        try:
            create_export(ExportRequest(source, build, base / "wrong-artifact", None))
        except ExportError as error:
            assert "artifact" in str(error)
        else:
            raise AssertionError("integrated export accepted wrong audited artifact")

        integrated_build_fixture(build)
        audit = build / "a8w8-d16-hp1/host-audit.json"
        audit_document = json.loads(audit.read_text(encoding="utf-8"))
        audit_document["physical_operations"] = False
        audit.write_text(json.dumps(audit_document) + "\n", encoding="utf-8")
        try:
            create_export(ExportRequest(source, build, base / "boolean-physical-operations", None))
        except ExportError as error:
            assert "host audit" in str(error)
        else:
            raise AssertionError("integrated export accepted boolean physical operation count")

        # Then: simulator-free host audit must itself pass.
        integrated_build_fixture(build)
        audit = build / "a8w8-d16-hp1/host-audit.json"
        audit.write_text(json.dumps({"status": "FAIL", "role": "HOST_COMMON",
                                     "simulator_markers": ["Verilated"], "physical_operations": 0}) + "\n",
                         encoding="utf-8")
        try:
            create_export(ExportRequest(source, build, base / "failed-audit", None))
        except ExportError as error:
            assert "host audit" in str(error)
        else:
            raise AssertionError("integrated export accepted failed no-SIM audit")

        # Then: duplicate selected-top definitions in profile filelist are ambiguous.
        integrated_build_fixture(build)
        top = "IM2PGemminiWSHP1A8W8D16"
        write(build / "a8w8-d16-hp1/rtl/duplicate.sv", f"module {top}; endmodule\n")
        write(build / "a8w8-d16-hp1/filelist.f", f"rtl/{top}.sv\nrtl/duplicate.sv\n")
        try:
            create_export(ExportRequest(source, build, base / "ambiguous-top", None))
        except ExportError as error:
            assert "exactly one" in str(error)
        else:
            raise AssertionError("integrated export accepted ambiguous selected top")

        integrated_build_fixture(build)
        write(build / "a8w8-d16-hp1/rtl/duplicate.sv", "module SharedHelper; endmodule\n")
        top_rtl = build / f"a8w8-d16-hp1/rtl/{top}.sv"
        top_rtl.write_text(top_rtl.read_text(encoding="utf-8") + "module SharedHelper; endmodule\n",
                           encoding="utf-8")
        write(build / "a8w8-d16-hp1/filelist.f", f"rtl/{top}.sv\nrtl/duplicate.sv\n")
        try:
            create_export(ExportRequest(source, build, base / "duplicate-helper", None))
        except ExportError as error:
            assert "duplicate RTL module" in str(error)
        else:
            raise AssertionError("integrated export accepted duplicate helper module")


def test_packaged_integrated_evidence_cannot_change_matrix_or_profile_paths() -> None:
    with tempfile.TemporaryDirectory(prefix="gemmini-integrated-export-package-gates-") as directory:
        base = Path(directory)
        source, build, output = base / "source", base / "build", base / "package"
        integrated_source_fixture(source)
        integrated_build_fixture(build)
        create_export(ExportRequest(source, build, output, None))
        evidence_path = output / "generated/stage-host-test.json"
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        evidence["execution"] = "parallel"
        evidence_path.write_text(json.dumps(evidence) + "\n", encoding="utf-8")
        refresh_sums(output)
        try:
            verify_export(output)
        except ExportError as error:
            assert "matrix" in str(error)
        else:
            raise AssertionError("packaged integrated export accepted parallel evidence")

        evidence["execution"] = "sequential"
        evidence["profiles"].append({**evidence["profiles"][0], "profile": "extra-profile"})
        evidence_path.write_text(json.dumps(evidence) + "\n", encoding="utf-8")
        refresh_sums(output)
        try:
            verify_export(output)
        except ExportError as error:
            assert "profiles" in str(error)
        else:
            raise AssertionError("packaged integrated export accepted extra matrix row")

        evidence["profiles"].pop()
        evidence_path.write_text(json.dumps(evidence) + "\n", encoding="utf-8")
        manifest_path = output / "profile-manifest.json"
        result_path = output / "result.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        result = json.loads(result_path.read_text(encoding="utf-8"))
        wrong_audit = "generated/a8w8-d16-hp1/logs/03.log"
        manifest["profiles"][0]["host_audit"] = wrong_audit
        result["profiles"][0]["host_audit"] = wrong_audit
        manifest_path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")
        result_path.write_text(json.dumps(result) + "\n", encoding="utf-8")
        refresh_sums(output)
        try:
            verify_export(output)
        except ExportError as error:
            assert "audit path" in str(error)
        else:
            raise AssertionError("packaged integrated export accepted cross-profile audit path")


def test_dependency_lock_accepts_clean_descendant_branch() -> None:
    # Given: a repository whose task branch descends from the approved baseline.
    with tempfile.TemporaryDirectory(prefix="gemmini-export-dependency-") as directory:
        repository = Path(directory) / "repository"
        for arguments in (("init", str(repository)), ("-C", str(repository), "config", "user.email", "test@example.com"),
                          ("-C", str(repository), "config", "user.name", "Test")):
            completed = subprocess.run(("git", *arguments), text=True, capture_output=True, check=False)
            assert completed.returncode == 0, completed.stderr
        write(repository / "tracked.txt", "baseline\n")
        for arguments in (("-C", str(repository), "add", "tracked.txt"), ("-C", str(repository), "commit", "-m", "baseline")):
            completed = subprocess.run(("git", *arguments), text=True, capture_output=True, check=False)
            assert completed.returncode == 0, completed.stderr
        baseline = subprocess.run(("git", "-C", str(repository), "rev-parse", "HEAD"), text=True,
                                  capture_output=True, check=False).stdout.strip()
        write(repository / "tracked.txt", "descendant\n")
        for arguments in (("-C", str(repository), "add", "tracked.txt"), ("-C", str(repository), "commit", "-m", "descendant")):
            completed = subprocess.run(("git", *arguments), text=True, capture_output=True, check=False)
            assert completed.returncode == 0, completed.stderr

        # When: the clean descendant is evaluated against its baseline.
        head = approved_head(repository, "llama_cpp_gemmini", baseline, True)
        require_clean_tracked_state(repository, "llama_cpp_gemmini")

        # Then: its actual descendant revision is accepted and recorded.
        assert head != baseline


def test_tamper_and_existing_output_rejected() -> None:
    # Given: valid package and occupied destination.
    with tempfile.TemporaryDirectory(prefix="gemmini-export-tamper-") as directory:
        base = Path(directory)
        source, build, output = base / "source", base / "build", base / "package"
        integrated_source_fixture(source)
        integrated_build_fixture(build)
        create_export(ExportRequest(source, build, output, None))

        # When: package content changes or output is reused.
        profile = json.loads((output / "profile-manifest.json").read_text(encoding="utf-8"))["profiles"][0]
        (output / profile["filelist"]).write_text("tampered\n", encoding="utf-8")

        # Then: verification fails and existing output remains untouched.
        try:
            verify_export(output)
        except ExportError as error:
            assert str(error)
        else:
            raise AssertionError("tampered package accepted")
        (output / "SHA256SUMS").write_text("malformed\n", encoding="utf-8")
        try:
            verify_export(output)
        except ExportError as error:
            assert str(error)
        else:
            raise AssertionError("malformed SHA256SUMS accepted")
        try:
            create_export(ExportRequest(source, build, output, None))
        except ExportError as error:
            assert str(error)
        else:
            raise AssertionError("existing output overwritten")


def fake_tool(path: Path, body: str) -> None:
    write(path, "#!/bin/sh\n" + body + "\n")
    path.chmod(0o755)


@dataclass(frozen=True, slots=True)
class AuditCase:
    role: str
    file_format: str
    nm_output: str = ""
    command_log: str | None = None
    missing_tool: str | None = None


def run_audit(base: Path, artifact: Path, case: AuditCase) -> dict[str, str | int | None | list[str]]:
    tools = base / "tools"
    tools.mkdir(exist_ok=True)
    fake_tool(tools / "file", f"printf '%s\\n' '{case.file_format}'")
    fake_tool(tools / "nm", f"if [ \"$1\" = '-a' ]; then printf '%s\\n' '{case.nm_output}'; fi")
    fake_tool(tools / "otool", "printf '%s\\n' '/usr/lib/libSystem.B.dylib'")
    fake_tool(tools / "readelf", "printf '%s\\n' 'ELF Header'")
    fake_tool(tools / "ldd", "printf '%s\\n' 'libc.so.6'")
    if case.missing_tool is not None:
        (tools / case.missing_tool).unlink()
    output = base / f"audit-{len(tuple(base.glob('audit-*.json'))):02}.json"
    command_log = base / f"commands-{output.stem}.txt"
    arguments = [sys.executable, str(ROOT / "scripts/gemmini_audit_host.py"), "--artifact", str(artifact),
                 "--role", case.role, "--out", str(output)]
    if case.command_log is not None:
        command_log.write_text(case.command_log, encoding="utf-8")
        arguments.extend(("--command-log", str(command_log)))
    environment = os.environ | {"PATH": str(tools)}
    process = subprocess.run(
        arguments,
        text=True, capture_output=True, env=environment, check=False,
    )
    assert output.is_file(), process.stderr
    return json.loads(output.read_text(encoding="utf-8"))


def test_host_audit_routes_platform_tools_and_roles() -> None:
    # Given: one artifact and deterministic platform inspection tools.
    with tempfile.TemporaryDirectory(prefix="gemmini-audit-test-") as directory:
        base = Path(directory)
        artifact = base / "host"
        artifact.write_bytes(b"host")

        # When: Mach-O, ELF, and host-with-simulator-symbol cases are audited.
        macho = run_audit(base, artifact, AuditCase("HOST_COMMON", "Mach-O 64-bit executable arm64"))
        elf = run_audit(base, artifact, AuditCase("PHYSICAL_HOST", "ELF 64-bit LSB pie executable, x86-64"))
        bad = run_audit(base, artifact, AuditCase("HOST_COMMON", "Mach-O 64-bit executable arm64", "_Verilated"))
        allowed = run_audit(base, artifact, AuditCase("RTL_TEST_ADAPTER", "Mach-O 64-bit executable arm64", "_Verilated"))
        bypass = run_audit(base, artifact, AuditCase("HOST_COMMON", "Mach-O 64-bit executable arm64",
                                                     command_log="c++ -Wl,-undefined,dynamic_lookup host.o"))
        missing = run_audit(base, artifact, AuditCase("HOST_COMMON", "Mach-O 64-bit executable arm64",
                                                      missing_tool="otool"))

        # Then: native tools differ and simulator linkage is role-gated.
        assert macho["status"] == elf["status"] == allowed["status"] == "PASS"
        assert macho["tools"] == ["file", "otool", "nm"]
        assert elf["tools"] == ["file", "readelf", "ldd", "nm"]
        assert bad["status"] == "FAIL" and bad["reason"] == "SIMULATOR_LINKAGE"
        assert bypass["status"] == "FAIL" and bypass["reason"] == "UNRESOLVED_SYMBOL_BYPASS"
        assert missing["status"] == "NOT_RUN" and missing["reason"] == "DEPENDENCY"


def main() -> None:
    test_export_round_trip()
    test_integrated_export_preserves_verified_profile_closure()
    test_rmd_profile_requires_rmd_runtime_evidence()
    test_dense_profile_retains_dense_runtime_contract()
    test_rmd_export_retains_runtime_and_source_closure()
    test_integrated_export_rejects_incomplete_or_mixed_top_metadata()
    test_integrated_export_requires_passing_runtime_and_no_sim_evidence()
    test_packaged_integrated_evidence_cannot_change_matrix_or_profile_paths()
    test_dependency_lock_accepts_clean_descendant_branch()
    test_tamper_and_existing_output_rejected()
    test_host_audit_routes_platform_tools_and_roles()
    print("GEMMINI EXPORT/AUDIT: PASS")


if __name__ == "__main__":
    main()
