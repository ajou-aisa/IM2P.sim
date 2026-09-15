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
#      uv run tests/test_gemmini_flow.py
# 3. Or make executable and run:
#      chmod +x tests/test_gemmini_flow.py && ./tests/test_gemmini_flow.py
# ─────────────────

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOARD_DIR = ROOT / "fpga/gemmini_hp1/boards"
FLOW = ROOT / "fpga/gemmini_hp1/flow/vivado_flow.tcl"
BOARD_VALIDATOR = ROOT / "scripts/gemmini_board.py"


def test_board_template_leaves_unknown_hardware_unset() -> None:
    # Given: board schema and unresolved template.
    schema = json.loads((BOARD_DIR / "board.schema.json").read_text(encoding="utf-8"))
    template = json.loads((BOARD_DIR / "board.template.json").read_text(encoding="utf-8"))

    # When: hardware-specific values are inspected.
    unknown = (template["board_id"], template["part"], template["top"],
               template["clock"]["port"], template["clock"]["frequency_mhz"])

    # Then: no board, part, top, clock, XDC, pin, or deployment artifact is invented.
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert "$schema" in schema["properties"]
    assert all(value is None for value in unknown)
    assert template["xdc"] == [] and template["pin_constraints"] is None
    assert template["deployment_artifact"] is None


def test_vivado_flow_order_is_real_and_complete() -> None:
    # Given: production Tcl source.
    text = FLOW.read_text(encoding="utf-8")

    # When: state-changing Vivado commands are located.
    commands = ["read_xdc", "synth_design", "opt_design", "place_design", "route_design",
                "report_timing_summary", "report_drc", "report_cdc", "write_bitstream"]
    offsets = [text.index(command) for command in commands]

    # Then: implementation and reports precede bitstream generation.
    assert offsets == sorted(offsets)
    assert "get_ports -quiet $clock_port" in text
    assert "get_property PART [current_project]" in text
    assert "IS_BLACKBOX == 1" in text


def test_board_validator_requires_resolved_confined_inputs() -> None:
    # Given: unresolved template, escaped XDC, and complete local board input.
    with tempfile.TemporaryDirectory(prefix="gemmini-board-test-") as directory:
        root = Path(directory)
        outside = root / "outside.xdc"
        outside.write_text("set_property PACKAGE_PIN A1 [get_ports core_clk]\n", encoding="utf-8")
        board_dir = root / "board"
        board_dir.mkdir()
        common = {"schema_version": 1, "board_id": "test", "part": "xc-test", "top": "Top",
                  "clock": {"port": "core_clk", "frequency_mhz": 100},
                  "memory_interface": "test-memory", "deployment_artifact": "bit"}
        escaped = board_dir / "escaped.json"
        escaped.write_text(json.dumps(common | {"xdc": ["../outside.xdc"],
                                                 "pin_constraints": "../outside.xdc"}), encoding="utf-8")
        local_xdc = board_dir / "pins.xdc"
        local_xdc.write_bytes(outside.read_bytes())
        resolved = board_dir / "resolved.json"
        resolved.write_text(json.dumps(common | {"xdc": ["pins.xdc"],
                                                  "pin_constraints": "pins.xdc"}), encoding="utf-8")

        # When: each manifest crosses validator boundary.
        template = subprocess.run([sys.executable, str(BOARD_VALIDATOR), "--manifest",
                                   str(BOARD_DIR / "board.template.json")], capture_output=True, text=True)
        escape = subprocess.run([sys.executable, str(BOARD_VALIDATOR), "--manifest", str(escaped)],
                                capture_output=True, text=True)
        valid = subprocess.run([sys.executable, str(BOARD_VALIDATOR), "--manifest", str(resolved)],
                               capture_output=True, text=True)

        # Then: only resolved XDC confined beside manifest is accepted.
        assert template.returncode != 0 and escape.returncode != 0
        assert valid.returncode == 0 and json.loads(valid.stdout)["status"] == "PASS"


def test_tcl_preflight_runs_without_vivado() -> None:
    # Given: valid board-independent RTL, filelist, and XDC fixture.
    tclsh = shutil.which("tclsh")
    assert tclsh is not None
    with tempfile.TemporaryDirectory(prefix="gemmini-flow-test-") as directory:
        root = Path(directory)
        rtl = root / "top.sv"
        rtl.write_text("module Top(input logic core_clk); endmodule\n", encoding="utf-8")
        filelist = root / "filelist.f"
        filelist.write_text("top.sv\n", encoding="utf-8")
        xdc = root / "board.xdc"
        xdc.write_text("create_clock -period 10.000 [get_ports core_clk]\n", encoding="utf-8")
        timing_xdc = root / "timing.xdc"
        timing_xdc.write_text("set_clock_uncertainty 0.100 [get_clocks core_clk]\n", encoding="utf-8")
        common = [tclsh, str(FLOW), "--stage", "bitstream", "--part", "xc-test-part",
                  "--top", "Top", "--clock-port", "core_clk", "--clock-mhz", "100",
                  "--xdc", str(xdc), "--xdc", str(timing_xdc),
                  "--filelist", str(filelist), "--out", str(root / "out")]

        # When: validate-only and missing-XDC paths execute under plain Tcl.
        valid = subprocess.run([*common, "--validate-only"], text=True, capture_output=True, check=False)
        invalid_args = common.copy()
        invalid_args[invalid_args.index(str(xdc))] = str(root / "missing.xdc")
        invalid = subprocess.run([*invalid_args, "--validate-only"], text=True, capture_output=True, check=False)
        escaped = root / "nested/filelist.f"
        escaped.parent.mkdir()
        escaped.write_text("../top.sv\n", encoding="utf-8")
        escaped_args = common.copy()
        escaped_args[escaped_args.index(str(filelist))] = str(escaped)
        traversal = subprocess.run([*escaped_args, "--validate-only"], text=True,
                                   capture_output=True, check=False)

        # Then: preflight succeeds explicitly without claiming Vivado; missing board input fails.
        assert valid.returncode == 0 and "FLOW_VALIDATED_NOT_RUN stage=bitstream sources=1 xdc=2" in valid.stdout
        assert not (root / "out").exists()
        assert invalid.returncode != 0 and "GEMMINI_VIVADO_FLOW_FAILED" in invalid.stderr
        assert traversal.returncode != 0 and "filelist entry must be confined" in traversal.stderr


def main() -> None:
    test_board_template_leaves_unknown_hardware_unset()
    test_vivado_flow_order_is_real_and_complete()
    test_board_validator_requires_resolved_confined_inputs()
    test_tcl_preflight_runs_without_vivado()
    print("GEMMINI VIVADO FLOW: PASS")


if __name__ == "__main__":
    main()
