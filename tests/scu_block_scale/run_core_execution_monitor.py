#!/usr/bin/env python3
"""Observe a captured, unchanged A8/W8/D16 production Verilator model."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot', type=Path, required=True)
    parser.add_argument('--model', type=Path, required=True,
                        help='Captured obj_dir, mkSynthA8W8D16.v and capture.json')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--jobs', type=int, default=2)
    args = parser.parse_args()
    assert args.jobs > 0
    snapshot, model, out = (p.resolve() for p in (args.snapshot, args.model, args.out))
    capture = json.loads((model / 'capture.json').read_text())
    assert digest(model / 'mkSynthA8W8D16.v') == capture['rtl_sha256']
    for item in capture['model_files']:
        assert Path(item['name']).name == item['name']
        assert digest(model / 'obj_dir' / item['name']) == item['sha256']
    ffi = snapshot / 'source/sim/ffi'
    assert 'signed-scu-sat-v2' in (ffi / 'im2p_config.h').read_text()
    makefile = (model / 'obj_dir/VmkSynthA8W8D16.mk').read_text()
    runtime = Path(re.search(r'^VERILATOR_ROOT = (.+)$', makefile, re.M).group(1)) / 'include'
    assert (runtime / 'verilated.h').is_file()
    source = Path(__file__).with_name('core_execution_monitor.cpp').resolve()
    inputs = [source, Path(__file__).resolve(), model / 'capture.json',
              model / 'mkSynthA8W8D16.v', snapshot / 'identity.json',
              snapshot / 'source-sha256.json', ffi / 'im2p_verilator.cpp',
              ffi / 'im2p_verilator.h', ffi / 'im2p_config.h',
              *sorted((model / 'obj_dir').iterdir()),
              *[runtime / name for name in ('verilated.h', 'verilated.cpp',
                                             'verilated_threads.cpp', 'verilated.mk')]]
    hashes = {str(path): digest(path) for path in inputs if path.is_file()}
    out.mkdir(parents=True, exist_ok=False)
    shutil.copytree(model / 'obj_dir', out / 'obj_dir')
    shutil.copy2(source, out / source.name)
    shutil.copy2(__file__, out / Path(__file__).name)
    (out / 'input-sha256.json').write_text(json.dumps(hashes, indent=2) + '\n')
    commands = []

    def run(command, log, timeout):
        commands.append(command)
        (out / 'commands.json').write_text(json.dumps(commands, indent=2) + '\n')
        with (out / log).open('x') as stream:
            result = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT, timeout=timeout)
        assert result.returncode == 0, f'{log}: exit {result.returncode}'

    common = ['c++', '-std=c++20', '-O2', '-pthread', '-DIM2P_ACTIVATION_BITS=8',
              '-DIM2P_WEIGHT_BITS=8', '-DIM2P_DIM=16',
              f'-DIM2P_MONITOR_FFI="{ffi / "im2p_verilator.cpp"}"',
              '-I' + str(out / 'obj_dir'), '-I' + str(runtime), '-I' + str(runtime / 'vltstd')]
    error = None
    try:
        run([*common, '-c', str(out / source.name), '-o', str(out / 'monitor.o')],
            'compile.log', 180)
        run(['make', '-C', str(out / 'obj_dir'), '-f', 'VmkSynthA8W8D16.mk',
             'libVmkSynthA8W8D16', '-j', str(args.jobs)], 'model-build.log', 600)
        run([*common, str(out / 'monitor.o'), str(out / 'obj_dir/libVmkSynthA8W8D16.a'),
             str(out / 'obj_dir/libverilated.a'), '-o', str(out / 'monitor')], 'link.log', 180)
        run([str(out / 'monitor')], 'run.log', 120)
        text = (out / 'run.log').read_text()
        assert text.count('SCU_CORE_CASE_PASS ') == 6
        assert text.count('SCU_CORE_CONSUME ') == text.count('SCU_CORE_CAPTURE ') == 24
        assert text.count('SCU_CORE_ACC_WRITE ') == 24
        assert 'SCU_CORE_MONITOR_PASS cases=6 actual_vector_commits=24 actual_accumulator_writes=24' in text
        print(text, end='')
    except Exception as exc:
        error = str(exc)
        raise
    finally:
        changed = [path for path, expected in hashes.items() if digest(Path(path)) != expected]
        (out / 'summary.json').write_text(json.dumps({
            'status': 'PASS' if error is None and not changed else 'FAIL', 'error': error,
            'input_files': len(hashes), 'changed_inputs': changed,
            'rtl_sha256': capture['rtl_sha256'], 'numerical_revision': 'signed-scu-sat-v2',
            'cases': 6 if error is None else None, 'physical_jobs': 0,
            'executable_sha256': digest(out / 'monitor') if (out / 'monitor').exists() else None,
        }, indent=2) + '\n')
        assert not changed, 'frozen monitor inputs changed'


if __name__ == '__main__':
    main()
