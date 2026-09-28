from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, assert_never

from scripts.gemmini_resolve_profile import BuildFailure
from sim.cycle import stateful_sequence_evidence as evidence_helpers
from sim.cycle import stateful_sequence_evidence_v2 as current_evidence
from sim.cycle.certificate_contract import (
    PROFILES,
    TIMING,
    array_value,
    object_value,
    read_document,
)
from sim.cycle.npu_trace_schema import Record
from sim.cycle.reconstruct_graph import sha256
from sim.cycle.sequence_domain import LEGACY_REVISION, TAG5_REVISION, profile_domain
from sim.cycle.stateful_sequence_evidence import (
    EvidenceContext,
    StatefulCertificateError,
    case_binding,
    reference,
    require,
    source_hashes,
)

ROOT: Final = evidence_helpers.ROOT
SCHEMA: Final = 'im2p-stateful-sequence-model-state-validation'
ROLE: Final = 'MODEL_STATE_VALIDATION'
SCOPE: Final = 'SCOPED_EVIDENCE'
SHARED_LIBRARY_SHA256: Final = '64f7532135fa46d9868f4daade9300818af220d7a62aeda4663273a8a2561bd8'
PINS: Final = {
    'aggregate': ('rtl/todo15-v2-sixprofile-targets-20260926T080433Z/aggregate.json',
                  'ae733dceb4c5b329395bf83d1b1b6ff99d77aaa95b5eae51468c1c960fd9c220'),
    'aggregate_review': ('rtl/todo15-v2-sixprofile-targets-review-20260926T082409Z/review-verdict.json',
                         '9c13ac28207b758c7b5f390e6a0a9a3d41591284aef448829b2fa9ab10fb890d'),
    'preflight': ('rtl/todo15-v2-sixprofile-targets-20260926T080433Z/preflight.json',
                  'c9efcd3f9c44c2994aaa435abaa92d5bca4b7f3a7daa8ba538a780b995795c96'),
    'state_audit': ('design/todo15-state-refinement-v2-current-20260926T083246Z/verdict.json',
                    '19cbfbe2f5561e04615bc8b60e92ce21427c0604e9fc1d6b88a26d2bafc1db6c'),
    'state_review': ('design/todo15-state-refinement-v2-review-20260926T084624Z/review-verdict.json',
                     '6aef0e549f86059a814a5da78f6f651fbfd71a239c9d0bfc510b368eebc2b818'),
    'pressure_review': ('rtl/todo15-tag-full-producer-pressure-review-20260926T090936Z/review-verdict.json',
                        '6acc2d62b73d4644fa111ae899638b4641d89d1d00a3ea43fed56026b8c0893b'),
}
EXPECTED_IDS: Final = tuple(f'exsia-rmd-cross-parent-v2/{profile}/phase{phase}'
                            for profile, phase in zip(PROFILES, (0, 1, 2, 3, 4, 0), strict=True))
RTL_OBJECT: Final = evidence_helpers.RTL_OBJECT
raw_digests = evidence_helpers.raw_digests
# One successful v3 proof only. Every reuse rechecks all external bytes.
_current_cache: tuple[EvidenceContext, Record] | None = None


class NotReadyError(StatefulCertificateError):
    pass


@dataclass(frozen=True, slots=True)
class ScopedEvidence:
    certificate_sha256: str
    library_sha256: str
    case_ids: tuple[str, ...]
    validation_scope: Literal['SCOPED_EVIDENCE'] = 'SCOPED_EVIDENCE'
    production_admitted: Literal[False] = False
    state_domain_revision: str = LEGACY_REVISION


def pinned(context: EvidenceContext) -> dict[str, Record]:
    result: dict[str, Record] = {}
    for name, (relative, digest) in PINS.items():
        path = context.evidence_root / relative
        reference(path, digest)
        result[name] = read_document(path)
    aggregate = result['aggregate']
    review = result['aggregate_review']
    audit = result['state_audit']
    require(review.get('verdict') == 'confirmed' and
            object_value(review.get('target_pins'), 'review pins').get('aggregate_sha256') == PINS['aggregate'][1] and
            result['state_review'].get('review_verdict') == 'CONFIRMED' and
            result['pressure_review'].get('review_verdict') == 'CONFIRMED' and
            result['pressure_review'].get('experiment_verdict') == 'FULL_STALL_NOT_OBSERVED' and
            result['pressure_review'].get('state_array_todo15') == 'UNRESOLVED' and
            audit.get('verdict') == 'UNRESOLVED' and audit.get('formal_proof') == 'NOT_RUN' and
            object_value(audit.get('inventory'), 'state inventory').get('proof_class_counts') ==
            {'SOURCE_AUDIT_ARGUMENT': 4, 'EXECUTABLE_ASSERTION': 27,
             'RTL_REGRESSION': 8, 'UNRESOLVED': 1},
            'reviewed evidence', 'state review or source audit changed')
    cases = [object_value(row, 'aggregate case') for row in array_value(aggregate.get('cases'), 'cases')]
    negative = [object_value(row, 'preflight mutation') for row in
                array_value(result['preflight'].get('negative_evidence'), 'preflight mutations')]
    require([row.get('mutation') for row in negative] ==
            ['run', 'tile', 'offer', 'fence', 'source-drift', 'old-library'] and
            all(row.get('exit_code') == 1 and row.get('out_exists') is False for row in negative),
            'independent mutations', 'producer negative controls incomplete')
    require([row.get('case_id') for row in cases] == list(EXPECTED_IDS) and
            aggregate.get('profile_denominator') == aggregate.get('passing_case_count') == 6 and
            aggregate.get('logical_work_denominator') == aggregate.get('observed_work_count') ==
            aggregate.get('numeric_pass_count') == 80 and
            aggregate.get('parent_window_denominator') == aggregate.get('observed_parent_window_count') == 12 and
            aggregate.get('first_failure') is None and aggregate.get('first_mismatch') is None and
            aggregate.get('maximum_tag_occupancy_observed') == 4 and
            aggregate.get('full_tag_occupancy_six_observed') is False,
            'corpus denominator', 'six reviewed cases or work/parent denominator incomplete')
    return result


def parents(context: EvidenceContext, library_digest: str,
            equivalence: Record | None = None) -> Record:
    result: Record = {}
    for name, path in (('base', context.base_parent),
                       ('run_aware', context.run_aware_parent),
                       ('service', context.service_parent)):
        document = read_document(path)
        key = 'model_library_sha256' if name == 'base' else 'library_sha256'
        old = document.get(key)
        require(isinstance(old, str), 'parent certificate', f'{name} library binding missing')
        review: Record | None = None
        if context.current_evidence_input is not None and old == library_digest:
            try:
                review = validate_current_parent(name, path, document, context)
            except (OSError, ValueError, KeyError, TypeError, IndexError, BuildFailure) as error:
                raise StatefulCertificateError('parent certificate', f'{name}: {error}') from error
        result[name] = {**reference(path, sha256(path)), 'library_sha256': old,
                        'status': 'CURRENT' if old == library_digest else 'STALE',
                        **({'independent_review': review} if review is not None else {})}
        if equivalence is not None:
            current = object_value(equivalence.get(name), 'parent equivalence')
            require(context.domain_delta_input is not None and
                    current.get('historical_certificate') == reference(path, sha256(path)) and
                    current.get('current_library_sha256') == library_digest,
                    'parent equivalence', f'{name} caller or current library differs')
            result[name] = {**object_value(result[name], name),
                            'status': 'CURRENT_EQUIVALENT', 'current_equivalence': current}
    return result


def validate_current_parent(name: str, path: Path, document: Record,
                            context: EvidenceContext) -> Record:
    if name == 'base':
        from sim.cycle.certificate_contract import validate_certificate

        validate_certificate(document, context.shared_library)
    elif name == 'run_aware':
        from sim.cycle.run_aware_certificate import validate_run_certificate

        require(validate_run_certificate(path, context.shared_library) == 'PRODUCTION_GENERATED',
                'parent certificate', 'production run-aware scope required')
    elif name == 'service':
        from sim.cycle.service_certificate import validate_service_certificate

        require(document.get('version') == 2 and
                document.get('artifact_role') == 'PRODUCTION_GENERATED_SEQUENCE' and
                document.get('status') == 'PASS',
                'parent certificate', 'production service scope required')
        first = object_value(array_value(document.get('cases'), 'service cases')[0], 'service case')
        report = read_document(Path(str(object_value(first.get('report'), 'service report')['path'])))
        trace = object_value(object_value(report.get('producer_artifacts'), 'producer artifacts')
                             .get('trace'), 'service trace')
        _ = validate_service_certificate(path, context.shared_library, Path(str(trace['path'])),
                base_certificate=context.base_parent, run_certificate=context.run_aware_parent,
                timing={key: value for key, value in TIMING.items() if key not in ('revision', 'reserved')},
                initial_scratchpad_half=0, initial_accumulator_half=0)
    return current_evidence.reviewed_parent(context, name, path, document,
                                            sha256(context.shared_library))


def expected(context: EvidenceContext) -> Record:
    evidence = pinned(context)
    first = object_value(array_value(evidence['aggregate']['cases'], 'cases')[0], 'first case')
    first_pins = object_value(first['source_binary_input_build_pins'], 'first case pins')
    library = reference(context.library, str(first_pins['native_abi2_library_sha256']))
    cases = [object_value(row, 'aggregate case') for row in array_value(evidence['aggregate']['cases'], 'cases')]
    audited = [object_value(row, 'audited profile') for row in array_value(
        object_value(evidence['state_audit']['pins'], 'audit pins')['six_profiles'], 'audited profiles')]
    source = source_hashes(context, evidence)
    timing: Record = {name: value for name, value in TIMING.items()}
    negative = [object_value(row, 'mutation') for row in
                array_value(evidence['preflight']['negative_evidence'], 'mutations')]
    result: Record = {'schema': SCHEMA, 'version': 1, 'artifact_role': ROLE,
            'validation_scope': SCOPE, 'production_admitted': False,
            'scenario': 'HP1_W/S_ONE_OUTSTANDING_CROSS_PARENT_ABSOLUTE_OFFER_V2',
            'work_domain_revision': 'PRODUCER_TWO_PARENT_ABI2_V2',
            'state_domain_revision': 'OBSERVED_TAG_OCCUPANCY_AT_MOST_4_PROPOSAL_V1',
            'reference_memory': {'timing': timing, 'initial_reset_count': 1,
                                 'initial_scratchpad_half': 0, 'initial_accumulator_half': 0},
            'library': library, 'shared_library': reference(context.shared_library, SHARED_LIBRARY_SHA256),
            'source_sha256': source,
            'reviewed_evidence': {name: {'path': str(context.evidence_root / relative), 'sha256': digest}
                                  for name, (relative, digest) in PINS.items()},
            'parents': parents(context, str(library['sha256'])),
            'mutations': {str(row['mutation']): {'input_sha256': row['input_sha256'],
                                               'exit_code': 1, 'output_created': False}
                          for row in negative},
            'state_refinement': {'proof_class_counts': object_value(evidence['state_audit']['inventory'],
                                                                    'inventory')['proof_class_counts'],
                                 'unresolved_field': 'State.array', 'formal_proof': 'NOT_RUN',
                                 'full6_supported': False, 'provider_guard': 'ABSENT',
                                 'max_tag_occupancy_observed': 4, 'tag_capacity': 6},
            'completeness': {'profiles_expected': 6, 'profiles_observed': 6,
                             'works_expected': 80, 'works_observed': 80,
                             'parent_windows_expected': 12, 'parent_windows_observed': 12,
                             'selected_event_pairs': evidence['aggregate']['selected_event_parity_count'],
                             'queue_v2_pairs': evidence['aggregate']['queue_v2_parity_count'],
                             'numeric_pass': 80},
            'cases': [case_binding(case, profile_audit)
                      for case, profile_audit in zip(cases, audited, strict=True)]}
    return result


def _expected_current(context: EvidenceContext) -> Record:
    if context.domain_delta_input is not None and not context.domain_delta_input.is_file():
        raise NotReadyError('NOT_READY', 'tag5 delta input/holdout bindings missing')
    try:
        reviewed = current_evidence.reviewed_current(context, EXPECTED_IDS)
    except (StatefulCertificateError, OSError, ValueError, BuildFailure) as error:
        if context.domain_delta_input is None:
            raise
        raise NotReadyError('NOT_READY', f'current stateful80 source/library equivalence unavailable: {error}; '
                            'base240/run42/service-fixture1056/service30 remain required') from error
    result: Record = {'schema': SCHEMA, 'version': 2, 'artifact_role': ROLE,
            'validation_scope': SCOPE, 'production_admitted': False,
            'scenario': 'HP1_W/S_ONE_OUTSTANDING_GUARDED_ABSOLUTE_OFFER_V2',
            'work_domain_revision': 'PRODUCER_TWO_PARENT_ABI2_V2',
            'state_domain_revision': 'GUARDED_TAG4_ROW_LT6_REVIEWED_V2',
            'reference_memory': {'timing': dict(TIMING), 'initial_reset_count': 1,
                                 'initial_scratchpad_half': 0, 'initial_accumulator_half': 0},
            'evidence_input': reviewed.input_reference,
            'library': reviewed.library, 'shared_library': reviewed.shared_library,
            'source_sha256': reviewed.source_sha256,
            'reviewed_evidence': reviewed.reviewed_evidence,
            'parents': parents(context, str(reviewed.shared_library['sha256']),
                               reviewed.parent_equivalence),
            'state_refinement': reviewed.state_refinement,
            'completeness': reviewed.completeness, 'cases': list(reviewed.cases)}
    if context.domain_delta_input is not None:
        from sim.cycle.stateful_sequence_evidence_tag5 import reviewed_delta

        delta = reviewed_delta(context, result)
        result.update(version=3, state_domain_revision=TAG5_REVISION, domain_delta=delta)
        result['state_refinement'] = {**reviewed.state_refinement,
            'max_tag_occupancy': 4, 'profile_max_tag_occupancy': {
                profile: profile_domain(profile, TAG5_REVISION).max_tag_occupancy
                for profile in PROFILES}}
    return result


def expected_current(context: EvidenceContext) -> Record:
    global _current_cache
    if context.domain_delta_input is not None and _current_cache is not None:
        cached_context, cached = _current_cache
        if cached_context == context:
            try:
                _recheck_current(cached)
            except (OSError, ValueError, KeyError, TypeError):
                _current_cache = None
                raise
            return deepcopy(cached)
    result = _expected_current(context)
    if context.domain_delta_input is not None:
        _recheck_current(result)
        _current_cache = (context, deepcopy(result))
    return result


def validate_current(path: Path, context: EvidenceContext, document: Record) -> ScopedEvidence:
    require(document.get('schema') == SCHEMA and type(document.get('version')) is int and
            document['version'] == (3 if context.domain_delta_input is not None else 2) and
            document.get('artifact_role') == ROLE and
            document.get('validation_scope') == SCOPE and document.get('production_admitted') is False,
            'schema or artifact role', 'current MODEL_STATE_VALIDATION SCOPED_EVIDENCE required')
    rows = [object_value(row, 'case') for row in array_value(document.get('cases'), 'cases')]
    require([row.get('case_id') for row in rows] == list(EXPECTED_IDS),
            'case set', 'six reviewed case IDs required in order')
    require(document.get('completeness') == {'profiles_expected': 6, 'profiles_observed': 6,
            'works_expected': 80, 'works_observed': 80,
            'parent_windows_expected': 12, 'parent_windows_observed': 12,
            'selected_event_pairs': 2538768, 'queue_v2_pairs': 31418,
            'boundary_v2_pairs': 18, 'numeric_pass': 80},
            'completeness', 'reviewed current denominator differs')
    target = expected_current(context)
    require(set(document) == set(target), 'schema or artifact role', 'unexpected or missing v2 field')
    for key, boundary in (('evidence_input', 'evidence input'), ('source_sha256', 'source closure'),
                          ('library', 'library/ABI2'), ('shared_library', 'library/ABI2'),
                          ('parents', 'parent certificate'), ('reviewed_evidence', 'independent review'),
                          ('state_refinement', 'State.array domain'), ('reference_memory', 'reference memory'),
                          ('scenario', 'scenario'), ('work_domain_revision', 'work domain'),
                          ('state_domain_revision', 'state domain')):
        require(document.get(key) == target[key], boundary, 'current certificate binding differs')
    if context.domain_delta_input is not None:
        require(document.get('domain_delta') == target['domain_delta'],
                'domain delta', 'source-bound tag5 evidence differs')
    for row, wanted in zip(rows, array_value(target['cases'], 'expected cases'), strict=True):
        require(row == object_value(wanted, 'expected case'), 'work/run/offer',
                f"{row['case_id']} reviewed case differs")
    return ScopedEvidence(sha256(path), str(object_value(target['library'], 'library')['sha256']),
                          EXPECTED_IDS, state_domain_revision=str(target['state_domain_revision']))


def validate(path: Path, context: EvidenceContext) -> ScopedEvidence:
    try:
        document = read_document(path)
    except (OSError, ValueError, TypeError) as error:
        raise StatefulCertificateError('document', str(error)) from error
    if document.get('schema') == 'stateful-profile-extension-v1':
        from sim.cycle import stateful_profile_extension

        document = stateful_profile_extension.validate_document(path, context)
        return ScopedEvidence(sha256(path), sha256(context.library), (str(document['case_id']),),
                              state_domain_revision=str(document['state_domain_revision']))
    if document.get('schema') == 'stateful-profile-domain-v1':
        from sim.cycle import stateful_profile_certificate as profile_certificate

        document = profile_certificate.validate_document(path, context)
        return ScopedEvidence(sha256(path), sha256(context.library), (str(document['case_id']),),
                              state_domain_revision=str(document['state_domain_revision']))
    if context.tag6_evidence_input is not None:
        from sim.cycle import stateful_sequence_replay_certificate as replay
        from sim.cycle.stateful_sequence_evidence_tag6 import validate_document

        require(context.current_evidence_input is None and context.domain_delta_input is None,
                'NOT_READY', 'Tag6 and historical evidence selectors are mutually exclusive')
        full_replay = document.get('schema') == replay.SCHEMA
        document = replay.validate_document(path, context) if full_replay else validate_document(path, context)
        case = 'actual-gpt2-full374' if full_replay else 'actual-gpt2-prefix240'
        return ScopedEvidence(sha256(path), sha256(context.library), (case,),
                              state_domain_revision=str(document['state_domain_revision']))
    require(context.domain_delta_input is None or context.current_evidence_input is not None,
            'NOT_READY', 'tag5 delta requires reviewed current v2 evidence; historical hashes cannot be rebased')
    if context.current_evidence_input is not None:
        return validate_current(path, context, document)
    require(document.get('schema') == SCHEMA and document.get('version') == 1 and
            document.get('artifact_role') == ROLE and document.get('validation_scope') == SCOPE and
            document.get('production_admitted') is False,
            'schema or artifact role', 'MODEL_STATE_VALIDATION SCOPED_EVIDENCE required')
    rows = [object_value(row, 'case') for row in array_value(document.get('cases'), 'cases')]
    require([row.get('case_id') for row in rows] == list(EXPECTED_IDS),
            'case set', 'six fixed reviewed profile/case IDs required in order')
    require(document.get('completeness') == {'profiles_expected': 6, 'profiles_observed': 6,
            'works_expected': 80, 'works_observed': 80,
            'parent_windows_expected': 12, 'parent_windows_observed': 12,
            'selected_event_pairs': 2538768, 'queue_v2_pairs': 31418, 'numeric_pass': 80},
            'completeness', 'reviewed denominator differs')
    target = expected(context)
    require(set(document) == set(target), 'schema or artifact role', 'unexpected or missing top-level field')
    for key, boundary in (('source_sha256', 'source closure'), ('library', 'library/ABI2'),
                          ('shared_library', 'library/ABI2'), ('parents', 'parent certificate'),
                          ('state_refinement', 'State.array domain'), ('reference_memory', 'reference memory'),
                          ('reviewed_evidence', 'independent review'),
                          ('mutations', 'independent mutations'), ('scenario', 'scenario'),
                          ('work_domain_revision', 'work domain'), ('state_domain_revision', 'state domain')):
        require(document.get(key) == target[key], boundary, 'certificate binding differs')
    for row, wanted in zip(rows, array_value(target['cases'], 'expected cases'), strict=True):
        expected_row = object_value(wanted, 'expected case')
        require(set(row) == set(expected_row), 'case set', 'unexpected or missing case field')
        for key, boundary in (('profile', 'case profile'), ('declared', 'work/run/offer'),
                              ('work_inputs_sha256', 'work/run/offer'), ('artifacts', 'RTL artifact'),
                              ('raw_digests', 'selected-event/queue/boundary'),
                              ('comparison', 'comparison summary')):
            require(row.get(key) == expected_row[key], boundary,
                    f"{row['case_id']} binding differs")
    return ScopedEvidence(sha256(path), str(object_value(target['library'], 'library')['sha256']),
                          EXPECTED_IDS)


def admit(path: Path, context: EvidenceContext) -> None:
    _ = validate(path, context)
    if read_document(path).get('schema') in ('stateful-profile-domain-v1', 'stateful-profile-extension-v1'):
        return
    if context.tag6_evidence_input is not None:
        from sim.cycle import stateful_sequence_replay_certificate as replay

        if read_document(path).get('schema') == replay.SCHEMA:
            return
        raise NotReadyError('NOT_READY', 'Tag6 domain alone requires full374 replay and state-transition certificates')
    if context.current_evidence_input is not None:
        document = read_document(path)
        bound = object_value(document['parents'], 'parent certificates')
        stale = [name for name, item in bound.items()
                 if object_value(item, name).get('status') not in
                    (('CURRENT', 'CURRENT_EQUIVALENT') if context.domain_delta_input is not None
                     else ('CURRENT',))]
        if stale:
            raise NotReadyError('NOT_READY', 'stale parent certificates: ' + ', '.join(stale))
        return
    raise NotReadyError('NOT_READY', 'State.array old-full6/6 unresolved; no enforced provider guard; '
                        'base/run-aware/service parent certificates STALE')


def _recheck_current(document: Record) -> None:
    for name, digest in object_value(document['source_sha256'], 'source closure').items():
        require(sha256(ROOT / name) == digest, 'source closure', f'changed: {name}')
    for key in ('evidence_input', 'library', 'shared_library'):
        item = object_value(document[key], key)
        _ = reference(Path(str(item['path'])), str(item['sha256']))
    for item in object_value(document['reviewed_evidence'], 'reviewed evidence').values():
        if isinstance(item, dict) and set(item) == {'path', 'sha256'}:
            _ = reference(Path(str(item['path'])), str(item['sha256']))
        elif isinstance(item, dict):
            for nested in item.values():
                bound = object_value(nested, 'parent review')
                _ = reference(Path(str(bound['path'])), str(bound['sha256']))
    for item in object_value(document['parents'], 'parents').values():
        bound = object_value(item, 'parent certificate')
        _ = reference(Path(str(bound['path'])), str(bound['sha256']))
    for case in array_value(document['cases'], 'cases'):
        artifacts = object_value(object_value(case, 'case')['artifacts'], 'case artifacts')
        for item in artifacts.values():
            bound = object_value(item, 'case artifact')
            _ = reference(Path(str(bound['path'])), str(bound['sha256']))
    if 'domain_delta' in document:
        from sim.cycle.stateful_sequence_evidence_tag5 import recheck_delta

        recheck_delta(object_value(document['domain_delta'], 'domain delta'))


def build(output: Path, context: EvidenceContext) -> Path:
    if context.tag6_evidence_input is not None:
        from sim.cycle.stateful_sequence_evidence_tag6 import expected as tag6_expected

        require(context.current_evidence_input is None and context.domain_delta_input is None,
                'NOT_READY', 'Tag6 and historical evidence selectors are mutually exclusive')
        if os.path.lexists(output):
            raise FileExistsError(output)
        document = tag6_expected(context)
        with tempfile.TemporaryDirectory(prefix=f'.{output.name}.', dir=output.parent) as temporary:
            staged = Path(temporary) / 'certificate.json'
            with staged.open('x') as stream:
                json.dump(document, stream, indent=2, sort_keys=True)
                _ = stream.write('\n')
            _ = validate(staged, context)
            os.link(staged, output)
        return output
    require(context.domain_delta_input is None or context.current_evidence_input is not None,
            'NOT_READY', 'tag5 delta requires reviewed current v2 evidence; historical hashes cannot be rebased')
    if context.current_evidence_input is not None:
        if os.path.lexists(output):
            raise FileExistsError(output)
        document = expected_current(context)
        with tempfile.TemporaryDirectory(prefix=f'.{output.name}.', dir=output.parent) as temporary:
            staged = Path(temporary) / 'certificate.json'
            with staged.open('x') as stream:
                json.dump(document, stream, indent=2, sort_keys=True)
                _ = stream.write('\n')
            require(read_document(staged) == document, 'document', 'staged certificate differs')
            _recheck_current(document)
            os.link(staged, output)
        return output
    document = expected(context)
    with output.open('x') as stream:
        json.dump(document, stream, indent=2, sort_keys=True)
        _ = stream.write('\n')
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description='Source-bound stateful sequence scope audit')
    for name in ('evidence_root', 'library', 'shared_library', 'base_parent',
                  'run_aware_parent', 'service_parent'):
        parser.add_argument(f'--{name.replace("_", "-")}', type=Path, required=True)
    parser.add_argument('--current-evidence-input', type=Path)
    parser.add_argument('--domain-delta-input', type=Path)
    parser.add_argument('--tag6-evidence-input', type=Path)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('build').add_argument('output', type=Path)
    commands.add_parser('validate').add_argument('path', type=Path)
    commands.add_parser('admit').add_argument('path', type=Path)
    args = parser.parse_args()
    context = EvidenceContext(args.evidence_root, args.library, args.shared_library,
                              args.base_parent, args.run_aware_parent, args.service_parent,
                              args.current_evidence_input, args.domain_delta_input, args.tag6_evidence_input)
    try:
        match args.command:
            case 'build':
                path = build(args.output, context)
                print(json.dumps({'certificate': str(path), 'validation_scope': SCOPE,
                                  'production_admitted': False}, sort_keys=True))
            case 'validate':
                result = validate(args.path, context)
                print(f'{result.validation_scope}, production_admitted=false')
            case 'admit':
                admit(args.path, context)
            case unreachable:
                assert_never(unreachable)
    except (StatefulCertificateError, OSError, ValueError, KeyError, TypeError, BuildFailure) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
