from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Final

from scripts.gemmini_resolve_profile import JsonValue
from sim.cycle.certificate_contract import PROFILES, array_value, object_value, read_document
from sim.cycle.npu_trace_schema import Record, integer, text
from sim.cycle.production_sequence_evidence import CertifiedCase, CertifiedWork, reference, require, validate_case
from sim.cycle.reconstruct_graph import read_manifest, sha256

ROOT: Final = Path(__file__).resolve().parents[2]
CORPUS: Final = ROOT / 'sim/cycle/production_sequence_corpus.json'
CORPUS_SHA256: Final = '4eca9ab2fe91127cf5c3fedee6f20c1fb037de3b1d993314f9d6e37db5aa0ee0'
SOURCE_PATHS: Final = (
    'sim/cycle/service_certificate.py', 'sim/cycle/production_sequence_certificate.py',
    'sim/cycle/production_sequence_evidence.py', 'sim/cycle/production_sequence_corpus.json',
    'sim/cycle/execution_cycle_provider.py',
)
SCHEMA: Final = 'im2p-drained-service-certificate'
ROLE: Final = 'PRODUCTION_GENERATED_SEQUENCE'


def load_corpus() -> Record:
    require(sha256(CORPUS) == CORPUS_SHA256, 'independent corpus changed')
    corpus = read_document(CORPUS)
    vectors = [[phase] * 4 for phase in range(5)]
    require(corpus.get('schema') == 'im2p-production-drained-sequence-corpus' and
            corpus.get('version') == 1 and corpus.get('scope') == 'native-production-dispatch-fixture' and
            corpus.get('period') == 5 and corpus.get('phase_vectors') == vectors and
            corpus.get('work_ids') == [0, 1, 2, 3] and corpus.get('initial_halves') == [0, 0] and
            corpus.get('case_count') == 5 * len(PROFILES) and
            set(object_value(corpus.get('profiles'), 'profiles')) == set(PROFILES),
            'independent case key set incomplete')
    return corpus


def validate_source_manifest(path: Path, digest: str) -> None:
    require(sha256(path) == digest, 'producer source manifest changed')
    seen: set[str] = set()
    for line in path.read_text().splitlines():
        fields = line.split('  ', 1)
        require(len(fields) == 2 and len(fields[0]) == 64 and fields[1] not in seen,
                'producer source manifest malformed or duplicate')
        name = fields[1]
        source = (ROOT.parent / 'llama.cpp-gemmini' / name).resolve(strict=True)
        require(source.is_relative_to(ROOT.parent) and sha256(source) == fields[0],
                'producer source changed: ' + name)
        seen.add(name)
    require(len(seen) >= 27, 'producer source closure incomplete')


def validate_producer_receipt(profile: str, trace: Path, pinned: Record) -> None:
    root = trace.parent.parent
    receipt_path = root / 'receipt.json'
    require(sha256(receipt_path) == pinned.get('receipt_sha256'), 'producer receipt changed')
    receipt = read_document(receipt_path)
    source = object_value(receipt.get('source'), 'producer source')
    require(receipt.get('schema') == 'im2p-native-producer-corpus-receipt' and
            receipt.get('version') == 1 and receipt.get('profile') == profile and
            receipt.get('scope') == 'native-production-dispatch-fixture' and
            source.get('llama_commit') == load_corpus().get('producer_commit') and
            source.get('producer_dirty_diff_sha256') == load_corpus().get('producer_dirty_diff_sha256') and
            source.get('source_manifest_sha256') == pinned.get('source_manifest_sha256') and
            receipt.get('hardware_contract_sha256') == pinned.get('hardware_contract_sha256'),
            'producer receipt identity differs')
    diff = (root / text(source, 'producer_dirty_diff')).resolve(strict=True)
    require(diff.is_relative_to(root.parent) and
            sha256(diff) == load_corpus().get('producer_dirty_diff_sha256'),
            'producer source diff artifact changed')
    artifacts = object_value(receipt.get('artifacts_sha256'), 'producer artifacts')
    require(bool(artifacts) and all((root / name).is_file() and sha256(root / name) == digest
                                    for name, digest in artifacts.items()),
            'producer raw artifact differs from receipt')
    qualification = read_document(root / 'qualification.json')
    require(qualification.get('status') == 'PASS' and
            object_value(receipt.get('validation'), 'validation').get('strict_manifest_lifecycle') == 'PASS',
            'producer qualification missing')
    validate_source_manifest(root / 'source-sha256.txt', text(pinned, 'source_manifest_sha256'))
    graph = read_manifest(root / 'run/semantic-graph.jsonl')
    producer = object_value(graph.run.get('producer'), 'semantic producer')
    require(producer.get('source_manifest_sha256') == pinned.get('source_manifest_sha256') and
            producer.get('git_commit') == load_corpus().get('producer_commit') and
            producer.get('hardware_contract_sha256') == pinned.get('hardware_contract_sha256'),
            'semantic producer source binding differs')


def validate_production_sequence(document: Record, library: Path, trace: Path,
                                 timing: Record, initial_halves: tuple[int, int]) -> tuple[CertifiedCase, ...]:
    corpus = load_corpus()
    require(document.get('schema') == SCHEMA and document.get('version') == 2 and
            document.get('artifact_role') == ROLE and document.get('status') == 'PASS' and
            document.get('corpus_sha256') == CORPUS_SHA256 and
            document.get('library_sha256') == sha256(library),
            'production schema, corpus or library differs')
    sources = object_value(document.get('source_sha256'), 'production sources')
    require(set(sources) == set(SOURCE_PATHS) and
            all(sources[name] == sha256(ROOT / name) for name in SOURCE_PATHS),
            'production validator source closure changed')
    profiles = object_value(corpus['profiles'], 'pinned profiles')
    expected = {(profile, tuple([phase] * 4)) for profile in PROFILES for phase in range(5)}
    rows = [object_value(value, 'case') for value in array_value(document.get('cases'), 'cases')]
    keys = [(text(row, 'profile'), tuple(integer({'phase': value}, 'phase') for value in
                                        array_value(row.get('phases'), 'phases'))) for row in rows]
    require(len(rows) == corpus['case_count'] == len(expected) and
            len(set(keys)) == len(keys) and set(keys) == expected,
            'production sequence case set missing, duplicate or extra')
    verified_profiles: set[str] = set()
    admitted: list[CertifiedCase] = []
    for row, (profile, phases) in zip(rows, keys, strict=True):
        report_path = reference(object_value(row.get('report'), 'report'), 'report')
        pinned = object_value(profiles[profile], 'pinned profile')
        if profile not in verified_profiles:
            report = read_document(report_path)
            producer = object_value(report.get('producer_artifacts'), 'producer artifacts')
            source_trace = reference(object_value(producer.get('trace'), 'trace'), 'trace')
            validate_producer_receipt(profile, source_trace, pinned)
            verified_profiles.add(profile)
        case = validate_case(report_path, library, profile, phases, pinned, timing)
        if profile == case.profile and sha256(trace) == pinned.get('trace_sha256'):
            admitted.append(case)
    require(len(verified_profiles) == len(PROFILES) and len(admitted) == 5 and
            timing.get('read_ready_period') == 5 and initial_halves == (0, 0),
            'target trace, timing or initial state outside production corpus')
    return tuple(admitted)


def build(inputs: Path, library: Path, base: Path, run: Path, fixture: Path, output: Path) -> Path:
    from sim.cycle.certificate_contract import validate_certificate
    from sim.cycle.run_aware_certificate import validate_run_certificate
    from sim.cycle.service_certificate import CORPUS as FIXTURE_CORPUS, validate_evidence

    corpus = load_corpus()
    fixture_document = read_document(fixture)
    validate_evidence(fixture_document, library)
    require(fixture_document.get('base_certificate_sha256') == sha256(base) and
            fixture_document.get('run_certificate_sha256') == sha256(run),
            'fixture certificate base/run binding differs')
    validate_certificate(read_document(base), library)
    require(validate_run_certificate(run, library) == 'PRODUCTION_GENERATED',
            'production run-aware certificate required')
    routes = array_value(read_document(inputs).get('cases'), 'input case routes')
    indexed: dict[tuple[str, tuple[int, ...]], Path] = {}
    for value in routes:
        route = object_value(value, 'case route')
        profile = text(route, 'profile')
        phases = tuple(integer({'phase': phase}, 'phase') for phase in
                       array_value(route.get('phases'), 'phases'))
        path = Path(text(route, 'report'))
        require(path.is_absolute() and (profile, phases) not in indexed,
                'duplicate or relative case report route')
        indexed[profile, phases] = path
    expected = {(profile, tuple([phase] * 4)) for profile in PROFILES for phase in range(5)}
    require(len(indexed) == corpus['case_count'] and set(indexed) == expected,
            'input routes do not cover pinned six-profile phases')
    cases: list[JsonValue] = []
    for (profile, phases), path in sorted(indexed.items()):
        cases.append({'profile': profile, 'phases': list(phases),
                      'report': {'path': str(path), 'sha256': sha256(path)}})
    certificate: Record = {
        'schema': SCHEMA, 'version': 2, 'artifact_role': ROLE, 'status': 'PASS',
        'corpus_sha256': CORPUS_SHA256, 'library_sha256': sha256(library),
        'base_certificate_sha256': sha256(base), 'run_certificate_sha256': sha256(run),
        'fixture_certificate': {'path': str(fixture.resolve(strict=True)), 'sha256': sha256(fixture)},
        'source_sha256': {name: sha256(ROOT / name) for name in SOURCE_PATHS},
        'cases': cases,
    }
    first_report = read_document(indexed[PROFILES[0], (0, 0, 0, 0)])
    producer = object_value(first_report.get('producer_artifacts'), 'producer artifacts')
    trace = reference(object_value(producer.get('trace'), 'trace'), 'trace')
    timing = {**object_value(read_document(FIXTURE_CORPUS)['timing'], 'reference timing'),
              'read_ready_period': 5}
    _ = validate_production_sequence(certificate, library, trace, timing, (0, 0))
    with output.open('x') as stream:
        json.dump(certificate, stream, indent=2, sort_keys=True)
        _ = stream.write('\n')
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description='Build source-bound production drained-sequence certificate')
    for name in ('inputs', 'library', 'base-certificate', 'run-certificate',
                 'fixture-certificate', 'out'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    try:
        path = build(args.inputs.resolve(), args.library.resolve(), args.base_certificate.resolve(),
                     args.run_certificate.resolve(), args.fixture_certificate.resolve(), args.out.resolve())
    except (OSError, ValueError, KeyError, IndexError) as error:
        print(f'production service certificate failed: {error}', file=sys.stderr)
        return 1
    print(json.dumps({'status': 'PASS', 'certificate': str(path)}, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
