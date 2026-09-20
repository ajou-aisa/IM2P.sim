#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
# Run: uv run tests/test_gemmini_export_closure.py
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from scripts.gemmini_export import ExportError, ExportRequest, create_export, verify_export
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
            "source/sim/include/im2p_geometry.h",
            "source/frontend/include/im2p_production_trace.hpp",
            "source/scripts/im2p_paths.py",
            "dependency/source/llama_cpp_gemmini/ggml/src/ggml-gemmini-utils/src/optrace.cpp",
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


if __name__ == "__main__":
    test_required_source_closure_when_checksums_are_recomputed()
    test_packaged_verifier_when_run_from_its_own_archive()
    print("GEMMINI SOURCE CLOSURE: PASS")
