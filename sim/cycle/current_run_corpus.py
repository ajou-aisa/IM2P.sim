from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Final, NotRequired

from scripts.gemmini_resolve_profile import JsonValue
from scripts.real_lib_manifest import sha256
from sim.cycle.certificate_contract import array_value, number, read_document
from sim.cycle.current_run_capture import (
    PRODUCER,
    PRODUCER_SHA256,
    reference,
    validate_producer,
)
from sim.cycle.npu_trace_schema import Record, object_value, require, text
from sim.tests.cycle.production_run_work import (
    PRODUCTION_CASE_NAMES,
    PRODUCTION_MANIFEST,
    PRODUCTION_MANIFEST_SHA256,
    PRODUCTION_SOURCE_FILES,
    WORKSPACE,
    Artifact,
    Case,
    Certificate,
    Manifest,
    load_manifest,
    read_work_fixture,
)
from sim.tests.cycle.rtl_hardening import PROFILES

CURRENT_SOURCE_FILES: Final = PRODUCTION_SOURCE_FILES + (
    'IM2P.sim/sim/cycle/current_run_corpus.py',
    'IM2P.sim/sim/cycle/current_run_capture.py',
    'IM2P.sim/sim/cycle/collection_build.py',
    'IM2P.sim/sim/cycle/certificate_contract.py',
    'IM2P.sim/sim/cycle/npu_trace_schema.py',
    'IM2P.sim/sim/tests/cycle/rtl_hardening.py',
    'IM2P.sim/scripts/real_lib_manifest.py',
    'IM2P.sim/scripts/real_matrix_fingerprint.py',
)


class SelectedCertificate(Certificate):
    corpus_authority: NotRequired[Artifact]


def load_selected_manifest(path: Path | None = None) -> Manifest:
    if path is None:
        return load_manifest()
    document = validate_current_corpus(path)

    def integers(value: JsonValue) -> list[int]:
        return [number(item, 'current corpus integer') for item in array_value(value, 'current corpus array')]

    cases: list[Case] = []
    for value in array_value(document['cases'], 'current corpus cases'):
        row = object_value(value)
        cases.append({'profile': text(row, 'profile'), 'case': text(row, 'case'),
                      'fixture_sha256': text(row, 'fixture_sha256'),
                      'descriptor': integers(row['descriptor']), 'geometry': integers(row['geometry']),
                      'runs_header': integers(row['runs_header']),
                      'runs': [integers(run) for run in array_value(row['runs'], 'runs')],
                      'row_map': [integers(pair) for pair in array_value(row['row_map'], 'row map')],
                      'shape': integers(row['shape']), 'tile': integers(row['tile']),
                      'original_k': number(row['original_k'], 'original K'),
                      'timing': {key: number(item, key) for key, item in object_value(row['timing']).items()},
                      'framing': text(row, 'framing')})
    return {'schema_version': 2, 'artifact_role': 'PRODUCTION_GENERATED',
            'producer_source': PRODUCER, 'producer_source_sha256': PRODUCER_SHA256,
            'profiles': list(PROFILES), 'case_count': 42, 'cases': cases}


def artifact(path: Path) -> Artifact:
    return {'path': str(path.resolve(strict=True)), 'sha256': sha256(path)}


def historical_inputs() -> Record:
    require(sha256(PRODUCTION_MANIFEST) == PRODUCTION_MANIFEST_SHA256, 'historical input authority changed')
    return read_document(PRODUCTION_MANIFEST)


def validate_capture(path: Path) -> Record:
    capture = read_document(path)
    require(capture.get('schema') == 'im2p-production-run-aware-capture' and
            type(capture.get('version')) is int and capture.get('version') == 1 and
            capture.get('artifact_role') == 'PRODUCTION_NATIVE_GEMMINI_HP1' and
            capture.get('expected_case_names') == list(PRODUCTION_CASE_NAMES) and
            capture.get('profiles') == list(PROFILES) and type(capture.get('case_count')) is int and
            capture.get('case_count') == 42, 'current capture fixed domain/role differs')
    producers = [object_value(value) for value in array_value(capture.get('producers'), 'producers')]
    require(len(producers) == 6 and [text(row, 'profile') for row in producers] == list(PROFILES),
            'current capture producers missing/duplicate')
    rows = [object_value(value) for value in array_value(capture.get('cases'), 'cases')]
    keys = [(text(row, 'profile'), text(row, 'case')) for row in rows]
    expected = {(profile, name) for profile in PROFILES for name in PRODUCTION_CASE_NAMES}
    require(len(keys) == len(set(keys)) == 42 and set(keys) == expected,
            'current capture cases missing/duplicate/extra')
    roots = {text(row, 'profile'): validate_producer(row) for row in producers}
    old = historical_inputs()
    fixed = {(text(row, 'profile'), text(row, 'case')): row for value in
             array_value(old['cases'], 'historical cases') if (row := object_value(value))}
    for row, key in zip(rows, keys, strict=True):
        fixture = reference(object_value(row.get('fixture')))
        require(fixture == roots[key[0]] / ('rmd-run-work-' + key[1].replace('_', '-') + '.txt'),
                'current capture fixture route differs')
        require(sha256(fixture) == fixed[key]['fixture_sha256'],
                'current input differs from reviewed historical case: ' + '/'.join(key))
        parsed = read_work_fixture(fixture)
        require(all(parsed[field] == fixed[key][field] for field in parsed),
                'current fixture metadata differs from fixed case')
    return capture


def current_document(capture_path: Path) -> Record:
    validate_capture(capture_path)
    old = historical_inputs()
    require(sha256(WORKSPACE / PRODUCER) == PRODUCER_SHA256, 'unreviewed current producer source')
    return {**old, 'schema_version': 2, 'producer_source_sha256': PRODUCER_SHA256,
            'input_equivalence': 'VERIFIED_BYTE_IDENTICAL_HISTORICAL42',
            'historical_manifest_sha256': PRODUCTION_MANIFEST_SHA256,
            'capture_index': {'path': str(capture_path.resolve(strict=True)), 'sha256': sha256(capture_path)}}


def validate_current_corpus(path: Path) -> Record:
    document = read_document(path)
    old = historical_inputs()
    require(set(document) == set(old) | {'input_equivalence', 'historical_manifest_sha256', 'capture_index'} and
            type(document.get('schema_version')) is int and document.get('schema_version') == 2 and
            document.get('artifact_role') == 'PRODUCTION_GENERATED' and
            document.get('producer_source') == PRODUCER and
            document.get('producer_source_sha256') == PRODUCER_SHA256 and
            document.get('input_equivalence') == 'VERIFIED_BYTE_IDENTICAL_HISTORICAL42' and
            document.get('historical_manifest_sha256') == PRODUCTION_MANIFEST_SHA256 and
            all(document.get(key) == old[key] for key in ('profiles', 'case_count', 'cases')),
            'current corpus differs from reviewed fixed inputs/source')
    capture_path = reference(object_value(document.get('capture_index')))
    require(json.dumps(document, sort_keys=True) == json.dumps(current_document(capture_path), sort_keys=True),
            'current corpus provenance differs')
    return document


def issue_current_corpus(capture_path: Path, output: Path) -> None:
    require(not output.exists(), 'current corpus output already exists')
    document = current_document(capture_path)
    with tempfile.NamedTemporaryFile(mode='w', dir=output.parent, prefix='.current-run-', delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(document, stream, indent=2, sort_keys=True)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
            validate_current_corpus(temporary)
            os.link(temporary, output)
        finally:
            temporary.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(description='Issue or verify current native run-aware corpus input equivalence')
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--capture-index', type=Path)
    action.add_argument('--validate', type=Path)
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    try:
        if args.validate is not None:
            require(args.out is None, '--out applies only to issuance')
            validate_current_corpus(args.validate)
        else:
            require(args.out is not None, 'issuance requires --out')
            issue_current_corpus(args.capture_index, args.out)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f'current corpus rejected: {error}', file=sys.stderr)
        return 1
    print(json.dumps({'status': 'VALIDATED_INPUT_EQUIVALENCE', 'case_count': 42,
                      'rtl_execution': 'NOT_RUN'}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
