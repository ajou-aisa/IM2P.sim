#!/usr/bin/env python3
"""Check sealed ARM64 binaries; explicitly run only local PTY/simulator tests."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path, PurePosixPath
import platform
import shutil
import subprocess
import sys

import native_build as native

SIM = "source/build/selected/a8-w8-d16/current/libim2p_sim.a"
FRONTEND = "source/build/selected/a8-w8-d16/current/libim2p_gemmini_frontend.a"
SCALAR = "host-build/scu_frontend_final"
DISPATCH = "fpga-host-build/scu_host_dispatch"


def verify(run, expected_manifest):
    native.verify_frozen(run)
    manifest = native.regular_file(run, "native-build-manifest.json")
    if native.digest(manifest) != expected_manifest:
        raise ValueError("native build manifest SHA256 mismatch")
    data = json.loads(manifest.read_text())
    if (data.get("status") != "BUILT_NOT_RUNTIME_TESTED" or data.get("machine") != "aarch64" or
            data.get("source_sha256") != native.SOURCE_SHA256 or
            data.get("identity_sha256") != native.digest(run / "identity.json")):
        raise ValueError("ARM64 source/build identity mismatch")
    artifacts = data["artifacts"]
    if not {SIM, FRONTEND, SCALAR, DISPATCH}.issubset(artifacts):
        raise ValueError("native build manifest lacks required artifacts")
    for name, record in artifacts.items():
        relative = PurePosixPath(name)
        if relative.is_absolute() or ".." in relative.parts or str(relative) != name:
            raise ValueError("invalid native artifact path")
        # The existing cache uses a selected/current symlink into this RUN.
        path = (run / name).resolve(strict=True)
        if not path.is_relative_to(run) or not path.is_file():
            raise ValueError("native artifact escapes the selected RUN")
        if native.digest(path) != record["sha256"] or path.stat().st_size != record["bytes"]:
            raise ValueError(f"native artifact SHA256/size mismatch: {name}")
    for name in (SCALAR, DISPATCH):
        with (run / name).open("rb") as source:
            header = source.read(20)
        if header[:6] != b"\x7fELF\x02\x01" or int.from_bytes(header[18:20], "little") != 183:
            raise ValueError(f"ELF64 little-endian AArch64 executable required: {name}")
    return data


def plan(run, out):
    source = run / "source"
    tests = source / "tests/scu_block_scale"
    commands = [
        ("pty", [sys.executable, str(source / "fpga/scu_block_scale/test_uart.py"),
                 "--out", str(out / "pty")], "IFR3_PTY PASS cap_semantic"),
        ("full-gate", [sys.executable, str(source / "fpga/scu_block_scale/test_full_gate.py"),
                       "--out", str(out / "full-gate")], "IFR3_FULL_GATE_SUITE PASS tests=21"),
        ("reject", [str(run / DISPATCH), "reject"],
                   "FPGA_UART_GGML_REJECT PASS cases=2 output_preserved=1 simulator_calls=0"),
    ]
    for label, fixture, count in (("scalar", "signed-scu-sat-v2", 22),
                                   ("carrier", "h1-carrier-edges-sat-v2", 48)):
        directory = tests / "fixtures" / fixture
        commands += [
            (label, [str(run / SCALAR), str(directory / "cases.txt")],
             f"SCU_FRONTEND_PASS cases={count} rejected_metadata=6 numerical_revision=signed-scu-sat-v2"),
            (label + "-golden", [sys.executable, str(tests / "golden.py"),
              "--expected", str(directory / "expected.json"), "--check", str(out / (label + ".log"))],
             f"INDEPENDENT_GOLDEN_EXACT_PASS cases={count} raw={count} f_out={count}"),
        ]
    return commands


def execute(run, out):
    out.mkdir(parents=True, exist_ok=False)
    (out / "tmp").mkdir()
    environment = native.build_environment(out)
    for key in list(environment):
        if key.startswith("IM2P_FPGA_") or key == "PYTHONOPTIMIZE":
            environment.pop(key)
    completed = []
    for label, command, marker in plan(run, out):
        record = {"argv": command, "cwd": str(out), "timeout_seconds": 900,
                  "start_utc": datetime.now(timezone.utc).isoformat(),
                  "physical_device_environment_removed": True}
        log_path = out / (label + ".log")
        try:
            with log_path.open("x") as log:
                result = subprocess.run(command, cwd=out, env=environment, stdout=log,
                                        stderr=subprocess.STDOUT, timeout=900)
            record["exit"] = result.returncode
        except subprocess.TimeoutExpired:
            record.update(exit=None, timeout=True)
        record["end_utc"] = datetime.now(timezone.utc).isoformat()
        native.write_json(out / (label + ".command.json"), record)
        text = log_path.read_text(errors="replace")
        if record["exit"] != 0 or marker not in text:
            raise RuntimeError(f"{label} failed; preserved log; later tests NOT_RUN")
        if label == "pty" and text.count("IFR3_PTY PASS ") != 20:
            raise RuntimeError("PTY completion count mismatch; later tests NOT_RUN")
        completed.append({"label": label, "log_sha256": native.digest(log_path)})
    return completed


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native-run", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--run-tests", action="store_true")
    args = parser.parse_args(argv)
    try:
        run, out = args.native_run.resolve(), args.out.resolve()
        if os.path.lexists(args.out) or any(out.is_relative_to(run / tree) for tree in ("source", "host", "params")):
            raise ValueError("fresh test output outside frozen source/host/params is required")
        verify(run, args.manifest_sha256)
        summary = {"status": "CHECKED_DRY_RUN", "source_sha256": native.SOURCE_SHA256,
                   "native_manifest_sha256": args.manifest_sha256,
                   "commands": [{"label": label, "argv": command} for label, command, _ in plan(run, out)],
                   "board_access": False, "ARM64_runtime": "NOT_RUN"}
        if args.run_tests:
            if platform.system() != "Linux" or platform.machine() != "aarch64" or not shutil.which("c++"):
                raise ValueError("NOT_RUN: native Linux AArch64 and C++20 compiler required; no emulation/cross runner")
            summary["completed"] = execute(run, out)
            verify(run, args.manifest_sha256)
            summary.update(status="SOFTWARE_PASS", ARM64_runtime="SCALAR_SIMULATOR_AND_PTY_PASS",
                           numerical_cases=70, raw_exact=70, f_out_exact=70,
                           caller_padding=140, metadata_rejections=12,
                           pty_cases=20, full_gate_cases=21, unsupported_route_cases=2,
                           pipeline_runtime="NOT_RUN", live_producer="NOT_RUN")
            native.write_json(out / "results.json", summary)
        print(json.dumps(summary, indent=2))
        return 0
    except (OSError, ValueError, RuntimeError, KeyError, subprocess.SubprocessError) as error:
        print(f"NATIVE_SOFTWARE_TEST_STOP: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
