#!/usr/bin/env python3
"""Independent IFR4 layout checks and real UART-bit provider qualification."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import zlib

import window_packets as wire


def layout_test():
    assert Path(__file__).with_name('window_protocol.svh').read_text() == wire.sv_constants()
    assert Path(__file__).with_name('window_protocol.hpp').read_text() == wire.cpp_constants()
    packet = wire.packet(wire.REFILL, 0x1122334455667788, 7, 9, (2, 11, 16, 1, 0), bytes(range(16)))
    assert packet[:8] == b'IFR4\x04\x03\x00\x00'
    assert int.from_bytes(packet[8:16], 'little') == 0x1122334455667788
    assert int.from_bytes(packet[16:20], 'little') == 7
    assert int.from_bytes(packet[20:24], 'little') == 9
    assert int.from_bytes(packet[24:28], 'little') == 16
    assert [int.from_bytes(packet[i:i+4], 'little') for i in range(28,48,4)] == [2,11,16,1,0]
    assert packet[48:-4] == bytes(range(16))
    assert int.from_bytes(packet[-4:], 'little') == zlib.crc32(packet[:-4])
    body = b'OFR4' + packet[4:-4]
    encoded = body + struct.pack('<I', zlib.crc32(body))
    decoded = wire.response(encoded)
    assert decoded['payload'] == bytes(range(16)) and decoded['args'] == [2,11,16,1,0]
    # Independent byte offsets for the shell's idle CAP reply.
    capability = b'OFR4\x04\x00\x00\x00' + bytes(20) + b'\x20\x04\x10\x08' + bytes(16)
    decoded = wire.response(capability + zlib.crc32(capability).to_bytes(4,'little'))
    assert decoded['run'] == decoded['generation'] == decoded['sequence'] == 0
    assert decoded['args'] == [0x08100420,0,0,0,0] and decoded['payload'] == b''
    for malformed in (encoded[:47], encoded[:-1], encoded + b'\0',
                      encoded[:-1] + bytes([encoded[-1] ^ 1]), packet):
        try:
            wire.response(malformed)
        except ValueError:
            pass
        else:
            raise AssertionError('malformed IFR4 response accepted')
    assert len(wire.packet(wire.REFILL, payload=bytes(4096))) == 4148
    try:
        wire.packet(wire.REFILL, payload=bytes(4097))
    except ValueError:
        pass
    else:
        raise AssertionError('oversized IFR4 request accepted')


class Device:
    def __init__(self, binary):
        self.process = subprocess.Popen([str(binary)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, text=True, bufsize=1)
        self.sequence = 0
        self.run_id = 0
        self.generation = 0
        self.started = False
        self.transactions = 0

    def line(self, value):
        self.process.stdin.write(value + '\n')
        self.process.stdin.flush()
        result = self.process.stdout.readline().strip()
        if not result:
            raise RuntimeError(self.process.stderr.read() or 'UART driver stopped')
        return result

    def exchange(self, op, args=(0,0,0,0,0), payload=b'', flags=0, corrupt=False, sequence=None):
        if op == wire.START:
            self.run_id = 77 + self.generation
            self.generation += 1
            self.sequence = 0
        if op != wire.CAP:
            self.sequence += 1
        sent_sequence = (self.sequence if op != wire.CAP else 0) if sequence is None else sequence
        value = wire.packet(op, self.run_id if op != wire.CAP else 0, self.generation if op != wire.CAP else 0,
                            sent_sequence, args, payload, flags)
        if corrupt:
            value = value[:-1] + bytes([value[-1] ^ 1])
        result = wire.response(bytes.fromhex(self.line(value.hex())))
        assert result['op'] == op
        assert result['sequence'] == sent_sequence
        assert (result['run'], result['generation']) == (self.run_id,self.generation), result
        assert result['args'] == [0x08100420,0,0,0,0], result
        self.transactions += 1
        return result

    def stats(self):
        return json.loads(self.line('stats'))

    def close(self):
        self.process.stdin.close()
        self.process.wait(timeout=10)
        error = self.process.stderr.read()
        assert self.process.returncode == 0, error


def operand_a(m, k, row, column):
    return (row * 13 + column * 7 + column // 32 * 11 + 3) % 15 - 7 if row < m and column < k else 0


def operand_w(n, k, row, column):
    return (row * 3 + column * 11 + row // 32 * 7 + 5) % 13 - 6 if row < k and column < n else 0


def scale_metadata(op, block, column):
    values = (0x80000000,0,1,7,15,30,31,32,127,32767) if op == 5 else (0,1,2,3,5,17,255,256,513,65790)
    return values[(block * 7 + column * 3) % len(values)]


def expected_values(m, n, k, op):
    result = {}
    clamp = lambda value: max(-(1 << 31), min((1 << 31)-1, value))
    for row in range(m):
        for column in range(n):
            for block in range((k+31)//32 if op == 3 else 1):
                begin,end = (block*32,min((block+1)*32,k)) if op == 3 else (0,k)
                total = 0
                for start in range(begin,end,16):
                    dot = sum(operand_a(m,k,row,x)*operand_w(n,k,x,column) for x in range(start,min(start+16,end)))
                    metadata = scale_metadata(op,start//32,column)
                    contribution = (0 if metadata == 0x80000000 else dot * (1 << min(metadata,32))) if op == 5 else dot * metadata
                    total = total + dot if op in (0,3) else clamp(total + clamp(contribution))
                result[(block,row,column)] = total
    return result


def start(device, m, n, k, live=False, wk=5, wn=4, op=3):
    kl, nl = max(4, (k-1).bit_length()), max(4, (n-1).bit_length())
    control = kl | nl << 5 | wk << 10 | wn << 13 | op << 16
    reply = device.exchange(wire.START, (m,n,k,control,0), flags=int(live))
    assert reply['status'] == 0, reply
    return nl


def window_payload(kind, description, m, n, k, published, wk, wn, op=3):
    row_origin, word_origin, valid_rows, generation, words, reserved = description
    assert reserved == 0 and words <= 256 and generation > 0
    per_row = 1 << (wk - 4 if kind == 0 else wn - 4 if kind == 1 else wn - 2)
    value = bytearray()
    for index in range(words):
        row = row_origin + index // per_row
        word = word_origin + index % per_row
        if kind == 2:
            value += struct.pack('<4I', *(scale_metadata(op,row,word*4+lane)
                                         if row < (k+31)//32 and word*4+lane < n else 0 for lane in range(4)))
        else:
            for lane in range(16):
                column = word * 16 + lane
                datum = operand_a(m,k,row,column) if kind == 0 and row < published else (
                    operand_w(n,k,row,column) if kind == 1 else 0)
                if row >= row_origin + valid_rows:
                    datum = 0
                value.append(datum & 255)
    return bytes(value)


def run(binary, m, n, k, live=False, chunk=256, op=3, stripe_rows=7, wk=5, wn=4):
    assert stripe_rows > 0 and 0 < chunk <= 256
    owns_device = not isinstance(binary, Device)
    device = Device(binary) if owns_device else binary
    try:
        before = device.stats()
        assert device.exchange(wire.CAP)['status'] == 0
        nl = start(device,m,n,k,live,wk=wk,wn=wn,op=op)
        published = 0 if live else m
        publications = retired = 0
        observed = {}
        output_order = []
        batch_sizes = []
        next_batch = 1
        max_refill_bytes = 0
        done = False
        for attempt in range(10000):
            if live and publications == retired and published < m:
                rows = min(stripe_rows,m-published)
                reply = device.exchange(wire.PUBLISH,(published,rows,publications,publications%2,0))
                assert reply['status'] == 0, reply
                published += rows
                publications += 1
            reply = device.exchange(wire.POLL)
            assert reply['status'] == 0, reply
            data, flags = reply['payload'], reply['flags']
            assert len(data) >= wire.POLL_BYTES
            for kind in range(3):
                if flags & (1 << kind):
                    description = struct.unpack_from('<6I',data,64+24*kind)
                    payload = window_payload(kind,description,m,n,k,published,wk,wn,op)
                    generation, words = description[3:5]
                    for index in range(0,words,chunk):
                        count = min(chunk, words-index)
                        max_refill_bytes = max(max_refill_bytes, count * 16)
                        refill = device.exchange(wire.REFILL,(kind,generation,index,count,int(index+count==words)),
                                                 payload[index*16:(index+count)*16])
                        assert refill['status'] == 0, refill
            batch, count = struct.unpack_from('<II',data,168)
            assert len(data) == wire.POLL_BYTES + count * wire.RECORD_BYTES
            if flags & 8:
                assert 0 < count <= 16 and batch == next_batch, 'output batch identity did not restart/increment'
                if not batch_sizes:
                    replay = device.exchange(wire.POLL)
                    assert replay['status'] == 0 and replay['flags'] & 8
                    assert replay['payload'][168:] == data[168:], 'unacknowledged output batch changed'
                batch_sizes.append(count)
                for index in range(count):
                    address, tag, columns, reserved, *values = wire.RECORD.unpack_from(data,wire.POLL_BYTES+index*wire.RECORD_BYTES)
                    assert reserved == 0 and 0 < columns <= 16 and address % 64 == 0
                    stride = 4 << nl
                    plane, address = divmod(address,m*stride) if op == 3 else (0,address)
                    row, column_bytes = divmod(address,stride)
                    column = column_bytes//4
                    assert row < published and column < n and columns == min(16,n-column)
                    output_order.append((plane,row,column,columns))
                    for lane in range(columns):
                        key = (plane,row,column+lane)
                        assert key not in observed
                        observed[key] = values[lane]
                ack = device.exchange(wire.OUTPUT_ACK,(batch,count,0,0,0))
                assert ack['status'] == 0, ack
                next_batch += 1
            if flags & 16:
                stripe, row, rows = struct.unpack_from('<III',data,136)
                # Refill processing may let the next output batch accumulate;
                # acknowledge only after a later poll confirms that buffer empty.
                if count == 0:
                    assert stripe == retired and row == retired*stripe_rows and rows == min(stripe_rows,m-row)
                    ack = device.exchange(wire.STRIPE_ACK,(stripe,row,rows,0,0))
                    assert ack['status'] == 0, ack
                    retired += 1
            if flags & 32 and count == 0 and not flags & 16:
                done = True
                break
        assert done and (not live or retired == (m+stripe_rows-1)//stripe_rows)
        expected = expected_values(m,n,k,op)
        assert observed == expected, next((key,observed.get(key),value) for key,value in expected.items() if observed.get(key)!=value)
        # Canonical main order is I work, J work, block publication, then row.
        # UART batch boundaries and refill packet sizes are not numerical work.
        expected_order = []
        for stripe_begin in range(0,m,stripe_rows if live else m):
            stripe_end = min(m,stripe_begin+stripe_rows) if live else m
            for i in range(stripe_begin,stripe_end,16):
                for j in range(0,n,16):
                    for block in range((k+31)//32 if op == 3 else 1):
                        for row in range(i,min(i+16,stripe_end)):
                            expected_order.append((block,row,j,min(16,n-j)))
        assert output_order == expected_order, 'internal I/J/block output order changed'
        assert device.exchange(wire.RELEASE)['status'] == 0
        stats = device.stats()
        assert stats['launches'] - before['launches'] == 1 and stats['stripe_acks'] - before['stripe_acks'] == retired
        return dict(m=m,n=n,k=k,live=live,chunk_words=chunk,op=op,values=len(observed),
                    window_k_log=wk,window_n_log=wn,
                    max_refill_bytes=max_refill_bytes,
                    stripe_rows=stripe_rows if live else m,publications=publications,completions=retired,
                    output_order='PASS',output_records=len(output_order),
                    raw_sha256=hashlib.sha256(b''.join(struct.pack('<i',observed[key]) for key in sorted(observed))).hexdigest(),
                    batch_sizes=sorted(set(batch_sizes)),transactions=device.transactions,**stats)
    finally:
        if owns_device:
            device.close()


def repeated_runs(binary):
    device = Device(binary)
    try:
        dense = run(device,1,1,32)
        residual = run(device,1,1,32,op=0)
        old_run, old_generation = device.run_id, device.generation
        start(device,1,1,32,op=0)
        reply = device.exchange(wire.POLL)
        assert struct.unpack_from('<I',reply['payload'],168)[0] == 1
        stale = wire.packet(wire.OUTPUT_ACK,old_run,old_generation,device.sequence+1,(1,1,0,0,0))
        before = device.stats()
        rejected = wire.response(bytes.fromhex(device.line(stale.hex())))
        assert rejected['status'] == 4 and device.stats()['output_acks'] == before['output_acks']
        assert device.exchange(wire.POLL)['status'] == 9
        return dict(dense=dense,residual=residual,stale_old_run_ack='REJECTED',status='PASS')
    finally:
        device.close()


def negative(binary, fault):
    device = Device(binary)
    try:
        start(device,3,17,64)
        reply = device.exchange(wire.POLL)
        assert reply['flags'] & 1
        description = struct.unpack_from('<6I',reply['payload'],64)
        payload = window_payload(0,description,3,17,64,3,5,4)
        gen,words = description[3:5]
        before = device.stats()
        if fault == 'sequence':
            rejected = device.exchange(wire.POLL,sequence=device.sequence)
        else:
            args = (0,gen+(fault=='generation'),int(fault=='index'),words,1)
            if fault == 'partial_word':
                payload = payload[:-1]
            rejected = device.exchange(wire.REFILL,args,payload,corrupt=fault=='crc')
        after = device.stats()
        expected_status = dict(crc=3,sequence=4,generation=5,index=5,partial_word=2)[fault]
        assert rejected['status'] == expected_status and after['loads'] == before['loads'] == 0
        assert device.exchange(wire.POLL)['status'] == 9, 'rejected request did not poison its run'
        return dict(fault=fault,status=rejected['status'],loads=after['loads'])
    finally:
        device.close()


def extent_probe(binary, m, n, k, accepted, op=3):
    device = Device(binary)
    try:
        kl, nl = max(4,(k-1).bit_length()), max(4,(n-1).bit_length())
        control = kl | nl << 5 | 6 << 10 | 6 << 13 | op << 16
        packet = wire.packet(wire.START,77,1,1,(m,n,k,control,0),flags=1)
        reply = wire.response(bytes.fromhex(device.line(packet.hex())))
        assert (reply['status'] == 0) == accepted, reply
        stats = device.stats()
        assert stats['launches'] == int(accepted) and stats['loads'] == 0
        return dict(m=m,n=n,k=k,op=op,accepted=accepted,loads=0,status='PASS')
    finally:
        device.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary',type=Path)
    parser.add_argument('--out',type=Path)
    parser.add_argument('--quick',action='store_true')
    args=parser.parse_args()
    layout_test()
    assert expected_values(1,1,33,3) == {(0,0,0): -117, (1,0,0): -12}
    assert expected_values(1,1,32,0) == {(0,0,0): -117}
    if not args.binary:
        print('IFR4_LAYOUT_PASS')
        return
    cases=[run(args.binary,3,17,64)]
    if not args.quick:
        cases += [run(args.binary,16,96,192,chunk=32),run(args.binary,16,96,192,chunk=256),
                  run(args.binary,33,49,191,live=True,chunk=32)]
        assert cases[1]['raw_sha256']==cases[2]['raw_sha256']
        # Public A8/DIM16 geometry with software tile_I=1: H=16, full K.
        # One START owns every stripe, internal J work, and K fragment.
        full = run(args.binary,33,49,192)
        live = run(args.binary,33,49,192,live=True,chunk=32,stripe_rows=16)
        assert full['raw_sha256'] == live['raw_sha256'] and live['completions'] == 3
        repeated = run(args.binary,65,49,192,live=True,chunk=32,stripe_rows=16)
        assert repeated['completions'] == 5
        cases += [full,live,repeated]
        for op in (4,5):
            full = run(args.binary,13,79,191,op=op)
            live = run(args.binary,13,79,191,live=True,chunk=32,op=op)
            assert full['raw_sha256'] == live['raw_sha256']
            cases += [full,live]
    # Maximum 4096-byte REFILL versus one-word frames, including repeated word
    # assembler reuse. Both must preserve the independent numerical/order oracle.
    rx_full = run(args.binary,3,17,64,wk=6,wn=6)
    rx_split = run(args.binary,3,17,64,chunk=1,wk=6,wn=6)
    assert rx_full['raw_sha256'] == rx_split['raw_sha256']
    assert rx_full['max_refill_bytes'] == 4096 and rx_split['max_refill_bytes'] == 16
    cases += [rx_full,rx_split]
    failures=[negative(args.binary,fault) for fault in ('crc','sequence','generation','index','partial_word')]
    extents=[extent_probe(args.binary,*e) for e in
             ((1,128256,2048,True),(16,96,65568,True),
              (31,1<<31,1<<31,True),(32,1<<31,1<<31,False),
              (32,1<<31,1<<31,True,0))]
    result=dict(status='PASS',cases=cases,negative=failures,extent=extents,repeated_runs=repeated_runs(args.binary),physical_access_count=0,PHY='UART bits')
    if args.out: args.out.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result))
    print('IFR4_UART_PROVIDER_PASS')


if __name__=='__main__':
    main()
