#!/usr/bin/env python3
"""Actual ggml failure/ownership regression over PTY; no numerical FPGA claim."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import select
import struct
import subprocess
import time
import zlib

from test_stream_uart import rd, reply, send


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def execute(host, out, reduction, pipeline, expected=None):
    out.mkdir()
    master, slave = os.openpty()
    environment = dict(os.environ, IM2P_FPGA_DEVICE=os.ttyname(slave),
                       IM2P_FPGA_TIMEOUT_SECONDS='10')
    for name in ('IM2P_FPGA_TEST_FAIL_AFTER_STRIPE', 'IM2P_FPGA_TEST_WAIT_FIRST_READ',
                 'IM2P_FPGA_TEST_PRODUCER_OVERLAP'):
        environment.pop(name, None)
    if pipeline:
        environment['IM2P_FPGA_TEST_FAIL_AFTER_STRIPE'] = '1'
    command = [str(host), 'failure', '321', '48', str(reduction),
               'STRIPE_PIPELINE' if pipeline else 'FULL']
    (out / 'command.json').write_text(json.dumps({'command': command,
        'device': environment['IM2P_FPGA_DEVICE'], 'physical_device': False,
        'fail_after_stripe': 1 if pipeline else None}, indent=2) + '\n')
    transactions = []
    full_input = None
    with (out / 'host.log').open('x') as log:
        process = subprocess.Popen(command, env=environment, stdout=log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 25
        try:
            while process.poll() is None or select.select([master], [], [], 0)[0]:
                if time.monotonic() > deadline:
                    raise TimeoutError('actual host did not finish failure path')
                if not select.select([master], [], [], 0.05)[0]:
                    continue
                header = rd(master, 32)
                length = struct.unpack_from('<I', header, 28)[0]
                assert length <= 65536, 'unbounded request'
                frame = header + rd(master, length + 4)
                assert header[:5] == b'IFR2\x02' and header[6:8] == b'\x10\x08'
                assert zlib.crc32(frame[:-4]) == struct.unpack('<I', frame[-4:])[0]
                operation = header[5]
                run, generation = struct.unpack_from('<QI', header, 8)
                bounds = struct.unpack_from('<4H', header, 20)
                payload = frame[32:-4]
                item = {'operation': operation, 'run': run, 'generation': generation,
                        'bounds': bounds, 'bytes': len(frame), 'received_ns': time.monotonic_ns()}
                index = len(transactions)
                (out / f'{index:02d}-request.bin').write_bytes(frame)
                if operation == 0:
                    assert index == 0 and (run, generation) == (0, 0)
                    response = reply(0, (336, 48, 96), 0, 0)
                else:
                    assert (run, generation) == (1, 1)
                    shape = (321, 48, reduction)
                    if not pipeline:
                        assert index == 1 and operation == 1 and bounds == (*shape, 0)
                        assert len(payload) == 321 * 128 + reduction * 64
                        assert any(payload[:321 * 128]), 'vacuous captured activation'
                        full_input = payload
                        # Fail before output callbacks. This is input capture only.
                        response = bytearray(reply(1, shape, 1, 1))
                        response[5] = 3
                        response[-4:] = struct.pack('<I', zlib.crc32(response[:-4]))
                    elif operation == 4:
                        assert index == 1 and bounds == (*shape, 160)
                        assert payload == expected[321 * 128:], 'FULL/PIPELINE W differs'
                        response = reply(4, shape, 1, 1)
                    elif operation == 5:
                        identifier = bounds[2]
                        assert identifier == sum(t['operation'] == 5 for t in transactions)
                        assert identifier in (0, 1) and bounds == (identifier * 160, 160, identifier, identifier)
                        begin = identifier * 160 * 128
                        assert payload == expected[begin:begin + 160 * 128], 'accepted A backing changed after producer failure'
                        assert any(payload), 'accepted A became all zero'
                        if identifier == 0:
                            # Hold the worker's first publication ACK. Producer can
                            # accept event1 and fail/zero its own backing while the
                            # worker has not copied event1 into the transport yet.
                            time.sleep(0.5)
                            item['injected_ack_delay_seconds'] = 0.5
                        response = reply(5, shape, 1, 1, idrow=(identifier, identifier * 160, 160))
                    elif operation == 6:
                        assert bounds == (0, 0, 0, 0) and not payload
                        response = reply(6, shape, 1, 1)
                    else:
                        raise AssertionError(f'unexpected recovery/finish/release opcode {operation}')
                (out / f'{index:02d}-response.bin').write_bytes(response)
                transactions.append(item)
                send(master, response)
            process.wait(timeout=3)
            assert process.returncode == 0, (out / 'host.log').read_text()
            output = (out / 'host.log').read_text()
            assert 'FPGA_UART_GGML_FAILURE PASS' in output and 'output_preserved=1' in output
            assert 'simulator_creates=0 simulator_executes=0' in output
            assert 'FPGA_UART_GGML_NUMERICAL' not in output
            assert not any(t['operation'] in (2, 3, 7) for t in transactions)
            if pipeline:
                assert sum(t['operation'] == 5 for t in transactions) == 2
                assert 'FPGA_UART_FAIL existing ExSIA quantizer failed' in output
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            os.close(master)
            os.close(slave)
            (out / 'transactions.json').write_text(json.dumps(transactions, indent=2) + '\n')
    return full_input, {'mode': 'PIPELINE' if pipeline else 'FULL input capture', 'k': reduction,
        'transactions': len(transactions), 'publications': sum(t['operation'] == 5 for t in transactions),
        'accepted_A_bytes_checked': 320 * 128 if pipeline else 0,
        'output_preserved': True, 'simulator_calls': 0, 'mock_protocol_only': True}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    host = args.host.resolve()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    identities = {str(p): digest(p) for p in (host, Path(__file__).resolve(),
                  Path(__file__).with_name('test_stream_uart.py').resolve())}
    (out / 'identity.json').write_text(json.dumps(identities, indent=2) + '\n')
    cases = []
    for reduction in (64, 96):
        payload, result = execute(host, out / f'full-k{reduction}', reduction, False)
        cases.append(result)
        _, result = execute(host, out / f'failure-k{reduction}', reduction, True, payload)
        cases.append(result)
    assert all(digest(Path(p)) == identity for p, identity in identities.items())
    summary = {'status': 'PASS', 'cases': cases, 'physical_device': False,
        'numerical_fpga_validation': False, 'all_inputs_preserved': True,
        'limitation': 'ACK delay exercises the race; original producer zero-fill is established by pinned source, not direct memory instrumentation.'}
    (out / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary), flush=True)


if __name__ == '__main__':
    main()
