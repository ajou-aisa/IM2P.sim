#!/usr/bin/env python3
"""Real root-CMake configure gates; no build, model, or device operation."""
import argparse
import json
import subprocess
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("host", "core", "params", "manifest", "out"):
        parser.add_argument("--" + name, required=True, type=Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    base = [
        "cmake", "-S", str(args.host.resolve()),
        "-DGGML_GEMMINI=ON", "-DGGML_GEMMINI_EXECUTION_BACKEND=FPGA_UART",
        "-DGGML_GEMMINI_OPTION=WS", "-DGGML_GEMMINI_COMPUTE_TYPE=INT",
        "-DGGML_GEMMINI_ACTIVATION_QUANT=EXSIA", "-DGGML_GEMMINI_ACTIVATION_BITS=8",
        "-DGGML_GEMMINI_WEIGHT_BITS=8", "-DGGML_GEMMINI_DIM=16",
        "-DGGML_GEMMINI_BLOCK_SIZE=32", "-DGGML_GEMMINI_ENABLE_RMD=OFF",
        "-DBUILD_SHARED_LIBS=ON", "-DGGML_BACKEND_DL=ON", "-DGGML_NATIVE=OFF",
        "-DGGML_CCACHE=OFF", "-DLLAMA_CURL=OFF", "-DLLAMA_BUILD_TESTS=OFF",
        "-DLLAMA_BUILD_SERVER=OFF", "-DGGML_GEMMINI_ENABLE_OPENMP=OFF",
        f"-DIM2P_SIM_ROOT={args.core.resolve()}",
        f"-DGEMMINI_SW_PATH={args.params.resolve()}",
        f"-DGGML_GEMMINI_FPGA_SIM_MANIFEST={args.manifest.resolve()}",
    ]
    cases = [
        ("valid", [], None),
        ("missing-manifest", ["-DGGML_GEMMINI_FPGA_SIM_MANIFEST="], "missing selected input"),
        ("dim64", ["-DGGML_GEMMINI_DIM:STRING=64"], "requires WS+A8/W8/DIM16"),
        ("cpu", ["-DGGML_GEMMINI_OPTION=CPU"], "requires WS+A8/W8/DIM16"),
        ("rmd", ["-DGGML_GEMMINI_ENABLE_RMD=ON"], None),
        ("alias-conflict", ["-DIM2P_DIM=64"], "IM2P_DIM conflicts"),
    ]
    results = []
    for name, overrides, error in cases:
        command = base + ["-B", str(args.out / name)] + overrides
        result = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        (args.out / (name + ".log")).write_text(result.stdout)
        passed = result.returncode == 0 if error is None else result.returncode != 0 and error in result.stdout
        results.append({"case": name, "argv": command, "exit": result.returncode,
                        "expected": "configure_pass" if error is None else error, "pass": passed})
        if not passed:
            break
    if all(row["pass"] for row in results):
        command = base + ["-B", str(args.out / "valid"), "-DGGML_BACKEND_DL=OFF"]
        result = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        (args.out / "stale-linkage.log").write_text(result.stdout)
        results.append({"case": "stale-linkage", "argv": command, "exit": result.returncode,
                        "expected": "fresh build directory", "pass": result.returncode != 0 and
                        "fresh build directory" in result.stdout})
    (args.out / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    print(f"CMake contract: {sum(row['pass'] for row in results)}/{len(results)} PASS")
    return 0 if len(results) == 7 and all(row["pass"] for row in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
