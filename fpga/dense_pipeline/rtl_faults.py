#!/usr/bin/env python3
"""Exercise IFR2 faults against the real shell/provider/core, without a numerical oracle."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess
import zlib

ROOT = Path(__file__).resolve().parents[2]


def packet(op, run_id, generation, fields=(0, 0, 0, 0), payload=b''):
    header = struct.pack('<4sBBHQI4HI', b'IFR2', 2, op, 0x810,
                         run_id, generation, *fields, len(payload))
    message = header + payload
    return message + struct.pack('<I', zlib.crc32(message))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--driver', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    driver, out = args.driver.resolve(), args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    identities = {str(p): sha(p) for p in (driver, Path(__file__).resolve(),
                                          ROOT/'fpga/dense_pipeline/PROTOCOL.md')}
    (out/'source-sha256.json').write_text(json.dumps(identities, indent=2)+'\n')
    # Real staged stimulus, not expected output. Unused columns are wire padding.
    weights, activation = bytearray(32*64), bytearray(16*128)
    for row in range(32):
        for col in range(16):
            weights[row*64+col] = (row+col*3) % 17 - 8 & 255
    for row in range(16):
        for col in range(32):
            activation[row*128+col] = (row*5+col) % 23 - 11 & 255
    steps, checks = [], []
    generation = launches = publications = 0
    current_run = 0
    case_name = ''

    def reset(name):
        nonlocal case_name, current_run
        case_name, current_run = name, 0
        steps.append('@reset explicit-rtl-only')

    def transact(name, request, status, op, expected_run=None):
        path = out/f'{len(checks):02d}-{case_name}-{name}'
        path.with_suffix('.request').write_bytes(request)
        response = path.with_suffix('.response')
        steps.append(f'{path.with_suffix(".request")} {response}')
        checks.append({'case':case_name, 'name':name, 'response':str(response),
                       'status':status, 'op':op, 'run_id':current_run if expected_run is None else expected_run,
                       'generation':generation, 'launches':launches,
                       'publications':publications, 'stripe_acks':0})

    def sticky():
        transact('sticky-cap', packet(0, 0, 0), 8, 0)

    def begin():
        nonlocal generation, launches, current_run
        generation += 1
        launches += 1
        current_run = 100 + generation
        transact('begin', packet(4, current_run, generation, (16,16,32,16), weights), 0, 4)

    def publish():
        nonlocal publications
        publications += 1
        transact('publish0', packet(5, current_run, generation, (0,16,0,0), activation), 0, 5)

    reset('profile')
    bad = bytearray(packet(0, 0, 0)); bad[6] ^= 1
    bad[-4:] = struct.pack('<I', zlib.crc32(bad[:-4]))
    transact('reject', bad, 2, 0); sticky()

    reset('payload-length')
    bad = bytearray(packet(4, 100, generation+1, (16,16,32,16), weights)[:32])
    struct.pack_into('<I', bad, 28, 1)
    transact('reject', bad, 2, 4); sticky()

    reset('capacity')
    bad = packet(4, 100, generation+1, (337,16,32,16), weights)[:32]
    transact('reject', bad, 2, 4); sticky()

    reset('new-generation')
    bad = packet(4, 100, generation+2, (16,16,32,16), weights)[:32]
    transact('reject', bad, 2, 4); sticky()

    reset('crc')
    bad = bytearray(packet(4, 100, generation+1, (16,16,32,16), weights)); bad[-1] ^= 1
    transact('reject', bad, 3, 4); sticky()

    reset('partial-payload-timeout')
    # The itinerary clocks until response; the hardware interbyte watchdog fires.
    bad = packet(4, 100, generation+1, (16,16,32,16), weights)[:40]
    transact('reject', bad, 8, 4); sticky()

    for name, fields, wrong_generation in (
        ('reverse-stripe', (0,16,1,1), False),
        ('wrong-row', (1,16,0,0), False),
        ('wrong-slot', (0,16,0,1), False),
        ('publication-generation', (0,16,0,0), True),
    ):
        reset(name); begin()
        bad = packet(5, current_run, generation+int(wrong_generation), fields, activation)[:32]
        transact('reject', bad, 2, 5); sticky()

    reset('duplicate-stripe'); begin(); publish()
    transact('reject', packet(5, current_run, generation, (0,16,0,0), activation)[:32], 2, 5)
    sticky()

    for name, op in (('premature-finish', 7), ('premature-release', 2)):
        reset(name); begin()
        transact('reject', packet(op, current_run, generation), 6, op); sticky()

    reset('abort-unpublished'); begin()
    # Legitimate producer wait exceeds the 2^20 progress watchdog without error.
    steps.append('@clocks 1100000')
    transact('empty-poll', packet(6, current_run, generation), 0, 6)
    transact('explicit-abort', packet(3, current_run, generation), 0, 3)
    current_run = 0
    transact('cap-generation-preserved', packet(0, 0, 0), 0, 0)

    reset('reset-stale-completion'); begin(); publish()
    steps.append('@clocks 2048')
    stale_run = current_run
    steps.append('@reset explicit-rtl-only')
    current_run = 0
    transact('cap-generation-preserved', packet(0, 0, 0), 0, 0)
    transact('reject-stale-poll', packet(6, stale_run, generation), 6, 6); sticky()

    reset('late-poll-no-job')
    transact('reject', packet(6, 999, generation), 6, 6); sticky()

    itinerary = out/'itinerary.txt'
    itinerary.write_text('\n'.join(steps)+'\n')
    (out/'expected-contract.json').write_text(json.dumps(checks, indent=2)+'\n')
    command = [str(driver), str(itinerary), '--trace', str(out/'trace')]
    (out/'command.json').write_text(json.dumps(command, indent=2)+'\n')
    with (out/'rtl.log').open('x') as log:
        result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT)
    log = (out/'rtl.log').read_text()
    errors = []
    if result.returncode != 0:
        errors.append(f'driver exit {result.returncode}')
    if re.search(r'RTL_FAIL:|%Error|Assertion failed|unexpected RTL \$finish', log):
        errors.append('RTL runtime failure/assertion/finish')
    marker = (f'RTL_PACKET_COMPLETE transactions={len(checks)} launches={launches} '
              f'publications={publications} stripe_acks=0')
    if marker not in log:
        errors.append('final transaction/launch/publication/ACK marker mismatch')
    observations = []
    for check in checks:
        try:
            response = Path(check['response'])
            data = response.read_bytes()
            assert len(data) == 148, 'unexpected raw payload/completion'
            assert data[:5] == b'OFR2\x02' and struct.unpack_from('<H',data,6)[0] == 0x810, 'profile/version'
            assert zlib.crc32(data[:-4]) == struct.unpack('<I',data[-4:])[0], 'response CRC'
            assert data[5] == check['status'] and data[88] == check['op'], 'status/op'
            assert data[89] == 0 and struct.unpack_from('<I',data,20)[0] == 0, 'unexpected completion/raw'
            assert struct.unpack_from('<QI',data,8) == (check['run_id'],check['generation']), 'run/generation'
            observed = json.loads(Path(str(response)+'.events.json').read_text())
            assert all(observed[key] == check[key] for key in ('launches','publications','stripe_acks')), 'launch/publication/ACK'
            if check['name'] == 'cap-generation-preserved':
                assert struct.unpack_from('<3H',data,24) == (336,48,96), 'CAP capacity'
                assert not any(data[32:88]) and not any(data[112:144]), 'CAP live counters'
            if check['case'] == 'partial-payload-timeout' and check['name'] == 'reject':
                index = checks.index(check)
                earlier = json.loads(Path(checks[index-1]['response']+'.events.json').read_text())
                assert observed['ticks'] - earlier['ticks'] > (1 << 20), 'watchdog did not reach interbyte deadline'
            observations.append({**check, 'observed':observed, 'status':'PASS'})
        except (AssertionError, FileNotFoundError, ValueError) as failure:
            errors.append(f"{check['case']}/{check['name']}: {failure}")
    if any(sha(Path(path)) != digest for path,digest in identities.items()):
        errors.append('source/driver changed during test')
    summary = {'status':'FAIL' if errors else 'PASS', 'transactions':len(checks),
               'actual_launches':launches, 'actual_publications':publications, 'stripe_acks':0,
               'fault_cases':len({check['case'] for check in checks}),
               'raw_numerical_comparisons':0, 'numerical_claim':False, 'physical_device_access':False,
               'explicit_rtl_resets':sum(line.startswith('@reset') for line in steps),
               'errors':errors, 'observations':observations,
               'not_run':['UART command during FULL RUN or TX drain: RX is not a queued command interface',
                          'forced BRAM/output-port stall', 'UART physical break/stop-bit injection']}
    (out/'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    print(json.dumps({key:value for key,value in summary.items() if key != 'observations'}))
    if errors:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
