#!/usr/bin/env python3
"""Link an explicitly captured test against a frozen real frontend/simulator."""
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import shutil
import subprocess


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--cases', type=Path)
    parser.add_argument('--pipeline', action='store_true')
    args = parser.parse_args()
    snapshot, out = args.snapshot.resolve(), args.out.resolve()
    build = snapshot / 'host-build'
    target = build / 'CMakeFiles/scu_frontend_final.dir'
    flags = dict(line.split(' = ', 1) for line in (target / 'flags.make').read_text().splitlines() if ' = ' in line)
    link = shlex.split((target / 'link.txt').read_text())
    libraries = [(build / word).resolve() for word in link if word.endswith('.a')]
    out.mkdir(parents=True, exist_ok=False)
    if not args.pipeline and args.cases is None:
        parser.error('--cases is required for FULL scalar fixtures')
    source = Path(__file__).with_name('pipeline_final_integer.cpp' if args.pipeline else 'frontend_final_integer.cpp')
    capture = [source, Path(__file__)] + ([args.cases] if args.cases else [])
    for path in capture:
        shutil.copy2(path, out / path.name)
    identities = {str(path): digest(path) for path in [*capture,
                  target / 'flags.make', target / 'link.txt', snapshot / 'integration-sha256.json', *libraries]}
    (out / 'input-sha256.json').write_text(json.dumps(identities, indent=2) + '\n')
    command = ['c++', '-std=c++20', '-O2', *shlex.split(flags['CXX_DEFINES']),
               *shlex.split(flags['CXX_INCLUDES']), str(out / source.name), '-o', str(out / 'test'),
               '-Wl,--start-group', *map(str, libraries), '-Wl,--end-group', '-pthread', '-ldl', '-lm']
    if args.pipeline:
        command += ['-Wl,--wrap=im2p_execute_matmul_extended', '-Wl,--wrap=im2p_begin_striped_matmul']
    (out / 'build-command.json').write_text(json.dumps(command) + '\n')
    with (out / 'build.log').open('x') as log:
        subprocess.run(command, check=True, stdout=log, stderr=subprocess.STDOUT)
    command = [str(out / 'test')] + ([str(out / args.cases.name)] if args.cases else [])
    (out / 'run-command.json').write_text(json.dumps(command) + '\n')
    with (out / 'run.log').open('x') as log:
        result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=120)
    (out / 'status.json').write_text(json.dumps({'exit': result.returncode,
                                    'executable_sha256': digest(out / 'test')}) + '\n')
    assert all(digest(Path(path)) == expected for path, expected in identities.items())
    text = (out / 'run.log').read_text()
    print(text, end='')
    marker = 'SCU_PIPELINE_PASS ' if args.pipeline else 'SCU_FRONTEND_PASS '
    assert result.returncode == 0 and marker in text, 'numerical test failed'


if __name__ == '__main__':
    main()
