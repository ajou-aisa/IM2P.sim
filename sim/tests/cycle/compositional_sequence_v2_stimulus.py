from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict
from pathlib import Path

from scripts.gemmini_resolve_profile import JsonValue
from sim.cycle.execution_lifecycle import project_lifecycle
from sim.cycle.execution_pipeline_contract import (
    OWNER_FIELDS,
    PARENT_FIELDS,
    parse_pipeline,
    required_ids,
)
from sim.cycle.npu_trace import work_binding
from sim.cycle.npu_trace_integrity import read_records, start_trace
from sim.cycle.npu_trace_schema import INPUT_KEYS, Record, integer, object_value
from sim.cycle.reconstruct_graph import array, json_records, read_manifest, sha256
from sim.tests.cycle import compositional_sequence_v2_corpus as corpus
from sim.tests.cycle import compositional_sequence_v2_values as values
from sim.tests.cycle.compositional_sequence_v2_base import (
    ABSOLUTE_POLICY,
    BASE,
    POLICY,
    ROOT,
    RUNNER,
    SOURCE,
    STIMULUS_SCHEMA,
    STIMULUS_SOURCES,
    AbsoluteOfferError,
    stimulus_digest,
    stimulus_source_hashes,
)

__all__ = (
    'ABSOLUTE_POLICY',
    'BASE',
    'POLICY',
    'ROOT',
    'RUNNER',
    'SOURCE',
    'STIMULUS_SCHEMA',
    'STIMULUS_SOURCES',
    'AbsoluteOfferError',
    'absolute_stimulus',
    'project',
    'stimulus_digest',
    'stimulus_source_hashes',
    'validate_stimulus',
)


def project(trace: Path, lifecycle: Path, semantic: Path, delays: tuple[int, ...],
            parent_indices: tuple[int, ...] = (0, 1)) -> Record:
    graph = read_manifest(semantic)
    projected_lifecycle = project_lifecycle(json_records(lifecycle), graph)
    records = read_records(trace)
    state = start_trace(records)
    pairs = [(row, work) for row in records if (work := state.consume(row)) is not None]
    all_parents = [{key: row[key] for key in PARENT_FIELDS} for row in projected_lifecycle.pipeline_parents]
    all_owners = [{key: row[key] for key in OWNER_FIELDS} for row in projected_lifecycle.pipeline_owners]
    contract: Record = {'pipeline_parents': list[JsonValue](all_parents),
                        'pipeline_owners': list[JsonValue](all_owners)}
    _ = parse_pipeline(contract, {'npu:' + str(integer(row, 'work_id')): row for row, _ in pairs})
    expected = [work_id for parent in all_parents for work_id in required_ids(parent, 'required_work_ids')]
    if sorted(expected) != sorted(work.identity for _, work in pairs):
        raise AbsoluteOfferError('producer parent/fence work coverage differs from trace')
    if not parent_indices or parent_indices[0] < 0 or parent_indices != tuple(range(parent_indices[0],
            parent_indices[0] + len(parent_indices))) or parent_indices[-1] >= len(all_parents):
        raise AbsoluteOfferError('select consecutive producer parent indices')
    parents = [all_parents[index] for index in parent_indices]
    selected = {work_id for parent in parents for work_id in required_ids(parent, 'required_work_ids')}
    pairs = [(row, work) for row, work in pairs if work.identity in selected]
    if len(pairs) <= 4 or len(delays) != len(pairs) or any(value < 0 for value in delays):
        raise AbsoluteOfferError('selected parents require >4 works and one nonnegative delay per work')
    if delays[0] >= 5:
        raise AbsoluteOfferError('first delay is a start phase in 0..4')
    owners = [row for row in all_owners if integer(row, 'parent_id') in
              {integer(parent, 'parent_id') for parent in parents}]
    dense_slots = {work.identity: integer(row, 'host_slot') for row, work in pairs
                   if work.scope == 'stripe'}
    residual_slots = {integer(binding, 'work_id'): dense_slots[integer(binding, 'dense_work_id')]
                      for parent in parents for binding in map(object_value, array(parent['residual_bindings']))}
    works: list[JsonValue] = []
    for ordinal, ((record, work), delay) in enumerate(zip(pairs, delays, strict=True)):
        slot = dense_slots.get(work.identity, residual_slots.get(work.identity))
        if slot not in (0, 1) or record['host_slot'] not in (None, slot):
            raise AbsoluteOfferError('producer workspace ownership differs from trace')
        works.append({'ordinal': ordinal, 'work_id': work.identity, 'scope': work.scope,
                      'slot': slot, 'arrival_delay': delay, 'work_binding': work_binding(work),
                      'input': dict(zip(INPUT_KEYS, work.inputs, strict=True)),
                      'original_k': work.original_k, 'runs': [asdict(run) for run in work.runs],
                      'trace_record': record})
    return {'schema': 'im2p-compositional-sequence-projection', 'version': 1,
            'offer_policy': POLICY, 'profile': state.run.profile,
            'selected_parent_indices': list(parent_indices),
            'hardware_contract': state.run.contract, 'npu_summary': state.summary(),
            'producer_artifacts': {name: {'path': str(path), 'sha256': sha256(path)}
                                   for name, path in (('trace', trace), ('lifecycle', lifecycle),
                                                      ('semantic_graph', semantic))},
            'pipeline_parents': list[JsonValue](parents),
            'pipeline_owners': list[JsonValue](owners), 'works': works}


def absolute_stimulus(projection: Record, case_id: str,
                      epochs: tuple[tuple[int, int], ...],
                      cycle_library_sha256: str) -> Record:
    if (projection.get('schema'), projection.get('version'), projection.get('offer_policy')) != \
            ('im2p-compositional-sequence-projection', 1, POLICY):
        raise AbsoluteOfferError('absolute stimulus requires current producer projection v1')
    works = [object_value(row) for row in array(projection['works'])]
    if len(works) != len(epochs):
        raise AbsoluteOfferError('one absolute availability/offer pair required per producer work')
    sealed: Record = {key: value for key, value in projection.items()
                      if key not in ('schema', 'version', 'offer_policy', 'works')}
    sealed.update({'schema': STIMULUS_SCHEMA, 'version': 2, 'case_id': case_id,
                   'offer_policy': ABSOLUTE_POLICY, 'source_sha256': stimulus_source_hashes(),
                   'cycle_library_sha256': cycle_library_sha256,
                   'works': [{key: value for key, value in work.items() if key != 'arrival_delay'} |
                             {'request_available_cycle': available, 'port_offer_cycle': offer}
                             for work, (available, offer) in zip(works, epochs, strict=True)]})
    sealed['stimulus_sha256'] = stimulus_digest(sealed)
    validate_stimulus(sealed)
    return sealed


def validate_stimulus(stimulus: Record, *, expected_stimulus_sha256: str | None = None,
                      expected_repeats: int | None = None) -> None:
    if stimulus.get('fixture_kind') == 'TEST_ONLY_REPEAT_TEMPLATE':
        from sim.tests.cycle.compositional_sequence_repeat import (
            validate_repeat_stimulus,
        )

        validate_repeat_stimulus(stimulus,
                                 expected_stimulus_sha256=expected_stimulus_sha256,
                                 expected_repeats=expected_repeats)
        return
    if expected_repeats is not None:
        raise AbsoluteOfferError('repeat authority on nonrepeat stimulus')
    if stimulus.get('fixture_kind') == 'GENUINE_COMPLETE_PARENT':
        if 'producer_corpus' not in stimulus:
            raise AbsoluteOfferError('producer corpus missing for genuine stimulus')
        if 'predeclared_offer_grid' not in stimulus:
            raise AbsoluteOfferError('predeclared offer grid missing for genuine stimulus')
        if expected_stimulus_sha256 is None or \
                re.fullmatch(r'[0-9a-f]{64}', expected_stimulus_sha256) is None:
            raise AbsoluteOfferError('expected genuine stimulus authority missing or malformed')
        frozen = (json.dumps(stimulus, indent=2, sort_keys=True) + '\n').encode()
        if hashlib.sha256(frozen).hexdigest() != expected_stimulus_sha256:
            raise AbsoluteOfferError('frozen stimulus file digest mismatch')
    elif expected_stimulus_sha256 is not None:
        raise AbsoluteOfferError('frozen authority on unsupported stimulus')
    if stimulus.get('stimulus_sha256') != stimulus_digest(stimulus):
        raise AbsoluteOfferError('stimulus digest mismatch')
    if (stimulus.get('schema'), stimulus.get('version'), stimulus.get('offer_policy')) != \
            (STIMULUS_SCHEMA, 2, ABSOLUTE_POLICY):
        raise AbsoluteOfferError('unsupported absolute-offer stimulus')
    if not isinstance(stimulus.get('case_id'), str) or not stimulus['case_id']:
        raise AbsoluteOfferError('stimulus case identity missing')
    library_digest = stimulus.get('cycle_library_sha256')
    if not isinstance(library_digest, str) or re.fullmatch(r'[0-9a-f]{64}', library_digest) is None:
        raise AbsoluteOfferError('cycle library digest missing or invalid')
    profile = stimulus.get('profile')
    if not isinstance(profile, str) or not re.fullmatch(
            r'a[48]w[48]-d(?:16|32|64)-hp1', profile):
        raise AbsoluteOfferError('stimulus profile invalid')
    if object_value(stimulus['source_sha256']) != stimulus_source_hashes():
        raise AbsoluteOfferError('stimulus source digest mismatch')
    if stimulus.get('reset_ordinals', []) != []:
        raise AbsoluteOfferError('mid-sequence reset unsupported')
    if stimulus.get('required_observations', []) not in (
            [], ['FRAGMENTS', 'TAG_STATE'],
            ['FRAGMENTS', 'TAG_STATE', 'BOUNDARY_V2'],
            ['FRAGMENTS', 'TAG_STATE', 'TAG_FULL_PRESSURE']):
        raise AbsoluteOfferError('unsupported required observations')
    diagnostic = stimulus.get('fixture_kind') == 'TEST_ONLY_RUN_AWARE_DIAGNOSTIC'
    works = [object_value(row) for row in array(stimulus['works'])]
    if diagnostic:
        if any(key in stimulus for key in ('pipeline_parents', 'pipeline_owners',
                                           'selected_parent_indices', 'producer_artifacts')) or \
                any('trace_record' in work for work in works) or \
                not 1 <= len(works) <= 2 or any(work.get('scope') != 'residual_compact' for work in works):
            raise AbsoluteOfferError('test-only run-aware diagnostic has producer claims or wrong work scope')
        source = object_value(stimulus['diagnostic_source'])
        path = Path(str(source['path']))
        if not path.is_file() or sha256(path) != source['sha256']:
            raise AbsoluteOfferError('test-only diagnostic source digest mismatch')
        parent_for_work: dict[int, Record] = {}
    else:
        parents = [object_value(row) for row in array(stimulus['pipeline_parents'])]
        indices = tuple(integer({'index': value}, 'index') for value in
                        array(stimulus['selected_parent_indices']))
        if not parents or not indices or indices != tuple(range(indices[0], indices[0] + len(parents))):
            raise AbsoluteOfferError('stimulus parent order invalid')
        required = [work_id for parent in parents for work_id in required_ids(parent, 'required_work_ids')]
        if not works or len(works) > 4096 or len(works) != len(required) or \
                len(set(required)) != len(required) or [integer(work, 'work_id') for work in works] != required:
            raise AbsoluteOfferError('stimulus producer work order/coverage differs')
        parent_for_work = {work_id: parent for parent in parents
                           for work_id in required_ids(parent, 'required_work_ids')}
        for parent in parents:
            if required_ids(parent, 'fence_required_work_ids') != required_ids(parent, 'required_work_ids'):
                raise AbsoluteOfferError('stimulus parent fence coverage differs')
            integer(parent, 'fence_call_id')
        if stimulus.get('fixture_kind') == 'EXACT_FOUR_WORK_REUSED_VERIFIED':
            if len(works) != 4:
                raise AbsoluteOfferError('exact four-work source coverage differs')
            for key in ('source_receipt', 'source_manifest'):
                artifact = object_value(stimulus[key])
                path = Path(str(artifact['path']))
                if not path.is_file() or sha256(path) != artifact['sha256']:
                    raise AbsoluteOfferError(f'exact four-work {key} digest mismatch')
        elif stimulus.get('fixture_kind') not in (None, 'GENUINE_COMPLETE_PARENT'):
            raise AbsoluteOfferError('unsupported fixture kind')
    previous_offer = -1
    for ordinal, work in enumerate(works):
        available = integer(work, 'request_available_cycle')
        offer = integer(work, 'port_offer_cycle')
        trace = {} if diagnostic else object_value(work['trace_record'])
        inputs = object_value(work['input'])
        slot = integer(work, 'slot')
        scope = work.get('scope')
        binding = work.get('work_binding')
        if (integer(work, 'ordinal') != ordinal or offer < available or offer <= previous_offer or
                slot not in (0, 1) or scope not in ('stripe', 'residual_compact') or
                (not diagnostic and (trace.get('work_id') != work['work_id'] or
                 trace.get('host_slot') not in (None, slot) or
                 (scope == 'stripe' and
                  trace.get('parent_id') != parent_for_work[integer(work, 'work_id')]['parent_id']))) or
                set(inputs) != set(INPUT_KEYS) or
                any(integer(inputs, key) == 0 for key in INPUT_KEYS) or
                not isinstance(binding, str) or re.fullmatch(r'[A-Za-z0-9_.-]+', binding) is None):
            raise AbsoluteOfferError(f'stimulus work {ordinal} geometry/epoch/identity invalid')
        if not diagnostic:
            for key in ('parent_id', 'call_id', 'row_begin', 'parent_m'):
                integer(trace, key)
            if trace['stripe_id'] is not None:
                integer(trace, 'stripe_id')
        previous_offer = offer
        runs = [object_value(row) for row in array(work['runs'])]
        if (scope == 'stripe' and (runs or work['original_k'] is not None)) or \
                (scope == 'residual_compact' and (not runs or integer(work, 'original_k') == 0)):
            raise AbsoluteOfferError(f'stimulus work {ordinal} run scope invalid')
        for run in runs:
            if set(run) != {'original_block_id', 'original_k_mask', 'compact_k_begin', 'compact_k_count'}:
                raise AbsoluteOfferError(f'stimulus work {ordinal} run shape invalid')
            for key in run:
                integer(run, key)
    if not diagnostic:
        artifacts = object_value(stimulus['producer_artifacts'])
        if set(artifacts) != {'trace', 'lifecycle', 'semantic_graph'}:
            raise AbsoluteOfferError('stimulus producer artifacts missing')
        for name in artifacts:
            artifact = object_value(artifacts[name])
            path = Path(str(artifact['path']))
            if not path.is_file() or sha256(path) != artifact['sha256']:
                raise AbsoluteOfferError(f'stimulus producer {name} digest mismatch')
    values.value_binding(stimulus)
    corpus.validate_corpus(stimulus, project)
