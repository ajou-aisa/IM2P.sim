#!/usr/bin/env python3
"""Host CLI guard tests over PTYs. Mock replies are not RTL numerical evidence."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import select
import struct
import subprocess
import time
import zlib

from test_stream_uart import rd, reply, send


SHAPES = {'m16n16k32': (16, 16, 32), 'm321n48k64': (321, 48, 64),
          'm321n48k96': (321, 48, 96)}
CYCLES = {'m16n16k32': 361, 'm321n48k64': 39907, 'm321n48k96': 58695}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fields(line):
    return dict(re.findall(r'(\w+)=([^\s]+)', line))


def wire_raw(directory, first, rows):
    """Only packetize saved expected words for mock parser/control tests."""
    manifest = json.loads((directory / 'manifest.json').read_text())
    m, n, k = (manifest[key] for key in ('I', 'J', 'K'))
    raw = (directory / 'expected-raw.bin').read_bytes()
    result = bytearray()
    for block in range(k // 32):
        for row in range(first, first + rows):
            offset = (block * m + row) * n * 4
            result += raw[offset:offset + n * 4]
            result += bytes((((n + 15) // 16) * 16 - n) * 4)
    return bytes(result)


def check_owned_telemetry(output, transactions, jobs):
    rows = [fields(line) for line in output.splitlines() if line.startswith('RUN_TELEMETRY ')]
    assert len(rows) == jobs, output
    stripe_rows = [fields(line) for line in output.splitlines() if line.startswith('RETIRED_STRIPE ')]
    device_rows = [fields(line) for line in output.splitlines() if line.startswith('DEVICE_STRIPE ')]
    pipeline_jobs = 0
    for number, row in enumerate(rows, 1):
        launch = next(t for t in transactions if t['run'] == number and t['op'] in (1, 4))
        pipeline = launch['op'] == 4
        pipeline_jobs += pipeline
        m, n, k = launch['bounds'][:3]
        name = next(name for name, shape in SHAPES.items() if shape == (m, n, k))
        expected = {'run_id': number, 'generation': number, 'activation_reads': 10 + number,
                    'weight_reads': 20 + number, 'host_wait_cycles': 700 + number,
                    'overlap_cycles': 800 + number, 'publication_reply_count': 3 if pipeline else 0,
                    'completion_reply_count': 3 if pipeline else 0,
                    'elapsed_cycles': 99999 + number if pipeline else CYCLES[name]}
        assert all(int(row[key]) == value for key, value in expected.items()), (row, expected)
        assert row['run_retired'] == row['complete'] == '1'
        assert row['device_publication_total'] == row['compute_cycles'] == 'not_exposed'
        assert row['cross_stripe_overlap_cycles'] == row['detailed_wait_cycles'] == 'not_exposed'
        assert row['source'] == 'device_reply' and row['clock'] == 'fpga_core'
        assert row['stripe_count_source'] == 'host_validated_replies'
        assert int(row['first_A_observed_host_ns']) > 0
        assert int(row['first_A_cycle']) == (number * 10000 + 101 if pipeline else 11 + number)
        if pipeline:
            device = [stripe for stripe in device_rows if int(stripe['run']) == number]
            assert len(device) == 3
            for identifier, stripe in enumerate(device):
                assert (int(stripe['id']), int(stripe['slot']), int(stripe['begin']), int(stripe['rows'])) == \
                    (identifier, identifier % 2, identifier * 160, min(160, m - identifier * 160))
                assert int(stripe['publish_cycle']) == number * 10000 + 100 + identifier * 100
                assert int(stripe['completion_cycle']) == number * 10000 + 1000 + identifier * 100
                assert stripe['per_stripe_first_A_cycle'] == 'not_exposed'
    assert len(device_rows) == len(stripe_rows) == pipeline_jobs * 3
    for group in range(pipeline_jobs):
        rows = stripe_rows[group * 3:group * 3 + 3]
        for identifier, row in enumerate(rows):
            assert row['owned'] == row['run_retired'] == '1'
            assert (int(row['id']), int(row['slot']), int(row['begin']), int(row['end'])) == \
                (identifier, identifier % 2, identifier * 160, min((identifier + 1) * 160, 321))
            assert int(row['completion_cycle']) - int(row['publish_cycle']) == 900
    for line in output.splitlines():
        if line.startswith(('DEVICE_STRIPE ', 'DEVICE_STRIPE_RECEIVE ', 'HOST_TRANSPORT ')):
            row = fields(line)
            timestamps = [int(row[key]) for key in ('host_begin_ns', 'host_send_begin_ns',
                'host_send_end_ns', 'host_receive_end_ns', 'host_validated_ns')]
            assert timestamps[0] > 0 and timestamps == sorted(timestamps), row


def run_case(command, out, fixtures, reference, *, fault=None, failure=False,
             no_device=False, expected_jobs=0, environment=None, checked_failure=False,
             child_log=None):
    out.mkdir()
    master, slave = os.openpty()
    device = os.ttyname(slave)
    env = dict(os.environ, IM2P_FPGA_DEVICE=device, IM2P_FPGA_TIMEOUT_SECONDS='10',
               IM2P_FPGA_FULL_REFERENCE=str(reference))
    for key in list(env):
        if key.startswith('IM2P_FPGA_TEST_') or key.startswith('IM2P_REPLAY_TEST_'):
            del env[key]
    if environment:
        env.update(environment)
    command = [device if item == '@PTY' else str(item) for item in command]
    (out / 'command.json').write_text(json.dumps({'argv': command, 'reference': str(reference),
        'physical_device': False, 'fault': fault, 'env': {k: v for k, v in env.items()
            if k.startswith('IM2P_FPGA_') or k.startswith('IM2P_REPLAY_')}}, indent=2) + '\n')
    transactions, completed, published = [], [], []
    poisoned = False
    jobs = 0
    current_shape = None
    with (out / 'host.log').open('x') as log:
        process = subprocess.Popen(command, env=env, stdout=log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 90
        try:
            while process.poll() is None or select.select([master], [], [], 0)[0]:
                if time.monotonic() >= deadline:
                    raise TimeoutError('PTY host guard deadline')
                if not select.select([master], [], [], 0.02)[0]:
                    continue
                header = rd(master, 32)
                length = struct.unpack_from('<I', header, 28)[0]
                assert length <= 65536, 'unbounded request'
                frame = header + rd(master, length + 4)
                assert frame[:5] == b'IFR2\x02' and frame[6:8] == b'\x10\x08'
                assert zlib.crc32(frame[:-4]) == struct.unpack('<I', frame[-4:])[0]
                op = frame[5]
                run, generation = struct.unpack_from('<QI', frame, 8)
                bounds = struct.unpack_from('<4H', frame, 20)
                index = len(transactions)
                transactions.append({'op': op, 'run': run, 'generation': generation,
                                     'bounds': bounds, 'after_fault': poisoned})
                (out / f'{index:02d}-request.bin').write_bytes(frame)
                assert not no_device, 'preflight rejection sent a device command'
                assert not poisoned, 'device command sent after mismatch, including cleanup'
                if op == 0:
                    assert index == 0 and (run, generation) == (0, 0)
                    response = reply(0, (336, 48, 96), 0, 0)
                else:
                    if op in (1, 4):
                        current_shape = bounds[:3]
                        name = next(name for name, shape in SHAPES.items() if shape == current_shape)
                        directory = fixtures / name
                        m, n, k = current_shape
                        jobs += 1
                        assert run == jobs and generation == jobs
                        published, completed = [], []
                    assert (run, generation) == (jobs, jobs)
                    m, n, k = current_shape
                    works = ((m + 15) // 16) * ((n + 15) // 16)
                    writes = m * ((n + 15) // 16) * (k // 32)
                    # Deliberately vary valid telemetry across invocations. None
                    # of these mock counters are presented as device evidence.
                    elapsed = CYCLES[name] if op == 1 else 99999 + jobs
                    counts = (elapsed, works * (k // 16), works, 10 + jobs, 20 + jobs, writes, writes)
                    if op == 1:
                        response = reply(1, current_shape, run, generation,
                                         payload=wire_raw(directory, 0, m), counts=counts,
                                         first=11 + jobs, firstrows=m)
                        if fault and fault.startswith('cycle:'):
                            changed = bytearray(response)
                            struct.pack_into('<Q', changed, 32, CYCLES[name] + int(fault.split(':')[1]))
                            changed[-4:] = struct.pack('<I', zlib.crc32(changed[:-4]))
                            response = bytes(changed)
                            poisoned = True
                    elif op == 4:
                        assert bounds[3] == 160
                        response = reply(4, current_shape, run, generation)
                    elif op == 5:
                        row, rows, identifier, slot = bounds
                        assert identifier == len(published) and slot == identifier % 2
                        assert row == identifier * 160 and rows == min(160, m - row)
                        published.append((identifier, row, rows))
                        response = reply(5, current_shape, run, generation,
                            idrow=published[-1], first=101 if identifier else 0,
                            firstrows=160 if identifier else 0)
                    elif op == 6:
                        if len(completed) == len(published):
                            response = reply(6, current_shape, run, generation,
                                first=101 if completed else 0, firstrows=160 if completed else 0)
                        else:
                            entry = published[len(completed)]
                            completed.append(entry)
                            response = reply(6, current_shape, run, generation,
                                payload=wire_raw(directory, entry[1], entry[2]),
                                idrow=entry, first=101, firstrows=160)
                            if fault == 'pipeline-identity':
                                changed = bytearray(response)
                                changed[90] ^= 1
                                changed[-4:] = struct.pack('<I', zlib.crc32(changed[:-4]))
                                response = bytes(changed)
                                poisoned = True
                    elif op == 7:
                        assert len(published) == len(completed) == 3
                        response = reply(7, current_shape, run, generation, counts=counts,
                                         first=101, firstrows=160)
                        if fault == 'pipeline-count':
                            changed = bytearray(response)
                            changed[40] ^= 1
                            changed[-4:] = struct.pack('<I', zlib.crc32(changed[:-4]))
                            response = bytes(changed)
                            poisoned = True
                    elif op == 2:
                        response = reply(2, current_shape, run, generation)
                    else:
                        raise AssertionError(f'unexpected automatic recovery opcode {op}')
                    if op in (1, 5, 6, 7):
                        changed = bytearray(response)
                        struct.pack_into('<QQ', changed, 128, 700 + jobs, 800 + jobs)
                        if op == 5 or (op == 6 and changed[89]):
                            identifier = struct.unpack_from('<H', changed, 90)[0]
                            struct.pack_into('<Q', changed, 96, jobs * 10000 + 100 + identifier * 100)
                            if op == 6:
                                struct.pack_into('<Q', changed, 104, jobs * 10000 + 1000 + identifier * 100)
                        if op in (5, 6) and struct.unpack_from('<Q', changed, 112)[0]:
                            struct.pack_into('<Q', changed, 112, jobs * 10000 + 101)
                        if op == 7:
                            struct.pack_into('<Q', changed, 112, jobs * 10000 + 101)
                        changed[-4:] = struct.pack('<I', zlib.crc32(changed[:-4]))
                        response = bytes(changed)
                (out / f'{index:02d}-response.bin').write_bytes(response)
                send(master, response)
            process.wait(timeout=3)
            output = (out / 'host.log').read_text()
            if child_log:
                output += '\n' + child_log.read_text()
            if failure:
                if checked_failure:
                    # Existing failure CLI returns success only after checking
                    # GGML_STATUS_FAILED, no observer call and caller sentinel.
                    assert process.returncode == 0, output
                    assert 'FPGA_UART_GGML_FAILURE PASS' in output and 'output_preserved=1' in output
                else:
                    assert process.returncode != 0, output
                assert not re.search(r'^(SAMPLE |HOST_BENCH_SAMPLE |PERSISTENT_\w+_PASS )', output, re.M), output
                assert not re.search(r'^(RUN_TELEMETRY |RETIRED_STRIPE )', output, re.M), output
                if fault and fault.startswith('cycle:'):
                    assert 'IFR2 FULL cycle mismatch' in output, output
                    assert f'expected={CYCLES[name]}' in output
                    assert f'actual={CYCLES[name] + int(fault.split(":")[1])}' in output
                    assert 'response_header=' in output
                assert jobs == (0 if no_device else 1)
            else:
                assert process.returncode == 0, output
                assert 'PASS' in output and jobs == expected_jobs, output
                assert sum(t['op'] == 2 for t in transactions) == expected_jobs
                check_owned_telemetry(output, transactions, jobs)
                samples = [fields(line) for line in output.splitlines() if line.startswith('SAMPLE ')]
                for sample in samples:
                    assert sample['validation'] == 'expected_files_exact'
                    assert sample['cpu_reference_calls'] == 'not_instrumented'
                    assert sample['cpu_reference_path'] == 'not_called_source_audit'
                benchmarks = [fields(line) for line in output.splitlines() if line.startswith('HOST_BENCH_SAMPLE ')]
                if benchmarks:
                    assert benchmarks[0]['validation'] == 'reference'
                    assert int(benchmarks[0]['reference_dot_calls']) > 0
                    assert int(benchmarks[0]['reference_matmul_calls']) == 1
                    assert all(sample['validation'] == 'check' and sample['reference_dot_calls'] ==
                        sample['reference_matmul_calls'] == '0' for sample in benchmarks[1:])
            assert not any(t['after_fault'] for t in transactions)
            result = {'status': 'PASS', 'fault': fault, 'exit_code': process.returncode,
                      'jobs_submitted': jobs, 'transactions': len(transactions),
                      'commands_after_fault': 0, 'physical_device': False,
                      'mock_parser_control_only': True, 'numerical_fpga_validation': False}
            (out / 'status.json').write_text(json.dumps(result, indent=2) + '\n')
            return result, output
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            os.close(master)
            os.close(slave)
            (out / 'transactions.json').write_text(json.dumps(transactions, indent=2) + '\n')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host-dir', required=True, type=Path)
    parser.add_argument('--referencefile', required=True, type=Path)
    parser.add_argument('--fixtures', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--plan', type=Path, help='new sealed plan for parent fail-fast test')
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    host, persistent = (args.host_dir.resolve() / name for name in ('dense_host_dispatch', 'persistent_replay'))
    fixtures, reference = args.fixtures.resolve(), args.referencefile.resolve()
    identities = {str(path): digest(path) for path in (host, persistent, reference,
        Path(__file__).resolve(), Path(__file__).with_name('test_stream_uart.py').resolve())}
    for name in SHAPES:
        identities.update({str(path): digest(path) for path in (fixtures / name).iterdir() if path.is_file()})
    (args.out / 'inputs-sha256.json').write_text(json.dumps(identities, indent=2) + '\n')
    cases = []
    def run(label, command, **kwargs):
        result, output = run_case(command, args.out / label, fixtures, reference, **kwargs)
        cases.append({'case': label, **result})
        print(json.dumps(cases[-1]), flush=True)
        return output
    for name, shape in SHAPES.items():
        seed = json.loads((fixtures / name / 'manifest.json').read_text())['run_id']
        for delta in (-1, 1):
            for mode in ('uart', 'uart-live'):
                run(f'{mode}-{name}-{delta:+d}', [persistent, mode, '@PTY', '1', fixtures / name],
                    fault=f'cycle:{delta}', failure=True)
            run(f'graph-{name}-{delta:+d}', [host, 'run', *map(str, shape), str(seed), '2', 'FULL'],
                fault=f'cycle:{delta}', failure=True)
    run('graph-warmup-mismatch', [host, 'benchmark', '16', '16', '32', '1', '1', 'FULL'],
        fault='cycle:1', failure=True)
    run('graph-caller-output-preserved', [host, 'failure', '16', '16', '32', 'FULL'],
        fault='cycle:1', failure=True, checked_failure=True)
    invalids = {'missing-file': None,
                'backend': reference.read_text().replace('backend FPGA_UART', 'backend SIMULATOR'),
                'profile': reference.read_text().replace('profile 0810', 'profile 0410'),
                'hardware': reference.read_text().replace('hardware_sha256 8', 'hardware_sha256 9'),
                'missing-fixture': '\n'.join(line for line in reference.read_text().splitlines()
                    if not line.startswith('fixture m16n16k32 ')) + '\n'}
    for invalid, text in invalids.items():
        candidate = args.out / f'{invalid}.txt'
        if text is not None:
            candidate.write_text(text)
        for caller, command in [('persistent', [persistent, 'uart', '@PTY', '1', fixtures / 'm16n16k32']),
                                ('graph', [host, 'run', '16', '16', '32', '1', '1', 'FULL'])]:
            run(f'{caller}-reject-{invalid}', command, no_device=True, failure=True,
                environment={'IM2P_FPGA_FULL_REFERENCE': str(candidate.resolve())})
    for mode in ('uart', 'uart-live'):
        run(f'{mode}-correct-full', [persistent, mode, '@PTY', '1',
            *[fixtures / name for name in SHAPES]], expected_jobs=6)
    run('graph-correct-warmup', [host, 'benchmark', '16', '16', '32', '1', '1', 'FULL'], expected_jobs=2)
    for mode in ('uart-pipeline', 'uart-live-pipeline'):
        run(f'{mode}-elapsed-not-full', [persistent, mode, '@PTY', '1',
            fixtures / 'm321n48k64', fixtures / 'm321n48k96'], expected_jobs=4)
    for fault in ('pipeline-count', 'pipeline-identity'):
        run(fault, [persistent, 'uart-pipeline', '@PTY', '1', fixtures / 'm321n48k64'],
            fault=fault, failure=True)
    simulator_out = args.out / 'simulator-not-board-cycle'
    simulator_out.mkdir()
    command = [str(persistent), 'simulator', '-', '1', str(fixtures / 'm16n16k32')]
    environment = dict(os.environ, IM2P_FPGA_FULL_REFERENCE='/missing/reference-is-irrelevant-to-simulator')
    with (simulator_out / 'host.log').open('x') as log:
        result = subprocess.run(command, env=environment, stdout=log, stderr=subprocess.STDOUT, timeout=30)
    output = (simulator_out / 'host.log').read_text()
    assert result.returncode == 0 and 'PERSISTENT_FULL_PASS jobs=2' in output, output
    samples = [fields(line) for line in output.splitlines() if line.startswith('SAMPLE ')]
    assert len(samples) == 2 and all(sample['cycles'] == '344' for sample in samples), output
    (simulator_out / 'command.json').write_text(json.dumps(command) + '\n')
    cases.append({'case': 'simulator-not-board-cycle', 'status': 'PASS', 'simulator_jobs': 2})
    if args.plan:
        plan = json.loads(args.plan.read_text())
        assert Path(plan['persistent_host']).resolve() == persistent
        parent_out = args.out / 'parent-measurement'
        command = ['python3', str(Path(__file__).with_name('measure.py')), str(args.plan.resolve()),
                   '--run', 'board', '--device', '@PTY', '--approved-bitstream-sha256',
                   plan['bitstream_sha256'], '--out', str(parent_out.resolve())]
        run('parent-warmup-mismatch', command, fault='cycle:1', failure=True,
            child_log=parent_out / '00-uart.log')
        assert len(list(parent_out.glob('*.command.json'))) == 1
        summary = json.loads((parent_out / 'summary.json').read_text())
        assert summary['failure'] and not summary['conditions'] and summary['retries'] == 0
        # The runner's board label selects UART code, but the only device here
        # is this PTY. It is not a physical board measurement.
        (parent_out / 'TEST_ONLY.json').write_text(json.dumps({'physical_board': False,
            'mock_parser_control_only': True, 'next_condition_processes': 0}) + '\n')
    assert all(digest(Path(path)) == identity for path, identity in identities.items()), 'input changed'
    summary = {'status': 'PASS', 'cases': cases, 'count': len(cases), 'physical_board_jobs': 0,
               'mock_parser_control_only': True, 'numerical_fpga_validation': False,
               'all_inputs_preserved': True}
    (args.out / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary), flush=True)


if __name__ == '__main__':
    main()
