#!/usr/bin/env python3
"""Build current mkScuPipeline and run its direct provider and optional UART4 tests.

This runs the synthesis provider/core RTL without a simulator library,
historical RTL, host reconstruction, or devices.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def utc():
    return datetime.now(timezone.utc).isoformat()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('out', type=Path)
    parser.add_argument('--jobs', type=int, default=2)
    parser.add_argument('--asserted', action='store_true')
    parser.add_argument('--plugin', action='store_true', help='also build the explicit RTL transport library')
    parser.add_argument('--uart4', action='store_true', help='also qualify the IFR4 shell over simulated UART bits')
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error('--jobs must be positive')
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    result = dict(status='RUNNING', started=utc(), source_root=str(ROOT),
                  top='mkScuPipeline', uart_bypassed=True,
                  physical_access_count=0, asserted=args.asserted, commands=[])

    def save():
        (out / 'result.json').write_text(json.dumps(result, indent=2) + '\n')

    def command(name, argv, cwd):
        argv = [str(value) for value in argv]
        record = dict(name=name, argv=argv, cwd=str(cwd), started=utc())
        result['commands'].append(record)
        save()
        start = time.monotonic()
        with (out / (name + '.stdout')).open('wb') as stdout, \
                (out / (name + '.stderr')).open('wb') as stderr:
            completed = subprocess.run(argv, cwd=cwd, stdout=stdout, stderr=stderr)
        record.update(ended=utc(), elapsed=time.monotonic() - start,
                      exit=completed.returncode)
        save()
        if completed.returncode:
            raise RuntimeError(f'{name}: exit {completed.returncode}; see {out / (name + ".stderr")}')

    try:
        files = sorted({p.relative_to(ROOT) for tree in ('src', 'synth')
                        for p in (ROOT / tree).rglob('*.bsv')})
        files += [Path('fpga/scu_block_scale') / name
                  for name in ('bounded_driver.cpp', 'build_bounded.py')]
        if args.plugin:
            files += [Path('fpga/scu_block_scale') / name
                      for name in ('rtl_plugin.cpp', 'rtl_plugin.hpp', 'plugin_driver.cpp')]
            files += [Path('sim/include/im2p_sim.h')]
        if args.uart4:
            files += [Path('fpga/scu_block_scale') / name for name in
                      ('scu_window_uart.sv', 'window_protocol.svh', 'window_protocol.hpp', 'window_packets.py',
                       'test_window_packets.py', 'window_uart_driver.cpp')]
            files += [Path('fpga/full_replay/uart.sv')]
        before = {str(name): digest(ROOT / name) for name in files}
        for name in files:
            target = out / 'source' / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, target)
            if digest(target) != before[str(name)]:
                raise RuntimeError(f'source changed while copying: {name}')
        (out / 'source-sha256.json').write_text(json.dumps(before, indent=2) + '\n')
        result['source_manifest_sha256'] = digest(out / 'source-sha256.json')
        for name in ('bsc', 'info', 'rtl', 'primitives', 'tmp'):
            (out / name).mkdir()
        os.environ.update(TMPDIR=str(out / 'tmp'), TMP=str(out / 'tmp'), TEMP=str(out / 'tmp'))
        bsc = shutil.which(os.environ.get('BSC', 'bsc'))
        verilator = shutil.which(os.environ.get('VERILATOR', 'verilator'))
        if not bsc or not verilator:
            raise RuntimeError('BSC and Verilator must be available in the selected host environment')
        prefix = Path(bsc).resolve().parents[1]
        candidates = [Path(os.environ['BSC_VERILOG'])] if os.environ.get('BSC_VERILOG') else []
        candidates += [prefix / 'libexec/lib/Verilog', prefix / 'lib/Verilog']
        primitives = next((p for p in candidates if (p / 'BRAM2.v').is_file()), None)
        if primitives is None:
            raise RuntimeError('BSC Verilog primitive directory unavailable')
        result['tools'] = {name: dict(path=str(Path(path).resolve()), sha256=digest(Path(path)))
                           for name, path in [('bsc', bsc), ('verilator', verilator)]}
        result['bsc_verilog'] = str(primitives.resolve())
        command('bsc-version', [bsc, '-v'], out)
        command('verilator-version', [verilator, '--version'], out)
        command('bsc-build', [bsc, '-u', '-verilog',
                *(['-check-assert'] if args.asserted else []),
                '-D', 'IM2P_POWER_OF_TWO_STRIDES', '-D', 'IM2P_BOUNDED_ONLY',
                '-p', '+:src/common:src/io:src/array:src/vector:src/accumulator:src/control:src/core:synth',
                '-steps', '4000000', '-steps-warn-interval', '1000000', '-steps-max-intervals', '20',
                '+RTS', '-K256M', '-RTS', '-bdir', out / 'bsc', '-info-dir', out / 'info',
                '-vdir', out / 'rtl', '-g', 'mkScuPipeline', 'synth/ScuPipeline.bsv'], out / 'source')
        for name in ('FIFO2.v', 'BRAM1.v', 'BRAM2.v', 'RegFile.v'):
            shutil.copy2(primitives / name, out / 'primitives' / name)
        generated = {str(p.relative_to(out)): digest(p) for tree in ('rtl', 'primitives')
                     for p in (out / tree).glob('*.v')}
        (out / 'generated-sha256.json').write_text(json.dumps(generated, indent=2) + '\n')
        command('verilator-build', [verilator, '--cc', '--exe', '--build', '-j', args.jobs,
                '--timing', '--assert', '--timescale', '1ns/1ps', '-Wno-fatal',
                '--top-module', 'mkScuPipeline', '--Mdir', out / 'obj_dir',
                '--output-split', '20000', '--output-split-cfuncs', '500',
                '-MAKEFLAGS', 'OPT_FAST=-O3 OPT_SLOW=-O3', '-CFLAGS', '-O3 -g0 -std=c++20',
                out / 'rtl/mkScuPipeline.v', *sorted((out / 'primitives').glob('*.v')),
                out / 'source/fpga/scu_block_scale/bounded_driver.cpp'], out)
        binary = out / 'obj_dir/VmkScuPipeline'
        result['binary'] = dict(path=str(binary), sha256=digest(binary), bytes=binary.stat().st_size)
        command('provider-test', [binary], out)
        output = (out / 'provider-test.stdout').read_text()
        if 'BOUNDED_PROVIDER_RTL_PASS' not in output:
            raise RuntimeError('provider completion marker missing')
        result['cases'] = [json.loads(line) for line in output.splitlines() if line.startswith('{')]
        if args.uart4:
            source = out / 'source/fpga/scu_block_scale'
            command('uart4-build', [verilator, '--cc', '--exe', '--build', '-j', args.jobs,
                    '--timing', '--assert', '--timescale', '1ns/1ps', '--public-flat-rw', '-Wno-fatal',
                    '--top-module', 'scu_window_uart_shell', '--Mdir', out / 'uart4_obj',
                    '--output-split', '20000', '--output-split-cfuncs', '500',
                    '-MAKEFLAGS', 'OPT_FAST=-O3 OPT_SLOW=-O3', '-CFLAGS', '-O3 -g0 -std=c++20',
                    '-I' + str(source), out / 'source/fpga/full_replay/uart.sv',
                    source / 'scu_window_uart.sv', out / 'rtl/mkScuPipeline.v',
                    *sorted((out / 'primitives').glob('*.v')), source / 'window_uart_driver.cpp'], out)
            uart4 = out / 'uart4_obj/Vscu_window_uart_shell'
            result['uart4'] = dict(path=str(uart4), sha256=digest(uart4), bytes=uart4.stat().st_size,
                                  uart_bypassed=False, physical_access_count=0)
            command('uart4-test', [sys.executable, source / 'test_window_packets.py',
                    '--binary', uart4, '--out', out / 'uart4-result.json'], out)
            if 'IFR4_UART_PROVIDER_PASS' not in (out / 'uart4-test.stdout').read_text():
                raise RuntimeError('UART4 completion marker missing')
            if digest(uart4) != result['uart4']['sha256']:
                raise RuntimeError('UART4 binary changed during execution')
        if args.plugin:
            identity = out / 'source/fpga/scu_block_scale/rtl_identity.hpp'
            identity.write_text('#pragma once\n#define IM2P_BSV_SHA256 "' +
                before['synth/ScuPipeline.bsv'] + '"\n#define IM2P_RTL_SHA256 "' +
                generated['rtl/mkScuPipeline.v'] + '"\n')
            plugin = out / 'plugin_obj/libim2p-scu-rtl.so'
            command('plugin-build', [verilator, '--cc', '--exe', '--build', '-j', args.jobs,
                    '--timing', '--assert', '--timescale', '1ns/1ps', '-Wno-fatal',
                    '--top-module', 'mkScuPipeline', '--Mdir', out / 'plugin_obj',
                    '--output-split', '20000', '--output-split-cfuncs', '500',
                    '-MAKEFLAGS', 'OPT_FAST=-O3 OPT_SLOW=-O3',
                    '-CFLAGS', '-O3 -g0 -fPIC -std=c++20 -I' + str(out / 'source/sim/include'),
                    '-LDFLAGS', '-shared', '-o', plugin,
                    out / 'rtl/mkScuPipeline.v', *sorted((out / 'primitives').glob('*.v')),
                    out / 'source/fpga/scu_block_scale/rtl_plugin.cpp'], out)
            compiler = shutil.which(os.environ.get('CXX', 'g++'))
            if not compiler:
                raise RuntimeError('C++ compiler unavailable')
            command('plugin-test-build', [compiler, '-O2', '-std=c++20',
                    '-I', out / 'source/sim/include', '-o', out / 'plugin_driver',
                    out / 'source/fpga/scu_block_scale/plugin_driver.cpp', '-ldl'], out)
            command('plugin-test', [out / 'plugin_driver', plugin], out)
            if 'PROVIDER_PLUGIN_RTL_PASS' not in (out / 'plugin-test.stdout').read_text():
                raise RuntimeError('plugin completion marker missing')
            result['plugin'] = dict(path=str(plugin), sha256=digest(plugin), bytes=plugin.stat().st_size)
        after = {str(name): digest(ROOT / name) for name in files}
        (out / 'source-after-sha256.json').write_text(json.dumps(after, indent=2) + '\n')
        # Builds and tests consume the captured source, not the mutable worktree.
        # Preserve its identity while allowing concurrent implementation work.
        result['worktree_changes_during_test'] = [name for name in before if after[name] != before[name]]
        result['tested_source'] = str(out / 'source')
        if any(digest(out / 'source' / name) != value for name, value in before.items()):
            raise RuntimeError('captured source changed during build/test')
        if any(digest(out / name) != value for name, value in generated.items()):
            raise RuntimeError('generated RTL changed during test')
        if digest(binary) != result['binary']['sha256']:
            raise RuntimeError('test binary changed during execution')
        result.update(status='PASS', source_preservation='PASS', source_scope='captured_source_manifest')
    except Exception as error:
        result.update(status='FAIL', first_error=str(error))
        raise
    finally:
        result['ended'] = utc()
        save()
    print('BOUNDED_PROVIDER_BUILD_PASS ' + str(out))


if __name__ == '__main__':
    main()
