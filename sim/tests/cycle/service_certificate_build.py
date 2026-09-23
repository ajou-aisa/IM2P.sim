#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
# Run: uv run sim/tests/cycle/service_certificate_build.py --help
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from sim.cycle.certificate_contract import PROFILES, read_document, validate_certificate
from sim.cycle.run_aware_certificate import validate_run_certificate
from sim.cycle.service_certificate import (
    CORPUS_SHA256, SCENARIO, SCHEMA, SOURCE_PATHS, file_sha256,
    validate_evidence, validate_guarded_rows,
)
from sim.tests.cycle.service_boundary import command, run_probe


def reference(path: Path) -> dict[str, str]:
    return {'path': str(path.resolve(strict=True)), 'sha256': file_sha256(path)}


def build(root: Path, library: Path, base: Path, run: Path, output: Path) -> Path:
    validate_certificate(read_document(base), library)
    if validate_run_certificate(run, library) != 'PRODUCTION_GENERATED':
        raise ValueError('production run-aware certificate required')
    output.mkdir(parents=True, exist_ok=False)
    profiles: dict[str, dict[str, dict[str, str]]] = {}
    for profile in PROFILES:
        target = output / profile
        target.mkdir()
        boundary = target / 'boundary'
        rtl_build = root / profile
        run_probe(rtl_build, library, boundary)
        argv = json.loads((boundary / 'command.json').read_text())
        source = str(Path(__file__).with_name('service_boundary_probe.cpp'))
        executable = str(boundary / 'service-boundary-probe')
        argv[argv.index(source)] = str(Path(__file__).with_name('service_certificate_probe.cpp'))
        argv[argv.index(executable)] = str(target / 'guarded-probe')
        guarded_command = target / 'guarded-command.json'
        guarded_command.write_text(json.dumps(argv, indent=2) + '\n')
        _ = command(argv, target / 'guarded-build.log')
        raw = command([str(target / 'guarded-probe')], target / 'guarded.log')
        rows = validate_guarded_rows(raw)
        profiles[profile] = {name: reference(path) for name, path in {
            'rtl_build_binding': rtl_build / 'rtl-build-binding.json',
            'boundary_report': boundary / 'service-boundary.json',
            'boundary_log': boundary / 'rtl.log',
            'boundary_binary': boundary / 'service-boundary-probe',
            'guarded_command': guarded_command,
            'guarded_log': target / 'guarded.log',
            'guarded_binary': target / 'guarded-probe',
        }.items()}
        print(f'{profile}: {len(rows)} guarded observations', flush=True)
    certificate = {
        'schema': SCHEMA, 'version': 1, 'status': 'PASS', 'scenario_id': SCENARIO,
        'corpus_sha256': CORPUS_SHA256, 'library_sha256': file_sha256(library),
        'base_certificate_sha256': file_sha256(base),
        'run_certificate_sha256': file_sha256(run),
        'source_sha256': {name: file_sha256(ROOT / name) for name in SOURCE_PATHS},
        'profiles': profiles, 'observations': 176 * len(PROFILES),
        'result_mismatches': 0, 'service_mismatches': 0, 'drain_failures': 0,
    }
    validate_evidence(certificate, library)
    path = output / 'service-certificate.json'
    with path.open('x') as stream:
        json.dump(certificate, stream, indent=2, sort_keys=True)
        _ = stream.write('\n')
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description='Certify drained single-NPU HP1 service from current RTL')
    parser.add_argument('--rtl-build-root', type=Path, required=True)
    parser.add_argument('--library', type=Path, required=True)
    parser.add_argument('--base-certificate', type=Path, required=True)
    parser.add_argument('--run-certificate', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    try:
        path = build(args.rtl_build_root.resolve(), args.library.resolve(),
                     args.base_certificate.resolve(), args.run_certificate.resolve(),
                     args.out.resolve())
    except (OSError, ValueError, IndexError) as error:
        print(f'service certificate failed: {error}', file=sys.stderr)
        return 1
    print(json.dumps({'status': 'PASS', 'certificate': str(path)}, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
