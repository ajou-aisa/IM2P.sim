"""Admission contract shared by the certificate producer and production replay."""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
import hashlib
import json
from pathlib import Path
import re
from typing import Final

from scripts.gemmini_replay_contract import contract_digest, validate_contract
from scripts.gemmini_resolve_profile import JsonValue
from scripts.gemmini_rtl_build_binding import BuildBindingError, validate_binding
from sim.cycle.corpus_authority import LARGE_K, CorpusError, validate_corpus

SCHEMA: Final = 'im2p-single-gemm-cycle-certificate'
VERSION: Final = 2
MARKER: Final = 'IM2P_SINGLE_GEMM_CYCLE_MODEL_CURRENT'
PROFILES: Final = tuple(f'a{b}w{b}-d{d}-hp1' for b in (4, 8) for d in (16, 32, 64))
FRAMINGS: Final = ('regression-tiles', 'planner-blocks')
SELECTED_EVENTS: Final = frozenset((
    'work', 'load_issue', 'execute_issue', 'store_issue', 'context', 'load_dma',
    'read_request', 'read_response', 'scratchpad_read', 'array_input', 'raw_completed',
    'array_output', 'accumulator_write', 'accumulator_commit', 'store_dma',
    'write_request', 'write_completion', 'loop_done', 'logical_done'))
COUNTERS: Final = {
    'start': 'start_cycle', 'done': 'done_cycle', 'cycles': 'total_cycles',
    'work_count': 'logical_work_count', 'loop_count': 'loop_count',
    'load_req': 'load_request_count', 'load_resp': 'load_response_count',
    'store_req': 'store_request_count', 'store_resp': 'store_response_count',
    'scale_req': 'scale_request_count', 'scale_resp': 'scale_response_count'}
TIMING: Final = {
    'revision': 1, 'backing_read_delay': 3, 'even_read_id_delay': 13,
    'scale_read_extra_delay': 17, 'backing_write_delay': 11,
    'read_ready_period': 5, 'backing_cycle_offset': 5, 'reserved': 0}


class CertificateError(ValueError):
    def __init__(self, detail: str) -> None:
        super().__init__('certificate: ' + detail)


def require(condition: bool, detail: str) -> None:
    if not condition:
        raise CertificateError(detail)


def object_value(value: JsonValue, label: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise CertificateError(label + ': object required')
    return value


def array_value(value: JsonValue, label: str) -> list[JsonValue]:
    if not isinstance(value, list):
        raise CertificateError(label + ': array required')
    return value


def unique_strings(value: JsonValue, label: str) -> tuple[str, ...]:
    values = array_value(value, label)
    result: list[str] = []
    for item in values:
        if not isinstance(item, str) or not item:
            raise CertificateError(label + ': nonempty strings required')
        result.append(item)
    require(bool(result) and len(result) == len(set(result)), label + ': empty or duplicate entries')
    return tuple(result)


def number(value: JsonValue, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise CertificateError(label + ': nonnegative integer required')
    return value


def digest_value(value: JsonValue, label: str) -> str:
    if not isinstance(value, str) or re.fullmatch('[0-9a-f]{64}', value) is None:
        raise CertificateError(label + ': SHA256 required')
    return value


def _unique_object(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {}
    for key, value in pairs:
        require(key not in result, 'duplicate JSON key: ' + key)
        result[key] = value
    return result


def read_document(path: Path) -> dict[str, JsonValue]:
    return object_value(json.loads(path.read_text(), object_pairs_hook=_unique_object), str(path))


def validate_reference(document: Mapping[str, JsonValue]) -> None:
    require(document.get('id') == 'rtl-regression-reference-memory-v1' and
            document.get('scope') == 'isolated-work-accounting' and
            type(document.get('accepted_cycle')) is int and document['accepted_cycle'] == 1,
            'reference-memory scope/epoch mismatch')
    timing = object_value(document.get('timing'), 'reference timing')
    require(timing == TIMING and all(type(v) is int for v in timing.values()),
            'reference-memory timing mismatch')
    hashes = object_value(document.get('source_sha256'), 'reference sources')
    require(set(hashes) == {'sim/cycle/c_api.cpp', 'sim/cycle/timing_profile.hpp'},
            'reference source closure missing')
    for value in hashes.values():
        digest_value(value, 'reference source')
    require(document.get('sha256') == contract_digest(document), 'reference-memory hash mismatch')


def _case(row: Mapping[str, JsonValue]) -> tuple[str, str, str]:
    profile, framing, identity = row.get('profile'), row.get('framing'), row.get('case')
    if not all(isinstance(v, str) and v for v in (profile, framing, identity)):
        raise CertificateError('case identity missing')
    if not isinstance(profile, str) or not isinstance(framing, str) or not isinstance(identity, str):
        raise CertificateError('case identity must be strings')
    require(profile in PROFILES and framing in FRAMINGS, 'case profile/framing unsupported')
    require(row.get('status') == 'PASS' and row.get('rtl_admitted') is True and
            row.get('model_attempted') is True and row.get('model_admitted') is True,
            'case is not admitted/exact')
    require(row.get('differences') == {} and row.get('first_event_difference') is None and
            row.get('selected_event_multiset_exact') is True and
            number(row.get('delta_cycles'), 'cycle delta') == 0, 'case mismatch')
    for name in ('shape', 'tile'):
        values = array_value(row.get(name), name)
        require(len(values) == 3 and all(number(v, name) > 0 for v in values), 'invalid ' + name)
    rtl = object_value(row.get('rtl_summary'), 'RTL endpoint/counters')
    model = object_value(row.get('model_summary'), 'model endpoint/counters')
    for left, right in COUNTERS.items():
        require(number(rtl.get(left), left) == number(model.get(right), right), 'endpoint/counter mismatch: ' + left)
    require(number(model.get('logical_work_count'), 'logical works') == 1 and
            number(model.get('total_cycles'), 'cycles') > 0 and
            number(model.get('done_cycle'), 'done') - number(model.get('start_cycle'), 'start') == model['total_cycles'],
            'invalid isolated endpoint convention')
    events = object_value(row.get('event_comparison'), 'event comparison')
    require(number(events.get('model_event_count'), 'model events') > 0 and
            events.get('model_event_count') == events.get('rtl_event_count'), 'empty or mismatched event counts')
    number(events.get('rtl_event_count'), 'RTL events')
    require(digest_value(events.get('model_multiset_sha256'), 'model events') ==
            digest_value(events.get('rtl_multiset_sha256'), 'RTL events'), 'event multiset mismatch')
    ownership = object_value(row.get('scale_ownership'), 'scale ownership')
    require(ownership.get('status') == 'PASS' and number(ownership.get('submissions'), 'submissions') > 0,
            'scale ownership missing or failed')
    return profile, framing, identity


def validate_certificate(document: Mapping[str, JsonValue], library: Path,
                         require_marker: bool = True) -> None:
    require(document.get('schema') == SCHEMA and type(document.get('version')) is int and
            document['version'] == VERSION, 'unsupported schema/version; regenerate certificate')
    require(document.get('status') == 'PASS' and document.get('first_mismatch') is None and
            'first_mismatch' in document, 'certificate failed or incomplete')
    require(document.get('marker') == MARKER if require_marker else document.get('marker') in (None, MARKER),
            'current marker missing or invalid')
    require(document.get('scope') == 'isolated-work-accounting' and
            document.get('timing_profile') == 'rtl-regression', 'unsupported timing scope/framing')
    require(document.get('execution_kind') in ('FRESH_RUN', 'REAGGREGATED_FROM_VERIFIED_EVIDENCE'),
            'evidence execution kind required')
    profiles = unique_strings(document.get('profiles'), 'profiles')
    framings = unique_strings(document.get('framings'), 'framings')
    require(set(profiles) == set(PROFILES) and set(framings) == set(FRAMINGS), 'profile/framing coverage incomplete')
    require(digest_value(document.get('model_library_sha256'), 'library') ==
            hashlib.sha256(library.read_bytes()).hexdigest(), 'model library SHA256 mismatch')
    contracts = object_value(document.get('hardware_contracts'), 'hardware contracts')
    require(set(contracts) == set(PROFILES), 'hardware contract coverage incomplete')
    for profile in PROFILES:
        validate_contract(object_value(contracts[profile], profile), profile)
    bindings = object_value(document.get('rtl_build_bindings'), 'RTL build bindings')
    require(set(bindings) == set(PROFILES), 'RTL build binding coverage incomplete')
    try:
        for profile in PROFILES:
            binding = object_value(bindings[profile], profile)
            kind = 'FRESH_BUILD' if document['execution_kind'] == 'FRESH_RUN' else 'VERIFIED_REUSE'
            require(binding.get('execution_kind') == kind, 'RTL build binding execution kind mismatch')
            validate_binding(binding, object_value(contracts[profile], profile))
        validate_corpus(document)
    except (BuildBindingError, CorpusError) as error:
        raise CertificateError(str(error)) from error
    validate_reference(object_value(document.get('reference_memory'), 'reference memory'))
    require(set(unique_strings(document.get('selected_events'), 'selected events')) == SELECTED_EVENTS,
            'selected event coverage mismatch')
    expected = object_value(document.get('expected_cases'), 'expected corpus')
    require(set(expected) == set(PROFILES), 'expected corpus profile coverage incomplete')
    captures = object_value(document.get('captured_corpus_counts'), 'captured corpus counts')
    require(set(captures) == set(PROFILES), 'captured corpus profile coverage incomplete')
    for profile in PROFILES:
        count = number(captures[profile], 'captured corpus count')
        require(count > 0, 'captured corpus cannot be empty')
        identities = unique_strings(expected[profile], 'expected case IDs')
        require(len(identities) == count + len(LARGE_K), 'expected corpus differs from captured corpus count')
        required = tuple(f'captured-{i:03}' for i in range(1, count + 1)) + tuple(f'large-k-{k}' for k in LARGE_K)
        require(identities == required, 'expected corpus identities differ from captured corpus and large-K coverage')
    expected_keys = {(p, f, c) for p in PROFILES for f in FRAMINGS
                     for c in unique_strings(expected[p], 'expected case IDs')}
    cases = array_value(document.get('cases'), 'cases')
    require(bool(cases), 'empty corpus')
    keys = [_case(object_value(row, 'case')) for row in cases]
    require(len(keys) == len(set(keys)) and set(keys) == expected_keys, 'missing/duplicate/extra corpus cases')
    if document['execution_kind'] == 'REAGGREGATED_FROM_VERIFIED_EVIDENCE':
        proof = object_value(document.get('reuse_proof'), 'raw case reuse proof')
        require(number(proof.get('raw_cases_reaggregated'), 'raw case count') == len(cases) and
                proof.get('same_model_library') is True and proof.get('fresh_rtl_execution') is False,
                'raw case reuse count/library/execution mismatch')
    counts = Counter(f for _, f, _ in keys)
    summaries = object_value(document.get('summaries'), 'summaries')
    require(set(summaries) == set(FRAMINGS), 'summary framing coverage incomplete')
    for framing in FRAMINGS:
        summary = object_value(summaries[framing], 'summary')
        for field in ('cases_attempted', 'cases_rtl_admitted', 'cases_model_admitted', 'cases_exact'):
            require(number(summary.get(field), field) == counts[framing], 'aggregate count mismatch')
        require(number(summary.get('max_abs_delta_cycles'), 'maximum delta') == 0, 'nonzero cycle delta')
    mutation = object_value(document.get('event_mutation'), 'mutation gate')
    rejected = object_value(mutation.get('mutations_rejected'), 'mutations rejected')
    require(mutation.get('status') == 'PASS' and mutation.get('control_exact') is True and
            mutation.get('endpoints_and_counters_unchanged') is True and
            set(rejected) == {'shift_non_endpoint_event', 'delete_event', 'duplicate_event'} and
            all(v is True for v in rejected.values()), 'mandatory mutation gate incomplete')
    require(set(unique_strings(mutation.get('selected_events'), 'mutation events')) == SELECTED_EVENTS,
            'mutation selected event coverage mismatch')


def finalize_certificate(document: Mapping[str, JsonValue], library: Path) -> dict[str, JsonValue]:
    validate_certificate(document, library, require_marker=False)
    result: dict[str, JsonValue] = {**document, 'marker': MARKER}
    validate_certificate(result, library)
    return result
