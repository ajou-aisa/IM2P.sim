# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
# ─── How to run ───
# PYTHONPATH=. uv run --offline pytest -q sim/tests/cycle/test_sequence_corpus.py
# ──────────────────
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from sim.cycle.npu_trace_schema import Record, object_value
from sim.cycle.reconstruct_graph import array, sha256
from sim.tests.cycle import compositional_sequence_work as sequence
from sim.tests.cycle.compositional_sequence_v2_corpus import validate_corpus
from sim.tests.cycle.test_sequence_absolute_offer import _sealed_manifest


def _control(tmp_path: Path) -> tuple[Record, Record]:
    stimulus = _sealed_manifest(tmp_path)
    second = object_value(array(stimulus['works'])[1])
    second['scope'] = 'residual_compact'
    second['original_k'] = 64
    second['runs'] = [
        {'original_block_id': 0, 'original_k_mask': 1,
         'compact_k_begin': 0, 'compact_k_count': 1},
        {'original_block_id': 1, 'original_k_mask': 1,
         'compact_k_begin': 1, 'compact_k_count': 1},
    ]
    stimulus['pipeline_owners'] = [{'source': 'independent-control', 'work_id': 12}]
    projected = deepcopy(stimulus)
    projected['schema'] = 'im2p-compositional-sequence-projection'
    projected['version'] = 1
    projected['offer_policy'] = 'public-ready-or-later-arrival-v1'
    projected['works'] = [deepcopy({key: value for key, value in object_value(row).items()
                                   if key not in ('request_available_cycle', 'port_offer_cycle')}) |
                          {'arrival_delay': 0} for row in array(stimulus['works'])]
    parents = [object_value(row) for row in array(stimulus['pipeline_parents'])]
    works = [object_value(row) for row in array(stimulus['works'])]
    receipt_path = tmp_path / 'source-receipt.json'
    receipt_path.write_text(json.dumps({'source': stimulus['producer_artifacts']}) + '\n')
    plan: Record = {
        'schema': 'im2p-two-parent-corpus-plan-v1', 'version': 1,
        'status': 'REVIEWED_SOURCE_BOUND', 'case_id': stimulus['case_id'],
        'profile': stimulus['profile'], 'selected_parent_indices': [0, 1],
        'parent_ids': [21, 22], 'required_work_ids': [11, 12],
        'fence_work_ids': [[11], [12]],
        'work_bindings': [row['work_binding'] for row in works],
        'work_slots': [row['slot'] for row in works],
        'offer_epochs': [[row['request_available_cycle'], row['port_offer_cycle']] for row in works],
        'producer_artifacts': stimulus['producer_artifacts'],
        'producer_receipt_sha256': sha256(receipt_path),
    }
    assert [row['parent_id'] for row in parents] == plan['parent_ids']
    grid_path = tmp_path / 'predeclared-offer-grid.json'
    grid_path.write_text(json.dumps({
        'schema': 'im2p-two-parent-absolute-offer-grid-v1', 'version': 1,
        'status': 'PREDECLARED_BEFORE_TARGET_RTL',
        'cases': [{
            'profile': stimulus['profile'], 'producer_artifacts': stimulus['producer_artifacts'],
            'required_work_ids': plan['required_work_ids'], 'parent_ids': plan['parent_ids'],
            'parent_fences': plan['fence_work_ids'],
            'request_available_cycles': [row['request_available_cycle'] for row in works],
            'electrical_port_offer_cycles': [row['port_offer_cycle'] for row in works],
        }],
    }, sort_keys=True) + '\n')
    plan['predeclared_offer_grid_sha256'] = sha256(grid_path)
    stimulus['predeclared_offer_grid'] = {'path': str(grid_path), 'sha256': sha256(grid_path)}
    plan_path = tmp_path / 'reviewed-plan.json'
    plan_path.write_text(json.dumps(plan, sort_keys=True) + '\n')
    stimulus['producer_corpus'] = {
        'schema': 'TWO_CONTIGUOUS_PARENTS_V1',
        'plan': {'path': str(plan_path), 'sha256': sha256(plan_path)},
        'receipt': {'path': str(receipt_path), 'sha256': sha256(receipt_path)},
    }
    return stimulus, projected


def _check(stimulus: Record, projected: Record) -> None:
    def projector(_trace: Path, _lifecycle: Path, _semantic: Path,
                  _delays: tuple[int, ...], _indices: tuple[int, ...]) -> Record:
        return deepcopy(projected)

    validate_corpus(stimulus, projector)


def test_two_parent_control_accepts_exact_authority(tmp_path: Path) -> None:
    # Given: two ordered parents and one separately bound authority projection.
    stimulus, projected = _control(tmp_path)

    # When: the opt-in authority comparison runs.
    _check(stimulus, projected)

    # Then: the complete unmodified work set remains admissible.
    assert len(array(stimulus['works'])) == 2


def test_two_parent_control_rejects_self_consistent_fence_work_shrink(tmp_path: Path) -> None:
    # Given: one parent removes its work and fence while raw authority remains complete.
    stimulus, projected = _control(tmp_path)
    second = object_value(array(stimulus['pipeline_parents'])[1])
    second['required_work_ids'] = []
    second['fence_required_work_ids'] = []
    stimulus['works'] = array(stimulus['works'])[:1]

    # When: the opt-in comparison checks the resealed content against raw authority.
    with pytest.raises(ValueError, match='producer corpus parent projection differs'):
        # Then: shrinking both internal sets cannot remove source-required work.
        _check(stimulus, projected)


def test_two_parent_control_rejects_ordered_run_shrink(tmp_path: Path) -> None:
    # Given: a residual loses only its second original-block run.
    stimulus, projected = _control(tmp_path)
    array(object_value(array(stimulus['works'])[1])['runs']).pop()

    # When: the work is checked against unchanged source authority.
    with pytest.raises(ValueError, match='producer corpus work projection differs'):
        # Then: an unchanged work ID cannot hide an altered run list.
        _check(stimulus, projected)


def test_two_parent_control_rejects_missing_owner(tmp_path: Path) -> None:
    # Given: the manifest drops its one source-bound ownership transition.
    stimulus, projected = _control(tmp_path)
    stimulus['pipeline_owners'] = []

    # When: the owner stream is checked against unchanged source authority.
    with pytest.raises(ValueError, match='producer corpus owner projection differs'):
        # Then: complete work IDs alone cannot hide missing ownership.
        _check(stimulus, projected)


def test_two_parent_control_rejects_fence_only_mutation(tmp_path: Path) -> None:
    # Given: the second parent's fence drops one required work but the work stays.
    stimulus, projected = _control(tmp_path)
    object_value(array(stimulus['pipeline_parents'])[1])['fence_required_work_ids'] = []

    # When: the parent fence is checked against unchanged source authority.
    with pytest.raises(ValueError, match='producer corpus parent projection differs'):
        # Then: the fence must retain the exact source-required set.
        _check(stimulus, projected)


def test_corpus_source_parser_hash_mutation_fails_before_output(tmp_path: Path) -> None:
    # Given: a sealed v2 input declaring a stale pipeline-parser source digest.
    stimulus = _sealed_manifest(tmp_path)
    source = object_value(stimulus['source_sha256'])
    parser = 'sim/cycle/execution_pipeline_contract.py'
    assert source[parser] != '0' * 64
    source[parser] = '0' * 64
    stimulus['stimulus_sha256'] = sequence.stimulus_digest(stimulus)

    # When: the v2 admission boundary checks its source closure.
    with pytest.raises(ValueError, match='stimulus source digest mismatch'):
        # Then: a rehashed manifest cannot hide parser source drift.
        sequence.validate_stimulus(stimulus)
