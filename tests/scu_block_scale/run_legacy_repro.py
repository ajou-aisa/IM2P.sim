#!/usr/bin/env python3
"""Observe the preserved frontend with its real RTL simulator; never open a device."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    snapshot, out = args.snapshot.resolve(), args.out.resolve()
    identity = json.loads((snapshot / 'identity.json').read_text())
    archive = Path(identity['sim_archive'])
    assert digest(archive) == identity['sim_archive_sha256'], 'simulator archive identity changed'
    assert digest(snapshot / 'integration-sha256.json') == identity['source_sha256']
    out.mkdir(parents=True, exist_ok=False)
    source = Path(__file__).with_name('legacy_h1_repro.cpp').resolve()
    build = snapshot / 'host-build'
    flags = {}
    for line in (build / 'CMakeFiles/persistent_replay.dir/flags.make').read_text().splitlines():
        if ' = ' in line:
            key, value = line.split(' = ', 1)
            flags[key] = shlex.split(value)
    libraries = [build / 'host-build/ggml/src/ggml-gemmini/libggml-gemmini.a',
                 build / 'host-build/ggml/src/libggml-base.a', archive,
                 build / 'host-build/ggml/src/ggml-gemmini-utils/libggml-gemmini-utils.a']
    inputs = [Path(__file__).resolve(), source, snapshot / 'identity.json',
              snapshot / 'integration-sha256.json',
              snapshot / 'source/frontend/src/im2p_gemmini_frontend.cpp',
              snapshot / 'source/frontend/include/im2p_gemmini_frontend.hpp',
              snapshot / 'source/sim/include/im2p_sim.h',
              snapshot / 'host/ggml/src/ggml-gemmini/ggml-gemmini-args.h',
              build / 'CMakeFiles/persistent_replay.dir/flags.make', *libraries]
    identities = {str(path): digest(path) for path in inputs}
    (out / 'inputs-sha256.json').write_text(json.dumps(identities, indent=2) + '\n')
    command = ['c++', '-std=c++20', '-O2', *flags['CXX_DEFINES'], *flags['CXX_INCLUDES'],
               str(source), '-o', str(out / 'legacy_h1_repro'), '-Wl,--start-group',
               *map(str, libraries), '-Wl,--end-group', '-pthread', '-ldl', '-lm']
    (out / 'build-command.json').write_text(json.dumps(command, indent=2) + '\n')
    with (out / 'build.log').open('x') as log:
        subprocess.run(command, check=True, stdout=log, stderr=subprocess.STDOUT)
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(('IM2P_FPGA_', 'IM2P_REPLAY_'))}
    results = []
    for name, arguments, expected_exit in [('legacy-positive', ['--legacy'], 0),
                                           ('new-target-negative', [], 2)]:
        command = [str(out / 'legacy_h1_repro'), *arguments]
        (out / f'{name}-command.json').write_text(json.dumps(command) + '\n')
        with (out / f'{name}.log').open('x') as log:
            result = subprocess.run(command, env=environment, stdout=log,
                                    stderr=subprocess.STDOUT, timeout=60)
        text = (out / f'{name}.log').read_text()
        assert result.returncode == expected_exit and 'LEGACY_H1_PASS ' in text, text
        if expected_exit:
            assert 'SCU_TARGET_FAILURE expected_final_signed32=8224' in text, text
        results.append({'case': name, 'exit_code': result.returncode,
                        'expected_exit_code': expected_exit, 'logical_invocations': 1})
        print(text, end='')
    assert all(digest(Path(path)) == wanted for path, wanted in identities.items())
    summary = {'status': 'REPRODUCED', 'target_correctness': 'FAIL', 'legacy_correctness': 'PASS',
               'cases': results, 'actual_rtl_simulator_invocations': 2, 'physical_board_jobs': 0,
               'scu_internal_consumption': 'not_observed', 'preserved_inputs': len(identities),
               'executable_sha256': digest(out / 'legacy_h1_repro')}
    (out / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary))


if __name__ == '__main__':
    main()
