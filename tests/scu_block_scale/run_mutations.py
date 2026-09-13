#!/usr/bin/env python3
"""Run bounded fault-injection and isolated frontend source mutations on real RTL."""
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
    args = parser.parse_args()
    snapshot, out = args.snapshot.resolve(), args.out.resolve()
    build = snapshot / 'host-build'
    target = build / 'CMakeFiles/scu_frontend_final.dir'
    flags = dict(line.split(' = ', 1) for line in (target / 'flags.make').read_text().splitlines() if ' = ' in line)
    libraries = [(build / word).resolve() for word in shlex.split((target / 'link.txt').read_text()) if word.endswith('.a')]
    frontend = snapshot / 'source/frontend/src/im2p_gemmini_frontend.cpp'
    fixture = snapshot / 'source/tests/scu_block_scale/frontend_final_integer.cpp'
    source = Path(__file__).with_name('mutation_sensitivity.cpp').resolve()
    inputs = [source, Path(__file__).resolve(), frontend, fixture, snapshot / 'integration-sha256.json',
              target / 'flags.make', target / 'link.txt', *libraries]
    hashes = {str(path): digest(path) for path in inputs}
    out.mkdir(parents=True, exist_ok=False)
    for path in (source, Path(__file__).resolve(), fixture):
        shutil.copy2(path, out / path.name)
    (out / 'input-sha256.json').write_text(json.dumps(hashes, indent=2) + '\n')
    commands, samples = [], []

    def run(command, name, expected=0):
        commands.append(command)
        (out / 'commands.json').write_text(json.dumps(commands, indent=2) + '\n')
        with (out / (name + '.log')).open('x') as log:
            result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=180)
        text = (out / (name + '.log')).read_text()
        samples.append({'name': name, 'actual_exit': result.returncode, 'expected_exit': expected})
        (out / 'samples.json').write_text(json.dumps(samples, indent=2) + '\n')
        assert result.returncode == expected, f'{name}: exit {result.returncode}, expected {expected}'
        return text

    common = ['c++', '-std=c++20', '-O2', '-pthread', *shlex.split(flags['CXX_DEFINES']),
              *shlex.split(flags['CXX_INCLUDES'])]
    error = None
    try:
        run([*common, f'-DIM2P_MUTATION_FIXTURE="{out / fixture.name}"', '-c', str(out / source.name),
             '-o', str(out / 'driver.o')], 'compile-driver')
        run([*common, str(out / 'driver.o'), '-Wl,--start-group', *map(str, libraries),
             '-Wl,--end-group', '-ldl', '-lm', '-o', str(out / 'test')], 'link-driver')
        for name in ('control', 'control_wide', 'identity', 'truncate8', 'truncate16',
                     'bypass', 'external', 'callback_block'):
            expected = 0 if name.startswith('control') else 1
            text = run([str(out / 'test'), name], name, expected)
            assert f'SCU_MUTATION_ACTUAL_RTL case={name} ' in text
            assert f'SCU_MUTATION_EXECUTION case={name} executor_calls=1 ' in text
            marker = 'SCU_MUTATION_UNCHANGED_PASS' if expected == 0 else 'SCU_MUTATION_DETECTED'
            assert f'{marker} case={name}' in text and 'SCU_MUTATION_TEST_ERROR' not in text
        # This is a real isolated source mutation, not a provider/descriptor proxy.
        original = frontend.read_text()
        token = 'double(values[n]) * double(shared_channel_scales[column + n]) *'
        assert original.count(token) == 1, 'shared-scale mutation site changed'
        mutant = original.replace(token, token + ' double(shared_channel_scales[column + n]) *')
        (out / 'frontend-shared-twice.cpp').write_text(mutant)
        (out / 'source-mutation.json').write_text(json.dumps({
            'source': str(frontend), 'original_sha256': digest(frontend),
            'mutation': 'multiply final SCU result by shared channel S twice',
            'mutant_sha256': digest(out / 'frontend-shared-twice.cpp'),
            'replacement_count': 1, 'production_source_modified': False,
        }, indent=2) + '\n')
        run([*common, '-DIM2P_GEMMINI_FRONTEND_EXPECTED_DIM=16',
             '-DIM2P_GEMMINI_FRONTEND_ACTIVATION_BITS=8', '-c', str(out / 'frontend-shared-twice.cpp'),
             '-o', str(out / 'frontend-shared-twice.o')], 'compile-shared-twice')
        others = [path for path in libraries if path.name != 'libim2p_gemmini_frontend.a']
        assert len(others) + 1 == len(libraries)
        run([*common, str(out / 'driver.o'), str(out / 'frontend-shared-twice.o'), '-Wl,--start-group',
             *map(str, others), '-Wl,--end-group', '-ldl', '-lm', '-o', str(out / 'test-shared-twice')],
            'link-shared-twice')
        text = run([str(out / 'test-shared-twice'), 'shared_twice'], 'shared_twice', 1)
        assert 'SCU_MUTATION_ACTUAL_RTL case=shared_twice ' in text
        assert 'SCU_MUTATION_DETECTED case=shared_twice checker=raw_or_fout_exact' in text
        assert 'SCU_MUTATION_TEST_ERROR' not in text
    except Exception as exc:
        error = str(exc)
        raise
    finally:
        changed = [path for path, expected in hashes.items() if digest(Path(path)) != expected]
        (out / 'summary.json').write_text(json.dumps({
            'status': 'PASS' if error is None and not changed else 'FAIL', 'error': error,
            'controls': 2 if error is None else None,
            'descriptor_or_provider_faults_detected': 6 if error is None else None,
            'isolated_frontend_source_mutants_detected': 1 if error is None else None,
            'rtl_source_mutations': 'NOT RUN: separate from descriptor/provider boundary injection',
            'inputs': len(hashes), 'changed_inputs': changed, 'physical_jobs': 0,
        }, indent=2) + '\n')
        assert not changed, 'frozen production inputs changed'
    print('SCU_MUTATION_SUITE_PASS controls=2 injected_faults=6 frontend_source_mutants=1 physical_jobs=0')


if __name__ == '__main__':
    main()
