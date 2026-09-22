from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from sim.tests.cycle import certify_run_aware as cert


CORPUS = Path(__file__).with_name("run_aware_corpus.json")
EXPECTED_KEYS = "268fa642294cc3a50156b8a2f60a744b31caea812059154ca38669c857f619e5"
RTL = Path(__file__).resolve().parents[4] / "evidence/rmd-run-aware-20260921T062828Z/task-08-rtl-073757"


class RunAwareCertificateTest(unittest.TestCase):
    def test_manifest_is_independently_fixed_before_rtl_parse(self) -> None:
        corpus = cert.load_manifest(CORPUS)
        self.assertEqual(corpus.case_count, 48)
        self.assertEqual(corpus.case_key_sha256, EXPECTED_KEYS)
        self.assertEqual(len(corpus.profiles), 6)
        self.assertEqual(len(corpus.cases), 8)
        self.assertEqual(corpus.cases[2].k, 32)
        self.assertEqual(corpus.cases[1].runs[1].original_block_id, 3)

    def test_manifest_rejects_missing_or_duplicate_profile_and_case(self) -> None:
        original = json.loads(CORPUS.read_text())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "corpus.json"
            for field in ("profiles", "cases"):
                for variant in ("missing", "duplicate"):
                    mutated = json.loads(json.dumps(original))
                    rows = mutated[field]
                    mutated[field] = rows[:-1] if variant == "missing" else rows + rows[:1]
                    path.write_text(json.dumps(mutated))
                    with self.subTest(field=field, variant=variant), self.assertRaises(ValueError):
                        cert.load_manifest(path)

    def test_truncated_duplicate_missing_and_wrong_profile_fail_closed(self) -> None:
        corpus = cert.load_manifest(CORPUS)
        profile = corpus.profiles[0]
        original = (RTL / profile / "isolated/run-aware-manual.log").read_text()
        cert.parse_rtl_log(original, profile, corpus)
        case_line = next(line for line in original.splitlines() if "case=unequal_12_10" in line)
        variants = (
            "\n".join(original.splitlines()[:-1]),
            original + case_line + "\n",
            original.replace(case_line + "\n", ""),
            original.replace(f"profile={profile}", "profile=a8w8-d64-hp1"),
        )
        for bad in variants:
            with self.subTest(bad=bad[-80:]), self.assertRaises(ValueError):
                cert.parse_rtl_log(bad, profile, corpus)

    def test_event_shift_delete_and_duplicate_are_hard_failures(self) -> None:
        corpus = cert.load_manifest(CORPUS)
        profile = "a8w8-d16-hp1"
        original = (RTL / profile / "isolated/events.csv").read_text()
        actual = list(cert.parse_events_csv(original, corpus, profile)[0])
        self.assertTrue(cert.events_exact(actual, actual))
        observed = cert.parse_rtl_log(
            (RTL / profile / "isolated/run-aware-manual.log").read_text(), profile, corpus)[0]
        library = CORPUS.parents[4] / "evidence/cycle-t7-8bZZn2/build/libim2p_cycle_model.dylib"
        model = cert.estimate_runs(library, profile, corpus.cases[0], corpus)
        self.assertEqual(cert.compare_case(observed, model, corpus.cases[0], tuple(actual), 16)["status"],
                         "PASS")
        selected = next(line for line in original.splitlines()
                        if line.startswith("1,") and ",load_issue," in line)
        fields = selected.split(",")
        fields[1] = str(int(fields[1]) + 1)
        for bad in (
            original.replace(selected, ",".join(fields), 1),
            original.replace(selected + "\n", "", 1),
            original.replace(selected + "\n", selected + "\n" + selected + "\n", 1),
        ):
            with self.subTest(bad=bad[-80:]):
                changed = list(cert.parse_events_csv(bad, corpus, profile)[0])
                self.assertFalse(cert.events_exact(actual, changed))
                comparison = cert.compare_case(observed, model, corpus.cases[0], tuple(changed), 16)
                self.assertEqual(comparison["status"], "FAIL")
                self.assertIsNotNone(comparison["selected_event_comparison"]["first_difference"])

    def test_wrong_library_and_source_hash_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source"
            path.write_bytes(b"expected")
            digest = hashlib.sha256(b"expected").hexdigest()
            cert.check_hash(path, digest)
            path.write_bytes(b"changed")
            with self.assertRaises(ValueError):
                cert.check_hash(path, digest)

    def test_model_request_contains_only_independent_geometry_and_scalar_runs(self) -> None:
        corpus = cert.load_manifest(CORPUS)
        library = CORPUS.parents[4] / "evidence/cycle-t7-8bZZn2/build/libim2p_cycle_model.dylib"
        answer = cert.estimate_runs(library, corpus.profiles[0], corpus.cases[0], corpus)
        request = answer["request"]
        self.assertEqual(request["accepted_cycle"], 1)
        self.assertEqual(request["k"], 22)
        self.assertEqual(request["runs"], [[0, 4095, 0, 12], [1, 1023, 12, 10]])
        self.assertTrue({"start", "done", "cycles", "actual", "expected", "carriers"}.isdisjoint(request))


if __name__ == "__main__":
    unittest.main()
