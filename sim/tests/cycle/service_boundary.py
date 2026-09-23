#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
# Run: uv run sim/tests/cycle/service_boundary.py --help
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, fields
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
from typing import Final

ROOT: Final = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from scripts.gemmini_rtl_build_binding import verify_build
from scripts.real_lib_manifest import sha256
from sim.cycle.certificate_contract import number, object_value, read_document


class ServiceBoundaryError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Observation:
    sequence: int
    period: int
    phase: int
    work: int
    reset_isolated: int
    backing_cycle_offset: int
    slot: int
    m: int
    n: int
    k: int
    tile_i: int
    tile_j: int
    tile_k: int
    run_count: int
    initial_scratchpad_half: int
    initial_accumulator_half: int
    accepted: int
    result_ready: int
    final_scale_release: int
    resource_ready: int
    submissions: int
    release_count: int
    model_done: int
    model_final_scale_release: int
    model_service_release: int
    model_resource_ready: int


def parse_rows(raw: str) -> list[Observation]:
    result = []
    for line in raw.splitlines():
        if line.startswith("SERVICE "):
            record = object_value(json.loads(line.removeprefix("SERVICE ")), "service")
            expected = {field.name for field in fields(Observation)}
            if set(record) != expected:
                raise ServiceBoundaryError("service observation fields differ")
            values = {name: number(record[name], name) for name in expected}
            result.append(Observation(**values))
    if not result:
        raise ServiceBoundaryError("missing service observations; isolated totals are insufficient")
    return result


def validate_rows(rows: list[Observation]) -> None:
    expected = {(sequence, period, phase, isolated, work)
                for sequence in range(5) for period in (3, 5)
                for phase in range(period) for isolated in (0, 1) for work in (0, 1)}
    indexed = {(r.sequence, r.period, r.phase, r.reset_isolated, r.work): r for r in rows}
    if len(indexed) != len(rows) or set(indexed) != expected:
        raise ServiceBoundaryError("sequence/phase coverage missing or duplicate")
    for row in rows:
        if not (row.accepted < row.result_ready < row.final_scale_release < row.resource_ready):
            raise ServiceBoundaryError("result/release/resource ordering invalid")
        if (row.model_done != row.result_ready or row.model_service_release != row.final_scale_release
                or row.model_resource_ready != row.resource_ready):
            raise ServiceBoundaryError("service model endpoint mismatch")
        if row.slot not in (0, 1) or min(row.m, row.n, row.k, row.tile_i, row.tile_j, row.tile_k) < 1:
            raise ServiceBoundaryError("service geometry or slot invalid")
        runs = row.sequence == 2 or (row.sequence == 1 and row.work == 1) or (row.sequence == 3 and row.work == 0)
        if ((row.m, row.n, row.k, row.run_count) != (2, 3, 37 if runs else 64, 2 if runs else 0)
                or row.slot != (row.work if row.sequence == 4 else 0)):
            raise ServiceBoundaryError("fixed sequence fixture changed")
        if row.work == 0 and row.accepted % row.period != row.phase:
            raise ServiceBoundaryError("accepted phase differs from declared sweep")
        if row.work == 1 and not row.reset_isolated:
            previous = indexed[(row.sequence, row.period, row.phase, 0, 0)]
            if row.accepted < previous.resource_ready:
                raise ServiceBoundaryError("next request accepted before prior drained resource")


def command(argv: list[str], output: Path) -> str:
    with output.open("x") as log:
        result = subprocess.run(argv, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                check=False, timeout=180)
    if result.returncode:
        raise ServiceBoundaryError(f"command exit {result.returncode}; log: {output}")
    return output.read_text()


def run_probe(build: Path, library: Path, output: Path) -> None:
    resolved = read_document(build / "resolved-profile.json")
    profile = resolved.get("profile")
    if not isinstance(profile, str):
        raise ServiceBoundaryError("resolved profile missing")
    binding = verify_build(build, profile)
    output.mkdir(parents=True, exist_ok=False)
    objects = build / "rtl-test-obj"
    makefile = objects / "VIM2PGemminiWSHP1RtlTest.mk"
    captured = makefile.read_text()
    flags_match = re.search(r"VM_USER_CFLAGS = \\\n(.*?)\n\n", captured, re.S)
    verilator_match = re.search(r"^VERILATOR_ROOT = (.+)$", captured, re.M)
    if not flags_match or not verilator_match:
        raise ServiceBoundaryError("official Verilator compile inputs missing")
    flags = shlex.split(flags_match[1].replace("\\\n", " ").rstrip().removesuffix("\\"))
    source = object_value(resolved["llama_source"], "llama source")
    recorded_root = source.get("root")
    if not isinstance(recorded_root, str):
        raise ServiceBoundaryError("recorded llama include root missing")
    current_llama = ROOT.parent / "llama.cpp-gemmini"
    flags = [flag.replace(recorded_root, str(current_llama)) for flag in flags]
    include = Path(verilator_match[1]) / "include"
    flags += ["-ffunction-sections", "-fdata-sections", f"-I{objects}", f"-I{include}",
              f"-I{include / 'vltstd'}", "-O1"]
    probe = Path(__file__).with_name("service_boundary_probe.cpp")
    executable = output / "service-boundary-probe"
    link_flags = (["-Wl,-dead_strip", "-Wl,-U,__Z15vl_time_stamp64v,-U,__Z13sc_time_stampv"]
                  if sys.platform == "darwin" else ["-Wl,--gc-sections"])
    argv = ["c++", *flags, str(probe), str(ROOT / "sim/common/gemmini_schedule.cpp"),
            str(objects / "VIM2PGemminiWSHP1RtlTest__ALL.a"),
            str(objects / "verilated.o"), str(objects / "verilated_threads.o"),
            str(library), f"-Wl,-rpath,{library.parent}", *link_flags,
            "-pthread", "-o", str(executable)]
    _ = (output / "command.json").write_text(json.dumps(argv, indent=2) + "\n")
    _ = command(argv, output / "build.log")
    raw = command([str(executable)], output / "rtl.log")
    rows = parse_rows(raw)
    validate_rows(rows)
    mismatches = [asdict(r) for r in rows if r.model_done != r.result_ready]
    release_mismatches = [asdict(r) for r in rows if r.model_final_scale_release != r.final_scale_release]
    service_mismatches = [asdict(r) for r in rows if r.model_service_release != r.final_scale_release
                         or r.model_resource_ready != r.resource_ready]
    variations = []
    for sequence in range(5):
        for period in (3, 5):
            durations = sorted({r.result_ready - r.accepted for r in rows
                                if r.sequence == sequence and r.period == period and r.work == 0})
            variations.append({"sequence": sequence, "period": period, "result_durations": durations})
    report = {"schema": "im2p-npu-service-boundary-probe", "version": 1,
              "status": "OBSERVATIONS_COMPLETE", "profile": profile,
              "scope": "fixed 2x3 dense K64 / runs [17,20], serialized drained reference memory",
              "execution_kind": "FRESH_RTL_EXECUTION_WITH_VERIFIED_OBJECT_REUSE",
              "rtl_build_binding_sha256": sha256(build / "rtl-build-binding.json"),
              "hardware_contract": binding["hardware_contract"],
              "cycle_library_sha256": sha256(library), "probe_source_sha256": sha256(probe),
              "probe_binary_sha256": sha256(executable), "observation_count": len(rows),
              "source_sha256": {str(path.relative_to(ROOT)): sha256(path) for path in (
                  probe, Path(__file__), ROOT / "fpga/gemmini_hp1/host/test_ws_rtl.cpp",
                  ROOT / "fpga/gemmini_hp1/host/run_aware_rtl_driver.inc",
                  ROOT / "sim/common/gemmini_schedule.cpp")},
              "sequence_names": ["dense-dense", "dense-multirun", "multirun-multirun",
                                 "multirun-dense", "stripe0-stripe1"],
              "residual_fixture": {"original_k": 116, "runs": [[0, 131071, 0, 17], [3, 1048575, 17, 20]]},
              "observations": [asdict(row) for row in rows], "phase_variations": variations,
              "result_mismatches": mismatches, "release_mismatches": release_mismatches,
              "service_mismatches": service_mismatches,
              "logical_result_comparison": "PASS" if not mismatches else "FAIL",
              "resource_model_comparison": "PASS" if not service_mismatches else "FAIL",
              "scheduler_generalization": "NOT_READY",
              "blocker": "Finite fixture coverage is not whole-model or persistent-state certification; current-source base/run certificates require separate verification."}
    _ = (output / "service-boundary.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"status": report["status"], "observations": len(rows),
                      "result_mismatches": len(mismatches), "release_mismatches": len(release_mismatches),
                      "service_mismatches": len(service_mismatches)}))


def main() -> int:
    parser = argparse.ArgumentParser(description="Observe finite same-DUT result/release/slot boundaries; never certifies arbitrary scheduler services")
    parser.add_argument("--rtl-build", type=Path, required=True)
    parser.add_argument("--library", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        run_probe(args.rtl_build.resolve(), args.library.resolve(), args.out.resolve())
    except (ServiceBoundaryError, OSError, ValueError, subprocess.TimeoutExpired) as error:
        print(f"service-boundary: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
