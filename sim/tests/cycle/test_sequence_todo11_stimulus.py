# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
# ─── How to run ───
# PYTHONPATH=. uv run pytest -q sim/tests/cycle/test_sequence_todo11_stimulus.py
# ──────────────────
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.gemmini_resolve_profile import JsonValue
from sim.cycle.npu_trace_schema import Record, integer, object_value
from sim.cycle.reconstruct_graph import array
from sim.tests.cycle import compositional_sequence_work as sequence
from sim.tests.cycle.test_sequence_absolute_offer import _coherent_log, _sealed_manifest


def test_rejects_explicit_mid_sequence_reset(tmp_path: Path) -> None:
    # Given: a valid sealed schedule with an added reset between works.
    stimulus = _sealed_manifest(tmp_path)
    stimulus['reset_ordinals'] = [1]
    stimulus['stimulus_sha256'] = sequence.stimulus_digest(stimulus)

    # When: the schema checks the re-sealed schedule.
    with pytest.raises(ValueError, match='mid-sequence reset'):
        # Then: an in-session reset cannot be silently ignored.
        sequence.validate_stimulus(stimulus)


def test_run_aware_diagnostic_needs_no_fake_producer_parent(tmp_path: Path) -> None:
    # Given: two run-aware test descriptors with no pipeline parent or trace record.
    stimulus = _sealed_manifest(tmp_path)
    runs: list[JsonValue] = [
        {'original_block_id': 0, 'original_k_mask': 0xFFF,
         'compact_k_begin': 0, 'compact_k_count': 12},
        {'original_block_id': 1, 'original_k_mask': 0x3FF,
         'compact_k_begin': 12, 'compact_k_count': 10},
    ]
    for raw in array(stimulus['works']):
        work = object_value(raw)
        work['scope'] = 'residual_compact'
        work['original_k'] = 42
        work['runs'] = runs
        work.pop('trace_record')
        object_value(work['input']).update({
            'm': 16, 'n': 16, 'k': 22, 'tile_i_count': 1,
            'tile_j_count': 1, 'tile_k_count': 1,
            'activation_stride_bytes': 22, 'weight_stride_bytes': 16,
            'output_stride_bytes': 64, 'scale_stride_elements': 16,
        })
    for key in ('pipeline_parents', 'pipeline_owners', 'selected_parent_indices',
                'producer_artifacts'):
        stimulus.pop(key)
    source = tmp_path / 'run-aware-source.json'
    source.write_text(json.dumps({'input': object_value(array(stimulus['works'])[0])['input'],
                                  'runs': runs}, sort_keys=True) + '\n')
    stimulus['fixture_kind'] = 'TEST_ONLY_RUN_AWARE_DIAGNOSTIC'
    stimulus['diagnostic_source'] = {
        'path': str(source), 'sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
    }
    stimulus['stimulus_sha256'] = sequence.stimulus_digest(stimulus)

    # When: the diagnostic schema and numeric serializer consume the sealed input.
    sequence.validate_stimulus(stimulus)
    numeric = sequence.absolute_numeric_text(stimulus)

    # Then: two residual works are serialized without a fake producer parent.
    assert 'WORKS 2\n' in numeric
    assert numeric.count('\nR ') == 4


def test_strict_observation_scope_rejects_missing_wire_fields(tmp_path: Path) -> None:
    # Given: a sealed Todo11 scope over a log containing only Todo10 fields.
    stimulus = _sealed_manifest(tmp_path)
    stimulus['required_observations'] = ['FRAGMENTS', 'TAG_STATE']
    stimulus['stimulus_sha256'] = sequence.stimulus_digest(stimulus)

    # When: the v2 comparator evaluates the strict declared scope.
    with pytest.raises(ValueError, match='required observation'):
        # Then: absent physical fragments and tag state cannot inherit endpoint PASS.
        sequence.validate_absolute_log(_coherent_log(stimulus), stimulus)


def _tagged_log(stimulus: Record) -> str:
    rows: list[str] = []
    for line in _coherent_log(stimulus).splitlines():
        if line.startswith(('COMPOSITION_WORK ', 'MODEL_WORK ')):
            name, payload = line.split(' ', 1)
            work = json.loads(payload)
            work.update({'mesh_tag_queue_len': 1, 'mesh_tag_head_id': 0,
                         'mesh_tag_enqueues': 0, 'mesh_tag_dequeues': 0,
                         'mesh_tag_max_occupancy': 1})
            if name == 'COMPOSITION_WORK':
                work.update({'physical_fragment_count': 0,
                             'mesh_tag_full_backpressure_cycles': 0})
            else:
                work.update({'mesh_tag_full_backpressure_mapped': False,
                             'mesh_tag_full_backpressure_cycles': None})
            line = name + ' ' + json.dumps(work)
        rows.append(line)
    for ordinal, raw in enumerate(array(stimulus['works'])):
        offer = integer(object_value(raw), 'port_offer_cycle')
        resource = 6 if ordinal == 0 else offer + 1
        for kind in ('RTL_TAG_EDGE', 'MODEL_TAG_EDGE'):
            for phase, first in (('OFFER', offer), ('RESOURCE', resource - 1)):
                rows.extend(f'{kind} {ordinal} {phase} {cycle} 1 0 0 0'
                            for cycle in (first, first + 1))
    return '\n'.join(rows)


def test_strict_tag_state_accepts_unmapped_separate_full_pressure(tmp_path: Path) -> None:
    # Given: fragment and tag-state fields match while separate full pressure is unmapped.
    stimulus = _sealed_manifest(tmp_path)
    stimulus['required_observations'] = ['FRAGMENTS', 'TAG_STATE']
    stimulus['stimulus_sha256'] = sequence.stimulus_digest(stimulus)

    # When: the strict comparator checks only the declared observations.
    works = sequence.validate_absolute_log(_tagged_log(stimulus), stimulus)

    # Then: two works may pass tag state without fabricating a full-stall witness.
    assert len(works) == 2


def test_explicit_full_pressure_scope_rejects_unmapped_metric(tmp_path: Path) -> None:
    # Given: full pressure is separately required and native mapping is false.
    stimulus = _sealed_manifest(tmp_path)
    stimulus['required_observations'] = ['FRAGMENTS', 'TAG_STATE', 'TAG_FULL_PRESSURE']
    stimulus['stimulus_sha256'] = sequence.stimulus_digest(stimulus)

    # When: the strict comparator checks the separately declared pressure metric.
    with pytest.raises(ValueError, match='UNRESOLVED_TAG_FULL_PRESSURE'):
        # Then: no exact pressure PASS is inferred from a null native counter.
        sequence.validate_absolute_log(_tagged_log(stimulus), stimulus)


def test_full_pressure_scope_reports_available_tag_mismatch_first(tmp_path: Path) -> None:
    # Given: full pressure is unmapped and one available tag-head observation differs.
    stimulus = _sealed_manifest(tmp_path)
    stimulus['required_observations'] = ['FRAGMENTS', 'TAG_STATE', 'TAG_FULL_PRESSURE']
    stimulus['stimulus_sha256'] = sequence.stimulus_digest(stimulus)
    raw = _tagged_log(stimulus).replace('MODEL_TAG_EDGE 1 OFFER 6 1 0 0 0',
                                        'MODEL_TAG_EDGE 1 OFFER 6 1 1 0 0', 1)

    # When: the strict comparator checks independent tag windows.
    with pytest.raises(ValueError, match='TAG_EDGE first divergence'):
        # Then: the first real field mismatch is preserved ahead of deferred pressure.
        sequence.validate_absolute_log(raw, stimulus)


def test_strict_scope_rejects_tag_edge_head_divergence(tmp_path: Path) -> None:
    # Given: one native OFFER tag-head differs while its queue length is one.
    stimulus = _sealed_manifest(tmp_path)
    stimulus['required_observations'] = ['FRAGMENTS', 'TAG_STATE']
    stimulus['stimulus_sha256'] = sequence.stimulus_digest(stimulus)
    raw = _tagged_log(stimulus).replace('MODEL_TAG_EDGE 1 OFFER 6 1 0 0 0',
                                        'MODEL_TAG_EDGE 1 OFFER 6 1 1 0 0', 1)

    # When: the strict comparator checks the bounded offer window.
    with pytest.raises(ValueError, match='TAG_EDGE'):
        # Then: first tag-head divergence is visible before the unmapped full metric.
        sequence.validate_absolute_log(raw, stimulus)


def test_strict_scope_rejects_paired_missing_offer_tail(tmp_path: Path) -> None:
    # Given: a valid two-edge OFFER window with the same last row removed on both sides.
    stimulus = _sealed_manifest(tmp_path)
    stimulus['required_observations'] = ['FRAGMENTS', 'TAG_STATE']
    stimulus['stimulus_sha256'] = sequence.stimulus_digest(stimulus)
    raw = '\n'.join(line for line in _tagged_log(stimulus).splitlines()
                    if line not in ('RTL_TAG_EDGE 0 OFFER 6 1 0 0 0',
                                    'MODEL_TAG_EDGE 0 OFFER 6 1 0 0 0'))

    # When: the strict comparator checks the paired-truncated log.
    with pytest.raises(ValueError, match='OFFER window differs at work 0'):
        # Then: matching but incomplete RTL/native tag windows cannot pass.
        sequence.validate_absolute_log(raw, stimulus)


def test_strict_scope_rejects_reachable_full_tag_occupancy(tmp_path: Path) -> None:
    # Given: both sides claim max tag occupancy six, whose pressure mapping is deferred.
    stimulus = _sealed_manifest(tmp_path)
    stimulus['required_observations'] = ['FRAGMENTS', 'TAG_STATE']
    stimulus['stimulus_sha256'] = sequence.stimulus_digest(stimulus)
    raw = _tagged_log(stimulus).replace('"mesh_tag_max_occupancy": 1',
                                        '"mesh_tag_max_occupancy": 6', 2)

    # When: the strict slice checks the reachable tag state.
    with pytest.raises(ValueError, match='FULL_TAG_PRESSURE_REQUIRES_TODO14'):
        # Then: a full queue cannot be called exact before the later pressure audit.
        sequence.validate_absolute_log(raw, stimulus)


def test_strict_scope_ignores_empty_tag_head_value(tmp_path: Path) -> None:
    # Given: both OFFER queues are empty but their unused head IDs differ.
    stimulus = _sealed_manifest(tmp_path)
    stimulus['required_observations'] = ['FRAGMENTS', 'TAG_STATE']
    stimulus['stimulus_sha256'] = sequence.stimulus_digest(stimulus)
    raw = _tagged_log(stimulus).replace('RTL_TAG_EDGE 0 OFFER 5 1 0 0 0',
                                        'RTL_TAG_EDGE 0 OFFER 5 0 0 0 0', 1)
    raw = raw.replace('MODEL_TAG_EDGE 0 OFFER 5 1 0 0 0',
                      'MODEL_TAG_EDGE 0 OFFER 5 0 99 0 0', 1)

    # When: the bounded tag window is checked.
    works = sequence.validate_absolute_log(raw, stimulus)

    # Then: an empty queue's head value is outside the exact comparison.
    assert len(works) == 2


def test_static_strict_report_marks_full_pressure_not_run(tmp_path: Path) -> None:
    # Given: a sealed Todo11 fixture requiring fragments and tag state only.
    stimulus = _sealed_manifest(tmp_path)
    stimulus['required_observations'] = ['FRAGMENTS', 'TAG_STATE']
    stimulus['stimulus_sha256'] = sequence.stimulus_digest(stimulus)
    path = tmp_path / 'strict.json'
    path.write_text(json.dumps(stimulus))

    # When: the public runner publishes its static report.
    sequence.run_absolute(path, 'synthetic-two-work', tmp_path / 'out', None, None)

    # Then: full-stall pressure remains a separately visible NOT_RUN observation.
    report = json.loads((tmp_path / 'out' / 'report.json').read_text())
    assert report['required_observations'] == ['FRAGMENTS', 'TAG_STATE']
    assert report['tag_full_pressure_status'] == 'NOT_RUN'
