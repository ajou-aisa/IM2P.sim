#!/usr/bin/env python3
"""Synthetic IFR3 packets and independent golden checks, with no device access."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import zlib

ROOT = Path(__file__).resolve().parents[2]


def packet(op, run=0, generation=0, m=0, n=0, k=0, t=0, payload=b''):
    header = struct.pack('<4sBBHQIHHHHI', b'IFR3', 3, op, 0x0810,
                         run, generation, m, n, k, t, len(payload))
    body = header + payload
    return body + struct.pack('<I', zlib.crc32(body))


def prepare(out):
    out.mkdir(parents=True, exist_ok=False)
    inputs = ROOT / 'tests/scu_block_scale/fixtures/signed-scu-sat-v2/cases.txt'
    expected = ROOT / 'tests/scu_block_scale/fixtures/signed-scu-sat-v2/expected.json'
    selected = []
    itinerary = []
    def emit(name, data):
        request, response = out / (name + '.request.bin'), out / (name + '.response.bin')
        request.write_bytes(data)
        itinerary.append(f'{request} {response}')
    emit('cap', packet(0))
    for line in inputs.read_text().splitlines():
        name, route, codes, residual, shared, theta, activation, weight, mutate = line.split()
        if route != 'h1':
            continue
        a_codes, w_codes, stored = ([int(v) for v in field.split(',')]
                                    for field in (activation, weight, codes))
        a, w, scales = bytearray(128), bytearray(64 * 64), bytearray(2 * 256)
        for fragment in range(4):
            for lane in range(16):
                reduction = fragment * 16 + lane
                a[reduction] = a_codes[fragment] & 255
                w[reduction * 64] = w_codes[fragment] & 255
        for block in range(2):
            struct.pack_into('<I', scales, block * 256, stored[block] + int(residual))
        run = len(selected) + 1
        emit(name, packet(1, run, run, 1, 1, 64, payload=a + w + scales))
        emit(name + '-release', packet(2, run, run))
        selected.append(name)
    (out / 'itinerary.txt').write_text('\n'.join(itinerary) + '\n')
    (out / 'selected.json').write_text(json.dumps(selected) + '\n')
    (out / 'expected.json').write_bytes(expected.read_bytes())
    (out / 'cases.txt').write_bytes(inputs.read_bytes())
    (out / 'inputs-sha256.json').write_text(json.dumps({p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in out.iterdir() if p.is_file()}, indent=2) + '\n')


def check(out):
    for name, wanted in json.loads((out / 'inputs-sha256.json').read_text()).items():
        assert hashlib.sha256((out / name).read_bytes()).hexdigest() == wanted, name
    expected = json.loads((out / 'expected.json').read_text())
    assert expected['numerical_revision'] == 'signed-scu-sat-v2'
    for request_path in sorted(out.glob('*.request.bin')):
        request = request_path.read_bytes()
        reply = request_path.with_name(request_path.name.replace('.request.', '.response.')).read_bytes()
        assert reply[:8] == b'OFR3\x03\x00\x10\x08'
        assert struct.unpack_from('<H', reply, 30)[0] == 0x0294
        assert reply[8:20] == request[8:20] and reply[88] == request[5]
        assert len(reply) == 148 + struct.unpack_from('<I', reply, 20)[0]
        assert zlib.crc32(reply[:-4]) == struct.unpack_from('<I', reply, len(reply)-4)[0]
        if request[5] == 0:
            assert struct.unpack_from('<3H', reply, 24) == (336, 48, 96)
        elif request[5] == 2:
            assert len(reply) == 148
    # This is G1 only. Host reconstruction is tested through the actual frontend.
    rows = expected['cases']
    if isinstance(rows, list):
        rows = {row['name']: row for row in rows}
    results = []
    for index, name in enumerate(json.loads((out / 'selected.json').read_text()), 1):
        reply = (out / (name + '.response.bin')).read_bytes()
        assert reply[:8] == b'OFR3\x03\x00\x10\x08' and len(reply) == 212
        assert struct.unpack_from('<QIH', reply, 8)[:2] == (index, index)
        assert struct.unpack_from('<H', reply, 30)[0] == 0x0294
        assert zlib.crc32(reply[:-4]) == struct.unpack_from('<I', reply, len(reply)-4)[0]
        values = struct.unpack_from('<16i', reply, 144)
        wanted = rows[name]['integer']
        assert values[0] == wanted and values[1:] == (0,) * 15, (name, values, wanted)
        cycles, fragments, works, a_reads, w_reads, writes, acks = struct.unpack_from('<7Q', reply, 32)
        assert cycles > 0 and (fragments, works, writes, acks) == (4, 1, 1, 1)
        results.append(dict(name=name, raw=values[0], cycles=cycles, fragments=fragments,
                            works=works, writes=writes, acks=acks, a_reads=a_reads, w_reads=w_reads))
    (out / 'result.json').write_text(json.dumps({'backend': 'production_uart_rtl',
        'final_raw_comparisons': len(results), 'wire_padding_comparisons': 15*len(results),
        'host_f_out': 'NOT_RUN_IN_THIS_TEST', 'results': results}, indent=2) + '\n')
    print(f'IFR3_RTL_G1_PASS jobs={len(results)} raw={len(results)} wire_padding={15*len(results)}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('stage', choices=['prepare', 'check'])
    parser.add_argument('out', type=Path)
    args = parser.parse_args()
    (prepare if args.stage == 'prepare' else check)(args.out.resolve())
