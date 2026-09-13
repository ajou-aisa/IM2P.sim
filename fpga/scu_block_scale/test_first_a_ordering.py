#!/usr/bin/env python3
"""Deterministic first-A publication interleaving on isolated UART source copies."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pty
import select
import shutil
import subprocess
import sys

sys.dont_write_bytecode = True
ROWS = '        last_first_activation_published_rows.store(get(response, 120, 8));'
CYCLE = '        last_first_activation_cycle.store(get(response, 112, 8));'
COMMENT = '        // A nonzero cycle publishes the matching row observation to the producer.'
FIXED = '\n'.join((ROWS, COMMENT, CYCLE))
OBSERVER = r'''
        uint64_t observed_cycle = 0, observed_rows = 0;
        // Only this isolated test pauses transfer between the two stores.
        std::thread observer([&] {
            observed_cycle = last_first_activation_cycle.load();
            observed_rows = last_first_activation_published_rows.load();
        });
        observer.join();
        std::cout << "FIRST_A_INTERLEAVE cycle=" << observed_cycle
                  << " rows=" << observed_rows
                  << " response_cycle=" << get(response, 112, 8)
                  << " response_rows=" << get(response, 120, 8) << std::endl;
        require(observed_cycle == 0 || observed_rows != 0, "FIRST_A_ORDERING_STALE_ROWS");
'''
TAIL = r'''
 ck(u.begin(&d,sr)==0,"begin");
 im2p_activation_stripe_t s{};s.abi_version=5;
 s.activation_bits=s.weight_bits=8;s.activation_storage_bytes=s.weight_storage_bytes=1;
 s.dim=16;s.stripe_id=0;s.i_start=0;s.rows=sr;s.activations=a.data();
 s.activation_row_stride_bytes=k;s.context=0x12345678;
 const auto published=u.publish(&s);
 ck(published==0,u.error().c_str());
 ck(u.last_first_activation_cycle.load()==101&&u.last_first_activation_published_rows.load()==sr,
    "final first-A observation");
 std::cout<<"FIRST_A_ORDERING_PASS cycle=101 rows=16 mock_framing_only=1\n";
 }catch(const std::exception&e){std::cerr<<"FIRST_A_ORDERING_FAIL "<<e.what()<<'\n';return 1;}}
'''


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    snapshot, out = args.snapshot.resolve(), args.out.resolve()
    source = snapshot / 'source'
    uart = source / 'fpga/scu_block_scale/uart.cpp'
    helper = uart.with_name('test_uart.py')
    manifest = snapshot / 'integration-sha256.json'
    selected = [uart, helper, uart.with_name('uart.hpp'), source / 'sim/include/im2p_sim.h']
    frozen = json.loads(manifest.read_text())
    assert all(digest(p) == frozen[str(p.relative_to(snapshot))] for p in selected)
    inputs = {str(p): digest(p) for p in [*selected, manifest, Path(__file__).resolve()]}
    original = uart.read_text()
    assert original.count(FIXED) == 1, 'reviewed stores missing or ambiguous'
    spec = importlib.util.spec_from_file_location('frozen_ifr3_pty', helper)
    wire = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wire)
    prefix = wire.CPP[:wire.CPP.index(' ck(u.begin(&d,sr)')]
    out.mkdir(parents=True, exist_ok=False)
    shutil.copy2(__file__, out / Path(__file__).name)
    (out / 'input-sha256.json').write_text(json.dumps(inputs, indent=2) + '\n')
    (out / 'test.cpp').write_text(prefix + TAIL)
    records = []
    try:
        for name, stores, expected_exit in (
                ('cycle-first-mutant', (CYCLE, ROWS), 1),
                ('reviewed-rows-first', (ROWS, CYCLE), 0)):
            folder = out / name
            folder.mkdir()
            changed = '#include <iostream>\n#include <thread>\n' + original.replace(
                FIXED, stores[0] + OBSERVER + stores[1])
            test_uart = folder / 'uart.cpp'
            test_uart.write_text(changed)
            exe = folder / 'test'
            command = ['c++', '-std=c++20', '-O2', '-pthread',
                       '-I' + str(uart.parent), '-I' + str(source / 'sim/include'),
                       str(out / 'test.cpp'), str(test_uart), '-o', str(exe)]
            (folder / 'command.json').write_text(json.dumps(command, indent=2) + '\n')
            with (folder / 'build.log').open('x') as log:
                subprocess.run(command, check=True, stdout=log, stderr=subprocess.STDOUT)
            master, slave = pty.openpty()
            host_command = [str(exe), os.ttyname(slave), 'success']
            (folder / 'run-command.json').write_text(json.dumps(host_command) + '\n')
            requests, responses = [], []
            with (folder / 'run.log').open('x') as log:
                process = subprocess.Popen(host_command, stdout=log, stderr=subprocess.STDOUT)
                try:
                    for op, response in ((0, wire.reply(0, (336, 48, 96), 0, 5)),
                                         (4, wire.reply(4, (33, 19, 64))),
                                         (5, wire.reply(5, (33, 19, 64), idrow=(0, 0, 16),
                                                        first=101, firstrows=16))):
                        request = wire.recv(master, op, 0 if op == 0 else 1,
                                            0 if op == 0 else 6)
                        requests.append(request)
                        responses.append(response)
                        wire.send(master, response)
                    actual_exit = process.wait(timeout=10)
                    assert actual_exit == expected_exit, f'{name}: exit {actual_exit}'
                    assert not select.select([master], [], [], 0)[0], 'unexpected next command'
                finally:
                    os.close(master)
                    os.close(slave)
                    (folder / 'requests.bin').write_bytes(b''.join(requests))
                    (folder / 'responses.bin').write_bytes(b''.join(responses))
            text = (folder / 'run.log').read_text()
            assert text.count('FIRST_A_INTERLEAVE ') == 3
            if expected_exit:
                assert 'cycle=101 rows=0 response_cycle=101 response_rows=16' in text
                assert 'FIRST_A_ORDERING_FAIL FIRST_A_ORDERING_STALE_ROWS' in text
                assert 'FIRST_A_ORDERING_PASS' not in text
            else:
                assert 'cycle=0 rows=16 response_cycle=101 response_rows=16' in text
                assert 'FIRST_A_ORDERING_PASS cycle=101 rows=16' in text
            records.append({'variant': name, 'actual_exit': actual_exit, 'expected_exit': expected_exit,
                            'source_sha256': digest(test_uart), 'executable_sha256': digest(exe),
                            'operations': [0, 4, 5], 'commands_after_observation': 0,
                            'request_bytes': sum(map(len, requests)),
                            'response_bytes': sum(map(len, responses)),
                            'response_sha256': digest(folder / 'responses.bin')})
            print(text, end='', flush=True)
        assert records[0]['response_sha256'] == records[1]['response_sha256']
    finally:
        changed_inputs = [p for p, h in inputs.items() if digest(Path(p)) != h]
        status = {'pass': len(records) == 2 and not changed_inputs, 'samples': records,
                  'changed_inputs': changed_inputs, 'mock_framing_only': True,
                  'physical_devices': 0, 'rtl_numerical_jobs': 0,
                  'scope': 'Observer thread runs between isolated stores; not a performance or natural-race sample.'}
        (out / 'status.json').write_text(json.dumps(status, indent=2) + '\n')
    assert status['pass']
    print('FIRST_A_ORDERING_SUITE PASS mutant_exit=1 reviewed_exit=0 physical_devices=0')


if __name__ == '__main__':
    main()
