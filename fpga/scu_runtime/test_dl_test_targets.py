#!/usr/bin/env python3
"""Configure actual direct-backend test blocks against MODULE/static controls."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent


def blocks(text):
    result = []
    for start, end in (
        ('llama_build_and_test(test-gemmini-weight-formats.cpp NAME gemmini_weight_formats)', 'add_test(NAME test-gemmini-weight-reader-callers'),
        ('add_executable(test-gemmini-rmd\n', 'add_executable(test-gemmini-rmd-packet\n'),
        ('add_executable(test-gemmini-rmd-telemetry\n', 'add_executable(test-gemmini-rmd-reducer\n'),
    ):
        # New guards precede each target block; extract their if/endif as well.
        match = re.search(re.escape(start).replace('\\\n', r'\n\s*'), text)
        assert match
        begin = match.start()
        prior = text[:begin]
        guard = prior.rfind('if (NOT GGML_BACKEND_DL)')
        if guard >= 0 and not prior[guard:].split('\n', 1)[1].strip():
            begin = guard
        finish = text.index(end, match.end())
        prefix = text[:finish]
        marker = prefix.rfind('# Direct backend symbols')
        if marker > begin:
            finish = marker
        result.append(text[begin:finish])
    return '\n'.join(result)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--baseline-host', type=Path, required=True)
    args = parser.parse_args()
    out = Path(tempfile.mkdtemp(prefix='scu-dl-test-targets-'))
    logs = []
    prelude = '''cmake_minimum_required(VERSION 3.16)
project(test_backend_test_guards LANGUAGES CXX)
enable_testing()
add_library(ggml-base STATIC dummy.cpp)
add_library(ggml-gemmini-utils STATIC dummy.cpp)
if (GGML_BACKEND_DL)
  add_library(ggml-gemmini MODULE dummy.cpp)
else()
  add_library(ggml-gemmini STATIC dummy.cpp)
endif()
function(llama_build_and_test source)
  cmake_parse_arguments(TEST "" "NAME" "" ${ARGN})
  add_executable(${TEST_NAME} "${CMAKE_CURRENT_SOURCE_DIR}/dummy.cpp")
endfunction()
'''
    for version, text in [('before', (args.baseline_host / 'tests/CMakeLists.txt').read_text()),
                          ('after', (ROOT / 'host-overlay/tests/CMakeLists.txt').read_text())]:
        code = blocks(text)
        for dl in ('ON', 'OFF'):
            case = out / f'{version}-{dl}'
            case.mkdir()
            (case / 'dummy.cpp').write_text('int main(){return 0;}\n')
            selected = re.sub(r'[^\s()]+\.cpp', '${CMAKE_CURRENT_SOURCE_DIR}/dummy.cpp', code)
            expected = version == 'after' and dl == 'ON'
            condition = 'if(TARGET ${target})' if expected else 'if(NOT TARGET ${target})'
            check = '\nforeach(target IN ITEMS gemmini_weight_formats test-gemmini-rmd test-gemmini-rmd-telemetry)\n' + condition + '\nmessage(FATAL_ERROR "wrong target registration: ${target}")\nendif()\nendforeach()\n'
            (case / 'CMakeLists.txt').write_text(prelude + selected + check)
            command = ['cmake', '-S', str(case), '-B', str(case / 'build'), '-DGGML_BACKEND_DL=' + dl]
            process = subprocess.run(command, text=True, capture_output=True)
            (case / 'stdout').write_text(process.stdout); (case / 'stderr').write_text(process.stderr)
            expected_failure = version == 'before' and dl == 'ON'
            assert (process.returncode != 0) == expected_failure, (version, dl, process.stderr)
            if expected_failure:
                assert 'MODULE_LIBRARY' in process.stderr
            logs.append({'version': version, 'DL': dl, 'exit': process.returncode, 'expected_failure': expected_failure})
    (out / 'result.json').write_text(json.dumps({'status': 'PASS', 'cases': logs,
        'scope': 'actual selected test CMake blocks; main task separately configures the complete host tree'}, indent=2) + '\n')
    print(json.dumps({'status': 'PASS', 'cases': len(logs), 'out': str(out)}))


if __name__ == '__main__':
    main()
