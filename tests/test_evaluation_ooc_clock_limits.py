from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
import sys

from test_evaluation_ooc_tcl import prepare, run_flow
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class ClockLimitTests(unittest.TestCase):
    def test_legacy_report_cannot_claim_clock_limit_coverage(self) -> None:
        from scripts.evaluation_ooc_report import normalized_values
        raw = {"schema": "im2p-ooc-tool-result", "version": 1, "status": "ROUTE_COMPLETED",
               "part": "xcu250-figd2104-2L-e", "clock_port": "CLK", "timing_stage": "POST_ROUTE",
               "core_top": "IM2PGemminiWSHP1A8W8D16", "top": "PoTalEvaluationOoc",
               "setup_slack_ns": "0.1", "hold_slack_ns": "0.1", "timed_paths": 2,
               "unconstrained_paths": 0, "blocking_drc": 0}
        utilization = "| Site Type | Used | Available |\n" + "\n".join(
            f"| {name} | 1 | 100 |" for name in ("CLB LUTs", "CLB Registers", "Block RAM Tile", "DSPs", "URAM"))
        with self.assertRaises(ValueError):
            normalized_values(raw, utilization, {"profile": "a8w8-d16-hp1"})

    def test_routed_clock_switching_failure_rejects_publication(self) -> None:
        for values in ("-0.001 0.000 1 8", "0.125 -0.001 0 8", "0.125 0.000 1 8",
                       "0.125 0.000 0 0", "unknown", "NaN 0.000 0 8"):
            with self.subTest(values=values), tempfile.TemporaryDirectory(prefix="im2p-clock-limits-") as temporary:
                root = Path(temporary)
                prepare(root)
                result = run_flow(root, "set fixture_switching {" + values + "}")
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((root / "out/ooc-result.json").exists())


if __name__ == "__main__":
    unittest.main()
