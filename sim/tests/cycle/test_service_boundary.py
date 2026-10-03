from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from sim.tests.cycle.service_boundary import Observation, parse_rows, validate_rows


def complete_rows() -> list[Observation]:
    rows = []
    for sequence in range(5):
        for period in (3, 5):
            for phase in range(period):
                for isolated in (0, 1):
                    for work in (0, 1):
                        runs = sequence == 2 or (sequence == 1 and work == 1) or (sequence == 3 and work == 0)
                        accepted = phase + 3 * period + work * 1000
                        rows.append(Observation(sequence, period, phase, work, isolated, 5,
                            work if sequence == 4 else 0, 2, 3, 37 if runs else 64,
                            1, 1, 2 if runs else 4, 2 if runs else 0, 0, 0, accepted,
                            accepted + 100, accepted + 115, accepted + 117,
                            2 if runs else 1, 32, accepted + 100, 0,
                            accepted + 115, accepted + 117))
    return rows


class ServiceBoundaryTest(unittest.TestCase):
    def test_legacy_isolated_summary_is_not_service_proof(self) -> None:
        with self.assertRaisesRegex(ValueError, "missing service observations"):
            parse_rows("FIXTURE_ONLY start=1 done=395 cycles=394\n")

    def test_self_shrunk_phase_corpus_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "sequence/phase coverage"):
            validate_rows([])

    def test_complete_distinct_milestones_are_accepted(self) -> None:
        validate_rows(complete_rows())

    def test_duplicate_phase_case_is_rejected(self) -> None:
        rows = complete_rows()
        with self.assertRaisesRegex(ValueError, "sequence/phase coverage"):
            validate_rows(rows + [rows[0]])

    def test_done_substituted_for_resource_ready_is_rejected(self) -> None:
        rows = complete_rows()
        rows[0] = replace(rows[0], resource_ready=rows[0].result_ready)
        with self.assertRaisesRegex(ValueError, "ordering invalid"):
            validate_rows(rows)

    def test_next_work_before_resource_ready_is_rejected(self) -> None:
        rows = complete_rows()
        rows[1] = replace(rows[1], accepted=rows[0].resource_ready - 1)
        with self.assertRaisesRegex(ValueError, "before prior drained resource"):
            validate_rows(rows)

    def test_case_name_cannot_hide_changed_shape(self) -> None:
        rows = complete_rows()
        rows[0] = replace(rows[0], k=32)
        with self.assertRaisesRegex(ValueError, "fixed sequence fixture changed"):
            validate_rows(rows)

    def test_shifted_model_resource_endpoint_is_rejected(self) -> None:
        rows = complete_rows()
        rows[0] = replace(rows[0], model_resource_ready=rows[0].resource_ready + 1)
        with self.assertRaisesRegex(ValueError, "service model endpoint mismatch"):
            validate_rows(rows)

    def test_cli_help_exits_without_requiring_rtl_artifacts(self) -> None:
        result = subprocess.run([sys.executable, str(Path(__file__).with_name("service_boundary.py")),
                                 "--help"], capture_output=True, text=True, timeout=10, check=False)
        self.assertEqual(result.returncode, 0)
        self.assertIn("--rtl-build", result.stdout)

    def test_cli_missing_binding_publishes_no_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = subprocess.run([sys.executable,
                str(Path(__file__).with_name("service_boundary.py")),
                "--rtl-build", str(root / "missing"), "--library", str(root / "library"),
                "--out", str(root / "output")], capture_output=True, text=True,
                timeout=10, check=False)
            self.assertEqual(result.returncode, 1)
            self.assertFalse((root / "output").exists())


if __name__ == "__main__":
    unittest.main()
