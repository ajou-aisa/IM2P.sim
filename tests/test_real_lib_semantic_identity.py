#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
# Run: uv run tests/test_real_lib_semantic_identity.py
from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import real_matrix_fingerprint
from scripts.im2p_config import profile_config
from scripts.real_lib_manifest import SCHEMA, IdentityData, artifact_rows, atomic_json, verify_manifest


def test_manifest_requires_current_semantics() -> None:
    with tempfile.TemporaryDirectory(prefix="im2p-semantic-manifest-") as raw:
        root = Path(raw)
        artifact = Path("libim2p_sim.a")
        (root / artifact).write_bytes(b"test artifact")
        path = root / "real-lib.json"
        for bits in (4, 8, 16):
            for dim in (16, 32, 64):
                profile = profile_config(bits, bits, dim)
                identity: IdentityData = {
                    **profile, "id": f"a{bits}-w{bits}-d{dim}", "block_size": 32,
                    "platform": "test", "platform_release": "test", "arch": "test",
                }
                atomic_json(path, {
                    "schema": SCHEMA, "fingerprint": "a" * 64,
                    "identity": identity, "toolchains": {}, "build_config": {},
                    "artifact_root": ".", "artifacts": artifact_rows(root, (artifact,)),
                })
                assert verify_manifest(path) == (True, "ok")
                original = path.read_text()
                for field, value in profile.items():
                    for missing in (True, False):
                        malformed = json.loads(original)
                        if missing:
                            del malformed["identity"][field]
                        else:
                            malformed["identity"][field] = value + 1 if type(value) is int else f"stale-{value}"
                        path.write_text(json.dumps(malformed))
                        valid, reason = verify_manifest(path)
                        assert not valid, f"accepted invalid {field}, missing={missing}"
                        assert "identity" in reason, reason
                legacy = json.loads(original)
                legacy["schema"] = "im2p-real-lib-cache-v3"
                path.write_text(json.dumps(legacy))
                assert verify_manifest(path) == (False, "schema")


def fingerprint(root: Path) -> str:
    args = [
        "real_matrix_fingerprint.py", "--bits", "8", "--weight-bits", "8",
        "--dim", "16", "--gemmini-root", str(root / "gemmini"),
        "--params-root", str(root / "params/include"),
    ]
    output = io.StringIO()
    with patch.object(sys, "argv", args), patch.object(real_matrix_fingerprint, "ROOT", root):
        with contextlib.redirect_stdout(output):
            assert real_matrix_fingerprint.main() == 0
    return output.getvalue().strip()


def test_fingerprint_includes_semantics_and_generated_sources() -> None:
    with tempfile.TemporaryDirectory(prefix="im2p-semantic-fingerprint-") as raw:
        root = Path(raw)
        paths = (
            "Makefile", "sim/Cargo.toml", "sim/Cargo.lock", "sim/build.rs",
            "synth/SynthA8W8D16.bsv", "config/im2p_profiles.json",
            "scripts/im2p_config.py", "src/common/Config.bsv",
            "sim/ffi/im2p_config.h", "params/gemmini_params.h",
            "gemmini/ggml/src/ggml-gemmini/ggml-gemmini-args.h",
            "gemmini/ggml/src/ggml-common.h", "gemmini/ggml/include/ggml.h",
        )
        for relative in paths:
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("baseline\n")
        baseline = fingerprint(root)
        assert fingerprint(root) == baseline
        for relative in paths:
            path = root / relative
            path.write_text("changed\n")
            assert fingerprint(root) != baseline, f"ignored source {relative}"
            path.write_text("baseline\n")
        profile = profile_config(8, 8, 16)
        for field, value in profile.items():
            changed = dict(profile)
            changed[field] = value + 1 if type(value) is int else f"changed-{value}"
            with patch.object(real_matrix_fingerprint, "profile_config", return_value=changed):
                assert fingerprint(root) != baseline, f"ignored semantic field {field}"


if __name__ == "__main__":
    test_manifest_requires_current_semantics()
    test_fingerprint_includes_semantics_and_generated_sources()
    print("REAL LIB SEMANTIC IDENTITY PASS")
