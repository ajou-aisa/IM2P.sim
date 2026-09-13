#!/usr/bin/env python3
"""Isolated tests of the production CMake fingerprint section; no ABI/build/device test."""
import argparse
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out = args.out.resolve()
    args.out.mkdir(parents=True, exist_ok=False)
    helper = (ROOT / "host-overlay/cmake/ggml-gemmini-fpga.cmake").read_text()
    section = helper[helper.index("# The cache key records"):]
    cases = [
        ("quantizer-header", "ggml/src/ggml-gemmini/quants/view/test.hpp", None),
        ("backend-control", "ggml/src/ggml-gemmini/ggml-gemmini.cpp", None),
        ("rmd-mode-header", "generated/ggml-gemmini-matmul-config.hpp", None),
        ("params-header", "params/gemmini_params.h", None),
        ("build-type", None, "-DCMAKE_BUILD_TYPE=Debug"),
        ("c-release-flags", None, "-DCMAKE_C_FLAGS_RELEASE=-O1"),
        ("cxx-debug-flags", None, "-DCMAKE_CXX_FLAGS_DEBUG=-Og"),
        ("module-link-flags", None, "-DCMAKE_MODULE_LINKER_FLAGS=-Wl,--as-needed"),
        ("target", None, "-DCMAKE_CXX_COMPILER_TARGET=aarch64-linux-gnu"),
        ("dynamic-linkage", None, "-DGGML_BACKEND_DL=OFF"),
    ]
    files = [
        "manifest.json", "core/sim/include/im2p_sim.h",
        "core/frontend/include/im2p_gemmini_frontend.hpp",
        "core/frontend/src/im2p_gemmini_frontend.cpp",
        "core/fpga/ggml-gemmini-fpga.hpp", "core/fpga/ggml-gemmini-fpga.cpp",
        "core/fpga/uart.hpp", "core/fpga/uart.cpp", "ggml/CMakeLists.txt",
        "ggml/src/CMakeLists.txt", "ggml/src/ggml-quants.c", "ggml/src/ggml-quants.h",
        "ggml/src/ggml-common.h", "ggml/include/ggml.h",
        "ggml/src/ggml-gemmini/CMakeLists.txt",
        "ggml/src/ggml-gemmini-utils/include/test.h",
    ] + [path for _, path, _ in cases if path]
    records = []
    for name, mutation, override in cases:
        source = args.out / name
        source.mkdir()
        for relative in files:
            path = source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("// isolated fingerprint input\n")
        (source / "fingerprint.cmake").write_text(section)
        (source / "CMakeLists.txt").write_text("""cmake_minimum_required(VERSION 3.16)
project(fingerprint_contract NONE)
set(IM2P_SIM_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/core")
set(GGML_GEMMINI_FPGA_SOURCE_DIR "${IM2P_SIM_ROOT}/fpga")
set(GGML_GEMMINI_FPGA_SIM_MANIFEST "${CMAKE_CURRENT_SOURCE_DIR}/manifest.json")
set(GGML_GEMMINI_GENERATED_CONFIG_DIR "${CMAKE_CURRENT_SOURCE_DIR}/generated")
set(GEMMINI_SW_PATH "${CMAKE_CURRENT_SOURCE_DIR}/params")
include(fingerprint.cmake)
""")
        command = ["cmake", "-S", str(source), "-B", str(source / "build"),
                   "-DCMAKE_BUILD_TYPE=Release", "-DGGML_BACKEND_DL=ON", "-DBUILD_SHARED_LIBS=ON"]
        attempts = []
        for phase in ("initial", "unchanged", "mutated"):
            argv = list(command)
            if phase == "mutated":
                if mutation:
                    with (source / mutation).open("a") as stream:
                        stream.write("// isolated expected cache rejection mutation\n")
                else:
                    argv.append(override)
            result = subprocess.run(argv, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            (source / (phase + ".log")).write_text(result.stdout)
            passed = (result.returncode != 0 and "fresh build directory" in result.stdout
                      if phase == "mutated" else result.returncode == 0)
            attempts.append({"phase": phase, "argv": argv, "exit": result.returncode, "pass": passed})
            if not passed:
                break
        records.append({"case": name, "attempts": attempts,
                        "pass": len(attempts) == 3 and all(row["pass"] for row in attempts)})
        if not records[-1]["pass"]:
            break
    summary = {"scope": "fingerprint-only CMake harness; no numerical/ABI/build evidence",
               "results": records, "pass": len(records) == len(cases) and all(row["pass"] for row in records)}
    (args.out / "results.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(f"Fingerprint gates: {sum(row['pass'] for row in records)}/{len(cases)} PASS")
    return 0 if summary["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
