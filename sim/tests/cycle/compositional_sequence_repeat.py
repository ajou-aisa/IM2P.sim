from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Final

from scripts.gemmini_resolve_profile import JsonValue
from sim.cycle.npu_trace_schema import Record, integer, object_value, unique_pairs
from sim.cycle.reconstruct_graph import array, sha256
from sim.tests.cycle import compositional_sequence_v2_base as stimulus

TEMPLATE_SHA256: Final = 'bcd9919d8c9dec1851c29e91bfa06055ec60734fea5b3699af3c6d7f128463f7'
MATRIX_SHA256: Final = '99847424246ee1e2c179d86cb65268480a14b5c19bac3f70770b1e64d4e37c5d'
REPEAT_KIND: Final = 'TEST_ONLY_REPEAT_TEMPLATE'
OFFER_STEP: Final = 200_000


def frozen_stimulus_text(repeated: Record) -> str:
    return json.dumps(repeated, indent=2, sort_keys=True) + '\n'


def _read_bound(path: Path, digest: str, label: str) -> Record:
    if not path.is_absolute() or not path.is_file() or sha256(path) != digest:
        raise stimulus.AbsoluteOfferError(f'{label} digest mismatch')
    return object_value(json.loads(path.read_text(), object_pairs_hook=unique_pairs))


def _source(template: Path, matrix: Path, template_sha256: str,
            matrix_sha256: str) -> tuple[Record, list[Record], list[Record], Record]:
    source = _read_bound(template, template_sha256, 'source template')
    if (source.get('schema'), source.get('version'), source.get('fixture_kind'),
            source.get('profile')) != (stimulus.STIMULUS_SCHEMA, 2,
                                       'GENUINE_COMPLETE_PARENT', 'a4w4-d16-hp1') or \
            source.get('stimulus_sha256') != stimulus.stimulus_digest(source):
        raise stimulus.AbsoluteOfferError('source template schema or digest differs')
    parents = [object_value(row) for row in array(source['pipeline_parents'])]
    works = [object_value(row) for row in array(source['works'])]
    if (len(parents) != 2 or len(works) != 12 or
            [integer(work, 'work_id') for work in works] != list(range(12)) or
            [integer(work, 'ordinal') for work in works] != list(range(12)) or
            [integer(work, 'port_offer_cycle') for work in works] !=
            [5 + OFFER_STEP * index for index in range(12)] or
            [integer(work, 'request_available_cycle') for work in works] !=
            [5 + OFFER_STEP * index for index in range(12)]):
        raise stimulus.AbsoluteOfferError('source template work or absolute offer grid differs')
    required = [integer({'work_id': work_id}, 'work_id') for parent in parents
                for work_id in array(parent['required_work_ids'])]
    if required != list(range(12)) or any(
            parent['fence_required_work_ids'] != parent['required_work_ids'] for parent in parents):
        raise stimulus.AbsoluteOfferError('source template parent/fence order differs')
    producer = object_value(object_value(source['producer_corpus'])['receipt'])
    producer_path = Path(str(producer['path']))
    _read_bound(producer_path, str(producer['sha256']), 'source producer receipt')
    matrix_record = _read_bound(matrix, matrix_sha256, 'source matrix receipt')
    progress = [object_value(row) for row in array(matrix_record['progress'])
                if object_value(row).get('profile') == 'a4w4-d16-hp1']
    if (matrix_record.get('schema') != 'im2p-todo13-five-profile-rtl-native-matrix-receipt' or
            matrix_record.get('status') != 'FIVE_PROFILE_PASS_INDEPENDENT_RAW_AUDIT' or
            object_value(matrix_record['source_and_input_sha256']).get('producer_receipt') !=
            producer['sha256'] or len(progress) != 1 or progress[0].get('work_count') != 12 or
            object_value(progress[0]['sha256']).get('stimulus') != template_sha256):
        raise stimulus.AbsoluteOfferError('source matrix/producer receipt binding differs')
    return source, parents, works, producer


def build_repeat_stimulus(template: Path, matrix: Path, case_id: str, repeats: int,
                          *, template_sha256: str = TEMPLATE_SHA256,
                          matrix_sha256: str = MATRIX_SHA256) -> Record:
    if not case_id or not 1 <= repeats <= 32:
        raise stimulus.AbsoluteOfferError('repeat case or count invalid')
    template = template.resolve()
    matrix = matrix.resolve()
    source, parents, source_works, producer = _source(template, matrix,
                                                     template_sha256, matrix_sha256)
    owner = {integer({'id': value}, 'id'): parent for parent in parents
             for value in array(parent['required_work_ids'])}
    if len(owner) != 12:
        raise stimulus.AbsoluteOfferError('source template parent work ownership differs')
    parent_stride = max(integer(object_value(work['trace_record']), 'parent_id')
                        for work in source_works) + 1
    call_stride = max(*(integer(object_value(work['trace_record']), 'call_id')
                        for work in source_works),
                      *(integer(parent, 'fence_call_id') for parent in parents)) + 1
    span = OFFER_STEP * len(source_works)
    repeated_works: list[Record] = []
    occurrences: list[Record] = []
    for repeat in range(repeats):
        for parent_index, parent in enumerate(parents):
            original_parent_id = integer(parent, 'parent_id')
            original_fence_id = integer(parent, 'fence_call_id')
            required = [integer({'id': work_id}, 'id') for work_id in
                        array(parent['required_work_ids'])]
            occurrences.append({
                'kind': 'SYNTHETIC_REPEAT_PARENT_OCCURRENCE', 'repeat_index': repeat,
                'source_template_sha256': template_sha256,
                'source_parent_index': parent_index, 'source_parent_id': original_parent_id,
                'parent_id': repeat * parent_stride + original_parent_id,
                'source_fence_call_id': original_fence_id,
                'fence_call_id': repeat * call_stride + original_fence_id,
                'required_work_ids': [repeat * 12 + work_id for work_id in required],
                'fence_required_work_ids': [repeat * 12 + work_id for work_id in required],
            })
        for source_work in source_works:
            original_id = integer(source_work, 'work_id')
            trace = object_value(source_work['trace_record'])
            parent = owner[original_id]
            work = deepcopy(source_work)
            del work['trace_record']
            work['ordinal'] = repeat * 12 + original_id
            work['work_id'] = repeat * 12 + original_id
            work['work_binding'] = f'repeat-{repeat}-{source_work["work_binding"]}'
            work['request_available_cycle'] = integer(source_work, 'request_available_cycle') + repeat * span
            work['port_offer_cycle'] = integer(source_work, 'port_offer_cycle') + repeat * span
            work['synthetic_identity'] = {
                'parent_id': repeat * parent_stride + integer(trace, 'parent_id'),
                'call_id': repeat * call_stride + integer(trace, 'call_id'),
                'fence_call_id': repeat * call_stride + integer(parent, 'fence_call_id'),
                'stripe_id': trace['stripe_id'], 'row_begin': trace['row_begin'],
                'parent_m': trace['parent_m'],
            }
            work['origin'] = {
                'template_sha256': template_sha256, 'repeat_index': repeat,
                'source_work_ordinal': original_id, 'source_work_id': original_id,
                'source_work_binding': source_work['work_binding'],
                'source_parent_id': trace['parent_id'], 'source_call_id': trace['call_id'],
                'source_fence_call_id': parent['fence_call_id'],
            }
            repeated_works.append(work)
    result: Record = {
        'schema': stimulus.STIMULUS_SCHEMA, 'version': 2, 'case_id': case_id,
        'fixture_kind': REPEAT_KIND, 'offer_policy': stimulus.ABSOLUTE_POLICY,
        'profile': source['profile'], 'hardware_contract': source['hardware_contract'],
        'cycle_library_sha256': source['cycle_library_sha256'],
        'source_sha256': stimulus.stimulus_source_hashes(),
        'required_observations': source.get('required_observations', []),
        'repeat_count': repeats, 'reset_ordinals': [],
        'source_template': {'path': str(template), 'sha256': template_sha256,
                            'source_case_id': source['case_id'],
                            'producer_receipt': producer,
                            'matrix_receipt': {'path': str(matrix), 'sha256': matrix_sha256}},
        'synthetic_parent_occurrences': list[JsonValue](occurrences),
        'works': list[JsonValue](repeated_works),
    }
    result['stimulus_sha256'] = stimulus.stimulus_digest(result)
    return result


def validate_repeat_stimulus(repeated: Record, *,
                             expected_stimulus_sha256: str | None = None,
                             expected_repeats: int | None = None,
                             template_sha256: str = TEMPLATE_SHA256,
                             matrix_sha256: str = MATRIX_SHA256) -> None:
    if expected_stimulus_sha256 is None or expected_repeats is None:
        raise stimulus.AbsoluteOfferError('expected repeat authority missing')
    if (re.fullmatch(r'[0-9a-f]{64}', expected_stimulus_sha256) is None or
            type(expected_repeats) is not int or not 1 <= expected_repeats <= 32):
        raise stimulus.AbsoluteOfferError('expected repeat authority malformed')
    if hashlib.sha256(frozen_stimulus_text(repeated).encode()).hexdigest() != \
            expected_stimulus_sha256:
        raise stimulus.AbsoluteOfferError('frozen stimulus file digest mismatch')
    if (integer(repeated, 'repeat_count') != expected_repeats or
            len(array(repeated['works'])) != 12 * expected_repeats or
            len(array(repeated['synthetic_parent_occurrences'])) != 2 * expected_repeats):
        raise stimulus.AbsoluteOfferError('expected repeat work/parent coverage differs')
    if repeated.get('fixture_kind') != REPEAT_KIND or \
            repeated.get('stimulus_sha256') != stimulus.stimulus_digest(repeated):
        raise stimulus.AbsoluteOfferError('repeat stimulus digest or kind differs')
    binding = object_value(repeated['source_template'])
    expected = build_repeat_stimulus(Path(str(binding['path'])),
                                     Path(str(object_value(binding['matrix_receipt'])['path'])),
                                     str(repeated['case_id']), integer(repeated, 'repeat_count'),
                                     template_sha256=template_sha256,
                                     matrix_sha256=matrix_sha256)
    if repeated != expected:
        raise stimulus.AbsoluteOfferError('repeat stimulus differs from source template')
