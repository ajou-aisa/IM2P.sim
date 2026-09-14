#!/usr/bin/env python3
"""Isolated tests of the production CMake fingerprint section; no ABI/build/device test."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--host", type=Path, default=ROOT.parents[2] / "llama.cpp-gemmini")
    args = parser.parse_args()
    args.out = args.out.resolve()
    args.out.mkdir(parents=True, exist_ok=False)
    helper_path = args.host.resolve() / "cmake/ggml-gemmini-fpga.cmake"
    helper = helper_path.read_text()
    section = helper[helper.index("# The cache key records"):]
    cases = [
        ("quantizer-header", "ggml/src/ggml-gemmini/quants/view/test.hpp", None),
        ("backend-control", "ggml/src/ggml-gemmini/ggml-gemmini.cpp", None),
        ("window-transport", "core/fpga/scu_block_scale/window_uart.cpp", None),
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
        "core/sim/include/im2p_sim.h",
        "core/frontend/include/im2p_gemmini_frontend.hpp",
        "core/frontend/src/im2p_gemmini_frontend.cpp",
        "core/fpga/scu_block_scale/ggml-gemmini-fpga.hpp", "core/fpga/scu_block_scale/ggml-gemmini-fpga.cpp",
        "core/fpga/scu_block_scale/uart.hpp", "core/fpga/scu_block_scale/uart.cpp",
        "core/fpga/scu_block_scale/rtl_plugin.hpp", "core/fpga/scu_block_scale/window_uart.hpp",
        "core/fpga/scu_block_scale/window_uart.cpp", "core/fpga/scu_block_scale/window_protocol.hpp",
        "ggml/CMakeLists.txt",
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
set(GGML_GEMMINI_FPGA_SOURCE_DIR "${IM2P_SIM_ROOT}/fpga/scu_block_scale")
set(GGML_GEMMINI_FPGA_ABI_RUNTIME "NOT_RUN_fingerprint_only")
set(GGML_GEMMINI_GENERATED_CONFIG_DIR "${CMAKE_CURRENT_SOURCE_DIR}/generated")
set(GEMMINI_SW_PATH "${CMAKE_CURRENT_SOURCE_DIR}/params")
include(fingerprint.cmake)
file(WRITE "${CMAKE_CURRENT_BINARY_DIR}/build-id.txt" "${GGML_GEMMINI_FPGA_BUILD_ID}\n")
""")
        command = ["cmake", "-S", str(source), "-B", str(source / "build"),
                   "-DCMAKE_BUILD_TYPE=Release", "-DGGML_BACKEND_DL=ON", "-DBUILD_SHARED_LIBS=ON"]
        attempts = []
        for phase in ("initial", "unchanged", "mutated", "mutated-unchanged"):
            argv = list(command)
            if phase == "mutated":
                if mutation:
                    with (source / mutation).open("a") as stream:
                        stream.write("// isolated reconfiguration input mutation\n")
            if phase.startswith("mutated") and override:
                argv.append(override)
            result = subprocess.run(argv, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            (source / (phase + ".log")).write_text(result.stdout)
            fingerprint = None
            passed = result.returncode == 0
            if passed:
                cache = (source / "build/CMakeCache.txt").read_text().splitlines()
                fingerprint = next(line.split("=", 1)[1] for line in cache
                                   if line.startswith("GGML_GEMMINI_FPGA_CONFIG_FINGERPRINT:INTERNAL="))
                contract = (source / "build/fpga-build-contract.txt").read_text().splitlines()
                recorded = next(line.split("=", 1)[1] for line in contract if line.startswith("fingerprint="))
                build_id = (source / "build/build-id.txt").read_text().strip()
                passed = len(fingerprint) == 64 and fingerprint == recorded == build_id
                if attempts:
                    changed = fingerprint != attempts[-1]["fingerprint"]
                    passed = passed and (changed if phase == "mutated" else not changed)
            attempts.append({"phase": phase, "argv": argv, "exit": result.returncode,
                             "fingerprint": fingerprint, "cache_contract_build_id_match": passed,
                             "pass": passed})
            if not passed:
                break
        records.append({"case": name, "attempts": attempts,
                        "pass": len(attempts) == 4 and all(row["pass"] for row in attempts)})
        if not records[-1]["pass"]:
            break
    summary = {"scope": "fingerprint-only CMake harness; no numerical/ABI/build evidence",
               "production_helper": str(helper_path),
               "production_helper_sha256": hashlib.sha256(helper.encode()).hexdigest(),
               "results": records, "pass": len(records) == len(cases) and all(row["pass"] for row in records)}
    (args.out / "results.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(f"Fingerprint gates: {sum(row['pass'] for row in records)}/{len(cases)} PASS")
    return 0 if summary["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
