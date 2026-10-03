from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sim.cycle.certificate_contract import array_value, read_document
from sim.cycle.npu_trace_schema import object_value
from sim.cycle.run_aware_certificate import case_evidence_matches
from sim.cycle.run_aware_production_evidence import compare_events
from sim.tests.cycle import certify_production_run_aware as certificate
from sim.tests.cycle import production_run_work
from sim.tests.cycle.production_run_work import (
    PRODUCTION_MANIFEST,
    CertificateCase,
    load_manifest,
    read_work_fixture,
)

WORK = """RMD_RUN_WORK_V1
descriptor 5 8 1 8 1 16 2 1 2 2 1 1 16 1 32 128 1 0 1 2 5 2 19
geometry 1 104 8 8 16 0 2 1 2 1 1 1 2 0 2 0
runs 1 32 128 2
run 0 1 0 1
run 3 2 1 1
ROW_MAP 2
0 7
1 8
A 4
1 2 3 4
B 2
1 1
CARRIERS 2
1 1
OUTPUT 2
3 7
"""


class ProductionRunAwareCertificateTest(unittest.TestCase):
    def test_fixed_production_manifest_rejects_case_shrink(self) -> None:
        # Given: the 42 producer-captured cases across all six profiles.
        manifest = load_manifest()
        self.assertEqual(manifest["case_count"], 42)
        with tempfile.TemporaryDirectory() as directory:
            changed = read_document(PRODUCTION_MANIFEST)
            _ = array_value(changed["cases"], "cases").pop()
            path = Path(directory) / "short.json"
            _ = path.write_text(json.dumps(changed))
            # When: the manifest loses one captured case.
            # Then: the independent byte pin rejects the shrink.
            with (patch.object(production_run_work, "PRODUCTION_MANIFEST", path),
                  self.assertRaisesRegex(ValueError, "manifest changed")):
                _ = load_manifest()

    def test_captured_work_preserves_ordered_runs_and_row_map(self) -> None:
        # Given: one producer-formatted work file with nonidentity lane IDs.
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rmd-run-work-gap-0-3.txt"
            _ = path.write_text(WORK)
            # When: the independent certificate parses the captured work.
            work = read_work_fixture(path)
        # Then: it retains producer geometry and row identity exactly.
        self.assertEqual(work["runs"], [[0, 1, 0, 1], [3, 2, 1, 1]])
        self.assertEqual(work["row_map"], [[0, 7], [1, 8]])
        self.assertEqual(work["shape"], [2, 1, 2])

    def test_selected_event_one_cycle_mutation_fails(self) -> None:
        # Given: identical model and RTL selected event multisets.
        model = [(1, "work"), (3, "load_issue"), (7, "logical_done")]
        rtl = list(model)
        # When: one real RTL event moves by one cycle.
        rtl[1] = (4, "load_issue")
        # Then: multiset comparison rejects it and identifies the first difference.
        comparison = compare_events(model, rtl)
        self.assertFalse(comparison["exact"])
        self.assertIsNotNone(comparison["first_difference"])

    def test_official_rtl_route_rejects_unbound_binary(self) -> None:
        # Given: a resolved profile and a binary outside its official build root.
        root = Path("/tmp/official/a8w8-d16-hp1")
        files = {"resolved_profile": root / "resolved-profile.json",
                 "rtl_binary": Path("/tmp/stale/VIM2PGemminiWSHP1RtlTest")}
        # When: certificate admission resolves the official profile root.
        # Then: the foreign executable is rejected before observation.
        with self.assertRaisesRegex(ValueError, "official RTL binary"):
            _ = certificate.official_profile_root(files)

    def test_raw_recomputation_rejects_coordinated_certificate_tamper(self) -> None:
        # Given: one recomputed case from raw RTL and the value-free model.
        comparison = compare_events([(1, "work")], [(1, "work")])
        summary = {"start": 1, "done": 2, "cycles": 1, "loops": 1,
                   "loads": 1, "executes": 1, "stores": 1, "commits": 1,
                   "scale_reads": 1, "scale_responses": 1, "completions": 1}
        recomputed: CertificateCase = {
            "profile": "a4w4-d16-hp1", "case": "gap_0_3", "shape": [1, 1, 1],
            "tile": [1, 1, 1], "original_k": 128, "runs": [[0, 1, 0, 1]],
            "row_map": [[0, 0]], "timing": {}, "framing": "planner-blocks",
            "status": "PASS", "rtl_admitted": True, "model_admitted": True,
            "rtl_summary": summary, "model_summary": summary,
            "selected_event_multiset": True, "selected_event_comparison": comparison,
            "differences": {}, "delta_cycles": 0}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "row.json"
            _ = path.write_text(json.dumps(recomputed))
            row = read_document(path)
            self.assertTrue(case_evidence_matches(row, recomputed))
            # When: the certificate's matching endpoint values are forged.
            row["rtl_summary"] = {"cycles": 2}
            row["model_summary"] = {"cycles": 2}
            # Then: raw recomputation still rejects the changed summary.
            self.assertFalse(case_evidence_matches(row, recomputed))
            original = read_document(path)
            row["rtl_summary"] = original["rtl_summary"]
            row["model_summary"] = original["model_summary"]
            altered = object_value(original["selected_event_comparison"])
            altered["model_sha256"] = altered["rtl_sha256"] = "0" * 64
            row["selected_event_comparison"] = altered
            self.assertFalse(case_evidence_matches(row, recomputed))


if __name__ == "__main__":
    _ = unittest.main()
