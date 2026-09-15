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

import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.gemmini_export import (
    ExportError,
    ExportRequest,
    create_export,
    extract_export,
    verify_export,
)


def write(path: Path, data: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, bytes):
        path.write_bytes(data)
    else:
        path.write_text(data, encoding="utf-8")


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


def build_fixture(root: Path) -> None:
    profile = root / "a4w4-d16-hp1"
    write(profile / "resolved-profile.json", '{"profile":"a4w4-d16-hp1"}\n')
    write(profile / "rtl/StandaloneTop.sv", "module StandaloneTop; endmodule\n")
    write(profile / "rtl/defs.svh", "`define DIM 16\n")
    write(profile / "result.json", '{"elaboration":{"status":"PASS"}}\n')
    write(profile / "forbidden.gguf", b"model")
    write(profile / "rtl/forbidden.v", bytes.fromhex("cafebabf") + b"payload")
    (profile / "rtl-link").symlink_to(Path("/tmp"))


def test_export_round_trip() -> None:
    # Given: source plus generated RTL contain safe files and forbidden artifacts.
    with tempfile.TemporaryDirectory(prefix="gemmini-export-test-") as directory:
        base = Path(directory)
        source, build, output = base / "source", base / "build", base / "package"
        source_fixture(source)
        build_fixture(build)
        board_dir = base / "board"
        write(board_dir / "pins.xdc", "set_property PACKAGE_PIN A1 [get_ports core_clk]\n")
        board = board_dir / "board.json"
        board.write_text(json.dumps({"schema_version": 1, "board_id": "test-board", "part": "xc-test",
            "top": "StandaloneTop", "clock": {"port": "core_clk", "frequency_mhz": 100},
            "xdc": ["pins.xdc"], "pin_constraints": "pins.xdc", "memory_interface": "test-memory",
            "deployment_artifact": "bit"}), encoding="utf-8")

        # When: package is created and extracted at a different path.
        result = create_export(ExportRequest(source, build, output, board))
        relocated = base / "relocated"
        extracted = extract_export(result.archive, relocated)

        # Then: hashes and relative RTL closure survive relocation; unsafe data stays out.
        assert result.relocation_verified and verify_export(output).status == "PASS"
        assert extracted.status == "PASS"
        filelist = (output / "filelist.f").read_text(encoding="utf-8").splitlines()
        assert filelist and all(not Path(line).is_absolute() for line in filelist)
        assert all((output / line).is_file() for line in filelist)
        names = {path.relative_to(output).as_posix() for path in output.rglob("*")}
        assert not any("forbidden" in name or "external-link" in name or "rtl-link" in name for name in names)
        assert "source/fpga/gemmini_hp1/host/uart.cpp" in names
        assert "source/fpga/gemmini_hp1/host/uart.hpp" in names
        assert "generated/a4w4-d16-hp1/result.json" in names
        assert (output / "SHA256SUMS").is_file()
        resolved_board = json.loads((output / "board/resolved-board.json").read_text(encoding="utf-8"))
        assert resolved_board["schema_version"] == 1
        assert resolved_board["xdc"] == ["board/constraints/00-pins.xdc"]


def test_tamper_and_existing_output_rejected() -> None:
    # Given: valid package and occupied destination.
    with tempfile.TemporaryDirectory(prefix="gemmini-export-tamper-") as directory:
        base = Path(directory)
        source, build, output = base / "source", base / "build", base / "package"
        source_fixture(source)
        build_fixture(build)
        create_export(ExportRequest(source, build, output, None))

        # When: package content changes or output is reused.
        (output / "filelist.f").write_text("tampered\n", encoding="utf-8")

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
    test_tamper_and_existing_output_rejected()
    test_host_audit_routes_platform_tools_and_roles()
    print("GEMMINI EXPORT/AUDIT: PASS")


if __name__ == "__main__":
    main()
