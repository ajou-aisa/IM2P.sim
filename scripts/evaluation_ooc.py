#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
# How to run: python3 -B scripts/evaluation_ooc.py --help
from __future__ import annotations

import argparse
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile

if __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.evaluation_clock_contract import ClockError, Record, file_ref, read_record, require, text
from scripts.evaluation_ooc_policy import PART, POLICY_PATH, PROFILES, ROOT, dimensions, frequencies, policy, target_frequency
from scripts.evaluation_ooc_report import select_ooc, write_sample
from scripts.evaluation_ooc_rtl import constraints, core_top, wrap_core
from scripts.gemmini_replay_contract import hardware_contract

FLOW = ROOT / "fpga/gemmini_hp1/flow/evaluation_ooc.tcl"


def write_json(path: Path, value: Record) -> None:
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")


def preflight(grid: list[int] | None) -> Record:
    current = policy()
    gaps: list[str] = []
    tool = shutil.which("vivado")
    if tool is None:
        gaps.append("vivado_executable")
    if platform.system() != "Linux":
        gaps.append("linux_vivado_execution_host")
    selected_grid = frequencies(grid) if grid is not None else ()
    if not selected_grid:
        gaps.append("explicit_finite_frequency_hz_set")
    part_status = "NOT_RUN"
    probe: Record = {}
    if tool is not None and platform.system() == "Linux":
        try:
            with tempfile.TemporaryDirectory(prefix="im2p-ooc-part-query-") as temporary:
                completed = subprocess.run([tool, "-mode", "batch", "-nojournal", "-nolog", "-source", str(FLOW),
                                            "-tclargs", "--check-part"], cwd=temporary,
                                           capture_output=True, text=True, timeout=30)
            probe = {"exit_code": completed.returncode, "stdout": completed.stdout, "stderr": completed.stderr}
            part_status = "SUPPORTED" if completed.returncode == 0 else "UNSUPPORTED_OR_TOOL_FAILURE"
            if completed.returncode:
                gaps.append("fixed_reference_part_unavailable_no_fallback")
        except subprocess.TimeoutExpired:
            gaps.append("part_query_timeout")
            part_status = "QUERY_TIMEOUT"
    return {
        "schema": "im2p-evaluation-ooc-preflight", "version": 1,
        "status": "NOT_READY" if gaps else "CONFIGURED_NOT_RUN", "configuration_gaps": list(gaps),
        "policy": file_ref(POLICY_PATH), "part": PART, "part_support": part_status, "part_probe": probe,
        "profiles": list(PROFILES), "frequencies_hz": list(selected_grid),
        "target_frequency_hz": {profile: str(target_frequency(profile)) for profile in PROFILES},
        "target_peak_tops": current["target_peak_tops"], "target_basis": current["target_basis"],
        "clock_port": "CLK", "timing_stage_required": "POST_ROUTE", "tool": tool,
        "synthesis": "NOT_RUN", "selected_frequency_hz": None, "physical_programming": "NOT_RUN",
        "reset_policy": "no exclusion unless actual generated asynchronous RST_N event is proven",
    }


def execute(arguments: list[str], output: Path, timeout: int) -> int:
    with output.open("x", encoding="utf-8") as log:
        completed = subprocess.run(arguments, cwd=output.parent, stdout=log, stderr=subprocess.STDOUT,
                                   timeout=timeout, check=False)
    return completed.returncode


def prepare(profile: str, output: Path, timeout: int) -> Path:
    bits, dim = dimensions(profile)
    before = hardware_contract(profile)
    rtl_build = output / "rtl-build"
    command = [sys.executable, "-B", str(ROOT / "scripts/gemmini_build.py"), "--a-bits", str(bits),
               "--w-bits", str(bits), "--dim", str(dim), "--scu", "hp1-left-shift", "--top", "integrated",
               "--stage", "rtl", "--memory-contract", str(ROOT / f"config/gemmini_host_memory_contracts/{profile}.json"),
               "--out", str(rtl_build)]
    code = execute(command, output / "rtl-build.log", timeout)
    require(code == 0, "official RTL build failed: " + profile)
    require(hardware_contract(profile) == before, "hardware source changed during RTL build")
    top = rtl_build / "rtl" / (core_top(profile) + ".sv")
    wrapper, reset_policy = wrap_core(top.read_text(), profile)
    wrapper_path = output / "ooc-wrapper.sv"
    wrapper_path.write_text(wrapper, encoding="utf-8")
    artifacts: Record = {str(path.relative_to(output)): file_ref(path) for path in sorted((rtl_build / "rtl").glob("*"))
                 if path.is_file() and path.suffix in (".sv", ".v", ".svh", ".vh")}
    artifacts.update({"filelist": file_ref(rtl_build / "filelist.f"), "wrapper": file_ref(wrapper_path),
                      "resolved_profile": file_ref(rtl_build / "resolved-profile.json"), "flow": file_ref(FLOW),
                      "rtl_tool_lock": file_ref(rtl_build / "tool-lock.json"),
                      "rtl_stage_result": file_ref(rtl_build / "stage-rtl.json")})
    source: Record = {"profile": profile, "execution_kind": "FRESH_OFFICIAL_RTL_BUILD",
                      "hardware_contract": before, "reset_policy": reset_policy,
                      "artifacts": artifacts, "build_command": list(command)}
    path = output / "source-binding.json"
    write_json(path, source)
    return path


def run(output: Path, grid: list[int], timeout: int) -> Record:
    require(not output.exists(), "fresh OOC sweep output required")
    check = preflight(grid)
    output.mkdir(parents=True)
    write_json(output / "preflight.json", check)
    if check["status"] != "CONFIGURED_NOT_RUN":
        return check
    tool = text(check, "tool")
    outcomes: list[Record] = []
    for profile in PROFILES:
        profile_root = output / profile
        profile_root.mkdir()
        source = prepare(profile, profile_root, timeout)
        source_record = read_record(source)
        attempts: list[Record] = []
        samples: list[Path] = []
        for frequency in frequencies(grid):
            candidate = profile_root / str(frequency)
            candidate.mkdir()
            write_json(candidate / "request.json", {"frequency_hz": frequency, "part": PART,
                                                    "policy": file_ref(POLICY_PATH)})
            (candidate / "clock.xdc").write_text(constraints(frequency, text(source_record, "reset_policy")))
            command = [tool, "-mode", "batch", "-nojournal", "-nolog", "-source", str(FLOW), "-tclargs",
                       "--filelist", str(profile_root / "rtl-build/filelist.f"),
                       "--wrapper", str(profile_root / "ooc-wrapper.sv"), "--xdc", str(candidate / "clock.xdc"),
                       "--core-top", core_top(profile), "--out", str(candidate / "implementation")]
            try:
                code = execute(command, candidate / "vivado.log", timeout)
                attempts.append({"frequency_hz": frequency, "exit_code": code, "command": list(command),
                                 "log": file_ref(candidate / "vivado.log")})
                if code == 0:
                    samples.append(write_sample(candidate, source))
            except subprocess.TimeoutExpired:
                attempts.append({"frequency_hz": frequency, "status": "TIMEOUT", "log": file_ref(candidate / "vivado.log")})
        sweep = profile_root / "sweep.json"
        write_json(sweep, {"profile": profile, "attempted_frequency_hz": list(frequencies(grid)), "attempts": list(attempts)})
        result = select_ooc(samples, sweep) if samples else {
            "status": "NOT_READY", "profile": profile, "selected_frequency_hz": None,
            "reason": "NO_COMPLETED_POST_ROUTE_SAMPLE", "sweep": file_ref(sweep)}
        write_json(profile_root / "clock-selection.json", result)
        outcomes.append({"profile": profile, "status": result["status"],
                         "clock_selection": file_ref(profile_root / "clock-selection.json")})
    return {"schema": "im2p-evaluation-ooc-sweep", "version": 1, "profiles": list(outcomes),
            "status": "PASS" if all(row["status"] == "PASS" for row in outcomes) else "NOT_READY",
            "physical_programming": "NOT_RUN"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Fixed reference-part evaluation-only OOC post-route sweep")
    parser.add_argument("action", choices=("preflight", "run"))
    parser.add_argument("--frequencies-hz", type=int, nargs="+")
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        require(1 <= args.timeout_seconds <= 7200, "finite command timeout 1..7200 seconds required")
        if args.action == "preflight":
            result = preflight(args.frequencies_hz)
            args.out.parent.mkdir(parents=True, exist_ok=True)
            write_json(args.out, result)
        else:
            require(args.frequencies_hz is not None, "explicit finite frequency set required")
            result = run(args.out, args.frequencies_hz, args.timeout_seconds)
            write_json(args.out / "result.json", result)
        print(json.dumps({"status": result["status"], "output": str(args.out)}))
        return 0 if result["status"] in ("PASS", "CONFIGURED_NOT_RUN") else 2
    except (ClockError, OSError, ValueError, subprocess.TimeoutExpired) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
