#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = Path(os.environ.get("IM2P_WORKSPACE_ROOT", ROOT.parent))
LLAMA_ROOT = WORKSPACE_ROOT / "llama.cpp-gemmini"
INCLUDE_ROOT = WORKSPACE_ROOT / "RISC-V-DynDNN-gemmini-include"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def local_includes() -> tuple[Path, ...]:
    return (
        LLAMA_ROOT / "ggml/src/ggml-gemmini",
        LLAMA_ROOT / "ggml/src/ggml-gemmini-utils/include",
        LLAMA_ROOT / "ggml/include",
        LLAMA_ROOT / "ggml/src",
        LLAMA_ROOT / "common",
        INCLUDE_ROOT,
    )


def source_text() -> str:
    return r'''
#include <gemmini_params.h>
#include <gemmini.h>
#include <cstdio>
extern "C" void gemmini_log_debug_layer(const char *, const char *, ...) noexcept {}
int main(int argc, char **argv) {
  if (argc != 4) return 2;
  ggml_gemmini_args_t args{};
  args.I = std::strtoull(argv[1], nullptr, 10);
  args.J = std::strtoull(argv[2], nullptr, 10);
  args.K = std::strtoull(argv[3], nullptr, 10);
  args.matmul_layer = "memory-contract-probe";
  ggml::gemmini::gemmini_set_tile_ws(&args);
  const auto geometry = args.activation_geometry();
  if (!geometry.ok()) return 3;
  std::printf(
      "{\"dim\":%d,\"bank_count\":%d,\"bank_rows\":%d,"
      "\"accumulator_rows\":%d,\"host_operand_bytes\":%zu,"
      "\"accumulator_bytes\":%zu,\"tile_i\":%zu,\"tile_j\":%zu,"
      "\"tile_k\":%zu,\"outer_i\":%zu,\"outer_j\":%zu,"
      "\"outer_k\":%zu,\"stripe_h\":%zu,\"stripe_count\":%zu,"
      "\"final_rows\":%zu,\"ws_inner_calls\":%zu}\n",
      DIM, BANK_NUM, BANK_ROWS, ACC_ROWS, sizeof(elem_t), sizeof(acc_t),
      args.tile_I, args.tile_J, args.tile_K, geometry.geometry.outer.i,
      geometry.geometry.outer.j, geometry.geometry.outer.k,
      geometry.geometry.stripe_rows, geometry.geometry.stripe_count,
      geometry.geometry.final_rows, geometry.geometry.ws_inner_calls);
  return 0;
}
'''


def compiler_flags(profile: dict[str, object], params: Path) -> list[str]:
    bits = profile.get("activation_bits")
    dim = profile.get("dim")
    if not isinstance(bits, int) or isinstance(bits, bool) or \
       not isinstance(dim, int) or isinstance(dim, bool):
        raise RuntimeError("profile activation bits or DIM is not an integer")
    return [
        "-std=c++20",
        f"-DGGML_GEMMINI_ACTIVATION_BITS={bits}",
        f"-DGGML_GEMMINI_WEIGHT_BITS={bits}",
        f"-DGGML_GEMMINI_CONFIGURED_DIM={dim}",
        "-DGGML_GEMMINI_ENABLE_RMD=0",
        f"-I{params.parent}",
        *(f"-I{path}" for path in local_includes()),
    ]


def run_checked(arguments: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(arguments, cwd=cwd, text=True, capture_output=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(
            f"command failed ({completed.returncode}): {' '.join(arguments)}\n"
            f"{completed.stderr.strip()}"
        )
    return completed


def parse_macros(output: str) -> dict[str, int]:
    wanted = {"DIM", "BANK_NUM", "BANK_ROWS", "ACC_ROWS"}
    values: dict[str, int] = {}
    for line in output.splitlines():
        fields = line.split()
        if len(fields) == 3 and fields[0] == "#define" and fields[1] in wanted:
            values[fields[1]] = int(fields[2])
    if set(values) != wanted:
        raise RuntimeError("preprocessor did not expose required Gemmini parameters")
    return values


def probe(manifest: Path, m: int, n: int, k: int, cxx: str) -> dict[str, object]:
    profile = json.loads(manifest.read_text(encoding="utf-8"))
    if not isinstance(profile, dict):
        raise RuntimeError(f"profile is not an object: {manifest}")
    params = manifest.parent / "host-params/gemmini_params.h"
    if not params.is_file():
        raise RuntimeError(f"generated host parameters missing: {params}")
    compiler = shutil.which(cxx)
    if compiler is None:
        raise RuntimeError(f"C++ compiler missing: {cxx}")
    flags = compiler_flags(profile, params)
    with tempfile.TemporaryDirectory(prefix="im2p-gemmini-memory-probe-") as temporary:
        work = Path(temporary)
        source = work / "probe.cpp"
        executable = work / "probe"
        source.write_text(source_text(), encoding="utf-8")
        macros = parse_macros(run_checked(
            [compiler, *flags, "-dM", "-E", str(source)], work,
        ).stdout)
        trace = run_checked(
            [compiler, *flags, "-H", "-fsyntax-only", str(source)], work,
        ).stderr.splitlines()
        run_checked([compiler, *flags, str(source), "-o", str(executable)], work)
        runtime = json.loads(run_checked(
            [str(executable), str(m), str(n), str(k)], work,
        ).stdout)
    memory = profile.get("memory")
    if not isinstance(memory, dict):
        raise RuntimeError("profile memory contract missing")
    expected = {
        "DIM": profile.get("dim"),
        "BANK_NUM": memory.get("bank_count"),
        "BANK_ROWS": memory.get("bank_rows"),
        "ACC_ROWS": memory.get("accumulator_rows"),
    }
    runtime_expected = {
        "dim": expected["DIM"],
        "bank_count": expected["BANK_NUM"],
        "bank_rows": expected["BANK_ROWS"],
        "accumulator_rows": expected["ACC_ROWS"],
    }
    if macros != expected or any(runtime[key] != value for key, value in runtime_expected.items()):
        raise RuntimeError(
            f"compiler profile mismatch: {manifest}: macros={macros} expected={expected} runtime={runtime}"
        )
    dim = int(profile["dim"])
    tile_i = int(runtime["tile_i"])
    tile_j = int(runtime["tile_j"])
    tile_k = int(runtime["tile_k"])
    sp_rows = dim * (tile_i * tile_k + tile_k * tile_j)
    acc_rows = dim * tile_i * tile_j
    if sp_rows > int(memory["ws_scratchpad_rows_per_buffer"]) or \
       acc_rows > int(memory["ws_accumulator_rows_per_buffer"]):
        raise RuntimeError(f"tiler exceeds memory contract: {manifest}")
    traced = []
    for line in trace:
        candidate = Path(line.lstrip(". "))
        if candidate.name in {"gemmini.h", "gemmini_params.h", "ggml-gemmini-args.h"} \
           and candidate.is_file():
            traced.append({"path": str(candidate.resolve()), "sha256": sha256(candidate)})
    return {
        "profile": profile["profile"],
        "manifest": str(manifest.resolve()),
        "manifest_sha256": sha256(manifest),
        "compiler": str(Path(compiler).resolve()),
        "selected_parameter_header": {
            "path": str(params.resolve()),
            "sha256": sha256(params),
        },
        "shape": {"m": m, "n": n, "k": k},
        "preprocessor": macros,
        "runtime": runtime,
        "rows": {"scratchpad_budget": sp_rows, "accumulator_budget": acc_rows},
        "bytes": {
            "host_operand_storage": runtime["host_operand_bytes"],
            "wire_scratchpad_row": memory["scratchpad_row_bytes"],
            "accumulator_row": memory["accumulator_row_bytes"],
        },
        "include_trace": traced,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--build-root", type=Path, required=True)
    parser.add_argument("--m", type=int, default=129)
    parser.add_argument("--n", type=int, default=129)
    parser.add_argument("--k", type=int, default=96)
    parser.add_argument("--cxx", default=os.environ.get("CXX", "c++"))
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if min(args.m, args.n, args.k) <= 0 or os.path.lexists(args.out):
        raise SystemExit("positive shape and new output required")
    manifests = sorted(args.build_root.glob("*/resolved-profile.json"))
    if len(manifests) != 6:
        raise SystemExit(f"six resolved profiles required, found {len(manifests)}")
    try:
        profiles = [probe(path, args.m, args.n, args.k, args.cxx) for path in manifests]
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, json.JSONDecodeError) as error:
        print(str(error), file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({
        "schema_version": 1,
        "status": "PASS",
        "profiles": profiles,
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
