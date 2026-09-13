#!/usr/bin/env python3
"""Small offline tests. No compiler, FPGA, or original artifact is executed."""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("native_build", Path(__file__).with_name("native_build.py"))
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)


class NativeBuildTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="scu-native-wrapper-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.package = self.root / "package"
        self.frozen = self.package / "host/frozen"
        self.run = self.root / "new-run"
        entries = {}
        for name, data in {"source/Makefile": b"sealed make\n", "host/input": b"host\n",
                           "params/include/gemmini_params.h": b"params\n"}.items():
            path = self.frozen / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            entries[name] = hashlib.sha256(data).hexdigest()
        self.entries = entries
        manifest = self.frozen / "integration-sha256.json"
        manifest.write_text(json.dumps(entries, indent=2) + "\n")
        self.source_sha = native.digest(manifest)
        identity = dict(native.SEMANTICS, source_sha256=self.source_sha, machine="historical-x86",
                        sim_archive="/historical/x86/libim2p_sim.a", sim_archive_sha256="old",
                        sim_archive_machine="x86_64", physical_board_access=False)
        (self.frozen / "identity.json").write_text(json.dumps(identity) + "\n")
        self.addCleanup(patch.stopall)
        patch.object(native, "SOURCE_SHA256", self.source_sha).start()
        patch.object(native, "SOURCE_FILES", len(entries)).start()

    def invoke(self, *options):
        with contextlib.redirect_stdout(io.StringIO()) as output, contextlib.redirect_stderr(io.StringIO()) as error:
            code = native.main(["--package", str(self.package), "--run", str(self.run), *options])
        return code, output.getvalue(), error.getvalue()

    def test_default_never_runs_or_writes(self):
        with patch.object(native.subprocess, "run", side_effect=AssertionError("unexpected execution")):
            code, output, _ = self.invoke()
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output)["status"], "CHECKED_DRY_RUN")
        self.assertFalse(self.run.exists())

    def test_materialize_preserves_bytes_and_clears_archive(self):
        before = (self.frozen / "identity.json").read_bytes()
        (self.frozen / "source/old.a").write_bytes(b"excluded historical binary")
        self.assertEqual(self.invoke("--materialize")[0], 0)
        identity = json.loads((self.run / "identity.json").read_text())
        self.assertIsNone(identity["sim_archive"])
        self.assertNotIn("sim_archive_sha256", identity)
        self.assertNotIn("sim_archive_machine", identity)
        self.assertEqual(identity["source_sha256"], self.source_sha)
        self.assertEqual((self.run / "identity-origin.json").read_bytes(), before)
        self.assertEqual((self.frozen / "identity.json").read_bytes(), before)
        self.assertFalse((self.run / "source/old.a").exists())
        self.assertEqual(native.verify_frozen(self.run)[0], self.entries)

    def test_existing_destination_is_not_overwritten(self):
        self.run.mkdir()
        sentinel = self.run / "sentinel"
        sentinel.write_text("keep")
        self.assertNotEqual(self.invoke("--materialize")[0], 0)
        self.assertEqual(sentinel.read_text(), "keep")

    def test_source_tamper_stops_before_materialize(self):
        (self.frozen / "source/Makefile").write_text("changed")
        self.assertNotEqual(self.invoke("--materialize")[0], 0)
        self.assertFalse(self.run.exists())

    def test_manifest_and_semantics_rejected(self):
        with patch.object(native, "SOURCE_SHA256", "0" * 64):
            self.assertNotEqual(self.invoke()[0], 0)
        identity = json.loads((self.frozen / "identity.json").read_text())
        identity["abi"] = 4
        (self.frozen / "identity.json").write_text(json.dumps(identity))
        self.assertNotEqual(self.invoke()[0], 0)

    def test_symlink_and_escape_rejected(self):
        (self.frozen / "source/link").symlink_to(self.frozen / "source/Makefile")
        for relative in ("source/link", "../outside", "/absolute", "source/../host/input"):
            with self.assertRaises(ValueError):
                native.regular_file(self.frozen, relative)

    def test_missing_tools_or_cross_settings_do_not_materialize(self):
        with patch.object(native.shutil, "which", return_value=None):
            self.assertNotEqual(self.invoke("--build")[0], 0)
        self.assertFalse(self.run.exists())
        with patch.dict(os.environ, {"CARGO_BUILD_TARGET": "aarch64-unknown-linux-gnu"}):
            self.assertNotEqual(self.invoke("--build")[0], 0)
        self.assertFalse(self.run.exists())

    def test_stage_failure_stops_and_keeps_log(self):
        self.assertEqual(self.invoke("--materialize")[0], 0)
        def fail(command, **kwargs):
            kwargs["stdout"].write("preserved failure\n")
            return native.subprocess.CompletedProcess(command, 7)
        with patch.dict(os.environ, {}, clear=True), patch.object(native.subprocess, "run", side_effect=fail) as runner:
            with self.assertRaisesRegex(RuntimeError, "native stage native failed"):
                native.build(self.run, 2)
        self.assertEqual(runner.call_count, 1)
        self.assertEqual((self.run / "migration-evidence/00-native.log").read_text(), "preserved failure\n")
        self.assertEqual(json.loads((self.run / "migration-evidence/00-native.json").read_text())["exit"], 7)
        self.assertFalse((self.run / "native-build-manifest.json").exists())

    def vendor(self):
        path = self.package / "host/toolchain/cargo-vendor/example/src/lib.rs"
        path.parent.mkdir(parents=True)
        path.write_text("// vendored source\n")
        lock = self.frozen / "source/sim/Cargo.lock"
        lock.parent.mkdir()
        lock.write_text("# locked dependencies\n")
        self.vendor_manifest = self.package / "manifest.json"
        self.vendor_manifest.write_text(json.dumps({"source_manifest_sha256": self.source_sha,
            "files": {path.relative_to(self.package).as_posix():
                      {"sha256": native.digest(path), "bytes": path.stat().st_size}}}))
        return path

    def test_vendor_uses_isolated_cargo_home_and_offline_config(self):
        self.assertEqual(self.invoke("--materialize")[0], 0)
        self.vendor()
        cargo = native.cargo_sources(self.package)
        def fail(command, **kwargs):
            self.assertEqual(kwargs["env"]["CARGO_HOME"], str(self.run / "cache/cargo"))
            self.assertEqual(kwargs["env"]["CARGO_NET_OFFLINE"], "true")
            return native.subprocess.CompletedProcess(command, 7)
        with patch.dict(os.environ, {"CARGO_HOME": "/unused/caller/cache"}), \
             patch.object(native.subprocess, "run", side_effect=fail) as runner:
            with self.assertRaisesRegex(RuntimeError, "native stage native failed"):
                native.build(self.run, 2, cargo)
        self.assertEqual(runner.call_count, 1)
        config = (self.run / "cache/cargo/config.toml").read_text()
        self.assertIn('replace-with = "vendored-sources"', config)
        self.assertIn(json.dumps(cargo["directory"]), config)
        evidence = json.loads((self.run / "migration-evidence/00-native.json").read_text())
        self.assertEqual(evidence["cargo_sources"]["package_manifest_sha256"],
                         native.digest(self.vendor_manifest))

    def test_vendor_manifest_and_content_must_match(self):
        path = self.vendor()
        self.assertEqual(native.cargo_sources(self.package)["files"], 1)
        path.write_text("tampered")
        with self.assertRaisesRegex(ValueError, "vendor SHA256"):
            native.cargo_sources(self.package)
        self.vendor_manifest.write_text("{}")
        with self.assertRaisesRegex(ValueError, "sealed package"):
            native.cargo_sources(self.package)

    def test_no_vendor_fallback_is_explicit_and_offline(self):
        with patch.dict(os.environ, {"CARGO_HOME": "/caller/preseeded/cache"}):
            cargo = native.cargo_sources(self.package)
        self.assertEqual(cargo["kind"], "existing_cargo_home")
        self.assertEqual(cargo["cargo_home"], "/caller/preseeded/cache")
        self.assertTrue(cargo["offline"])
        self.assertIn("locked registry cache", cargo["reason"])


if __name__ == "__main__":
    unittest.main()
