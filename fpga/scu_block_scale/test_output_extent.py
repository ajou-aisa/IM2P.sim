#!/usr/bin/env python3
"""Run frozen frontend mocks and an isolated pre-fix output-extent mutation."""
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import shutil
import subprocess

GUARD = '        column_offset == std::numeric_limits<size_t>::max() ||\n'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def boundary_test(original):
    start = original.index('bool test_provider_output_extent_overflow() {')
    end = original.index('\nbool test_provider_final_integer_full_pipeline()', start)
    body = original[start:end]
    body = body.replace('  fake::reset();',
                        '  bool all_ok = true;\n  for (size_t delta : {0, 1, 2}) {\n  fake::reset();', 1)
    body = body.replace('std::array<float, 2> output = {91, 91};',
                        'std::array<float, 4> output = {91, 91, 91, 91};')
    body = body.replace('args.I = 1;', 'args.I = delta == 0 ? 1 : 2;')
    body = body.replace('args.A.allocate(1, 32,', 'args.A.allocate(args.I, 32,')
    body = body.replace('args.col_stride_f_out = std::numeric_limits<size_t>::max();',
                        'args.col_stride_f_out = std::numeric_limits<size_t>::max() - delta;')
    body = body.replace('  return expect(', '  const bool passed = expect(')
    body = body.replace('output == std::array<float, 2>{91, 91}',
                        'output == std::array<float, 4>{91, 91, 91, 91}')
    assert body.endswith('}\n')
    body = body[:-2] + '''  std::printf("PROVIDER_EXTENT_CASE delta=%zu rows=%zu col_stride=%zu "
              "executor_calls=%zu simulator_creates=%zu output_untouched=%d pass=%d\\n",
              delta, args.I, args.col_stride_f_out, calls, size_t(fake::sim_created),
              int(output == std::array<float, 4>{91, 91, 91, 91}), int(passed));
  all_ok = passed && all_ok;
  }
  return all_ok;
}
'''
    return original[:start] + body + original[end:]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    snapshot, out = args.snapshot.resolve(), args.out.resolve()
    source = snapshot / 'source'
    frontend = source / 'frontend/src/im2p_gemmini_frontend.cpp'
    test = source / 'frontend/tests/test_frontend.cpp'
    manifest = snapshot / 'integration-sha256.json'
    frozen = json.loads(manifest.read_text())
    selected = [source / 'Makefile', frontend, test,
                source / 'frontend/include/im2p_gemmini_frontend.hpp',
                source / 'frontend/tests/im2p_gemmini_frontend_testing.hpp',
                source / 'sim/include/im2p_sim.h']
    assert all(digest(p) == frozen[str(p.relative_to(snapshot))] for p in selected)
    hashes = {str(p): digest(p) for p in [*selected, manifest, Path(__file__).resolve()]}
    original = frontend.read_text()
    assert original.count(GUARD) == 1
    out.mkdir(parents=True, exist_ok=False)
    shutil.copy2(__file__, out / Path(__file__).name)
    (out / 'input-sha256.json').write_text(json.dumps(hashes, indent=2) + '\n')
    records, completed, baseline_exit = [], False, None
    try:
        command = ['make', 'gemmini-frontend-test', 'GEMMINI_ROOT=' + str(snapshot / 'host'),
                   'GEMMINI_PARAMS_ROOT=' + str(snapshot / 'params/include'),
                   'BUILD_DIR=' + str(out / 'build')]
        (out / 'mock-command.json').write_text(json.dumps({'cwd': str(source), 'argv': command}, indent=2) + '\n')
        with (out / 'mock.log').open('x') as log:
            result = subprocess.run(command, cwd=source, stdout=log, stderr=subprocess.STDOUT)
        baseline_exit = result.returncode
        assert baseline_exit == 0, 'frozen frontend mock suite failed; stop without mutation'
        log = (out / 'mock.log').read_text()
        assert 'IM2P Gemmini frontend: PASS' in log
        assert 'IM2P Gemmini frontend case q8_hp1_extent_contract: PASS' in log
        compile_lines = [line for line in log.replace('\\\n', '').splitlines()
                         if 'frontend/tests/test_frontend.cpp' in line and ' -o ' in line]
        assert len(compile_lines) == 1, 'ambiguous existing mock compile command'
        base_command = shlex.split(compile_lines[0])
        archives = [word for word in base_command if word.endswith('.a')]
        assert len(archives) == 1 and archives[0].endswith('/libim2p_gemmini_frontend_testing.a')
        expanded_test = boundary_test(test.read_text())
        for variant, body, expected_exit in (
                ('guard-removed-mutant', original.replace(GUARD, ''), 1),
                ('reviewed-guard', original, 0)):
            folder = out / variant
            folder.mkdir()
            variant_frontend = folder / 'im2p_gemmini_frontend.cpp'
            variant_frontend.write_text(body)
            variant_test = folder / 'test_frontend.cpp'
            variant_test.write_text(expanded_test)
            exe = folder / 'test'
            replacements = {'frontend/tests/test_frontend.cpp': str(variant_test),
                            archives[0]: str(variant_frontend),
                            base_command[base_command.index('-o') + 1]: str(exe)}
            command = [replacements.get(word, word) for word in base_command]
            command.insert(1, '-I' + str(source / 'frontend/tests'))
            (folder / 'build-command.json').write_text(json.dumps({'cwd': str(source), 'argv': command}, indent=2) + '\n')
            with (folder / 'build.log').open('x') as log:
                subprocess.run(command, cwd=source, check=True, stdout=log, stderr=subprocess.STDOUT)
            command = [str(exe), 'provider_output_extent_overflow']
            (folder / 'run-command.json').write_text(json.dumps(command) + '\n')
            with (folder / 'run.log').open('x') as log:
                result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT)
            text = (folder / 'run.log').read_text()
            rows = [dict(field.split('=') for field in line.split()[1:])
                    for line in text.splitlines() if line.startswith('PROVIDER_EXTENT_CASE ')]
            assert result.returncode == expected_exit, f'{variant}: unexpected exit {result.returncode}'
            assert len(rows) == 3 and [int(row['delta']) for row in rows] == [0, 1, 2]
            for index, row in enumerate(rows):
                rejected = expected_exit == 0 or index != 0
                assert int(row['executor_calls']) == int(not rejected)
                assert int(row['simulator_creates']) == 0 and int(row['output_untouched']) == 1
                assert int(row['pass']) == int(rejected)
            records.append({'variant': variant, 'expected_exit': expected_exit,
                            'actual_exit': result.returncode, 'cases': rows,
                            'frontend_sha256': digest(variant_frontend),
                            'test_sha256': digest(variant_test), 'executable_sha256': digest(exe)})
            print(text, end='', flush=True)
        completed = True
    finally:
        changed = [p for p, h in hashes.items() if digest(Path(p)) != h]
        status = {'pass': completed and not changed, 'baseline_mock_exit': baseline_exit,
                  'samples': records, 'changed_inputs': changed, 'physical_devices': 0,
                  'rtl_numerical_jobs': 0, 'mock_only': True,
                  'scope': 'SIZE_MAX, SIZE_MAX-1, SIZE_MAX-2 column strides; safe executor returns error without output callbacks.'}
        (out / 'status.json').write_text(json.dumps(status, indent=2) + '\n')
    assert status['pass']
    print('PROVIDER_EXTENT_SUITE PASS mutant_exit=1 reviewed_exit=0 boundary_cases=3 physical_devices=0')


if __name__ == '__main__':
    main()
