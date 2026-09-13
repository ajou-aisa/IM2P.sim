#!/usr/bin/env python3
"""Run scripts with recording make/cmake commands; never build or open hardware."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path)
    parser.add_argument('--host', type=Path, default=ROOT.parents[2] / 'llama.cpp-gemmini')
    args = parser.parse_args()
    out = (args.out or Path(tempfile.mkdtemp(prefix='im2p-build-options-t1-'))).resolve()
    out.mkdir(parents=True, exist_ok=args.out is None)
    host = out / 'host source'
    for name in ('build-x86.sh', 'build-arm64.sh', 'build-arm64-cpu.sh', 'build-riscv.sh',
                 'scripts/im2p-build-options.py', 'scripts/im2p-host-provision.sh'):
        destination = host / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(args.host / name, destination)
    fake = out / 'fake'
    fake.mkdir()
    recorder = '''#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
name=Path(sys.argv[0]).name
with open(os.environ['ARGV_LOG'],'a') as f:f.write(json.dumps([name,*sys.argv[1:]])+'\\n')
if name=='uname':print(os.environ.get('TEST_ARCH','x86_64') if sys.argv[1:] == ['-m'] else os.environ.get('TEST_UNAME','Linux'))
if name=='brew':sys.exit(1)
if name=='make':
    if os.environ.get('TEST_MAKE_FAIL'):sys.exit(7)
    for arg in sys.argv[1:]:
        if arg.startswith('BUILD_DIR='):
            selected=Path(arg.split('=',1)[1])/'selected/a8-w8-d16'
            generation=selected/'generations/test-generation'
            generation.mkdir(parents=True)
            (generation/'real-lib.json').write_text('{}')
            (selected/'current').symlink_to('generations/test-generation')
'''
    for name in ('make', 'cmake', 'brew', 'rm', 'uname'):
        path = fake / name
        path.write_text(recorder)
        path.chmod(0o755)
    sim = out / 'selected core'
    sim.mkdir()
    manifest = out / 'artifact generation' / 'real-lib.json'
    manifest.parent.mkdir()
    manifest.write_text('{}\n')  # Script validates selection; real CMake validates contents.
    results = []

    def run(name, script='build-x86.sh', cli=(), env=None, cache=None, expect=0, dry=False,
            provisioning_failure=False):
        case = out / name
        case.mkdir()
        build = case / 'binary output'
        if cache:
            build.mkdir()
            (build / 'CMakeCache.txt').write_text('\n'.join(f'{k}:STRING={v}' for k, v in cache.items()) + '\n')
        environment = {k: v for k, v in os.environ.items() if not k.startswith(('GGML_', 'IM2P_', 'LOG_', 'CYCLE_', 'CMAKE_', 'LLAMA_', 'BUILD_'))}
        environment.update(PATH=str(fake) + ':' + os.environ['PATH'], ARGV_LOG=str(case / 'argv.jsonl'),
                           BUILD_DIR=str(build), BUILD_JOBS='2', IM2P_SIM_ROOT=str(sim),
                           TEST_ARCH='aarch64' if script.startswith('build-arm64') else 'x86_64')
        environment.update(env or {})
        command = ['bash', str(host / script), *cli, *(['--dry-run'] if dry else [])]
        process = subprocess.run(command, cwd=case, env=environment, text=True, capture_output=True)
        (case / 'stdout').write_text(process.stdout)
        (case / 'stderr').write_text(process.stderr)
        log = [json.loads(line) for line in (case / 'argv.jsonl').read_text().splitlines()] if (case / 'argv.jsonl').exists() else []
        summary = next((json.loads(line.split('=', 1)[1]) for line in process.stderr.splitlines() if line.startswith('IM2P_EFFECTIVE_CONFIG=')), None)
        result = {'name': name, 'argv': command, 'exit': process.returncode, 'expected_exit': expect, 'status': 'PASS' if process.returncode == expect else 'FAIL', 'commands': log, 'summary': summary}
        (case / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
        assert process.returncode == expect, (name, process.stderr)
        assert not any(item[0] == 'rm' for item in log), name
        if provisioning_failure:
            assert any(item[0] == 'make' for item in log) and not any(item[0] == 'cmake' for item in log), (name, log)
        elif dry or expect or '--help' in cli:
            assert not any(item[0] in ('make', 'cmake', 'brew') for item in log), (name, log)
        elif summary:
            config = next(item for item in log if item[0] == 'cmake' and '--build' not in item)
            effective_argv = {}
            for arg in config:
                if arg.startswith('-D') and '=' in arg:
                    key, value = arg[2:].split('=', 1)
                    effective_argv[key.split(':', 1)[0]] = value
            for key in ('GGML_GEMMINI_EXECUTION_BACKEND', 'GGML_GEMMINI_DIM', 'GGML_GEMMINI_OPTION', 'GGML_GEMMINI_ENABLE_RMD'):
                assert effective_argv[key] == summary['effective'][key], (name, key)
            auto_fpga = (summary['effective']['GGML_GEMMINI_EXECUTION_BACKEND'] == 'FPGA_UART' and
                         not summary['effective'].get('GGML_GEMMINI_FPGA_SIM_MANIFEST'))
            if summary['effective']['GGML_GEMMINI_EXECUTION_BACKEND'] == 'IM2P_SIM' or auto_fpga:
                provision = next(item for item in log if item[0] == 'make')
                assert f"IM2P_DIM={summary['effective']['GGML_GEMMINI_DIM']}" in provision, name
                if auto_fpga:
                    assert 'BUILD_DIR=' + str(build / 'im2p-native') in provision, name
                    pinned = Path(effective_argv['GGML_GEMMINI_FPGA_SIM_MANIFEST'])
                    assert pinned.is_file() and 'current' not in pinned.parts and 'generations' in pinned.parts, name
            else:
                assert not any(item[0] == 'make' for item in log), name
            if script.startswith('build-arm64') and environment.get('TEST_UNAME', 'Linux') == 'Linux':
                assert not any(item[0] == 'brew' for item in log), name
                assert not any(x.startswith('-DCMAKE_OSX_') for x in config), name
        results.append(result)
        return result

    fpga = ('-DGGML_GEMMINI_EXECUTION_BACKEND=FPGA_UART', '-DGGML_GEMMINI_FPGA_SIM_MANIFEST:FILEPATH=' + str(manifest))
    for script in ('build-x86.sh', 'build-arm64.sh', 'build-arm64-cpu.sh'):
        run('fpga-' + script, script, fpga)
        run('help-' + script, script, ('--help',))
        run('dry-' + script, script, fpga, dry=True)
        run('fpga-provision-' + script, script, (fpga[0],))
        run('dry-provision-' + script, script, (fpga[0],), dry=True)
    run('arm-fpga-on-x86-reject', 'build-arm64.sh', fpga, env={'TEST_ARCH': 'x86_64'}, expect=2)
    run('arm-fpga-on-x86-dry', 'build-arm64.sh', fpga, env={'TEST_ARCH': 'x86_64'}, dry=True)
    run('x86-fpga-on-arm-reject', cli=fpga, env={'TEST_ARCH': 'aarch64'}, expect=2)
    run('arm-sim-on-x86-unchanged', 'build-arm64.sh', env={'TEST_ARCH': 'x86_64'})
    run('x86-user-defaults')
    run('arm-sim-defaults', 'build-arm64.sh')
    run('arm-cpu-defaults', 'build-arm64-cpu.sh')
    assert run('log-dependent-default', cli=('-DLOG_CYCLE=0',))['summary']['effective']['GGML_CPU_CYCLE_LOG'] == '0'
    assert run('log-dependent-explicit', cli=('-DLOG_CYCLE=0', '-DGGML_CPU_CYCLE_LOG=1'))['summary']['effective']['GGML_CPU_CYCLE_LOG'] == '1'
    run('cpu-explicit', cli=('-DGGML_GEMMINI_EXECUTION_BACKEND=HARDWARE', '-DGGML_GEMMINI_OPTION=CPU'))
    run('typed-cli-before-provision', 'build-arm64.sh', ('-DGGML_GEMMINI_DIM:STRING=16',))
    run('cli-over-env', cli=fpga + ('-DGGML_GEMMINI_DIM:STRING=16',), env={'GGML_GEMMINI_DIM': '64', 'GGML_GEMMINI_EXECUTION_BACKEND': 'IM2P_SIM'})
    run('env-over-cache', env={'LOG_DEBUG': '0'}, cache={'LOG_DEBUG': '1'})
    run('cache-over-defaults', cache={'GGML_GEMMINI_EXECUTION_BACKEND': 'HARDWARE', 'GGML_GEMMINI_OPTION': 'CPU', 'LOG_DEBUG': '0'})
    run('env-fpga', env={'GGML_GEMMINI_EXECUTION_BACKEND': 'FPGA_UART', 'GGML_GEMMINI_FPGA_SIM_MANIFEST': str(manifest)})
    run('alias-env', env={'IM2P_DIM': '32'})
    run('alias-cli', cli=('-DIM2P_DIM:STRING=32',))
    run('alias-cli-over-env', cli=('-DGGML_GEMMINI_DIM=16',), env={'IM2P_DIM': '64'})
    run('alias-conflict', env={'IM2P_DIM': '32', 'GGML_GEMMINI_DIM': '16'}, expect=2)
    run('alias-cli-conflict', cli=('-DIM2P_DIM=32', '-DGGML_GEMMINI_DIM=16'), expect=2)
    for key, bad in (('GGML_GEMMINI_DIM', '64'), ('GGML_GEMMINI_ACTIVATION_BITS', '4'), ('GGML_GEMMINI_WEIGHT_BITS', '16'), ('GGML_GEMMINI_BLOCK_SIZE', '64'), ('GGML_GEMMINI_COMPUTE_TYPE', 'FLOAT'), ('GGML_GEMMINI_ACTIVATION_QUANT', 'UNKNOWN'), ('GGML_GEMMINI_OPTION', 'CPU'), ('GGML_GEMMINI_DEQUANT_FP_TEST', 'ON'), ('GGML_GEMMINI', 'OFF')):
        run('conflict-' + key, cli=fpga + (f'-D{key}={bad}',), expect=2)
    run('unknown-backend', cli=('-DGGML_GEMMINI_EXECUTION_BACKEND=UNKNOWN',), expect=2)
    run('provision-failure', cli=(fpga[0],), env={'TEST_MAKE_FAIL': '1'}, expect=7, provisioning_failure=True)
    run('bool-false', cli=fpga + ('-DGGML_GEMMINI_ENABLE_RMD:BOOL=false',))
    assert run('rmd-enabled', cli=fpga + ('-DGGML_GEMMINI_ENABLE_RMD:BOOL=ON',))['summary']['effective']['GGML_GEMMINI_ENABLE_RMD'] == 'ON'
    run('rmd-invalid', cli=fpga + ('-DGGML_GEMMINI_ENABLE_RMD=invalid',), expect=2)
    run('stale-backend', cli=fpga, cache={'GGML_GEMMINI_EXECUTION_BACKEND': 'IM2P_SIM'}, expect=2)
    run('stale-dim', cli=('-DGGML_GEMMINI_DIM=16',), cache={'GGML_GEMMINI_DIM': '64'}, expect=2)
    run('reject-preset', cli=('--preset=unknown',), expect=2)
    run('reject-toolchain', cli=('-DCMAKE_TOOLCHAIN_FILE=arm.cmake',), expect=2)
    run('reject-cached-toolchain', cache={'CMAKE_TOOLCHAIN_FILE': 'arm.cmake'}, expect=2)
    run('env-default-over-cache', env={'GGML_GEMMINI_COMPUTE_TYPE_DEFAULT': 'INT'}, cache={'GGML_GEMMINI_COMPUTE_TYPE': 'INT'})
    run('reject-split-D', cli=('-D', 'GGML_GEMMINI_DIM=16'), expect=2)
    run('riscv-dry', 'build-riscv.sh', dry=True)
    run('riscv-static-dry', 'build-riscv.sh', ('static',), dry=True)
    run('riscv-fpga-reject', 'build-riscv.sh', fpga, expect=2)
    run('riscv-help', 'build-riscv.sh', ('--help',))
    run('darwin-control', 'build-arm64.sh', env={'TEST_UNAME': 'Darwin'})
    run('darwin-libomp-path', 'build-arm64.sh', env={'TEST_UNAME': 'Darwin', 'LIBOMP_PREFIX': '/tmp/lib omp', 'CMAKE_PREFIX_PATH': '/tmp/other prefix'})
    injection = out / 'MUST_NOT_EXIST'
    run('literal-path-quoting', cli=fpga + ('-DGGML_TEST_PATH:PATH=space `touch ' + str(injection) + '` $(touch ' + str(injection) + ')',))
    assert not injection.exists()
    report = {'status': 'PASS', 'checks': len(results), 'kind': 'fake command option/provision/configure checks; not build PASS',
              'source_sha256': {str(p.relative_to(host)): hashlib.sha256(p.read_bytes()).hexdigest() for p in host.rglob('*') if p.is_file()},
              'results': results}
    (out / 'summary.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'status': 'PASS', 'checks': len(results), 'out': str(out)}))


if __name__ == '__main__':
    main()
