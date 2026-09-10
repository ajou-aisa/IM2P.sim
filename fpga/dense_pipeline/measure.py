#!/usr/bin/env python3
"""Sealed persistent FULL/PIPELINE measurement; board execution is explicit."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import statistics
import subprocess
import time


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def verify(plan):
    required = {plan['persistent_host'], plan['bitstream'], *plan['required_manifests']}
    reference_path = plan.get('full_cycle_reference')
    if not reference_path:
        raise ValueError('missing synchronous FPGA FULL cycle reference')
    required.add(reference_path)
    if [entry['suffix'] for entry in plan['conditions']] != ['', '-pipeline', '-live', '-live-pipeline']:
        raise ValueError('unexpected measurement backend conditions')
    for condition in plan['conditions']:
        fixtures = condition['fixtures']
        if not fixtures or len({Path(path).name for path in fixtures}) != len(fixtures):
            raise ValueError('empty or ambiguous fixture list')
        for name in fixtures:
            fixture = Path(name)
            members = ['fixture.bin', 'staging.bin', 'expected-raw.bin', 'expected-fout.bin', 'manifest.json', 'sha256.json']
            if '-live' in condition['suffix']:
                members.append('input-f32.bin')
            required.update(str(fixture / member) for member in members)
    if not required.issubset(plan['files']):
        raise ValueError('host, fixture or source manifest is not sealed')
    for path, expected in plan['files'].items():
        if digest(path) != expected:
            raise ValueError('sealed artifact changed: ' + path)
    if digest(plan['bitstream']) != plan['bitstream_sha256']:
        raise ValueError('bitstream identity mismatch')
    expected_reference = (
        'IFR2_FULL_REFERENCE_V1\n'
        'hardware_sha256 ' + plan['bitstream_sha256'] + '\n'
        'production_rtl_sha256 b29147ce384e9384b3821c358863350f03c0696cbc8b8e286a768f2e7155b853\n'
        'backend FPGA_UART\nprotocol 2\nprofile 0810\nmode FULL\n'
        'fixture m16n16k32 16 16 32 361\n'
        'fixture m321n48k64 321 48 64 39907\n'
        'fixture m321n48k96 321 48 96 58695\n')
    if Path(reference_path).read_text() != expected_reference:
        raise ValueError('FULL cycle reference provenance/content mismatch')
    if plan['protocol'] != 'IFR2' or plan['profile'] != 'A8/W8/D16':
        raise ValueError('measurement protocol/profile mismatch')
    if plan['warmups'] != 1 or plan['repetitions'] != 5:
        raise ValueError('this measurement requires one warmup and five samples')


def fields(line):
    return dict(re.findall(r'(\w+)=([^\s]+)', line))


def summarize(log, backend, fixture_count, expectations=None):
    samples = [fields(line) for line in log.splitlines() if line.startswith('SAMPLE ')]
    markers = [line for line in log.splitlines() if line.startswith('PERSISTENT_') and '_PASS ' in line]
    if len(markers) != 1 or len(samples) != fixture_count * 6 or 'PERSISTENT_REPLAY_FAIL' in log:
        raise ValueError('missing numerical/backend completion marker or sample count')
    marker = fields(markers[0])
    expected_marker = 'PERSISTENT_PIPELINE_PASS ' if backend.endswith('-pipeline') else 'PERSISTENT_FULL_PASS '
    if not markers[0].startswith(expected_marker):
        raise ValueError('FULL/PIPELINE completion marker mismatch')
    if int(marker['jobs']) != len(samples):
        raise ValueError('logical completion count mismatch')
    if backend.startswith('uart') and any(int(marker[key]) for key in
            ('simulator_creates', 'simulator_executes', 'simulator_stream_begins')):
        raise ValueError('FPGA simulator fallback')
    if int(marker['raw']) <= 0 or int(marker['logical']) <= 0 or int(marker['padding']) < 0:
        raise ValueError('invalid comparison count')
    if expectations is not None:
        for key in ('raw', 'logical', 'padding'):
            if int(marker[key]) != 6 * sum(item[key] for item in expectations.values()):
                raise ValueError('raw/f_out/padding comparison count mismatch')
    groups = {}
    for sample in samples:
        if sample['backend'] != backend or sample['exact'] != '1' or sample['commit'] != '1':
            raise ValueError('sample backend/correctness mismatch')
        if int(sample['cycles']) <= 0 or not (0 < int(sample['service_ns']) <= int(sample['sustained_ns'])):
            raise ValueError('invalid cycle or service/sustained endpoint')
        groups.setdefault(sample['fixture'], []).append(sample)
    if len(groups) != fixture_count:
        raise ValueError('fixture identity count mismatch')
    if expectations is not None and set(groups) != set(expectations):
        raise ValueError('unexpected fixture identity')
    result = []
    for fixture, rows in groups.items():
        if [int(row['iteration']) for row in rows] != list(range(6)) or \
                [int(row['warmup']) for row in rows] != [1, 0, 0, 0, 0, 0]:
            raise ValueError('warmup/repetition sequence mismatch')
        entry = {'fixture': fixture, 'backend': backend, 'warmups': 1, 'measured': 5,
                 'samples': rows, 'failures': 0, 'timeouts': 0, 'retries': 0}
        for key in ('service_ns', 'sustained_ns', 'cycles', 'request_bytes', 'response_bytes', 'transactions'):
            values = [int(row[key]) for row in rows[1:]]
            if any(value < 0 for value in values) or (key in ('service_ns', 'sustained_ns', 'cycles') and min(values) == 0):
                raise ValueError('nonpositive execution counter/time or negative transport count')
            entry[key] = {'median': statistics.median(values), 'min': min(values), 'max': max(values)}
        # PIPELINE elapsed cycles include publication/completion waits. This is
        # nominal counter time, not an external frequency measurement or MAC time.
        entry['counter_derived_ns'] = {key: value * 40 for key, value in entry['cycles'].items()}
        result.append(entry)
    return {'comparisons': marker, 'results': result}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('plan', type=Path)
    parser.add_argument('--run', choices=['simulator', 'board'])
    parser.add_argument('--out', type=Path)
    parser.add_argument('--device')
    parser.add_argument('--approved-bitstream-sha256')
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text())
    verify(plan)                         # Entire hash walk outside timed calls.
    if not args.run:
        print(json.dumps({'verified': True, 'board_access': False, 'conditions': plan['conditions']}, indent=2))
        return
    if args.out is None:
        parser.error('--out required for execution')
    if args.run == 'board' and (not args.device or args.approved_bitstream_sha256 != plan['bitstream_sha256']):
        parser.error('board execution requires an explicitly approved full SHA256 and device path')
    args.out.mkdir(parents=True, exist_ok=False)
    (args.out / 'plan.json').write_text(json.dumps(plan, indent=2) + '\n')
    (args.out / 'preflight.json').write_text(json.dumps({'verified': True, 'time_ns': time.time_ns()}) + '\n')
    report = {'backend': args.run, 'board_measured': args.run == 'board', 'conditions': [],
              'failure': None, 'retries': 0, 'programming': False, 'flash_write': False}
    environment = dict(os.environ, IM2P_FPGA_TIMEOUT_SECONDS=str(plan['transport_timeout_seconds']),
                       IM2P_FPGA_FULL_REFERENCE=plan['full_cycle_reference'])
    for key in list(environment):
        if key.startswith('IM2P_FPGA_TEST_') or key.startswith('IM2P_REPLAY_TEST_'):
            raise ValueError('diagnostic delays/faults are forbidden in performance measurement: ' + key)
    try:
        for index, condition in enumerate(plan['conditions']):
            backend = ('uart' if args.run == 'board' else 'simulator') + condition['suffix']
            command = [plan['persistent_host'], backend, args.device if args.run == 'board' else '-',
                       str(plan['repetitions']), *condition['fixtures']]
            prefix = args.out / f'{index:02d}-{backend}'
            prefix.with_suffix('.command.json').write_text(json.dumps(command, indent=2) + '\n')
            with prefix.with_suffix('.log').open('x') as output:
                completed = subprocess.run(command, env=environment, stdout=output, stderr=subprocess.STDOUT,
                                           timeout=plan['process_timeout_seconds'])
            if completed.returncode:
                raise RuntimeError(f'{backend}: first process failure {completed.returncode}; no retry/reset')
            expected = {}
            for fixture in condition['fixtures']:
                manifest = json.loads((Path(fixture) / 'manifest.json').read_text())
                m, n, k = (manifest[key] for key in ('I', 'J', 'K'))
                expected[Path(fixture).name] = {'raw': m*n*(k//32), 'logical': m*n,
                    'padding': (Path(fixture) / 'expected-fout.bin').stat().st_size//4 - m*n}
            data = summarize(prefix.with_suffix('.log').read_text(), backend, len(condition['fixtures']), expected)
            report['conditions'].append(data)
    except Exception as error:
        report['failure'] = str(error)
        raise
    finally:
        try:
            verify(plan)
            report['artifacts_preserved'] = True
        except Exception as error:
            report['artifacts_preserved'] = False
            report['preservation_error'] = str(error)
        (args.out / 'summary.json').write_text(json.dumps(report, indent=2) + '\n')
    if not report['artifacts_preserved']:
        raise RuntimeError('post-measurement artifact changed')


if __name__ == '__main__':
    main()
