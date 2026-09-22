#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
# Run: uv run tests/test_gemmini_export_closure.py
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from scripts.gemmini_export import (
    HOST_LLAMA_TRACE_OFF_SOURCES, REQUIRED_SOURCE_FILES, ExportError, ExportRequest, JsonValue, create_export, extract_export,
    linux_instructions, verify_export,
)
from test_gemmini_export import refresh_sums, source_fixture


def test_required_source_closure_when_checksums_are_recomputed() -> None:
    # Given: a source-only package intended to rebuild the official host.
    with tempfile.TemporaryDirectory(prefix="gemmini-source-closure-") as directory:
        base = Path(directory)
        source = base / "source"
        source_fixture(source)
        package = base / "package"
        create_export(ExportRequest(source, None, package, None))
        required = (
            "source/sim/include/im2p_compact_runs.h",
            "source/sim/cycle/npu_trace_runs.py",
            "source/sim/cycle/run_aware_certificate.py",
            "source/sim/cycle/reconstruct_npu.py",
            "source/frontend/include/im2p_cpu_functional.hpp",
            "source/frontend/src/im2p_cpu_functional.cpp",
            "source/sim/ffi/im2p_geometry_ffi.h",
            "source/sim/backends/gemmini_hp1/runtime.hpp",
            "source/sim/backends/gemmini_hp1/runtime.cpp",
            "source/sim/backends/gemmini_hp1/backing_memory.cpp",
            "source/sim/tests/cycle/test_run_aware_cycle.cpp",
            "source/sim/tests/cycle/run_aware_corpus.json",
            "source/sim/cycle/corpus-authority-v3.json",
            "source/sim/tests/cycle/production_run_work.py",
            "source/sim/cycle/run_aware_production_evidence.py",
            "source/sim/tests/cycle/production_run_aware_corpus.json",
            "source/sim/tests/cycle/certify_production_run_aware.py",
            "source/sim/tests/cycle/run_aware_fixture.py",
            "source/sim/tests/cycle/run_aware_events.py",
            "source/sim/tests/cycle/certify_run_aware.py",
            "source/sim/cycle/corpus-authority-v2.json",
            "source/sim/tests/cycle/passive_log.py",
            "source/tests/test_production_trace_build.py",
            "source/fpga/gemmini_hp1/host/CMakeLists.txt",
            "source/fpga/gemmini_hp1/host/rmd.hpp",
            "source/fpga/gemmini_hp1/host/rmd.cpp",
            "source/fpga/gemmini_hp1/host/run_aware_rtl_driver.inc",
            "source/fpga/gemmini_hp1/host/test_run_aware_rtl.cpp",
            "source/fpga/gemmini_hp1/host/test_run_aware_bridge.cpp",
            "source/sim/include/im2p_geometry.h",
            "source/frontend/include/im2p_production_trace.hpp",
            "source/scripts/im2p_paths.py",
            "dependency/source/llama_cpp_gemmini/ggml/src/ggml-gemmini-utils/src/optrace.cpp",
            "dependency/source/llama_cpp_gemmini/ggml/src/ggml-gemmini/residual/rmd/rmd-run-aware.cpp",
            "dependency/source/llama_cpp_gemmini/ggml/src/ggml-gemmini-utils/src/debug.cpp",
            "dependency/source/llama_cpp_gemmini/ggml/src/ggml-gemmini-utils/src/semantic.cpp",
            "dependency/source/llama_cpp_gemmini/ggml/src/ggml-gemmini-utils/include/gemmini/semantic.hpp",
            "dependency/source/llama_cpp_gemmini/ggml/src/ggml-gemmini-utils/include/gemmini/semantic.h",
        )
        for index, name in enumerate(required):
            assert (package / name).is_file(), f"source closure missing: {name}"
            mutated = base / f"missing-{index}"
            shutil.copytree(package, mutated)
            # When: a test copy loses a required input but its inventory is refreshed.
            (mutated / name).unlink()
            refresh_sums(mutated)
            try:
                verify_export(mutated)
            except ExportError as error:
                # Then: dependency closure rejects independently of SHA256SUMS.
                assert "closure" in str(error) and name in str(error), str(error)
            else:
                raise AssertionError(f"missing source admitted after rehash: {name}")


def test_packaged_verifier_when_run_from_its_own_archive() -> None:
    # Given: a package containing its real verification entry point.
    with tempfile.TemporaryDirectory(prefix="gemmini-self-verify-") as directory:
        base = Path(directory)
        source = base / "source"
        source_fixture(source)
        for name in ("gemmini_export.py", "gemmini_board.py", "gemmini_evidence.py", "im2p_paths.py",
                     "gemmini_replay_contract.py", "gemmini_resolve_profile.py",
                     "real_lib_manifest.py", "im2p_config.py"):
            shutil.copyfile(ROOT / "scripts" / name, source / "scripts" / name)
        package = base / "package"
        create_export(ExportRequest(source, None, package, None))
        # When: the packaged CLI verifies its own immutable inventory.
        completed = subprocess.run(
            (sys.executable, str(package / "source/scripts/gemmini_export.py"), "verify", str(package)),
            cwd=base, text=True, capture_output=True, check=False,
        )
        # Then: importing its helpers must not mutate that inventory.
        assert completed.returncode == 0, completed.stderr
        assert not tuple(package.rglob("*.pyc"))


def test_pinned_trace_off_package_when_producer_has_no_optrace() -> None:
    # Given: a pinned-style llama source with only the official trace-OFF host closure.
    with tempfile.TemporaryDirectory(prefix="gemmini-pinned-export-") as directory:
        base = Path(directory)
        source, llama, package = base / "source", base / "pinned-develop", base / "package"
        source_fixture(source, llama, trace_enabled=False)
        # When: export explicitly selects that root and trace capability.
        result = create_export(ExportRequest(source, None, package, None, llama, False))
        # Then: the selected source and manifest remain coherent after relocation.
        assert result.relocation_verified and verify_export(package).status == "PASS"
        assert all((package / "dependency/source/llama_cpp_gemmini" / name).is_file()
                   for name in HOST_LLAMA_TRACE_OFF_SOURCES)
        assert not (package / "dependency/source/llama_cpp_gemmini/ggml/src/ggml-gemmini-utils/src/optrace.cpp").exists()
        assert '"production_trace_enabled": false' in (package / "source-manifest.json").read_text()
        repository: dict[str, JsonValue] = {"remote": "fixture", "head": "fixture", "tracked_patch": "fixture.patch"}
        repositories: dict[str, JsonValue] = {
            "chipyard": repository, "llama_cpp_gemmini": repository, "gemmini_include": repository,
        }
        instructions = linux_instructions({"repositories": repositories}, False)
        assert "-DIM2P_PRODUCTION_TRACE_ENABLED=OFF" in instructions
        for name in ("ggml/src/ggml-gemmini-utils/src/performance.cpp",
                     "ggml/src/ggml-gemmini-utils/src/trace-context.cpp", "common/json.hpp"):
            mutated = base / f"missing-{Path(name).name}"
            shutil.copytree(package, mutated)
            (mutated / "dependency/source/llama_cpp_gemmini" / name).unlink()
            refresh_sums(mutated)
            try:
                verify_export(mutated)
            except ExportError as error:
                assert "closure" in str(error), str(error)
            else:
                raise AssertionError(f"missing pinned source admitted after rehash: {name}")
        try:
            create_export(ExportRequest(source, None, base / "forced-on", None, llama, True))
        except ExportError as error:
            assert "optrace" in str(error), str(error)
        else:
            raise AssertionError("trace ON accepted missing real optrace API")


def test_relocated_run_headers_and_fixture_cli() -> None:
    # Given: a package built from the real public headers and Python entry points.
    with tempfile.TemporaryDirectory(prefix="gemmini-run-relocation-") as directory:
        base = Path(directory)
        source = base / "source"
        source_fixture(source)
        real_files = (
            "sim/include/im2p_sim.h", "sim/include/im2p_geometry.h",
            "sim/include/im2p_cycle_model.h", "sim/include/im2p_compact_runs.h",
            "scripts/gemmini_resolve_profile.py", "sim/tests/cycle/rtl_hardening.py",
            "sim/cycle/corpus-authority-v2.json", "sim/tests/cycle/passive_log.py",
            *(name for name in REQUIRED_SOURCE_FILES if name.endswith(".py")),
        )
        for name in real_files:
            shutil.copyfile(ROOT / name, source / name)
        package = base / "package"
        result = create_export(ExportRequest(source, None, package, None))
        extracted = base / "extracted"
        assert extract_export(result.archive, extracted).status == "PASS"
        relocated = extracted / "gemmini-hp1-export"
        include = relocated / "source/sim/include"
        clean_env = {key: value for key, value in os.environ.items()
                     if key not in {"CPATH", "C_INCLUDE_PATH", "CPLUS_INCLUDE_PATH", "PYTHONPATH"}}
        program = (
            '#include "im2p_sim.h"\n#include "im2p_cycle_model.h"\n'
            'int main(void) { im2p_compact_run_t run = {0}; '
            'im2p_compact_runs_t view = {IM2P_COMPACT_RUNS_VERSION, '
            'sizeof(im2p_compact_runs_t), 32, 1, &run}; '
            'return view.version != 1; }\n'
        )
        # When: independent compilers and the fixture CLI consume only relocated inputs.
        for compiler, language in (("cc", "c"), ("c++", "c++")):
            completed = subprocess.run(
                (compiler, "-x", language, "-std=c11" if language == "c" else "-std=c++17",
                 "-fsyntax-only", "-I", str(include), "-"),
                input=program, cwd=base, env=clean_env, text=True, capture_output=True, check=False,
            )
            assert completed.returncode == 0, completed.stderr
        completed = subprocess.run(
            (sys.executable, "-B", str(relocated / "source/sim/tests/cycle/certify_run_aware.py"), "--help"),
            cwd=base, env=clean_env, text=True, capture_output=True, check=False,
        )
        # Then: no original checkout include or import path is needed.
        assert completed.returncode == 0, completed.stderr
        assert "--rtl-root" in completed.stdout
        current = subprocess.run(
            (sys.executable, "-B", str(relocated / "source/sim/tests/cycle/current_rtl_certificate.py"), "--help"),
            cwd=base, env=clean_env, text=True, capture_output=True, check=False,
        )
        assert current.returncode == 0, current.stderr
        offline = subprocess.run(
            (sys.executable, "-B", str(relocated / "source/sim/cycle/npu_trace.py"), "--help"),
            cwd=base, env=clean_env, text=True, capture_output=True, check=False,
        )
        assert offline.returncode == 0, offline.stderr
        assert "--run-aware-certificate" in offline.stdout
        joined = subprocess.run(
            (sys.executable, "-B", str(relocated / "source/sim/cycle/reconstruct.py"), "--help"),
            cwd=base, env=clean_env, text=True, capture_output=True, check=False,
        )
        assert joined.returncode == 0, joined.stderr
        assert "--run-aware-certificate" in joined.stdout


if __name__ == "__main__":
    test_required_source_closure_when_checksums_are_recomputed()
    test_packaged_verifier_when_run_from_its_own_archive()
    test_pinned_trace_off_package_when_producer_has_no_optrace()
    test_relocated_run_headers_and_fixture_cli()
    print("GEMMINI SOURCE CLOSURE: PASS")
