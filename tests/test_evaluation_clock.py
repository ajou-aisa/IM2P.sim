from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class ClockTests(unittest.TestCase):
    def test_policy_module_exists(self) -> None:
        # Given: hardware evaluation needs a dedicated finite policy.
        # When: the implemented contract is discovered.
        spec = importlib.util.find_spec("scripts.evaluation_clock_contract")
        # Then: it exists independently of the cycle timing engine.
        self.assertIsNotNone(spec)

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="im2p-clock-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def artifact(self, name: str) -> dict[str, str]:
        path = self.root / name
        path.write_text(name, encoding="utf-8")
        return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

    def observation(self, frequency: int, **changes):
        row = {
            "schema": "im2p-clock-observation", "version": 1,
            "execution_kind": "SYNTHETIC", "profile": "a8w8-d16-hp1",
            "top": "IM2PGemminiWSHP1A8W8D16", "top_scope": "INTEGRATED",
            "source": self.artifact("source.json"), "netlist": self.artifact("netlist.v"),
            "constraints": [self.artifact("clock.xdc")],
            "tool_report": self.artifact("timing.rpt"),
            "hardware_contract_sha256": "a" * 64,
            "tool": {"name": "fixture", "version": "1"},
            "technology": "FPGA fixture", "memory_implementation": "fixture memories",
            "memory_interface": "fixture backing interface", "timing_stage": "POST_ROUTE",
            "frequency_hz": frequency, "array_count": 1, "independent_macs_per_pe_per_cycle": 1,
            "peak_basis": self.artifact("mac-contract.txt"), "tool_exit_code": 0,
            "setup_slack_ns": "0.1", "hold_slack_ns": "0", "timed_paths": 50,
            "unconstrained_paths": 0, "fit": True,
            "resource_usage": {"LUT": 30}, "resource_limits": {"LUT": 100},
        }
        row.update(changes)
        measurements = ("top", "frequency_hz", "tool_exit_code", "setup_slack_ns", "hold_slack_ns",
                        "timed_paths", "unconstrained_paths", "fit", "resource_usage", "resource_limits")
        report = self.root / f"report-{frequency}.json"
        report.write_text(json.dumps({"schema": "im2p-clock-tool-report", "version": 1,
                                      **{key: row[key] for key in measurements}}), encoding="utf-8")
        row["tool_report"] = {"path": str(report), "sha256": hashlib.sha256(report.read_bytes()).hexdigest()}
        path = self.root / f"observation-{frequency}.json"
        path.write_text(json.dumps(row), encoding="utf-8")
        return path

    def select(self, paths: list[Path], target: str | None = "0.05"):
        module = importlib.import_module("scripts.evaluation_clock")
        return module.select_clock(paths, target, "synthetic dense GPU test basis")

    def test_nearest_verified_clock(self) -> None:
        paths = [self.observation(value) for value in (80_000_000, 100_000_000, 120_000_000)]
        result = self.select(paths)
        self.assertEqual(result["selected_frequency_hz"], 100_000_000)
        self.assertEqual(result["status"], "SYNTHETIC_ONLY")

    def test_unreachable_target_uses_highest_tested_passing(self) -> None:
        paths = [self.observation(80_000_000), self.observation(100_000_000, setup_slack_ns="-0.1")]
        result = self.select(paths, "1")
        self.assertEqual(result["selected_frequency_hz"], 80_000_000)
        self.assertEqual(result["selection_reason"], "HIGHEST_TESTED_PASSING")

    def test_invalid_hardware_never_has_clock(self) -> None:
        for mutation in ({"fit": False}, {"unconstrained_paths": 1}, {"timed_paths": 0},
                         {"hold_slack_ns": "-0.01"}, {"tool_exit_code": 1},
                         {"resource_usage": {"LUT": 101}}):
            with self.subTest(mutation=mutation):
                result = self.select([self.observation(100_000_000, **mutation)])
                self.assertEqual(result["status"], "NOT_READY")
                self.assertIsNone(result["selected_frequency_hz"])

    def test_post_synthesis_estimate_is_not_an_operating_clock(self) -> None:
        result = self.select([self.observation(100_000_000, timing_stage="POST_SYNTH_ESTIMATE")])
        self.assertEqual(result["status"], "NOT_READY")
        self.assertIsNone(result["selected_frequency_hz"])

    def test_missing_target_never_invents_clock(self) -> None:
        self.assertIsNone(self.select([self.observation(100_000_000)], None)["selected_frequency_hz"])

    def test_a4_does_not_double_mac_rate(self) -> None:
        path = self.observation(100_000_000, profile="a4w4-d16-hp1", top="IM2PGemminiWSHP1A4W4D16")
        self.assertEqual(self.select([path])["achieved_peak_tops"], "32/625")

    def test_six_profile_identity(self) -> None:
        for bits in (4, 8):
            for dim in (16, 32, 64):
                with self.subTest(bits=bits, dim=dim):
                    path = self.observation(100_000_000, profile=f"a{bits}w{bits}-d{dim}-hp1",
                                            top=f"IM2PGemminiWSHP1A{bits}W{bits}D{dim}")
                    self.assertEqual(self.select([path])["selected_frequency_hz"], 100_000_000)

    def test_current_consumer_rejects_generic_clock_without_fixed_policy(self) -> None:
        path = self.observation(100_000_000, execution_kind="TOOL_EXECUTION")
        result = self.select([path])
        selection = self.root / "clock-selection.json"
        selection.write_text(json.dumps(result), encoding="utf-8")
        module = importlib.import_module("scripts.evaluation_clock")
        with self.assertRaises(ValueError):
            module.load_selection(selection, "a8w8-d16-hp1")

    def test_duplicate_frequency_rejected(self) -> None:
        path = self.observation(100_000_000)
        with self.assertRaises(ValueError):
            self.select([path, path])

    def test_mixed_source_rejected(self) -> None:
        first = self.observation(100_000_000)
        second = self.observation(120_000_000, source=self.artifact("other-source.json"))
        with self.assertRaises(ValueError):
            self.select([first, second])

    def test_tampered_artifact_rejected(self) -> None:
        path = self.observation(100_000_000)
        (self.root / "netlist.v").write_text("changed", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.select([path])

    def test_report_answer_mismatch_rejected(self) -> None:
        path = self.observation(100_000_000)
        row = json.loads(path.read_text())
        row["setup_slack_ns"] = "10"
        path.write_text(json.dumps(row), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.select([path])

    def test_top_and_schema_rejected(self) -> None:
        for mutation in ({"top": "PEOnly"}, {"extra": True}, {"frequency_hz": True},
                         {"setup_slack_ns": "NaN"}, {"version": 2}):
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                self.select([self.observation(100_000_000, **mutation)])

    def test_real_consumer_rejects_synthetic_selection(self) -> None:
        result = self.select([self.observation(100_000_000)])
        path = self.root / "clock-selection.json"
        path.write_text(json.dumps(result), encoding="utf-8")
        module = importlib.import_module("scripts.evaluation_clock")
        with self.assertRaises(ValueError):
            module.load_selection(path, "a8w8-d16-hp1")

    def test_cli_preflight_reports_gaps_and_refuses_overwrite(self) -> None:
        output = self.root / "preflight.json"
        command = [sys.executable, str(ROOT / "scripts/evaluation_clock.py"), "preflight", "--out", str(output)]
        first = subprocess.run(command, capture_output=True, text=True, timeout=10)
        self.assertEqual(first.returncode, 2, first.stderr)
        result = json.loads(output.read_text())
        self.assertEqual(len(result["profiles"]), 6)
        self.assertIn("resolved_board_and_constraints", result["configuration_gaps"])
        original = output.read_bytes()
        second = subprocess.run(command, capture_output=True, text=True, timeout=10)
        self.assertNotEqual(second.returncode, 0)
        self.assertEqual(output.read_bytes(), original)

    def test_cli_selection_and_help(self) -> None:
        observation = self.observation(100_000_000)
        output = self.root / "clock-selection.json"
        command = [sys.executable, str(ROOT / "scripts/evaluation_clock.py"), "select", "--observations",
                   str(observation), "--target-peak-tops", "0.05", "--target-basis", "synthetic dense GPU",
                   "--out", str(output)]
        result = subprocess.run(command, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(json.loads(output.read_text())["status"], "SYNTHETIC_ONLY")
        help_result = subprocess.run(command[:2] + ["--help"], capture_output=True, text=True, timeout=10)
        self.assertEqual(help_result.returncode, 0, help_result.stderr)


if __name__ == "__main__":
    unittest.main()
