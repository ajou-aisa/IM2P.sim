#!/usr/bin/env python3
"""Bounded FULL packet adapter. No numerical reference and no fallback."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import select
import struct
import termios
import time
import zlib

HEADER = struct.Struct('<4sBBHQIHHHHI')
PROFILE = 0x0810


def packet(op, run_id, generation, shape=(0, 0, 0), payload=b''):
    m, n, k = shape
    if op == 1:
        if not (0 < m <= 32 and 0 < n <= 48 and k in (32, 64, 96)):
            raise ValueError('unsupported shape/profile capacity')
        if len(payload) != m * 128 + k * 64:
            raise ValueError('input payload length')
    elif op not in (0, 2, 3) or shape != (0, 0, 0) or payload:
        raise ValueError('unsupported command')
    body = HEADER.pack(b'IFR1', 1, op, PROFILE, run_id, generation, m, n, k, 0, len(payload)) + payload
    return body + struct.pack('<I', zlib.crc32(body))


def decode(data, run_id, generation=None, shape=None):
    if len(data) < 100 or len(data) > 18532:
        raise ValueError('response length')
    magic, version, status, profile, identity, gen, length = struct.unpack_from('<4sBBHQII', data)
    if magic != b'OFR1' or version != 1 or profile != PROFILE:
        raise ValueError('response profile/version')
    if identity != run_id or (generation is not None and gen != generation):
        raise ValueError('stale run/generation')
    if len(data) != 100 + length or zlib.crc32(data[:-4]) != struct.unpack_from('<I', data, len(data)-4)[0]:
        raise ValueError('response length/CRC')
    if status:
        raise ValueError(f'FPGA status {status}')
    actual_shape = struct.unpack_from('<HHH', data, 24)
    if shape is None and length != 0:
        raise ValueError('unexpected completion payload for control request')
    if shape is not None:
        if actual_shape != shape:
            raise ValueError('response shape')
        m, n, k = shape
        if length != m * ((n+15)//16) * (k//32) * 64:
            raise ValueError('output payload length')
        cycles, fragments, works, a_reads, w_reads, writes, acks = struct.unpack_from('<7Q', data, 32)
        expected_works = ((m+15)//16) * ((n+15)//16)
        if fragments != expected_works*(k//16) or works != expected_works or writes != m*((n+15)//16)*(k//32) or acks != writes:
            raise ValueError('completion counts')
        if not cycles or not a_reads or not w_reads:
            raise ValueError('missing hardware progress')
    return gen, data[96:-4]


class UART:
    """Free-running hardware; polling time never becomes an RTL cycle count."""
    def __init__(self, device, timeout=5.0):
        self.fd = os.open(device, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        self.failed = False
        self.timeout = timeout
        attr = termios.tcgetattr(self.fd)
        attr[0] = attr[1] = attr[3] = 0
        attr[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
        attr[4] = attr[5] = termios.B1000000
        attr[6][termios.VMIN] = attr[6][termios.VTIME] = 0
        termios.tcsetattr(self.fd, termios.TCSANOW, attr)

    def close(self):
        os.close(self.fd)

    def transfer(self, request):
        if self.failed:
            raise RuntimeError('sticky transport failure; reset/reopen required')
        deadline = time.monotonic()+self.timeout
        response = bytearray()
        sent = 0
        target = 96
        try:
            while sent < len(request) or len(response) < target:
                remaining = deadline-time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('UART FULL timeout')
                readable, writable, _ = select.select([self.fd], [self.fd] if sent < len(request) else [], [], remaining)
                if writable:
                    try:
                        count = os.write(self.fd, request[sent:])
                        if count == 0:
                            raise OSError('zero UART write')
                        sent += count
                    except BlockingIOError:
                        pass
                if readable:
                    try:
                        part = os.read(self.fd, target-len(response))
                    except BlockingIOError:
                        continue
                    if not part:
                        raise OSError('UART closed')
                    response.extend(part)
                    if len(response) == 96 and target == 96:
                        length = struct.unpack_from('<I', response, 20)[0]
                        if length > 18432:
                            raise ValueError('unbounded response length')
                        target = 100+length
            magic, version, op, profile, identity, gen, m, n, k, reserved, length = HEADER.unpack_from(request)
            decode(bytes(response), identity, gen if op in (1,2) else None,
                   (m,n,k) if op == 1 else None)
            return bytes(response)
        except Exception:
            self.failed = True
            raise


def seal(directory):
    files = ['fixture.bin', 'staging.bin', 'expected-raw.bin', 'expected-fout.bin', 'manifest.json']
    hashes = {name: hashlib.sha256((directory/name).read_bytes()).hexdigest() for name in files}
    (directory/'sha256.json').write_text(json.dumps(hashes, indent=2)+'\n')


def verify(directory):
    hashes = json.loads((directory/'sha256.json').read_text())
    if set(hashes) != {'fixture.bin','staging.bin','expected-raw.bin','expected-fout.bin','manifest.json'}:
        raise ValueError('fixture hash members')
    for name, digest in hashes.items():
        if name not in ('fixture.bin', 'staging.bin', 'expected-raw.bin', 'expected-fout.bin', 'manifest.json'):
            raise ValueError('unexpected fixture member')
        if hashlib.sha256((directory/name).read_bytes()).hexdigest() != digest:
            raise ValueError(f'fixture checksum {name}')


def prepare(directories, itinerary):
    lines = []
    for generation, directory in enumerate(directories, 1):
        verify(directory)
        manifest = json.loads((directory/'manifest.json').read_text())
        identity = manifest['run_id']
        shape = tuple(manifest[k] for k in ('I', 'J', 'K'))
        for name, request in [('run', packet(1, identity, generation, shape, (directory/'staging.bin').read_bytes())),
                              ('release', packet(2, identity, generation))]:
            path = directory/f'{name}-request.bin'
            path.write_bytes(request)
            lines.append(f'{path.resolve()} {directory.resolve() / (name+"-response.bin")}')
    itinerary.write_text('\n'.join(lines)+'\n')


def finish(directories):
    for generation, directory in enumerate(directories, 1):
        verify(directory)
        manifest = json.loads((directory/'manifest.json').read_text())
        data = (directory/'run-response.bin').read_bytes()
        shape = tuple(manifest[k] for k in ('I', 'J', 'K'))
        decode(data, manifest['run_id'], generation, shape)
        decode((directory/'release-response.bin').read_bytes(), manifest['run_id'], generation)
        # Canonical integer domain is signed64 at callback; wire storage is i32.
        (directory/'decoded.bin').write_bytes(struct.pack('<Q',manifest['run_id'])+data[32:88]+data[96:-4])


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['seal','prepare','finish'])
    parser.add_argument('directories', nargs='+', type=Path)
    parser.add_argument('--itinerary', type=Path, default=Path('itinerary.txt'))
    args = parser.parse_args()
    if args.action == 'seal':
        for directory in args.directories:
            seal(directory)
    elif args.action == 'prepare':
        prepare(args.directories,args.itinerary)
    else:
        finish(args.directories)
