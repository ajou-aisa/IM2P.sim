# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
# How to run: PYTHONPATH=IM2P.sim python3 -B -m sim.tests.cycle.actual_trace_capture OUT TIMEOUT BUDGET_GIB -- PROBE ARGS...
"""Run one RTL-then-model probe and retain both complete raw streams with restored-digest checks."""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import resource
import selectors
import shutil
import signal
import subprocess
import sys
import time
from contextlib import ExitStack
from pathlib import Path
from typing import Final

from sim.cycle.reconstruct_graph import sha256

SIDES: Final = ("rtl", "model")
MIN_FREE_BYTES: Final = 3 * 1024**3 // 2
MESH_PREFIX: Final = {"rtl": b"RTL_MESH_STATE_V1 ", "model": b"MODEL_MESH_STATE_V1 "}


def capture(output: Path, timeout: float, budget: int, command: list[str]) -> dict[str, object]:
    for side in SIDES:
        (output / side).mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    counts = {side: 0 for side in SIDES}
    lines, mesh_lines = dict(counts), dict(counts)
    hashes = {side: hashlib.sha256() for side in SIDES}
    mesh_hashes = {side: hashlib.sha256() for side in SIDES}
    side, error, blocks = "rtl", None, 0
    with ExitStack() as stack:
        files = {key: stack.enter_context((output / key / "raw.log.gz").open("xb")) for key in SIDES}
        mesh_files = {key: stack.enter_context((output / key / "mesh.jsonl.gz").open("xb")) for key in SIDES}
        streams = {key: stack.enter_context(gzip.GzipFile(fileobj=file, mode="wb", compresslevel=6, mtime=0))
                   for key, file in files.items()}
        mesh_streams = {key: stack.enter_context(gzip.GzipFile(fileobj=file, mode="wb", compresslevel=6, mtime=0))
                        for key, file in mesh_files.items()}
        diagnostics = stack.enter_context((output / "stderr.log").open("xb"))
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=diagnostics, start_new_session=True)
        if process.stdout is None:
            raise ValueError("probe stdout missing")
        stack.enter_context(process.stdout)
        selector = stack.enter_context(selectors.DefaultSelector())
        selector.register(process.stdout, selectors.EVENT_READ)
        pending = b""
        try:
            while True:
                remaining = timeout - (time.monotonic() - started)
                if remaining <= 0 or not selector.select(remaining):
                    raise TimeoutError("probe deadline reached")
                block = os.read(process.stdout.fileno(), 1 << 20)
                if not block:
                    if pending:
                        raise ValueError("truncated final raw line")
                    break
                pending += block
                *complete, pending = pending.split(b"\n")
                for body in complete:
                    line = body + b"\n"
                    if line.startswith(b"MODEL_RUN "):
                        side = "model"
                    counts[side] += len(line)
                    lines[side] += 1
                    hashes[side].update(line)
                    streams[side].write(line)
                    if line.startswith(MESH_PREFIX[side]):
                        payload = line.removeprefix(MESH_PREFIX[side])
                        mesh_streams[side].write(payload)
                        mesh_hashes[side].update(payload)
                        mesh_lines[side] += 1
                    if line.startswith((b"COMPOSITION_WORK ", b"MODEL_WORK ")):
                        row = json.loads(line.split(b" ", 1)[1])
                        if row["ordinal"] % 16 == 15:
                            print("ACTUAL_CAPTURE_PROGRESS " + json.dumps({
                                "side": side, "ordinal": row["ordinal"], "work_id": row["work_id"],
                                "resource_ready": row["resource_ready"], "raw_bytes": counts[side],
                                "elapsed_seconds": round(time.monotonic() - started)}), flush=True)
                    elif line.startswith(b"COMPOSITION_FAIL "):
                        print(line.decode().rstrip(), flush=True)
                if len(pending) > 1 << 20:
                    raise ValueError("unterminated raw line exceeds 1 MiB")
                blocks += 1
                if blocks % 256 == 0:
                    if sum(file.tell() for file in (*files.values(), *mesh_files.values())) > budget:
                        raise ValueError(f"compressed streams exceed {budget} bytes")
                    if shutil.disk_usage(output).free < MIN_FREE_BYTES:
                        raise ValueError("free disk below 1.5 GiB")
            process.wait(timeout=max(0.01, timeout - (time.monotonic() - started)))
        except (OSError, ValueError, TimeoutError, subprocess.TimeoutExpired) as caught:
            error = str(caught)
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
            returncode = process.returncode
    artifacts: dict[str, object] = {}
    for key in SIDES:
        restored = {}
        for name, digest in (("raw.log.gz", hashes[key]), ("mesh.jsonl.gz", mesh_hashes[key])):
            with gzip.open(output / key / name, "rb") as stream:
                restored[name] = hashlib.file_digest(stream, "sha256").hexdigest()
            if restored[name] != digest.hexdigest():
                error = error or f"{key}/{name} compressed/restored digest differs"
        artifacts[key] = {
            "raw": {"path": str(output / key / "raw.log.gz"), "sha256": sha256(output / key / "raw.log.gz"),
                    "restored_sha256": restored["raw.log.gz"], "raw_bytes": counts[key], "lines": lines[key]},
            "mesh": {"path": str(output / key / "mesh.jsonl.gz"), "sha256": sha256(output / key / "mesh.jsonl.gz"),
                     "restored_sha256": restored["mesh.jsonl.gz"], "lines": mesh_lines[key]}}
    return {
        "schema": "im2p-actual-trace-bounded-capture-v1",
        "status": "PASS_CAPTURE" if error is None and returncode == 0 else "FAILED_CAPTURE",
        "command": command, "cwd": os.getcwd(), "timeout_seconds": timeout, "compressed_budget_bytes": budget,
        "artifacts": artifacts, "child_returncode": returncode, "child_reaped": process.poll() is not None,
        "error": error, "wall_seconds": time.monotonic() - started,
        "child_peak_rss_bytes": resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
        "harness": {"path": str(Path(__file__)), "sha256": sha256(Path(__file__))}}


def main() -> int:
    output, timeout, budget_gib, separator, *command = sys.argv[1:]
    if separator != "--" or not command or not 0 < float(timeout) <= 43200:
        raise ValueError("usage: OUT TIMEOUT BUDGET_GIB -- PROBE ARGS...")
    receipt = capture(Path(output), float(timeout), int(float(budget_gib) * 1024**3), command)
    with (Path(output) / "capture.json").open("x") as stream:
        stream.write(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    print("ACTUAL_CAPTURE_FINISHED " + json.dumps({key: receipt[key] for key in (
        "status", "error", "child_returncode", "wall_seconds")}), flush=True)
    return 0 if receipt["status"] == "PASS_CAPTURE" else 1


if __name__ == "__main__":
    raise SystemExit(main())
