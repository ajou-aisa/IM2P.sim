from __future__ import annotations

import re

from sim.cycle.npu_trace_schema import Record, integer, object_value, require, text
from sim.cycle.reconstruct_graph import Manifest, array


def validate_source_pair(full: Record, potal: Record, potal_provenance_sha256: str) -> None:
    if full.get('execution_kind') != 'FORCED_CPU_COST_ONLY':
        require(full['command_arguments'] == potal['command_arguments'], 'normalized collection arguments differ')
        if full.get('version') == potal.get('version') == 1:
            return
        require(full.get('version') == potal.get('version') == 2 and
                full.get('source_role') == 'FULL_CPU' and potal.get('source_role') == 'POTAL_COLLECTION',
                'free-generation pairing requires matching current native provenance')
        require(full.get('execution_kind') == potal.get('execution_kind') == 'FREE_GENERATION' and
                full.get('actual_sampler_calls') == potal.get('actual_sampler_calls') == 128 and
                full.get('decode_calls') == potal.get('decode_calls') == 127,
                'free-generation pairing requires complete actual sampling')
        require(full.get('recipe_id') == potal.get('recipe_id') == 'wikitext2-test-256x128-greedy-seed1234-v1',
                'free-generation pairing recipe mismatch')
        require(integer(full, 'chunk_id') == integer(potal, 'chunk_id'), 'free-generation chunk mismatch')
        for name in ('model_sha256', 'input_tokens_sha256', 'output_tokens_sha256'):
            require(re.fullmatch('[0-9a-f]{64}', text(full, name)) is not None and full[name] == potal.get(name),
                    'free-generation identity mismatch: ' + name +
                    '; use forced PoTal-trajectory FullCPU cost-only recollection')
        return
    require(full.get('version') == 2 and potal.get('version') == 2 and
            full.get('source_role') == 'FULL_CPU' and potal.get('source_role') == 'POTAL_COLLECTION',
            'forced pairing requires current native FullCPU/PoTal provenance')
    require(full.get('trajectory_source') == 'POTAL' and full.get('actual_sampler_calls') == 0 and
            potal.get('actual_sampler_calls') == 128 and full.get('decode_calls') == potal.get('decode_calls') == 127,
            'forced pairing must not count CPU sampling as actual generation')
    require(full.get('recipe_id') == potal.get('recipe_id') == 'wikitext2-test-256x128-greedy-seed1234-v1',
            'forced pairing recipe mismatch')
    pairing = object_value(full.get('paired_trajectory'))
    require(pairing.get('schema') == 'potal-paired-trajectory' and pairing.get('version') == 1 and
            pairing.get('execution_kind') == 'FORCED_CPU_COST_ONLY' and pairing.get('trajectory_source') == 'POTAL',
            'missing explicit forced trajectory contract')
    require(pairing.get('source_potal_provenance_sha256') == potal_provenance_sha256 and
            pairing.get('source_potal_application_sha256') ==
                object_value(object_value(potal.get('artifacts')).get('application_endpoints')).get('sha256'),
            'forced input is not bound to this PoTal collection')
    for name in ('chunk_id', 'model_sha256', 'input_tokens_sha256', 'output_tokens_sha256'):
        require(name in full and full[name] == potal.get(name), 'forced pairing identity mismatch: ' + name)
    require(pairing.get('token_vector_sha256') == full['output_tokens_sha256'] and
            pairing.get('input_tokens_sha256') == full['input_tokens_sha256'], 'forced token digest mismatch')
    arguments = array(full['command_arguments'])
    require(arguments.count('--forced-token-ids') == 1, 'forced command lacks unique bound input')
    index = arguments.index('--forced-token-ids')
    require(index + 1 < len(arguments) and arguments[index + 1] == 'sha256:' + str(pairing.get('forced_file_sha256')),
            'forced command input hash mismatch')
    require(arguments[:index] + arguments[index + 2:] == array(potal['command_arguments']),
            'non-trajectory collection arguments differ')


def validate_forced_phases(full: Manifest, potal: Manifest) -> None:
    require(len(full.phases) == len(potal.phases), 'forced phase coverage mismatch')
    require(all(left['token_fingerprint'] == right['token_fingerprint']
                for left, right in zip(full.phases, potal.phases, strict=True)),
            'forced FullCPU/PoTal actual decode input trajectory differs')
