#!/usr/bin/env python3
"""Reject a mock IFR3 CAP in ordinary PPL, with and without its default warmup."""
import argparse
import json
import os
from pathlib import Path
import pty
import select
import struct
import subprocess

from test_standard_fail_closed import corrected_crc, load_helper, pins, sha


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('ppl', 'library', 'model', 'text', 'reference', 'out'):
        parser.add_argument('--' + name, required=True, type=Path)
    args = parser.parse_args()
    for name in ('ppl', 'library', 'model', 'text', 'reference'):
        setattr(args, name, getattr(args, name).resolve(strict=True))
    fields, _ = pins(args.reference)
    helper = load_helper()
    args.out.mkdir(parents=True, exist_ok=False)
    results = []
    for warmup in (False, True):
        out = args.out / ('default-warmup' if warmup else 'no-warmup')
        out.mkdir()
        master, slave = pty.openpty()
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(('IM2P_FPGA_', 'LLAMA_ARG_', 'GEMMINI_MATMUL_'))}
        env.update(IM2P_FPGA_DEVICE=os.ttyname(slave),
                   IM2P_FPGA_FULL_REFERENCE=str(args.reference),
                   IM2P_FPGA_HARDWARE_SHA256=fields['hardware_sha256'],
                   IM2P_FPGA_PRODUCTION_RTL_SHA256=fields['production_rtl_sha256'],
                   IM2P_FPGA_REQUIRE_COMPLETION='1', IM2P_FPGA_TIMEOUT_SECONDS='3',
                   GEMMINI_MATMUL_MODE='STRIPE_PIPELINE', GGML_BACKEND_PATH=str(args.library))
        command = [str(args.ppl), '-m', str(args.model), '--device', 'GEMMINI', '-ngl', '99',
                   '--file', str(args.text), '--chunks', '1', '--ctx-size', '32',
                   '--batch-size', '16', '--ubatch-size', '16', '--threads', '1']
        if not warmup:
            command.append('--no-warmup')
        result = {'argv': command, 'default_warmup': warmup, 'mock_only': True,
                  'expected_negative': True, 'physical_device_access': False, 'pass': False}
        process = None
        try:
            with (out / 'host.log').open('x') as log:
                process = subprocess.Popen(command, env=env, cwd=out,
                                           stdout=log, stderr=subprocess.STDOUT)
                request = helper.recv(master, 0, 0, 0)
                (out / 'request-cap.bin').write_bytes(request)
                cap = bytearray(helper.reply(0, (336, 48, 96), 0, 5))
                struct.pack_into('<H', cap, 30, 0x0214)
                response = corrected_crc(cap)
                (out / 'mock-cap-negative.bin').write_bytes(response)
                helper.send(master, response)
                process.wait(timeout=30)
            output = (out / 'host.log').read_text()
            remaining = bytearray()
            while select.select([master], [], [], 0)[0]:
                remaining.extend(os.read(master, 4096))
            (out / 'commands-after-failure.bin').write_bytes(remaining)
            result.update(exit=process.returncode, bytes_after_failure=len(remaining),
                          assigned_markers=output.count('FPGA_UART_ASSIGN'),
                          warmup_fail_marker='FPGA_UART_WARMUP_FAIL phase=decode' in output)
            assert process.returncode != 0, 'PPL accepted failed FPGA invocation'
            assert 'IFR3 semantic capability mismatch' in output, 'wrong failure gate'
            assert result['assigned_markers'] == 1, 'warmup continued to another invocation'
            assert result['warmup_fail_marker'] == warmup, 'wrong warmup error path'
            assert not remaining, 'device command after failure'
            result['pass'] = True
        except Exception as error:
            result['error'] = str(error)
        finally:
            if process is not None and process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
                result['local_test_child_terminated'] = True
            os.close(master)
            os.close(slave)
            (out / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
        results.append(result)
        (args.out / 'results.json').write_text(json.dumps({
            'inputs': {str(getattr(args, name)): sha(getattr(args, name))
                       for name in ('ppl', 'library', 'model', 'text', 'reference')},
            'cases': results, 'pass': len(results) == 2 and all(row['pass'] for row in results),
        }, indent=2) + '\n')
        print(out.name, 'EXPECTED_NEGATIVE_PASS' if result['pass'] else 'UNEXPECTED_FAILURE')
        if not result['pass']:
            raise SystemExit(1)


if __name__ == '__main__':
    main()
