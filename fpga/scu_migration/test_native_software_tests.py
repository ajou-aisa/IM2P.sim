#!/usr/bin/env python3
"""Exercise gates with tiny local files and mocked subprocesses only."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import native_software_tests as suite


class SoftwareTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="scu-native-tests-")
        self.addCleanup(self.temporary.cleanup)
        self.run = Path(self.temporary.name) / "native"
        self.run.mkdir()
        self.out = Path(self.temporary.name) / "results"
        (self.run / "identity.json").write_text("{}")
        self.artifacts = {}
        for name in (suite.SIM, suite.FRONTEND, suite.SCALAR, suite.DISPATCH):
            path = self.run / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"\x7fELF\x02\x01" + bytes(12) + (183).to_bytes(2, "little"))
            self.artifacts[name] = {"sha256": suite.native.digest(path), "bytes": path.stat().st_size}
        self.manifest = self.run / "native-build-manifest.json"
        self.manifest.write_text(json.dumps({"status": "BUILT_NOT_RUNTIME_TESTED", "machine": "aarch64",
            "source_sha256": suite.native.SOURCE_SHA256,
            "identity_sha256": suite.native.digest(self.run / "identity.json"), "artifacts": self.artifacts}))
        self.sha = suite.native.digest(self.manifest)
        self.verifier = patch.object(suite.native, "verify_frozen", return_value=None)
        self.verifier.start()
        self.addCleanup(self.verifier.stop)

    def invoke(self, *options):
        with contextlib.redirect_stdout(io.StringIO()) as output, contextlib.redirect_stderr(io.StringIO()) as error:
            result = suite.main(["--native-run", str(self.run), "--out", str(self.out),
                                 "--manifest-sha256", self.sha, *options])
        return result, output.getvalue(), error.getvalue()

    def test_dry_run_does_not_execute_or_write(self):
        with patch.object(suite.subprocess, "run", side_effect=AssertionError("executed")):
            result, output, _ = self.invoke()
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(output)["ARM64_runtime"], "NOT_RUN")
        self.assertFalse(self.out.exists())

    def test_x86_does_not_execute_arm_binary(self):
        with patch.object(suite.platform, "machine", return_value="x86_64"), \
             patch.object(suite.subprocess, "run", side_effect=AssertionError("executed")):
            self.assertNotEqual(self.invoke("--run-tests")[0], 0)
        self.assertFalse(self.out.exists())

    def test_hash_and_elf_mismatch_rejected(self):
        with self.assertRaisesRegex(ValueError, "manifest SHA256"):
            suite.verify(self.run, "0" * 64)
        (self.run / suite.SCALAR).write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "artifact SHA256"):
            suite.verify(self.run, self.sha)
        self.artifacts[suite.SCALAR] = {"sha256": suite.native.digest(self.run / suite.SCALAR), "bytes": 8}
        data = json.loads(self.manifest.read_text())
        data["artifacts"] = self.artifacts
        self.manifest.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "AArch64"):
            suite.verify(self.run, suite.native.digest(self.manifest))

    def test_fixed_plan_has_no_device_invocation(self):
        commands = suite.plan(self.run, self.out)
        self.assertEqual([name for name, _, _ in commands],
                         ["pty", "full-gate", "reject", "scalar", "scalar-golden", "carrier", "carrier-golden"])
        self.assertEqual(commands[2][1], [str(self.run / suite.DISPATCH), "reject"])
        self.assertIn("cases=22", commands[3][2])
        self.assertIn("cases=48", commands[5][2])

    def test_first_failure_preserved_and_no_next_test(self):
        def fail(command, **kwargs):
            self.assertNotIn("IM2P_FPGA_DEVICE", kwargs["env"])
            self.assertNotIn("PYTHONOPTIMIZE", kwargs["env"])
            kwargs["stdout"].write("original parser failure\n")
            return suite.subprocess.CompletedProcess(command, 9)
        with patch.dict(os.environ, {"IM2P_FPGA_DEVICE": "/dev/forbidden", "PYTHONOPTIMIZE": "1"}), \
             patch.object(suite.subprocess, "run", side_effect=fail) as runner:
            with self.assertRaisesRegex(RuntimeError, "later tests NOT_RUN"):
                suite.execute(self.run, self.out)
        self.assertEqual(runner.call_count, 1)
        self.assertEqual((self.out / "pty.log").read_text(), "original parser failure\n")
        self.assertEqual(json.loads((self.out / "pty.command.json").read_text())["exit"], 9)
        self.assertFalse((self.out / "results.json").exists())

    def test_exit_zero_without_marker_is_failure(self):
        with patch.object(suite.subprocess, "run", return_value=suite.subprocess.CompletedProcess([], 0)):
            with self.assertRaisesRegex(RuntimeError, "pty failed"):
                suite.execute(self.run, self.out)


if __name__ == "__main__":
    unittest.main()
