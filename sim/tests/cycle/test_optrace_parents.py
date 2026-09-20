from __future__ import annotations

import copy
from pathlib import Path
import sys
from typing import TypeAlias
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from sim.cycle.optrace_parents import JsonValue, ParentError, ParentValidator

Record: TypeAlias = dict[str, JsonValue]


def records(bits: int = 8, dim: int = 16) -> list[Record]:
    parent: Record = dict(kind="parent_begin", sequence=2, run_id="fixture", phase_id=0,
        parent_invocation_id=0, layer="same-layer", operation="gemmini.matmul",
        provenance="dense_main", scope="stripe", activation_bits=bits, weight_bits=bits, dim=dim,
        m=4, n=2, k=32, tile_i_count=1, tile_j_count=1, tile_k_count=1,
        activation_stride_bytes=32, weight_stride_bytes=2, output_stride_bytes=8,
        scale_stride_elements=2, block_size=32, vector_op=5, output_domain=2,
        production_geometry_version=1)
    first = dict(parent, kind="npu_work", sequence=3, m=2, geometry_m=4,
                 row_begin=0, row_count=2, stripe_id=0, host_slot=0)
    second = dict(first, sequence=4, row_begin=2, stripe_id=1, host_slot=1)
    return [dict(kind="run", activation_bits=bits, weight_bits=bits, dim=dim),
            dict(kind="phase", phase_id=0), parent, first, second,
            dict(kind="parent_end", sequence=5, run_id="fixture", phase_id=0,
                 parent_invocation_id=0, status="success"), dict(kind="run_end")]


def validate(rows: list[Record]) -> None:
    validator = ParentValidator()
    for row in rows:
        validator.consume(row)
    validator.finish()


class ParentTests(unittest.TestCase):
    def test_six_profiles(self) -> None:
        for bits in (4, 8):
            for dim in (16, 32, 64):
                validate(records(bits, dim))

    def test_host_slot_domain(self) -> None:
        for value in (999, 2, -1, True, None):
            rows = records(); rows[3]["host_slot"] = value
            with self.subTest(value=value), self.assertRaisesRegex(ParentError, "host_slot"):
                validate(rows)

    def test_duplicate_stripe_id(self) -> None:
        rows = records(); rows[4]["stripe_id"] = 0
        with self.assertRaisesRegex(ParentError, "stripe ID"):
            validate(rows)

    def test_duplicate_range_preserves_total_count(self) -> None:
        rows = records(); rows[4] = dict(rows[3], sequence=4)
        self.assertEqual(sum(row["kind"] == "npu_work" for row in rows), 2)
        with self.assertRaisesRegex(ParentError, "row range"):
            validate(rows)

    def test_overlap_and_gap(self) -> None:
        for begin in (1, 3):
            rows = records(); rows[4].update(row_begin=begin, row_count=1, m=1)
            with self.subTest(begin=begin), self.assertRaisesRegex(ParentError, "row range"):
                validate(rows)

    def test_missing_tail(self) -> None:
        rows = records(); del rows[4]
        with self.assertRaisesRegex(ParentError, "incomplete parent row coverage"):
            validate(rows)

    def test_parent_shape_and_final_geometry(self) -> None:
        for key in ("m", "n", "k", "activation_stride_bytes"):
            rows = records(); rows[2][key] = 999
            with self.subTest(key=key), self.assertRaises(ParentError):
                validate(rows)

    def test_stripe_final_geometry_can_change_within_parent(self) -> None:
        rows = records()
        rows[3].update(tile_i_count=2, tile_j_count=3, tile_k_count=4)
        rows[4].update(tile_i_count=3, tile_j_count=1, tile_k_count=2)
        validate(rows)

    def test_stripe_final_geometry_domain(self) -> None:
        for key, value in (("tile_i_count", 0), ("tile_j_count", 65535),
                           ("tile_k_count", 2**32)):
            rows = records(); rows[3][key] = value
            with self.subTest(key=key), self.assertRaises(ParentError):
                validate(rows)

    def test_full_parent_requires_exact_final_geometry(self) -> None:
        rows = records(); del rows[4]
        rows[2]["scope"] = "full"
        rows[3].update(scope="full", m=4, row_count=4, tile_i_count=2, host_slot=None)
        with self.assertRaisesRegex(ParentError, "descriptor"):
            validate(rows)

    def test_unknown_parent(self) -> None:
        rows = records(); rows[3]["parent_invocation_id"] = 999
        with self.assertRaisesRegex(ParentError, "undeclared"):
            validate(rows)

    def test_duplicate_parent_declaration(self) -> None:
        rows = records(); rows.insert(3, copy.deepcopy(rows[2]))
        with self.assertRaisesRegex(ParentError, "parent identity"):
            validate(rows)

    def test_work_after_parent_end(self) -> None:
        rows = records(); rows.insert(-1, dict(rows[4], sequence=6))
        with self.assertRaisesRegex(ParentError, "completed parent"):
            validate(rows)

    def test_incomplete_parent_at_run_end(self) -> None:
        rows = records(); del rows[5]
        with self.assertRaisesRegex(ParentError, "incomplete parent"):
            validate(rows)

    def test_stale_phase(self) -> None:
        rows = records(); rows[3]["phase_id"] = 1
        with self.assertRaisesRegex(ParentError, "stale parent phase"):
            validate(rows)

    def test_phase_cannot_change_with_open_parent(self) -> None:
        rows = records(); rows.insert(4, dict(kind="phase", phase_id=1))
        with self.assertRaisesRegex(ParentError, "incomplete parent"):
            validate(rows)

    def test_full_parent_cannot_accept_stripe(self) -> None:
        rows = records(); rows[2]["scope"] = "full"
        with self.assertRaisesRegex(ParentError, "descriptor"):
            validate(rows)

    def test_interleaved_same_layer_shape_parents(self) -> None:
        rows = records()
        other = [dict(row, parent_invocation_id=1) for row in rows[2:6]]
        interleaved = rows[:3] + [other[0], rows[3], other[1], rows[4], other[2], rows[5], other[3], rows[-1]]
        validate(interleaved)

    def test_compact_rows_are_not_source_stripe_coverage(self) -> None:
        rows = records(); del rows[4]
        for row in rows[2:4]:
            row.update(scope="residual_compact", provenance="residual")
        rows[3].update(m=4, row_count=4, source_row_begin=128, source_row_count=2, stripe_id=19, host_slot=None)
        validate(rows)

    def test_parent_fields_strict(self) -> None:
        for change in ("missing", "unknown"):
            rows = records()
            if change == "missing":
                del rows[2]["tile_k_count"]
            else:
                rows[2]["weights"] = [1, 2]
            with self.subTest(change=change), self.assertRaisesRegex(ParentError, "fields"):
                validate(rows)


if __name__ == "__main__":
    unittest.main()
