#!/usr/bin/env python3
"""Negative-only PTY checks of the standard backend; mock bytes are not RTL evidence."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pty
import select
import struct
import subprocess
import time
import zlib

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "fpga/scu_block_scale/test_uart.py"
HELPER_SHA = "b2a86d38203d5b329e5cfe73b261fc2ac2675a0f085ec268b20249f4ceef87b2"
REFERENCE_SHA = "4da8b84c9d1d2f678491e3b02893bda0065cae1bd4f3cd838ded316f0dd1276c"


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def load_helper():
    if sha(HELPER) != HELPER_SHA:
        raise ValueError("frozen IFR3 PTY helper identity mismatch")
    spec = importlib.util.spec_from_file_location("scu_pty_negative_helper", HELPER)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    return helper


def pins(reference):
    if sha(reference) != REFERENCE_SHA:
        raise ValueError("package02 FULL reference SHA mismatch")
    lines = reference.read_text().splitlines()
    if lines[0] != "IFR3_FULL_REFERENCE_V1":
        raise ValueError("FULL reference schema")
    fields = dict(line.split(maxsplit=1) for line in lines[1:] if not line.startswith("fixture "))
    expected = {"backend": "FPGA_UART", "protocol": "3", "profile": "0810",
                "semantic_capability": "0294", "numerical_revision": "2", "mode": "FULL"}
    if any(fields.get(key) != value for key, value in expected.items()):
        raise ValueError("FULL reference semantic identity")
    fixtures = {tuple(map(int, parts[2:5])): int(parts[5])
                for parts in (line.split() for line in lines) if parts[0] == "fixture"}
    if fixtures != {(16, 16, 64): 617, (321, 48, 96): 52687, (17, 19, 64): 1949}:
        raise ValueError("package02 fixture/cycle mapping")
    return fields, fixtures


ERRORS = {
    "missing-device": "IM2P_FPGA_DEVICE must explicitly select the transport",
    "missing-reference": "missing IFR3 FULL reference outside explicit PTY RTL test",
    "wrong-reference-hardware": "IFR3 FULL reference identity/entry mismatch",
    "cap-version2": "IFR3 response version/profile mismatch",
    "cap-profile": "IFR3 response version/profile mismatch",
    "cap-capability": "IFR3 semantic capability mismatch",
    "cap-capacity": "IFR3 capability capacity mismatch",
    "full-cycle-minus1": "IFR3 FULL cycle mismatch expected=617 actual=616",
    "full-cycle-plus1": "IFR3 FULL cycle mismatch expected=617 actual=618",
    "full-crc": "IFR3 response CRC mismatch",
    "full-run": "IFR3 stale run/generation",
    "full-generation": "IFR3 stale run/generation",
    "full-count": "IFR completion count mismatch",
    "full-domain": "IFR3 semantic capability mismatch",
    "full-shape": "IFR response shape mismatch",
    "full-padding": "IFR3 wire padding mismatch",
}


def corrected_crc(data):
    data[-4:] = struct.pack("<I", zlib.crc32(data[:-4]))
    return data


def run_case(args, helper, reference_fields, cycles, name, cli=False, ppl=False):
    prefix = "ppl-" if ppl else "cli-" if cli else ""
    out = args.out / (prefix + name)
    out.mkdir()
    m, n, k = (17, 19, 64) if name == "full-padding" else (16, 16, 64)
    master, slave = pty.openpty()
    env = dict({key: value for key, value in os.environ.items() if not key.startswith("LLAMA_ARG_")},
               IM2P_FPGA_DEVICE=os.ttyname(slave),
               IM2P_FPGA_FULL_REFERENCE=str(args.reference),
               IM2P_FPGA_HARDWARE_SHA256=reference_fields["hardware_sha256"],
               IM2P_FPGA_PRODUCTION_RTL_SHA256=reference_fields["production_rtl_sha256"],
               IM2P_FPGA_TIMEOUT_SECONDS="3", GGML_BACKEND_PATH=str(args.library))
    for key in ("IM2P_FPGA_ALLOW_UNPINNED_RTL_TEST", "IM2P_FPGA_TEST_PRODUCER_OVERLAP",
                "IM2P_FPGA_TEST_WAIT_FIRST_READ", "GEMMINI_MATMUL_INVOCATION"):
        env.pop(key, None)
    env["GEMMINI_MATMUL_MODE"] = "STRIPE_PIPELINE" if cli else "FULL"
    if name == "missing-device":
        env.pop("IM2P_FPGA_DEVICE")
    elif name == "missing-reference":
        env.pop("IM2P_FPGA_FULL_REFERENCE")
    elif name == "wrong-reference-hardware":
        env["IM2P_FPGA_HARDWARE_SHA256"] = "0" * 64
    command = [str(args.graph), str(args.library), str(m), str(n), str(k), "1", "1", "FULL", "expect-failure"]
    if cli:
        command = [str(args.cli), "-m", str(args.model), "--device", "GEMMINI", "-ngl", "99",
                   "--no-warmup", "--no-conversation", "-c", "64", "-b", "16", "-ub", "16",
                   "-n", "1", "-p", "a"]
        env["IM2P_FPGA_REQUIRE_COMPLETION"] = "1"
    if ppl:
        command = [str(args.ppl), "-m", str(args.model), "--device", "GEMMINI", "-ngl", "99",
                   "--file", str(args.ppl_input), "--chunks", "1", "-c", "32", "-b", "16",
                   "-ub", "16", "-t", "1"]
        if name != "warmup-reference":
            command.append("--no-warmup")
        env["IM2P_FPGA_REQUIRE_COMPLETION"] = "1"
    result = {"case": name, "program": "llama-perplexity" if ppl else "llama-cli" if cli else "standard_graph",
              "argv": command, "mock_only": True, "expected_negative": True,
              "physical_device_access": False, "request_opcodes": [], "pass": False,
              "environment": {key: value for key, value in env.items()
                              if key.startswith(("IM2P_FPGA_", "GEMMINI_MATMUL_", "GGML_BACKEND_"))}}
    (out / "command.json").write_text(json.dumps(result, indent=2) + "\n")
    process = None
    started = time.monotonic()
    try:
        with (out / "host.log").open("x") as log:
            process = subprocess.Popen(command, cwd=out, env=env, stdout=log, stderr=subprocess.STDOUT)

            def receive(op, run=1, generation=6):
                try:
                    packet = helper.recv(master, op, run, generation)
                except TimeoutError as error:
                    result["receive_error"] = f"{type(error).__name__}: {error}"
                    if process.poll() is not None:
                        result["exit"] = process.returncode
                        result["host_log_tail"] = (out / "host.log").read_text()[-8192:]
                        raise AssertionError(
                            f"host exited {process.returncode} before expected opcode {op}; "
                            "see host.log and host_log_tail") from error
                    raise
                result["request_opcodes"].append(op)
                (out / f"request-{len(result['request_opcodes']):02d}-op{op}.bin").write_bytes(packet)
                return packet

            def send(data, label):
                (out / (label + ".bin")).write_bytes(data)
                helper.send(master, data)

            if name not in ("missing-device", "missing-reference", "wrong-reference-hardware", "warmup-reference"):
                receive(0, 0, 0)
                cap = bytearray(helper.reply(0, (336, 48, 96), 0, 5))
                if name == "cap-version2":
                    cap[:5] = b"OFR2\x02"
                elif name == "cap-profile":
                    struct.pack_into("<H", cap, 6, 0x0410)
                elif name == "cap-capability":
                    struct.pack_into("<H", cap, 30, 0x0214)
                elif name == "cap-capacity":
                    struct.pack_into("<H", cap, 24, 335)
                send(corrected_crc(cap), "mock-cap")
                if name.startswith("full-"):
                    request = receive(1)
                    if struct.unpack_from("<3H", request, 20) != (m, n, k):
                        raise AssertionError("unexpected submitted FULL shape")
                    if len(request) != 36 + m * 128 + k * 64 + k // 32 * 256:
                        raise AssertionError("unexpected final-domain input payload length")
                    works, writes = ((m + 15) // 16) * ((n + 15) // 16), m * ((n + 15) // 16)
                    actual_cycles = cycles[(m, n, k)] + (-1 if name.endswith("minus1") else 1 if name.endswith("plus1") else 0)
                    counts = (actual_cycles, works * (k // 16), works, 1, 1, writes, writes)
                    response = bytearray(helper.reply(1, (m, n, k),
                        payload=bytes(m * ((n + 15) // 16) * 16 * 4), counts=counts))
                    if name == "full-run":
                        response[8] ^= 1
                    elif name == "full-generation":
                        response[16] ^= 1
                    elif name == "full-count":
                        response[40] ^= 1
                    elif name == "full-domain":
                        struct.pack_into("<H", response, 30, 0x0214)
                    elif name == "full-shape":
                        response[24] ^= 1
                    elif name == "full-padding":
                        response[144 + n * 4] = 1
                    corrected_crc(response)
                    if name == "full-crc":
                        response[-1] ^= 1
                    send(response, "mock-full-negative")
            process.wait(timeout=30)
        output = (out / "host.log").read_text()
        result["exit"] = process.returncode
        if cli or ppl:
            if process.returncode == 0 or "FPGA_UART_ASSIGN" not in output:
                raise AssertionError("standard CLI did not propagate assigned FPGA failure")
            if ppl:
                expected_shape = "M=1 N=16 K=64" if name == "warmup-reference" else "M=16 N=16 K=64"
                if output.count("FPGA_UART_ASSIGN") != 1 or expected_shape not in output:
                    raise AssertionError("unexpected PPL assignment shape/count: " + expected_shape)
        else:
            marker = "STANDARD_GRAPH_EXPECTED_FAILURE PASS"
            if process.returncode != 0 or marker not in output:
                raise AssertionError("graph expected-failure assertions failed")
            for field in ("raw=0", "f_out=0", "completed=0", "failed=1", "caller_output_preserved=1"):
                if field not in output:
                    raise AssertionError("graph result missing " + field)
        expected_error = ("IFR3 FULL reference fixture/shape missing: m1n16k64"
                          if name == "warmup-reference" else ERRORS[name])
        if expected_error not in output:
            raise AssertionError("wrong failure gate; expected: " + expected_error)
        if name == "warmup-reference" and "FPGA_UART_WARMUP_FAIL phase=decode" not in output:
            raise AssertionError("default warm-up failure did not propagate from decode")
        if "SCU_INDEPENDENT_REFERENCE" in output or "GRAPH_REFERENCE PASS" in output:
            raise AssertionError("negative mock response reached numerical success observer")
        remaining = bytearray()
        while select.select([master], [], [], 0)[0]:
            remaining.extend(os.read(master, 4096))
        (out / "commands-after-failure.bin").write_bytes(remaining)
        result["bytes_after_failure"] = len(remaining)
        if remaining:
            raise AssertionError("device command after failure")
        result["pass"] = True
    except Exception as error:
        result["error"] = f"{type(error).__name__}: {error}"
    finally:
        if process is not None and process.poll() is None:
            result["local_test_child_terminated"] = True
            process.terminate()
            process.wait(timeout=5)
        os.close(master)
        os.close(slave)
        result["wall_seconds"] = time.monotonic() - started
        (out / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("graph", "library", "reference", "out"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--cli", type=Path)
    parser.add_argument("--ppl", type=Path)
    parser.add_argument("--ppl-input", type=Path)
    parser.add_argument("--model", type=Path)
    args = parser.parse_args()
    if bool(args.cli or args.ppl) != bool(args.model):
        parser.error("--model and at least one of --cli/--ppl are required together")
    if bool(args.ppl) != bool(args.ppl_input):
        parser.error("--ppl and --ppl-input are required together")
    for key in ("graph", "library", "reference", "cli", "ppl", "ppl_input", "model"):
        if getattr(args, key):
            setattr(args, key, getattr(args, key).resolve(strict=True))
    fields, fixtures = pins(args.reference)
    helper = load_helper()
    args.out = args.out.resolve()
    args.out.mkdir(parents=True, exist_ok=False)
    files = [Path(__file__), HELPER, args.graph, args.library, args.reference]
    files.extend(path for path in (args.cli, args.ppl, args.ppl_input, args.model) if path)
    identity = {str(path): sha(path) for path in files}
    (args.out / "input-sha256.json").write_text(json.dumps(identity, indent=2) + "\n")
    results = []
    cases = [(name, False, False) for name in ERRORS]
    if args.cli:
        cases.append(("cap-capability", True, False))
    if args.ppl:
        cases.extend((("cap-capability", False, True), ("warmup-reference", False, True)))
    for name, cli, ppl in cases:
        result = run_case(args, helper, fields, fixtures, name, cli, ppl)
        results.append(result)
        print(f"{result['program']} {name}: {'EXPECTED_NEGATIVE_PASS' if result['pass'] else 'UNEXPECTED_FAILURE'}", flush=True)
        if not result["pass"]:
            break
    unchanged = all(sha(Path(path)) == digest for path, digest in identity.items())
    summary = {"pass": len(results) == len(cases) and all(row["pass"] for row in results) and unchanged,
               "results": results, "planned": len(cases), "completed": len(results),
               "inputs_unchanged": unchanged, "mock_only": True, "physical_device_access": False}
    (args.out / "results.json").write_text(json.dumps(summary, indent=2) + "\n")
    return 0 if summary["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
