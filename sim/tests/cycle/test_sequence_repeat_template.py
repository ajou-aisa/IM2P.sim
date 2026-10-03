# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
# ─── How to run ───
# PYTHONPATH=. uv run pytest -q sim/tests/cycle/test_sequence_repeat_template.py
# ──────────────────
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from sim.cycle.npu_trace_schema import INPUT_KEYS, Record, integer, object_value
from sim.cycle.reconstruct_graph import array, sha256
from sim.tests.cycle import compositional_sequence_repeat as repeat
from sim.tests.cycle import compositional_sequence_v2_stimulus as stimulus


def _rows(document: Record, key: str) -> list[Record]:
    return [object_value(value) for value in array(document[key])]


def _validate(repeated: Record, source_sha: str, matrix_sha: str) -> None:
    repeat.validate_repeat_stimulus(
        repeated, template_sha256=source_sha, matrix_sha256=matrix_sha,
        expected_stimulus_sha256=hashlib.sha256(
            repeat.frozen_stimulus_text(repeated).encode()).hexdigest(),
        expected_repeats=integer(repeated, 'repeat_count'))


def _source(tmp_path: Path) -> tuple[Path, Path, str, str]:
    receipt = tmp_path / 'producer-receipt.json'
    receipt.write_text('{}\n')
    source_path = tmp_path / 'source.json'
    parents = [{'parent_id': parent * 2, 'fence_call_id': parent * 10 + 8,
                'required_work_ids': list(range(parent * 6, parent * 6 + 6)),
                'fence_required_work_ids': list(range(parent * 6, parent * 6 + 6))}
               for parent in range(2)]
    works = [{'ordinal': ordinal, 'work_id': ordinal, 'scope': 'stripe',
              'slot': ordinal % 2, 'work_binding': f'source-{ordinal}',
              'request_available_cycle': 5 + ordinal * 200_000,
              'port_offer_cycle': 5 + ordinal * 200_000,
              'input': {key: 1 for key in INPUT_KEYS}, 'original_k': None, 'runs': [],
              'trace_record': {'work_id': ordinal, 'parent_id': (ordinal // 6) * 2,
                               'call_id': ordinal * 2, 'stripe_id': 0,
                               'row_begin': 0, 'parent_m': 1, 'host_slot': ordinal % 2}}
             for ordinal in range(12)]
    source: Record = object_value(json.loads(json.dumps({
        'schema': stimulus.STIMULUS_SCHEMA, 'version': 2,
        'offer_policy': stimulus.ABSOLUTE_POLICY,
        'fixture_kind': 'GENUINE_COMPLETE_PARENT',
        'case_id': 'source-case', 'profile': 'a4w4-d16-hp1',
        'hardware_contract': {'dim': 16}, 'cycle_library_sha256': 'a' * 64,
        'pipeline_parents': parents, 'works': works,
        'producer_corpus': {'receipt': {'path': str(receipt),
                                        'sha256': sha256(receipt)}},
        'producer_artifacts': {},
    })))
    source['stimulus_sha256'] = stimulus.stimulus_digest(source)
    source_path.write_text(json.dumps(source) + '\n')
    matrix = tmp_path / 'matrix-receipt.json'
    matrix.write_text(json.dumps({'schema': 'im2p-todo13-five-profile-rtl-native-matrix-receipt',
                                  'status': 'FIVE_PROFILE_PASS_INDEPENDENT_RAW_AUDIT',
                                  'source_and_input_sha256': {
                                      'producer_receipt': sha256(receipt)},
                                  'progress': [{'profile': 'a4w4-d16-hp1',
                                                'work_count': 12, 'sha256': {
                                                    'stimulus': sha256(source_path)}}]}) + '\n')
    return source_path, matrix, sha256(source_path), sha256(matrix)


def test_repeat_freeze_binds_every_synthetic_occurrence(tmp_path: Path) -> None:
    # Given: an immutable twelve-work source and matrix receipt.
    source, matrix, source_sha, matrix_sha = _source(tmp_path)

    # When: two diagnostic repeats are frozen before execution.
    repeated = repeat.build_repeat_stimulus(source, matrix, 'repeat-case', 2,
                                              template_sha256=source_sha,
                                              matrix_sha256=matrix_sha)

    # Then: work, parent, call and fence identities are source-bound and unique.
    works = _rows(repeated, 'works')
    assert len(works) == 24
    assert len(_rows(repeated, 'synthetic_parent_occurrences')) == 4
    assert [work['work_id'] for work in works] == list(range(24))
    assert works[12]['port_offer_cycle'] == 2_400_005
    assert object_value(works[12]['origin'])['template_sha256'] == source_sha
    assert object_value(works[12]['origin'])['source_work_id'] == 0
    assert object_value(works[12]['synthetic_identity'])['call_id'] != \
        object_value(works[0]['synthetic_identity'])['call_id']
    assert 'trace_record' not in works[12]
    assert 'pipeline_parents' not in repeated
    _validate(repeated, source_sha, matrix_sha)


def test_repeat_freeze_rejects_self_consistent_omission(tmp_path: Path) -> None:
    # Given: a sealed repeat with one omitted work and a recomputed outer digest.
    source, matrix, source_sha, matrix_sha = _source(tmp_path)
    repeated = repeat.build_repeat_stimulus(source, matrix, 'repeat-case', 2,
                                              template_sha256=source_sha,
                                              matrix_sha256=matrix_sha)
    array(repeated['works']).pop()
    repeated['stimulus_sha256'] = stimulus.stimulus_digest(repeated)

    # When/Then: reconstruction against source rejects the omission.
    with pytest.raises(ValueError, match='expected repeat|source template'):
        _validate(repeated, source_sha, matrix_sha)


def test_repeat_freeze_rejects_self_consistent_reorder(tmp_path: Path) -> None:
    # Given: a sealed repeat with two works swapped and a recomputed outer digest.
    source, matrix, source_sha, matrix_sha = _source(tmp_path)
    repeated = repeat.build_repeat_stimulus(source, matrix, 'repeat-case', 2,
                                              template_sha256=source_sha,
                                              matrix_sha256=matrix_sha)
    works = array(repeated['works'])
    works[0], works[1] = works[1], works[0]
    repeated['stimulus_sha256'] = stimulus.stimulus_digest(repeated)

    # When/Then: reconstruction against source rejects the reordered works.
    with pytest.raises(ValueError, match='source template'):
        _validate(repeated, source_sha, matrix_sha)


def test_repeat_freeze_rejects_source_mutation(tmp_path: Path) -> None:
    # Given: a valid freeze followed by a source-file mutation.
    source, matrix, source_sha, matrix_sha = _source(tmp_path)
    repeated = repeat.build_repeat_stimulus(source, matrix, 'repeat-case', 2,
                                              template_sha256=source_sha,
                                              matrix_sha256=matrix_sha)
    source.write_text(source.read_text().replace('source-0', 'changed-0'))

    # When/Then: the pinned source SHA rejects the changed file.
    with pytest.raises(ValueError, match='source template digest'):
        _validate(repeated, source_sha, matrix_sha)


@pytest.mark.parametrize('mutation', ('missing-parent', 'reordered-parents',
                                      'duplicate-parent-id', 'changed-fence',
                                      'duplicate-work-id'))
def test_repeat_freeze_rejects_self_consistent_identity_mutation(
        tmp_path: Path, mutation: str) -> None:
    # Given: a valid synthetic repeat whose work, parent or fence identity is changed.
    source, matrix, source_sha, matrix_sha = _source(tmp_path)
    repeated = repeat.build_repeat_stimulus(source, matrix, 'repeat-case', 2,
                                              template_sha256=source_sha,
                                              matrix_sha256=matrix_sha)
    parents = array(repeated['synthetic_parent_occurrences'])
    if mutation == 'missing-parent':
        parents.pop()
    elif mutation == 'reordered-parents':
        parents[0], parents[1] = parents[1], parents[0]
    elif mutation == 'duplicate-parent-id':
        object_value(parents[2])['parent_id'] = object_value(parents[0])['parent_id']
    elif mutation == 'changed-fence':
        parent = object_value(parents[2])
        parent['fence_call_id'] = integer(parent, 'fence_call_id') + 1
    else:
        works = _rows(repeated, 'works')
        works[12]['work_id'] = works[0]['work_id']
    repeated['stimulus_sha256'] = stimulus.stimulus_digest(repeated)

    # When/Then: even a recomputed outer digest cannot invent source authority.
    with pytest.raises(ValueError, match='expected repeat|source template'):
        _validate(repeated, source_sha, matrix_sha)
