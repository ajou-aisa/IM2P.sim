#!/usr/bin/env python3
"""Mutate one lowered RTL equation in an isolated generated-model copy."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--positive', type=Path, required=True,
                        help='Successful run_core_execution_monitor output directory')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--jobs', type=int, default=2)
    args = parser.parse_args()
    positive, out = args.positive.resolve(), args.out.resolve()
    assert args.jobs > 0
    assert json.loads((positive / 'summary.json').read_text())['status'] == 'PASS'
    inputs = [path for path in positive.rglob('*') if path.is_file()]
    hashes = {str(path): digest(path) for path in inputs}
    out.mkdir(parents=True, exist_ok=False)
    shutil.copytree(positive / 'obj_dir', out / 'obj_dir')
    shutil.copy2(positive / 'monitor.o', out / 'monitor.o')
    shutil.copy2(positive / 'core_execution_monitor.cpp', out / 'core_execution_monitor.cpp')
    shutil.copy2(__file__, out / Path(__file__).name)
    (out / 'input-sha256.json').write_text(json.dumps(hashes, indent=2) + '\n')
    target = out / 'obj_dir/VmkSynthA8W8D16___024root__1.cpp'
    original = target.read_text()
    start = original.index('if (vlSelfRef.mkSynthA8W8D16__DOT__core_core_matrixFragmentAccumulateReg__024EN) {')
    stop = original.index('core_core_matrixFragmentEndsBlockReg', start)
    equation = original[start:stop]
    token = '(IData)(vlSelfRef.mkSynthA8W8D16__DOT__core_core_workScheduler_resetAtBlockBoundaryReg)'
    assert equation.count(token) == 1
    changed_equation = equation.replace(token, '1U /* isolated mutation: always reset at scale block */')
    target.write_text(original[:start] + changed_equation + original[stop:])
    (out / 'mutation.json').write_text(json.dumps({
        'kind': 'generated C++ source mutation of lowered RTL accumulator-control equation',
        'source': str(positive / 'obj_dir' / target.name), 'original_sha256': hashes[str(positive / 'obj_dir' / target.name)],
        'mutant_sha256': digest(target), 'before': equation, 'after': changed_equation,
        'numerical_expected_changed': False, 'production_source_modified': False,
        'verilog_or_bsv_source_mutation': False,
    }, indent=2) + '\n')
    commands = [
        ['make', '-C', str(out / 'obj_dir'), '-f', 'VmkSynthA8W8D16.mk',
         'libVmkSynthA8W8D16', '-j', str(args.jobs)],
        ['c++', '-pthread', str(out / 'monitor.o'), str(out / 'obj_dir/libVmkSynthA8W8D16.a'),
         str(out / 'obj_dir/libverilated.a'), '-o', str(out / 'monitor-mutant')],
        [str(out / 'monitor-mutant')],
    ]
    (out / 'commands.json').write_text(json.dumps(commands, indent=2) + '\n')
    error, samples = None, []
    try:
        for name, command, expected in zip(('build', 'link', 'run'), commands, (0, 0, 1)):
            with (out / (name + '.log')).open('x') as log:
                result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=300)
            samples.append({'name': name, 'actual_exit': result.returncode, 'expected_exit': expected})
            assert result.returncode == expected, f'{name}: unexpected exit {result.returncode}'
        text = (out / 'run.log').read_text()
        assert 'SCU_CORE_MONITOR_FAIL replace/accumulate reset at the wrong boundary' in text
        assert text.count('SCU_CORE_CAPTURE ') == text.count('SCU_CORE_ACC_WRITE ') == 2
        assert 'SCU_CORE_CASE_PASS' not in text and 'SCU_CORE_MONITOR_PASS' not in text
        print(text, end='')
    except Exception as exc:
        error = str(exc)
        raise
    finally:
        changed = [path for path, expected in hashes.items() if digest(Path(path)) != expected]
        (out / 'summary.json').write_text(json.dumps({
            'status': 'PASS' if error is None and not changed else 'FAIL', 'error': error,
            'mutation_detected': error is None, 'samples': samples,
            'actual_partial_invocations': 1 if error is None else None,
            'actual_scu_contributions_before_rejection': 2 if error is None else None,
            'completed_logical_invocations': 0, 'physical_jobs': 0,
            'inputs': len(hashes), 'changed_inputs': changed,
        }, indent=2) + '\n')
        assert not changed, 'positive production model evidence changed'


if __name__ == '__main__':
    main()
