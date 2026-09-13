"""IFR4 bounded-window wire layout. This transport never opens devices."""
import struct
import zlib

VERSION = 4
HEADER_BYTES = 48
MAX_PAYLOAD = 4096
POLL_BYTES = 176
RECORD_BYTES = 88
OUTPUT_RECORDS = 16
CAP, START, POLL, REFILL, PUBLISH, OUTPUT_ACK, STRIPE_ACK, RELEASE = range(8)
HEADER = struct.Struct('<4sBBBBQIII5I')
RECORD = struct.Struct('<QQII16i')
assert HEADER.size == HEADER_BYTES and RECORD.size == RECORD_BYTES


def packet(op, run=0, generation=0, sequence=0, args=(0, 0, 0, 0, 0), payload=b'', flags=0):
    if len(payload) > MAX_PAYLOAD or len(args) != 5:
        raise ValueError('IFR4 payload/header extent')
    body = HEADER.pack(b'IFR4', VERSION, op, 0, flags, run, generation,
                       sequence, len(payload), *args) + payload
    return body + struct.pack('<I', zlib.crc32(body))


def response(data):
    if len(data) < HEADER_BYTES + 4:
        raise ValueError('IFR4 truncated response')
    fields = HEADER.unpack_from(data)
    magic, version, op, status, flags, run, generation, sequence, length, *args = fields
    if magic != b'OFR4' or version != VERSION or length > MAX_PAYLOAD or len(data) != HEADER_BYTES + length + 4:
        raise ValueError('IFR4 response identity/extent')
    if struct.unpack_from('<I', data, len(data) - 4)[0] != zlib.crc32(data[:-4]):
        raise ValueError('IFR4 response CRC')
    return dict(op=op, status=status, flags=flags, run=run, generation=generation,
                sequence=sequence, args=args, payload=data[HEADER_BYTES:-4])


CONSTANT_NAMES = ('VERSION', 'HEADER_BYTES', 'MAX_PAYLOAD', 'POLL_BYTES',
                  'RECORD_BYTES', 'OUTPUT_RECORDS', 'CAP', 'START', 'POLL',
                  'REFILL', 'PUBLISH', 'OUTPUT_ACK', 'STRIPE_ACK', 'RELEASE')


def sv_constants():
    return '// Generated from window_packets.py; checked by test_window_packets.py.\n' + ''.join(
        f'`define IFR4_{name} {globals()[name]}\n' for name in CONSTANT_NAMES)


def cpp_constants():
    return ('// Generated from window_packets.py; checked by test_window_packets.py.\n'
            '#pragma once\nnamespace im2p::fpga::ifr4 {\n' + ''.join(
                f'inline constexpr unsigned {name} = {globals()[name]};\n' for name in CONSTANT_NAMES) + '}\n')


if __name__ == '__main__':
    print(sv_constants(), end='')
