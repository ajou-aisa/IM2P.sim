#!/usr/bin/env python3
"""Prepare a sealed measurement plan. Never open a device or execute a workload."""
import argparse
import json
from pathlib import Path

from measure import digest, verify
from build import HOST_PIN, PARAMS_PIN


ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
BITSTREAM_SHA256 = '8aef393d040bb306e6ddf7b4b977976a9a924dbc5aec62b37b1690f0aa18c0ca'
AUTHORIZED_ROOT_CHANGES = (
    'frontend/include/im2p_gemmini_frontend.hpp',
    'frontend/src/im2p_gemmini_frontend.cpp',
    'frontend/tests/test_frontend.cpp',
    'fpga/full_replay/capture.cpp',
    'fpga/full_replay/route.tcl',
)


def seal(files, path, expected=None):
    path = Path(path).resolve(strict=True)
    actual = digest(path)
    if expected is not None and actual != expected:
        raise ValueError('preserved artifact mismatch: ' + str(path))
    if str(path) in files and files[str(path)] != actual:
        raise ValueError('artifact changed while preparing plan: ' + str(path))
    files[str(path)] = actual
    return actual


def add_manifest(files, manifest, base):
    seal(files, manifest)
    entries = json.loads(manifest.read_text())
    for name, expected in entries.items():
        member = (base / name).resolve()
        if not member.is_relative_to(base.resolve()):
            raise ValueError('manifest member escapes its source: ' + name)
        seal(files, member, expected)
    return entries


def prepare_portable(host, bitstream, output, fixtures=None):
    """Seal a native build and portable fixtures without historical experiment files."""
    host = Path(host).resolve(strict=True)
    bitstream = Path(bitstream).resolve(strict=True)
    output = Path(output).resolve()
    fixture_root = (Path(fixtures) if fixtures else HERE / 'fixtures').resolve(strict=True)
    if output.exists():
        raise ValueError('refusing to overwrite an existing plan')
    files = {}
    seal(files, bitstream, BITSTREAM_SHA256)
    identity_path = host / 'identity.json'
    seal(files, identity_path)
    identity = json.loads(identity_path.read_text())
    if (identity['backend'] != 'FPGA_UART' or identity['host_pin'] != HOST_PIN or
            identity['params_pin'] != PARAMS_PIN):
        raise ValueError('candidate host/backend/include identity mismatch')
    manifest = host / 'integration-sha256.json'
    seal(files, manifest, identity['source_sha256'])
    add_manifest(files, manifest, host)
    seal(files, identity['sim_archive'], identity['sim_archive_sha256'])
    cache_path = host / 'host-build/CMakeCache.txt'
    seal(files, cache_path)
    cache = dict(line.split('=', 1) for line in cache_path.read_text().splitlines()
                 if '=' in line and not line.startswith(('#', '//')))
    for key, expected in {'IM2P_FPGA_PROTOCOL_VERSION:STRING': '2',
                          'GGML_GEMMINI_EXECUTION_BACKEND:STRING': 'FPGA_UART',
                          'GGML_GEMMINI_ACTIVATION_BITS:STRING': '8',
                          'GGML_GEMMINI_WEIGHT_BITS:STRING': '8',
                          'GGML_GEMMINI_DIM:STRING': '16',
                          'GGML_GEMMINI_BLOCK_SIZE:STRING': '32',
                          'GGML_GEMMINI_ACTIVATION_QUANT:STRING': 'EXSIA',
                          'GGML_GEMMINI_ENABLE_RMD:BOOL': 'OFF'}.items():
        if cache.get(key) != expected:
            raise ValueError('host build profile mismatch: ' + key)
    required_manifests = [manifest]
    # Compare even relocated inputs to the committed fixture hashes. The
    # per-directory manifests predate input-f32.bin; its hash is in
    # the aggregate manifest instead.
    fixture_index = HERE / 'fixtures/sha256.json'
    seal(files, fixture_index)
    expected_files = json.loads(fixture_index.read_text())
    names = ('m16n16k32', 'm321n48k64', 'm321n48k96')
    for name in names:
        directory = fixture_root / name
        reference_manifest = HERE / 'fixtures' / name / 'sha256.json'
        seal(files, reference_manifest)
        seal(files, directory / 'sha256.json', digest(reference_manifest))
        add_manifest(files, directory / 'sha256.json', directory)
        required_manifests.append(directory / 'sha256.json')
        seal(files, directory / 'input-f32.bin', expected_files[name + '/input-f32.bin'])
    required_manifests.append(fixture_index)
    persistent_host = host / 'host-build/persistent_replay'
    ggml_host = host / 'host-build/dense_host_dispatch'
    reference = HERE / 'full-cycle-reference.txt'
    for path in (persistent_host, ggml_host, reference, HERE / 'measure.py',
                 HERE / 'build.py', HERE / 'deployment-lock.json', Path(__file__)):
        seal(files, path)
    long_fixtures = [str(fixture_root / name) for name in names[1:]]
    conditions = [{'suffix': suffix, 'fixtures':
                   ([str(fixture_root / names[0])] if suffix == '' else []) + long_fixtures}
                  for suffix in ('', '-pipeline', '-live', '-live-pipeline')]
    plan = {'full_cycle_reference': str(reference), 'schema': 1, 'status': 'Awaiting approval',
            'portable': True, 'physical_board_accessed': False,
            'protocol': 'IFR2', 'profile': 'A8/W8/D16', 'part': 'xc7a100tcsg324-1',
            'clock_hz_nominal': 25000000, 'route': 'native Q8_H1 / EXSIA / RMD OFF',
            'host_commit': HOST_PIN, 'bitstream': str(bitstream),
            'bitstream_sha256': BITSTREAM_SHA256, 'persistent_host': str(persistent_host),
            'ggml_host': str(ggml_host), 'required_manifests': list(map(str, required_manifests)),
            'host_identity': str(identity_path), 'warmups': 1, 'repetitions': 5,
            'transport_timeout_seconds': 30, 'process_timeout_seconds': 900,
            'conditions': conditions, 'logical_invocations_per_backend': 54,
            'failures_abort_immediately': True, 'reset_retry_programming': False, 'files': files}
    verify(plan)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x') as stream:
        stream.write(json.dumps(plan, indent=2) + '\n')
    return {'plan': str(output), 'sha256': digest(output), 'sealed_files': len(files),
            'logical_invocations_per_backend': 54, 'verified': True, 'physical_access': False}


def prepare(experiment, board_name, host_name, output):
    experiment = experiment.resolve(strict=True)
    board = (experiment / board_name).resolve(strict=True)
    host = (experiment / host_name).resolve(strict=True)
    output = output.resolve()
    mapping_path = experiment / 'evidence' / (output.stem + '-preservation.json')
    if output.exists() or mapping_path.exists():
        raise ValueError('refusing to overwrite an existing plan or preservation mapping')
    files = {}
    baseline_path = experiment / 'baseline/measured-full-verified.json'
    seal(files, baseline_path)
    baseline = json.loads(baseline_path.read_text())
    results = [ROOT / name for name in baseline['artifacts'] if name.endswith('/result-sha256.json')]
    if len(results) != 1:
        raise ValueError('expected one preserved FULL board measurement manifest')
    result_manifest = results[0]
    seal(files, result_manifest, baseline['artifacts'][str(result_manifest.relative_to(ROOT))])
    before_manifest = result_manifest.parent / 'before-sha256.json'
    before = json.loads(before_manifest.read_text())
    if len(before) != 616:
        raise ValueError('unexpected FULL preservation membership')
    seal(files, before_manifest)
    authorized = {str(ROOT / name): name for name in AUTHORIZED_ROOT_CHANGES}
    if not authorized.keys() <= before.keys():
        raise ValueError('authorized source originals are absent from FULL preservation manifest')
    backups = []
    for original, expected in before.items():
        if original in authorized:
            backup = experiment / 'baseline/root-files' / authorized[original]
            seal(files, backup, expected)
            backups.append({'original_path': original, 'original_sha256': expected,
                            'preserved_path': str(backup), 'preserved_sha256': expected,
                            'current_root_sha256_at_preparation': digest(original),
                            'reason': 'authorized integration source change; original bytes retained in baseline'})
        else:
            seal(files, original, expected)
    old_results = add_manifest(files, result_manifest, result_manifest.parent)
    if len(old_results) != 825:
        raise ValueError('unexpected prior measurement result membership')

    required_manifests = [board / 'integration-sha256.json', host / 'integration-sha256.json']
    for candidate in (board, host):
        identity_path = candidate / 'identity.json'
        seal(files, identity_path)
        identity = json.loads(identity_path.read_text())
        if (identity['backend'] != 'FPGA_UART' or identity['host_pin'] != baseline['source_pins']['host'] or
                identity['params_pin'] != baseline['source_pins']['params']):
            raise ValueError('candidate host/backend/include identity mismatch')
        seal(files, candidate / 'integration-sha256.json', identity['source_sha256'])
        add_manifest(files, candidate / 'integration-sha256.json', candidate)
        seal(files, identity['sim_archive'], identity['sim_archive_sha256'])
    cache_path = host / 'host-build/CMakeCache.txt'
    seal(files, cache_path)
    cache = dict(line.split('=', 1) for line in cache_path.read_text().splitlines()
                 if '=' in line and not line.startswith(('#', '//')))
    for key, expected in {'IM2P_FPGA_PROTOCOL_VERSION:STRING': '2',
                          'GGML_GEMMINI_EXECUTION_BACKEND:STRING': 'FPGA_UART',
                          'GGML_GEMMINI_ACTIVATION_BITS:STRING': '8',
                          'GGML_GEMMINI_WEIGHT_BITS:STRING': '8',
                          'GGML_GEMMINI_DIM:STRING': '16',
                          'GGML_GEMMINI_ACTIVATION_QUANT:STRING': 'EXSIA',
                          'GGML_GEMMINI_ENABLE_RMD:BOOL': 'OFF'}.items():
        if cache.get(key) != expected:
            raise ValueError('host build profile mismatch: ' + key)
    add_manifest(files, board / 'production/generated-sha256.json', board / 'production')
    fixture_root = experiment / 'final-fixtures'
    fixture_manifest = fixture_root / 'all-files-sha256.json'
    fixture_entries = add_manifest(files, fixture_manifest, fixture_root)
    members = {str(p.relative_to(fixture_root)) for p in fixture_root.rglob('*') if p.is_file()}
    if members != set(fixture_entries) | {'all-files-sha256.json'}:
        raise ValueError('final fixture membership changed')
    required_manifests.append(fixture_manifest)
    bitstream = board / 'route/dense-pipeline.bit'
    persistent_host = host / 'host-build/persistent_replay'
    ggml_host = host / 'host-build/dense_host_dispatch'
    for path in (bitstream, board / 'route/route.dcp', persistent_host, ggml_host,
                 host / 'host-build/compile_commands.json', HERE / 'measure.py',
                 HERE / 'test_measure.py', Path(__file__)):
        seal(files, path)
    long_fixtures = [str(fixture_root / name) for name in ('m321n48k64', 'm321n48k96')]
    conditions = [{'suffix': suffix, 'fixtures':
                   ([str(fixture_root / 'm16n16k32')] if suffix == '' else []) + long_fixtures}
                  for suffix in ('', '-pipeline', '-live', '-live-pipeline')]
    reference = HERE / 'full-cycle-reference.txt'
    seal(files, reference)
    plan = {'full_cycle_reference': str(reference), 'schema': 1, 'status': 'Awaiting approval', 'physical_board_accessed': False,
            'protocol': 'IFR2', 'profile': 'A8/W8/D16', 'part': 'xc7a100tcsg324-1',
            'clock_hz_nominal': 25000000, 'route': 'native Q8_H1 / EXSIA / RMD OFF',
            'host_commit': baseline['source_pins']['host'], 'bitstream': str(bitstream),
            'bitstream_sha256': files[str(bitstream)], 'persistent_host': str(persistent_host),
            'ggml_host': str(ggml_host), 'required_manifests': list(map(str, required_manifests)),
            'warmups': 1, 'repetitions': 5, 'transport_timeout_seconds': 30,
            'process_timeout_seconds': 900, 'conditions': conditions,
            'logical_invocations_per_backend': 54, 'failures_abort_immediately': True,
            'reset_retry_programming': False, 'files': files}
    verify(plan)  # Existing measurement verifier, before any plan is published.
    mapping = {'schema': 1, 'before_manifest': str(before_manifest),
               'prior_results_manifest': str(result_manifest), 'original_members': 616,
               'unchanged_in_place': 611, 'authorized_original_backups': backups,
               'prior_results_unchanged': 825, 'status': 'PASS', 'physical_access': False}
    mapping_path.parent.mkdir(parents=True, exist_ok=True)
    with mapping_path.open('x') as stream:
        stream.write(json.dumps(mapping, indent=2) + '\n')
    seal(files, mapping_path)
    plan['required_manifests'].append(str(mapping_path))
    plan['preservation_mapping'] = str(mapping_path)
    verify(plan)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x') as stream:
        stream.write(json.dumps(plan, indent=2) + '\n')
    return {'plan': str(output), 'sha256': digest(output), 'sealed_files': len(files),
            'preservation_mapping': str(mapping_path), 'logical_invocations_per_backend': 54,
            'verified': True, 'physical_access': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--portable', action='store_true', help='seal a native snapshot without historical logs')
    parser.add_argument('--experiment', type=Path)
    parser.add_argument('--board')
    parser.add_argument('--host', required=True)
    parser.add_argument('--bitstream', type=Path)
    parser.add_argument('--fixtures', type=Path)
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    if args.portable:
        if not args.bitstream or not args.out or args.experiment or args.board:
            parser.error('--portable requires --host, --bitstream, --out; no --experiment/--board')
        result = prepare_portable(args.host, args.bitstream, args.out, args.fixtures)
    else:
        if not args.experiment or not args.board or args.bitstream or args.fixtures:
            parser.error('historical mode requires --experiment, --board; no --bitstream/--fixtures')
        result = prepare(args.experiment, args.board, args.host,
                         args.out or args.experiment / 'measurement-plan.json')
    print(json.dumps(result, indent=2))
