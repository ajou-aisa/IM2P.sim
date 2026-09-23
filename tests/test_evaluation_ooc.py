from __future__ import annotations

from fractions import Fraction
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class OocTests(unittest.TestCase):
    def test_fixed_policy_implementation_exists(self) -> None:
        self.assertIsNotNone(importlib.util.find_spec("scripts.evaluation_ooc"))

    def test_exact_target_frequency_across_six_profiles(self) -> None:
        from scripts.evaluation_ooc_policy import target_frequency
        for bits in (4, 8):
            for dim in (16, 32, 64):
                self.assertEqual(target_frequency(f"a{bits}w{bits}-d{dim}-hp1"),
                                 Fraction(33 * 10**12, 2 * dim * dim))

    def test_wrapper_preserves_coordinates_and_sync_reset(self) -> None:
        from scripts.evaluation_ooc_rtl import wrap_core
        rtl = "module IM2PGemminiWSHP1A8W8D16(input clock, reset, input [7:0] a,b, output [31:0] y); endmodule"
        wrapper, reset = wrap_core(rtl, "a8w8-d16-hp1")
        self.assertIn("input CLK", wrapper)
        self.assertIn(".clock(CLK)", wrapper)
        self.assertIn(".reset(reset)", wrapper)
        self.assertIn("input [7:0] b", wrapper)
        self.assertEqual(reset, "NO_ASYNC_RST_N_EXCLUSION")

    def test_async_reset_requires_actual_source_event(self) -> None:
        from scripts.evaluation_ooc_rtl import wrap_core
        template = "module IM2PGemminiWSHP1A4W4D32(input clock, RST_N, output reg y); %s endmodule"
        wrapper, reset = wrap_core(template % "always @(posedge clock or negedge RST_N) y<=0;", "a4w4-d32-hp1")
        self.assertEqual(reset, "ASYNC_RST_N_SOURCE_EVENT_PROVEN")
        self.assertIn(".RST_N(RST_N)", wrapper)
        _, sync = wrap_core(template % "always @(posedge clock) y<=RST_N;", "a4w4-d32-hp1")
        self.assertEqual(sync, "NO_ASYNC_RST_N_EXCLUSION")

    def test_wrong_top_or_port_grammar_rejects(self) -> None:
        from scripts.evaluation_ooc_rtl import wrap_core
        for source in ("module PE(input clock); endmodule",
                       "module IM2PGemminiWSHP1A8W8D16(input other_clock); endmodule",
                       "module IM2PGemminiWSHP1A8W8D16(input clock, input foo[2]); endmodule"):
            with self.assertRaises(ValueError):
                wrap_core(source, "a8w8-d16-hp1")

    def test_string_literal_cannot_prove_async_reset(self) -> None:
        from scripts.evaluation_ooc_rtl import wrap_core
        source = ('module IM2PGemminiWSHP1A8W8D16(input clock, RST_N, output reg y); '
                  'always @(posedge clock) begin y<=RST_N; $display("always @(negedge RST_N)"); end endmodule')
        _, reset = wrap_core(source, "a8w8-d16-hp1")
        self.assertEqual(reset, "NO_ASYNC_RST_N_EXCLUSION")

    def test_tool_scratch_stays_in_evidence_directory(self) -> None:
        from scripts.evaluation_ooc import execute
        with tempfile.TemporaryDirectory(prefix="im2p-ooc-cwd-") as temporary:
            log = Path(temporary) / "tool.log"
            code = execute([sys.executable, "-c", "import os; print(os.getcwd())"], log, 10)
            self.assertEqual(code, 0)
            self.assertEqual(Path(log.read_text().strip()).resolve(), Path(temporary).resolve())

    def test_constraint_has_only_confirmed_clock_and_proven_async_reset(self) -> None:
        from scripts.evaluation_ooc_rtl import constraints
        xdc = constraints(100_000_000, "NO_ASYNC_RST_N_EXCLUSION")
        self.assertEqual(xdc, "create_clock -name core_clk -period 10 [get_ports CLK]\n")
        self.assertNotIn("false_path", xdc)
        async_xdc = constraints(100_000_000, "ASYNC_RST_N_SOURCE_EVENT_PROVEN")
        self.assertIn("set_false_path -from [get_ports RST_N]", async_xdc)

    def test_finite_grid_rejects_duplicates_and_nonpositive(self) -> None:
        from scripts.evaluation_ooc_policy import frequencies
        self.assertEqual(frequencies([200, 100]), (100, 200))
        for values in ([], [0], [-1], [100, 100], list(range(1, 66))):
            with self.assertRaises(ValueError):
                frequencies(values)

    def test_preflight_has_confirmed_policy_not_fake_board(self) -> None:
        with tempfile.TemporaryDirectory(prefix="im2p-ooc-preflight-") as temporary:
            path = Path(temporary) / "status.json"
            result = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/evaluation_ooc.py"),
                                     "preflight", "--frequencies-hz", "100000000", "200000000", "--out", str(path)],
                                    capture_output=True, text=True, timeout=30)
            self.assertIn(result.returncode, (0, 2), result.stderr)
            row = json.loads(path.read_text())
            self.assertEqual(row["part"], "xcu250-figd2104-2L-e")
            self.assertEqual(row["target_peak_tops"], 33)
            self.assertEqual(len(row["profiles"]), 6)
            self.assertIsNone(row["selected_frequency_hz"])
            self.assertNotIn("resolved_board_and_constraints", row["configuration_gaps"])
            self.assertEqual(row["physical_programming"], "NOT_RUN")

    def test_cli_rejects_other_part(self) -> None:
        result = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/evaluation_ooc.py"),
                                 "preflight", "--part", "xc7a35t", "--out", "/not-created"],
                                capture_output=True, text=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)

    def test_resource_report_keeps_half_bram_units(self) -> None:
        from scripts.evaluation_ooc_report import resources
        report = "| Site Type | Used | Fixed | Available | Util% |\n" + "\n".join(
            f"| {name} | {used} | 0 | 100 | 0 |" for name, used in
            (("CLB LUTs*", "4"), ("CLB Registers", "8"), ("Block RAM Tile", "1.5"), ("DSPs", "2"), ("URAM", "0")))
        usage, limits = resources(report)
        self.assertEqual(usage["BRAM18_EQUIVALENTS"], 3)
        self.assertEqual(limits["BRAM18_EQUIVALENTS"], 200)
        with self.assertRaises(ValueError):
            resources(report.replace("| URAM | 0 | 0 | 100 | 0 |", ""))

    def test_ooc_report_rejects_synth_only_and_wrong_part(self) -> None:
        from scripts.evaluation_ooc_report import normalized_values
        raw = {"schema": "im2p-ooc-tool-result", "version": 2, "status": "ROUTE_COMPLETED",
               "part": "xcu250-figd2104-2L-e", "clock_port": "CLK", "timing_stage": "POST_ROUTE",
               "core_top": "IM2PGemminiWSHP1A8W8D16", "top": "PoTalEvaluationOoc"}
        for field, value in (("part", "xc7a35t"), ("timing_stage", "POST_SYNTH_ESTIMATE"), ("top", "PEOnly")):
            with self.assertRaises(ValueError):
                normalized_values({**raw, field: value}, "unused", {"profile": "a8w8-d16-hp1"})

    def test_synthetic_report_chain_selection_and_tamper(self) -> None:
        from scripts.evaluation_clock import load_selection
        from scripts.evaluation_clock_contract import file_ref
        from scripts.evaluation_ooc_policy import POLICY_PATH
        from scripts.evaluation_ooc_report import select_ooc, write_sample
        from scripts.evaluation_ooc_rtl import constraints
        with tempfile.TemporaryDirectory(prefix="im2p-ooc-report-") as temporary:
            root = Path(temporary)
            source = root / "source.json"
            source.write_text(json.dumps({"profile": "a8w8-d16-hp1", "execution_kind": "SYNTHETIC",
                                          "hardware_contract": {"sha256": "a" * 64}, "artifacts": {},
                                          "reset_policy": "NO_ASYNC_RST_N_EXCLUSION"}))
            samples = []
            for frequency in (100_000_000, 200_000_000):
                candidate = root / str(frequency)
                implementation = candidate / "implementation"
                implementation.mkdir(parents=True)
                request = {"frequency_hz": frequency, "part": "xcu250-figd2104-2L-e", "policy": file_ref(POLICY_PATH)}
                (candidate / "request.json").write_text(json.dumps(request))
                (candidate / "clock.xdc").write_text(constraints(frequency, "NO_ASYNC_RST_N_EXCLUSION"))
                (implementation / "route.dcp").write_text("synthetic checkpoint")
                raw = {"schema": "im2p-ooc-tool-result", "version": 2, "status": "ROUTE_COMPLETED",
                       "part": "xcu250-figd2104-2L-e", "clock_port": "CLK", "timing_stage": "POST_ROUTE",
                       "core_top": "IM2PGemminiWSHP1A8W8D16", "top": "PoTalEvaluationOoc",
                       "setup_slack_ns": "0.5", "hold_slack_ns": "0", "timed_paths": 2,
                       "unconstrained_paths": 0, "blocking_drc": 0, "tool_version": "SYNTHETIC",
                       "period_ns": str(10**9 // frequency), "clock_switching_worst_slack_ns": "0.125",
                       "clock_switching_total_slack_ns": "0.000", "clock_switching_failing_endpoints": 0,
                       "clock_switching_total_endpoints": 8}
                (implementation / "ooc-result.json").write_text(json.dumps(raw))
                report = "| Site Type | Used | Fixed | Available | Util% |\n" + "\n".join(
                    f"| {name} | 1 | 0 | 100 | 1 |" for name in
                    ("CLB LUTs", "CLB Registers", "Block RAM Tile", "DSPs", "URAM"))
                (implementation / "post-route-utilization.rpt").write_text(report)
                for report_name in ("post-route-timing.rpt", "post-route-pulse-width.rpt"):
                    (implementation / report_name).write_text("SYNTHETIC clock switching report")
                samples.append(write_sample(candidate, source))
            sweep = root / "sweep.json"
            sweep.write_text(json.dumps({"attempted_frequency_hz": [100_000_000, 200_000_000],
                                         "attempts": [{"frequency_hz": f, "exit_code": 0} for f in (100_000_000, 200_000_000)]}))
            result = select_ooc(samples, sweep)
            self.assertEqual(result["version"], 2)
            self.assertEqual(result["status"], "SYNTHETIC_ONLY")
            self.assertEqual(result["selected_frequency_hz"], 200_000_000)
            output = root / "clock-selection.json"
            output.write_text(json.dumps(result))
            with self.assertRaises(ValueError):
                load_selection(output, "a8w8-d16-hp1")
            with self.assertRaises(ValueError):
                select_ooc(samples[:1], sweep)
            (root / "200000000/implementation/ooc-result.json").write_text("changed")
            with self.assertRaises(ValueError):
                select_ooc(samples, sweep)


if __name__ == "__main__":
    unittest.main()
