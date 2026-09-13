#!/usr/bin/env python3
"""Offline tests only: tiny synthetic images and a mocked Vivado subprocess."""

import contextlib
import copy
import hashlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import flash_prepare as prepare


def record(address, kind, data=b""):
    body = bytes([len(data)]) + address.to_bytes(2, "big") + bytes([kind]) + data
    return ":" + (body + bytes([-sum(body) & 255])).hex().upper() + "\n"


class FlashPrepareTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.payload = bytes.fromhex("01234567")
        self.bit = self.root / "fixture.bit"
        self.bit.write_bytes(b"HEAD" + self.payload + b"TAIL")
        self.tool = self.root / "vivado"
        self.tool.write_text("mock tool, never executed\n")
        self.plan = {
            "schema_version": 1,
            "fpga_part": "xc7a100tcsg324-1",
            "flash_part": "s25fl128sxxxxxx0-spi-x1_x2_x4",
            "capacity_bytes": prepare.CAPACITY,
            "part_evidence": "synthetic unit test; not a board identification",
            "geometry_evidence": "synthetic uniform sectors for parser tests",
            "interface": "SPIx1", "boot_interface": "SPIx1",
            "boot_interface_evidence": "synthetic, no boot validation claimed",
            "image_range": [0, 4], "erase_range": [0, 65536],
            "erase_regions": [{"offset": 0, "sector_bytes": 65536, "count": 256}],
            "bitstream": self.file_spec(self.bit),
            "load_payload": {
                "source_offset": 4, "length": 4,
                "sha256": hashlib.sha256(self.payload).hexdigest(),
                "bitswap": "none", "padding_bytes": 0,
                "evidence": "synthetic slice; not a .bit packet parser",
            },
            "tool": {**self.file_spec(self.tool), "version": "2025.2",
                     "startup_evidence": "mock subprocess; no startup evaluated"},
        }
        self.mcs = self.root / "fixture.mcs"
        self.mcs.write_text(record(0, 0, self.payload) + record(0, 1))

    @staticmethod
    def file_spec(path):
        return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

    def test_exact_payload_and_explicit_bitswap(self):
        self.assertEqual(prepare.validate_mcs(self.plan, self.mcs)["payload_bytes"], 4)
        self.plan["load_payload"]["bitswap"] = "reverse-per-byte"
        with self.assertRaisesRegex(ValueError, "payload mismatch"):
            prepare.validate_mcs(self.plan, self.mcs)
        swapped = self.payload.translate(prepare.BIT_REVERSE)
        self.mcs.write_text(record(0, 0, swapped) + record(0, 1))
        self.assertEqual(prepare.validate_mcs(self.plan, self.mcs)["hardware_operations"], 0)

    def test_record_failures(self):
        valid = record(0, 0, self.payload)
        cases = {
            "checksum": valid[:-3] + "FF\n" + record(0, 1),
            "length": ":05000000012345672C\n" + record(0, 1),
            "no EOF": valid,
            "after EOF": valid + record(0, 1) + valid,
            "duplicate": valid + valid + record(0, 1),
            "conflict": valid + record(0, 0, b"xxxx") + record(0, 1),
            "gap": record(0, 0, self.payload[:3]) + record(0, 1),
            "range": record(1, 0, self.payload) + record(0, 1),
            "extended overflow": record(0, 4, b"\xff\xff") + valid + record(0, 1),
            "record address overflow": record(65535, 0, b"xx") + record(0, 1),
            "unsupported type": record(0, 5, b"\0" * 4) + valid + record(0, 1),
            "bad EOF": valid + record(1, 1),
            "bad extended length": record(0, 4, b"\0") + valid + record(0, 1),
        }
        for name, text in cases.items():
            with self.subTest(name=name), self.assertRaises(ValueError):
                prepare.parse_mcs(text, prepare.CAPACITY, [0, 4])

    def test_extended_addresses_and_padding(self):
        for kind, high in ((2, b"\x10\x00"), (4, b"\x00\x01")):
            data, _ = prepare.parse_mcs(record(0, kind, high)
                                       + record(0, 0, self.payload) + record(0, 1),
                                       prepare.CAPACITY, [65536, 65540])
            self.assertEqual(data, self.payload)
        self.plan["image_range"] = [0, 6]
        self.plan["load_payload"]["padding_bytes"] = 2
        self.mcs.write_text(record(0, 0, self.payload + b"\xff\xff") + record(0, 1))
        self.assertEqual(prepare.validate_mcs(self.plan, self.mcs)["payload_bytes"], 6)

    def test_invalid_plan_never_launches_tool(self):
        mutations = [
            ("flash_part", "Unknown"), ("flash_part", "wrong-part"),
            ("fpga_part", "xc7a35tcsg324-1"), ("part_evidence", "Unknown"),
            ("capacity_bytes", prepare.CAPACITY + 1),
            ("image_range", [0, 0x100000000]), ("erase_range", [1, 65536]),
            ("erase_regions", [{"offset": 0, "sector_bytes": 65536, "count": 257}]),
            ("boot_interface", "SPIx4"), ("boot_interface_evidence", "Unknown"),
            ("bitstream", {**self.plan["bitstream"], "sha256": "0" * 64}),
            ("tool", {**self.plan["tool"], "sha256": "0" * 64}),
            ("load_payload", {**self.plan["load_payload"], "bitswap": "auto"}),
            ("load_payload", {**self.plan["load_payload"], "length": 0xFFFFFFFF}),
        ]
        with patch.object(prepare.subprocess, "run") as run:
            for key, value in mutations:
                plan = copy.deepcopy(self.plan)
                plan[key] = value
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    prepare.generate(plan, self.root / "rejected-output")
                run.assert_not_called()
                self.assertFalse((self.root / "rejected-output").exists())

    def test_execute_always_refused_without_hardware_or_tool(self):
        for additional in ({}, {"approval": "F2"},
                           {"approval": "F2", "backup": {"path": "missing"}},
                           {"approval": "F2", "backup": self.file_spec(self.bit)}):
            path = self.root / "plan.json"
            path.write_text(json.dumps({**self.plan, **additional}))
            with self.subTest(extra=additional), patch.object(prepare.subprocess, "run") as run:
                with contextlib.redirect_stderr(io.StringIO()) as error, self.assertRaises(SystemExit) as exit:
                    prepare.main(["--plan", str(path), "--execute"])
                self.assertEqual(exit.exception.code, 2)
                self.assertIn("Hardware execution is not implemented", error.getvalue())
                run.assert_not_called()

    def test_plan_only_default_and_unknown_rejection(self):
        path = self.root / "plan.json"
        for flag in ([], ["--check-plan"]):
            path.write_text(json.dumps(self.plan))
            before = sorted(self.root.rglob("*"))
            with patch.object(prepare.subprocess, "run") as run:
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    prepare.main(["--plan", str(path), *flag])
                self.assertEqual(json.loads(output.getvalue())["status"], "OFFLINE_FLASH_PLAN_PASS")
                self.assertEqual(sorted(self.root.rglob("*")), before)
                unknown = {**self.plan, "flash_part": "Unknown"}
                path.write_text(json.dumps(unknown))
                with contextlib.redirect_stderr(io.StringIO()) as error, self.assertRaises(SystemExit) as exit:
                    prepare.main(["--plan", str(path), *flag])
                self.assertEqual(exit.exception.code, 1)
                self.assertIn("unconfirmed or unsupported Flash part", error.getvalue())
                self.assertEqual(sorted(self.root.rglob("*")), before)
                run.assert_not_called()

    def test_mock_offline_generation_and_tool_failure(self):
        def fake_vivado(command, **kwargs):
            script = Path(command[-1]).read_text()
            self.assertIn("write_cfgmem", script)
            self.assertNotIn("-notrace", command)
            temporary = kwargs["cwd"] / "tmp"
            self.assertTrue(temporary.is_dir())
            for key in ("TMPDIR", "TMP", "TEMP"):
                self.assertEqual(kwargs["env"][key], str(temporary))
            for forbidden in ("open_hw", "program_hw", "readback_hw", "connect_hw"):
                self.assertNotIn(forbidden, script)
            (kwargs["cwd"] / "candidate.mcs").write_bytes(self.mcs.read_bytes())
            return subprocess.CompletedProcess(command, 0)

        with patch.object(prepare.subprocess, "run", side_effect=fake_vivado) as run:
            report = prepare.generate(self.plan, self.root / "success")
            self.assertEqual(report["status"], "OFFLINE_MCS_PASS")
            self.assertEqual(run.call_count, 1)
        with patch.object(prepare.subprocess, "run", return_value=subprocess.CompletedProcess([], 1)) as run:
            with self.assertRaisesRegex(ValueError, "offline Vivado failed"):
                prepare.generate(self.plan, self.root / "failure")
            self.assertEqual(run.call_count, 1)
            self.assertTrue((self.root / "failure" / "tool-output.log").exists())
            self.assertFalse((self.root / "failure" / "validation.json").exists())
            status = json.loads((self.root / "failure" / "execution.json").read_text())
            self.assertEqual(status["state"], "tool_failed")
            self.assertEqual(status["exit_code"], 1)
            self.assertIsNotNone(status["started_utc"])
            self.assertIsNotNone(status["ended_utc"])

    def test_timeout_keeps_failure_status_and_never_retries(self):
        with patch.object(prepare.subprocess, "run", side_effect=subprocess.TimeoutExpired("mock Vivado", 300)) as run:
            with self.assertRaises(subprocess.TimeoutExpired):
                prepare.generate(self.plan, self.root / "timeout")
            self.assertEqual(run.call_count, 1)
        status = json.loads((self.root / "timeout" / "execution.json").read_text())
        self.assertTrue(status["timeout"])
        self.assertEqual(status["state"], "tool_failed")
        self.assertEqual(status["retry_count"], 0)
        self.assertIsNone(status["exit_code"])
        self.assertIsNotNone(status["ended_utc"])
        self.assertTrue((self.root / "timeout" / "tool-output.log").exists())
        self.assertFalse((self.root / "timeout" / "validation.json").exists())

    def test_tcl_paths_cannot_inject_commands(self):
        self.assertEqual(prepare.tcl_word("/tmp/a;[command] $var"), "{/tmp/a;[command] $var}")
        for path in ("/tmp/a} {b", "/tmp/a\\\nb", "/tmp/a\nb"):
            with self.assertRaises(ValueError):
                prepare.tcl_word(path)


if __name__ == "__main__":
    unittest.main()
