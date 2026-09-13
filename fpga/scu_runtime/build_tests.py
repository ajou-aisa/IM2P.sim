#!/usr/bin/env python3
"""Compile observers against the ordinary distribution's libraries only."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser()
    for name in ('host', 'core', 'params', 'build', 'out'):
        parser.add_argument('--' + name, type=Path, required=True)
    a = parser.parse_args()
    a.out.mkdir(parents=True, exist_ok=False)
    library = a.build / 'bin'
    includes = [a.host / 'ggml/include', a.host / 'ggml/src',
                a.host / 'ggml/src/ggml-gemmini', a.host / 'ggml/src/ggml-gemmini-utils/include',
                a.build / 'generated', a.params, a.core / 'sim/include',
                a.core / 'frontend/include', a.core / 'fpga/scu_block_scale']
    common = [os.environ.get('CXX', 'c++'), '-std=c++20', '-O2', '-fno-fast-math',
              *['-I' + str(x) for x in includes]]
    here = Path(__file__).resolve().parent / 'tests'
    records = []
    for name in ('runtime_probe', 'graph_dispatch', 'simulator_audit'):
        command = common + [str(here / (name + '.cpp'))]
        if name == 'simulator_audit':
            output = a.out / (name + '.so')
            command += ['-shared', '-fPIC', '-ldl']
        else:
            output = a.out / name
            command += ['-L' + str(library), '-Wl,-rpath,' + str(library),
                        '-Wl,--export-dynamic', '-lggml', '-lggml-base',
                        '-lggml-gemmini-utils', '-ldl', '-pthread']
        command += ['-o', str(output)]
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        (a.out / (name + '.log')).write_bytes(result.stdout)
        record = {'argv': command, 'exit': result.returncode, 'source': str(here / (name + '.cpp'))}
        if output.exists():
            record['sha256'] = hashlib.sha256(output.read_bytes()).hexdigest()
        records.append(record)
        (a.out / 'commands.json').write_text(json.dumps(records, indent=2) + '\n')
        if result.returncode:
            raise SystemExit(f'{name} failed; see preserved log')
    print('standard-library observers compiled; no backend source/archive injected')


if __name__ == '__main__':
    main()
