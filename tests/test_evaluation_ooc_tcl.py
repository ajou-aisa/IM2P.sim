#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
# ─── How to run ───
# Install uv: https://docs.astral.sh/uv/getting-started/installation/
# Run: uv run tests/test_evaluation_ooc_tcl.py
"""Synthetic Tcl command-protocol checks; these do not execute Vivado."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Final

FLOW: Final = Path(__file__).resolve().parents[1] / "fpga/gemmini_hp1/flow/evaluation_ooc.tcl"
PART: Final = "xcu250-figd2104-2L-e"
STUB: Final = r'''
set fixture_part 1
set fixture_clock 1
set fixture_setup 0.125
set fixture_hold 0.025
set fixture_internal 0
set fixture_drc 0
set fixture_format 1
set fixture_switching {0.125 0.000 0 8}
proc record {name args} {
    set channel [open $::env(IM2P_TEST_TRACE) a]
    puts $channel [list $name {*}$args]
    close $channel
}
proc get_parts {args} {
    record get_parts {*}$args
    if {$args ne {-quiet xcu250-figd2104-2L-e}} { error "unexpected part query" }
    if {$::fixture_part} { return xcu250-figd2104-2L-e }
    return {}
}
proc create_project {args} { record create_project {*}$args }
proc current_project {} { return project }
proc current_fileset {} { return sources }
proc set_property {args} { record set_property {*}$args }
proc read_verilog {args} { record read_verilog {*}$args }
proc read_xdc {path} { record read_xdc $path }
proc synth_design {args} { record synth_design {*}$args }
proc get_ports {args} { return [lindex $args end] }
proc get_clocks {args} {
    if {$::fixture_clock} { return core_clk }
    return {}
}
proc get_cells {args} { return {} }
proc get_property {property item} {
    switch -- $property {
        PART { return xcu250-figd2104-2L-e }
        TOP { return PoTalEvaluationOoc }
        PERIOD { return 10.000 }
        SLACK { return [set ::fixture_$item] }
        default { error "unexpected property $property" }
    }
}
proc opt_design {} { record opt_design }
proc place_design {} { record place_design }
proc route_design {} { record route_design }
proc report {name args} {
    record $name {*}$args
    set index [lsearch -exact $args -file]
    if {$index < 0} { error "report has no output" }
    set channel [open [lindex $args [expr {$index + 1}]] w]
    puts $channel [expr {$name eq "report_timing_summary" ? "WNS(ns) TNS(ns) TNS Failing Endpoints TNS Total Endpoints WHS(ns) THS(ns) THS Failing Endpoints THS Total Endpoints WPWS(ns) TPWS(ns) TPWS Failing Endpoints TPWS Total Endpoints\n-------\n0.125 0 0 42 0.025 0 0 42 $::fixture_switching" : "SYNTHETIC COMMAND PROTOCOL ONLY: $name"}]
    close $channel
}
foreach name {report_utilization report_timing_summary report_pulse_width report_drc report_route_status} {
    interp alias {} $name {} report $name
}
proc write_checkpoint {args} {
    record write_checkpoint {*}$args
    close [open [lindex $args end] w]
}
proc get_timing_paths {args} {
    record get_timing_paths {*}$args
    foreach {key value} {-from core_clk -to core_clk -max_paths 1} {
        set index [lsearch -exact $args $key]
        if {$index < 0 || [lindex $args [expr {$index + 1}]] ne $value} {
            error "wrong path scope: $key"
        }
    }
    if {[lsearch -exact $args -unconstrained] >= 0} { error "unsupported switch" }
    set kind [lindex $args [expr {[lsearch -exact $args -delay_type] + 1}]]
    if {$kind eq "max"} { return setup }
    if {$kind eq "min"} { return hold }
    error "wrong timing analysis"
}
proc check_timing {args} {
    record check_timing {*}$args
    if {[lsearch -exact $args -return_string] < 0} { error "report text required" }
    if {!$::fixture_format} { return "unknown report format" }
    set checks [lindex $args [expr {[lsearch -exact $args -override_defaults] + 1}]]
    set result {}
    foreach name $checks {
        set count 0
        if {$name eq "unconstrained_internal_endpoints"} { set count $::fixture_internal }
        if {$name eq "no_input_delay"} { set count 3 }
        if {$name eq "no_output_delay"} { set count 4 }
        append result "  1. checking $name ($count)\n  details follow\n"
    }
    return $result
}
proc get_drc_violations {args} {
    if {$::fixture_drc} { return violation }
    return {}
}
proc version {args} { return SYNTHETIC_TCL_STUB_NOT_VIVADO }
'''


def prepare(root: Path) -> None:
    (root / "core.sv").write_text(
        "module IM2PGemminiWSHP1A8W8D16(input clock,input RST_N,output reg data);\n"
        "always @(posedge clock) data <= RST_N;\nendmodule\n", encoding="utf-8",
    )
    (root / "types.svh").write_text("`define WIDTH 8\n", encoding="utf-8")
    (root / "filelist.f").write_text("core.sv\ntypes.svh\n", encoding="utf-8")
    (root / "wrapper.sv").write_text(
        "module PoTalEvaluationOoc(input CLK,input RST_N,output data);\n"
        "IM2PGemminiWSHP1A8W8D16 core(.clock(CLK),.RST_N(RST_N),.data(data));\nendmodule\n", encoding="utf-8",
    )
    (root / "clock.xdc").write_text(
        "create_clock -name core_clk -period 10.000 [get_ports CLK]\n", encoding="utf-8",
    )


def run_flow(root: Path, tweaks: str = "", argv: tuple[str, ...] | None = None) -> subprocess.CompletedProcess[str]:
    executable = shutil.which("tclsh")
    assert executable is not None, "tclsh is required for these protocol checks"
    driver = root / "synthetic-driver.tcl"
    driver.write_text(STUB + tweaks + "\nsource $::env(IM2P_TEST_FLOW)\n", encoding="utf-8")
    arguments = argv if argv is not None else (
        "--filelist", str(root / "filelist.f"), "--wrapper", str(root / "wrapper.sv"),
        "--xdc", str(root / "clock.xdc"), "--core-top", "IM2PGemminiWSHP1A8W8D16",
        "--out", str(root / "out"),
    )
    return subprocess.run(
        [executable, str(driver), *arguments], capture_output=True, text=True, check=False,
        timeout=10, env=os.environ | {"IM2P_TEST_FLOW": str(FLOW), "IM2P_TEST_TRACE": str(root / "trace")},
    )


def test_fixed_part_when_unavailable() -> None:
    # Given: the exact part is absent; When: preflight runs; Then: no fallback or design run.
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        result = run_flow(root, "set fixture_part 0", ("--check-part",))
        assert result.returncode != 0 and "required FPGA part unavailable" in result.stderr
        assert (root / "trace").read_text().splitlines() == [f"get_parts -quiet {PART}"]
        assert not (root / "out").exists()


def test_fixed_part_when_available() -> None:
    # Given: the exact part exists; When: preflight runs; Then: only part availability is checked.
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        result = run_flow(root, argv=("--check-part",))
        assert result.returncode == 0, result.stderr
        assert PART in result.stdout and not (root / "out").exists()


def test_post_route_protocol_when_inputs_valid() -> None:
    # Given: valid closed inputs; When: real Tcl executes stubs; Then: order/scope/artifacts agree.
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        prepare(root)
        result = run_flow(root)
        assert result.returncode == 0, result.stderr
        row = json.loads((root / "out/ooc-result.json").read_text())
        assert set(row) == {
            "schema", "version", "part", "top", "core_top", "clock_port", "period_ns", "tool_version", "timing_stage",
            "status", "setup_slack_ns", "hold_slack_ns", "timed_paths", "unconstrained_paths", "no_clock",
            "multiple_clock", "loops", "external_input_without_delay", "external_output_without_delay", "blocking_drc", "clock_switching_worst_slack_ns", "clock_switching_total_slack_ns", "clock_switching_failing_endpoints", "clock_switching_total_endpoints",
        }
        assert row["schema"] == "im2p-ooc-tool-result" and row["version"] == 2
        assert row["part"] == PART and row["top"] == "PoTalEvaluationOoc"
        assert row["core_top"] == "IM2PGemminiWSHP1A8W8D16" and row["clock_port"] == "CLK"
        assert row["period_ns"] == "10.000"
        assert row["tool_version"] == "SYNTHETIC_TCL_STUB_NOT_VIVADO"
        assert row["timing_stage"] == "POST_ROUTE" and row["status"] == "ROUTE_COMPLETED"
        assert row["timed_paths"] == 2 and row["unconstrained_paths"] == 0
        assert row["external_input_without_delay"] == 3 and row["external_output_without_delay"] == 4
        trace = (root / "trace").read_text().splitlines()
        stages = [line.split()[0] for line in trace]
        sequence = ["read_xdc", "synth_design", "opt_design", "place_design", "route_design"]
        assert [stages.index(name) for name in sequence] == sorted(stages.index(name) for name in sequence)
        assert f"synth_design -mode out_of_context -top PoTalEvaluationOoc -part {PART}" in trace
        for index, line in enumerate(trace):
            if "post-route" in line or line.startswith(("write_checkpoint", "get_timing_paths")):
                assert index > stages.index("route_design")
            if "post-synth" in line:
                assert stages.index("synth_design") < index < stages.index("opt_design")
        reports = {path.name for path in (root / "out").iterdir()}
        assert {"post-synth-utilization.rpt", "post-synth-timing.rpt", "post-route-utilization.rpt",
                "post-route-timing.rpt", "post-route-check-timing.rpt", "post-route-drc.rpt", "route.dcp"} <= reports
        assert not any("bitstream" in line or "hw_" in line or "set_input_delay" in line for line in trace)


def test_observations_when_timing_fails() -> None:
    # Given: negative setup, internal endpoints and DRCs; When: route completes; Then: no PASS claim.
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        prepare(root)
        result = run_flow(root, "set fixture_setup -0.5\nset fixture_internal 7\nset fixture_drc 1")
        assert result.returncode == 0, result.stderr
        row = json.loads((root / "out/ooc-result.json").read_text())
        assert row["setup_slack_ns"] == "-0.5" and row["hold_slack_ns"] == "0.025"
        assert row["unconstrained_paths"] == 7 and row["blocking_drc"] == 2
        assert row["status"] == "ROUTE_COMPLETED"


def test_fail_closed_when_constraints_or_counts_missing() -> None:
    # Given: absent clock or unknown report format; When: flow runs; Then: no result can pass.
    for tweak in (
        "set fixture_clock 0", "set fixture_format 0",
        'rename check_timing base_check; proc check_timing args {return "[base_check {*}$args]\\nno_clock (0)"}',
        "proc get_timing_paths args {return {}}", "set fixture_setup NaN",
    ):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prepare(root)
            result = run_flow(root, tweak)
            assert result.returncode != 0
            assert not (root / "out/ooc-result.json").exists()


def test_closed_inputs_when_invalid() -> None:
    # Given: escaping, missing, foreign, duplicate, or symlink RTL; When: run; Then: rejected before synth.
    for entry in ("../core.sv", "/tmp/core.sv", "missing.sv", "clock.xdc", "core.sv\ncore.sv",
                  "link.sv", "linked/core.sv", "types.svh"):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prepare(root)
            (root / "link.sv").symlink_to(root / "core.sv")
            (root / "linked").symlink_to(root, target_is_directory=True)
            (root / "filelist.f").write_text(entry + "\n", encoding="utf-8")
            result = run_flow(root)
            assert result.returncode != 0, entry
            assert not (root / "out/ooc-result.json").exists()


def test_rejects_reuse_and_extra_options() -> None:
    # Given: an existing output or unknown CLI option; When: run; Then: preserve evidence and reject.
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        prepare(root)
        (root / "out").mkdir()
        sentinel = root / "out/evidence"
        sentinel.write_text("preserve", encoding="utf-8")
        result = run_flow(root)
        assert result.returncode != 0 and sentinel.read_text() == "preserve"
    for arguments in (("--part", "other"), ("--check-part", "--out", "somewhere"), ()):
        with tempfile.TemporaryDirectory() as directory:
            result = run_flow(Path(directory), argv=arguments)
            assert result.returncode != 0


def test_rejects_other_constraints_and_unproven_reset() -> None:
    # Given: extra constraints; When: OOC runs; Then: reject unsupported commands or absent reset.
    for command in (
        "set_property PACKAGE_PIN A1 [get_ports CLK]", "set_input_delay 0 [get_ports data]",
        "set_output_delay 0 [get_ports data]", "source arbitrary.tcl",
        "create_clock -name core_clk -period 10.000 [get_ports CLK]",
        "set_false_path -from [get_ports RST_N]", "set_false_path -from [get_ports reset]",
    ):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prepare(root)
            xdc = root / "clock.xdc"
            xdc.write_text(xdc.read_text() + command + "\n", encoding="utf-8")
            result = run_flow(root)
            assert result.returncode != 0, command
            assert not (root / "out/ooc-result.json").exists()


if __name__ == "__main__":
    for test in (test_fixed_part_when_unavailable, test_fixed_part_when_available,
                 test_post_route_protocol_when_inputs_valid, test_observations_when_timing_fails,
                 test_fail_closed_when_constraints_or_counts_missing, test_closed_inputs_when_invalid,
                 test_rejects_reuse_and_extra_options, test_rejects_other_constraints_and_unproven_reset):
        test()
    print("SYNTHETIC TCL COMMAND PROTOCOL: 8 checks passed; VIVADO NOT RUN")
