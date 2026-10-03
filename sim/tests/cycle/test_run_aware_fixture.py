from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.gemmini_replay_contract import hardware_contract
from sim.cycle import optrace
from sim.cycle.npu_trace_schema import NpuTraceError, parse_record
from sim.tests.cycle.run_aware_fixture import FixtureError, parse_fixture, read_fixture
from sim.tests.cycle.test_npu_trace import records as npu_records
from sim.tests.cycle.test_optrace import records as optrace_records


def fixture() -> dict:
    return {
        "schema": "im2p-run-aware-fixture", "version": 1,
        "profile": "a8w8-d16-hp1", "hardware_contract": hardware_contract("a8w8-d16-hp1"),
        "m": 16, "n": 16, "k": 22, "original_k": 64,
        "tile_i_count": 1, "tile_j_count": 1, "tile_k_count": 2,
        "runs": [
            {"original_block_id": 0, "original_k_mask": (1 << 12) - 1,
             "compact_k_begin": 0, "compact_k_count": 12},
            {"original_block_id": 1, "original_k_mask": (1 << 10) - 1,
             "compact_k_begin": 12, "compact_k_count": 10},
        ],
    }


class FixtureTests(unittest.TestCase):
    def test_deterministic_scalar_parse(self) -> None:
        source = fixture()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.json"
            path.write_text(json.dumps(source))
            parsed = read_fixture(path)
        self.assertEqual(parsed, parse_fixture(source))
        self.assertEqual((parsed.m, parsed.n, parsed.k, parsed.tile_i_count,
                          parsed.tile_j_count, parsed.tile_k_count), (16, 16, 22, 1, 1, 2))
        self.assertEqual(tuple(run.original_block_id for run in parsed.runs), (0, 1))
        self.assertEqual(tuple(run.compact_k_count for run in parsed.runs), (12, 10))

    def test_rejects_unknown_or_inferred_geometry(self) -> None:
        for field, value in (("version", True), ("m", 0), ("n", True), ("k", 21), ("tile_i_count", 0),
                             ("tile_j_count", 0), ("tile_k_count", 0), ("tile_k_count", 2**32),
                             ("profile", "a8w8-d48-hp1"), ("expected_rtl_cycles", 50),
                             ("activation_values", [1])):
            with self.subTest(field=field):
                source = fixture()
                source[field] = value
                with self.assertRaises(FixtureError):
                    parse_fixture(source)
        source = fixture()
        del source["tile_i_count"]
        with self.assertRaises(FixtureError):
            parse_fixture(source)

    def test_rejects_malformed_run_view(self) -> None:
        for change in (
            {"compact_k_begin": 11}, {"compact_k_count": 0}, {"original_k_mask": 0},
            {"original_k_mask": 1}, {"original_block_id": 0}, {"original_block_id": 2},
            {"original_k_mask": 1 << 31}, {"carrier": 4},
        ):
            with self.subTest(change=change):
                source = fixture()
                source["runs"][1].update(change)
                with self.assertRaises(FixtureError):
                    parse_fixture(source)

    def test_fixture_and_production_traces_are_mutually_exclusive(self) -> None:
        source = fixture()
        with self.assertRaises(NpuTraceError):
            parse_record(json.dumps(source))
        rows = optrace_records()
        rows[0] = source
        with self.assertRaises(optrace.TraceError):
            optrace.validate_records(rows)
        for production in (optrace_records()[0], npu_records()[0]):
            with self.assertRaises(FixtureError):
                parse_fixture(production)

    def test_duplicate_json_keys_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "duplicate.json"
            path.write_text('{"schema":"im2p-run-aware-fixture","schema":"im2p-run-aware-fixture"}')
            with self.assertRaises(FixtureError):
                read_fixture(path)


if __name__ == "__main__":
    unittest.main()
