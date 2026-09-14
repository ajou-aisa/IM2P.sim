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
'''
    for name in ('make', 'cmake', 'brew', 'rm', 'uname'):
        path = fake / name
        path.write_text(recorder)
        path.chmod(0o755)
    sim = out / 'selected core'
    sim.mkdir()
    manifest = out / 'artifact generation' / 'real-lib.json'
    manifest.parent.mkdir()
    manifest.write_text('{}\n')  # Even an existing manifest is forbidden for FPGA_UART.
    results = []

    def run(name, script='build-x86.sh', cli=(), env=None, cache=None, expect=0, dry=False,
            provisioning_failure=False, error=None):
        case = out / name
        case.mkdir()
        build = case / 'binary output'
        if cache:
            build.mkdir()
            (build / 'CMakeCache.txt').write_text('\n'.join(f'{k}:STRING={v}' for k, v in cache.items()) + '\n')
            (build / 'retained-output').write_bytes(b'existing build output\n')
        before = {str(p.relative_to(build)): p.read_bytes() for p in build.rglob('*') if p.is_file()}
        existed = build.exists()
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
        if error:
            assert error in process.stderr, (name, process.stderr)
        assert not any(item[0] == 'rm' for item in log), name
        if cache:
            assert (build / 'retained-output').read_bytes() == before['retained-output'], name
        if dry:
            assert build.exists() == existed and before == {
                str(p.relative_to(build)): p.read_bytes() for p in build.rglob('*') if p.is_file()}, name
        if provisioning_failure:
            assert any(item[0] == 'make' for item in log) and not any(item[0] == 'cmake' for item in log), (name, log)
        elif dry or expect or '--help' in cli:
            assert not any(item[0] in ('make', 'cmake', 'brew') for item in log), (name, log)
        elif summary:
            config = next(item for item in log if item[0] == 'cmake' and '--build' not in item)
            assert config[config.index('-B') + 1] == str(build), (name, config)
            builds = [item for item in log if item[:2] == ['cmake', '--build']]
            assert len(builds) == 1 and builds[0][2] == str(build), (name, builds)
            effective_argv = {}
            for arg in config:
                if arg.startswith('-D') and '=' in arg:
                    key, value = arg[2:].split('=', 1)
                    effective_argv[key.split(':', 1)[0]] = value
            for key in ('GGML_GEMMINI_EXECUTION_BACKEND', 'GGML_GEMMINI_DIM', 'GGML_GEMMINI_OPTION', 'GGML_GEMMINI_ENABLE_RMD'):
                assert effective_argv[key] == summary['effective'][key], (name, key)
            if summary['effective']['GGML_GEMMINI_EXECUTION_BACKEND'] == 'IM2P_SIM':
                provision = next(item for item in log if item[0] == 'make')
                assert f"IM2P_DIM={summary['effective']['GGML_GEMMINI_DIM']}" in provision, name
            else:
                assert not any(item[0] == 'make' for item in log), name
                if summary['effective']['GGML_GEMMINI_EXECUTION_BACKEND'] == 'FPGA_UART':
                    assert not effective_argv.get('GGML_GEMMINI_FPGA_SIM_MANIFEST'), name
            if script.startswith('build-arm64') and environment.get('TEST_UNAME', 'Linux') == 'Linux':
                assert not any(item[0] == 'brew' for item in log), name
                assert not any(x.startswith('-DCMAKE_OSX_') for x in config), name
        results.append(result)
        return result

    fpga = ('-DGGML_GEMMINI_EXECUTION_BACKEND=FPGA_UART',)
    sim_cli = ('-DGGML_GEMMINI_EXECUTION_BACKEND=IM2P_SIM',)
    for script in ('build-x86.sh', 'build-arm64.sh', 'build-arm64-cpu.sh'):
        run('fpga-' + script, script, fpga)
        run('help-' + script, script, ('--help',))
        run('dry-' + script, script, fpga, dry=True)
        run('fpga-no-provision-' + script, script, fpga, env={'TEST_MAKE_FAIL': '1'})
        run('fpga-manifest-reject-' + script, script,
            fpga + ('-DGGML_GEMMINI_FPGA_SIM_MANIFEST:FILEPATH=' + str(manifest),),
            expect=2, error='is invalid for FPGA_UART')
    run('arm-fpga-on-x86-reject', 'build-arm64.sh', fpga, env={'TEST_ARCH': 'x86_64'}, expect=2)
    run('arm-fpga-on-x86-dry', 'build-arm64.sh', fpga, env={'TEST_ARCH': 'x86_64'}, dry=True)
    run('x86-fpga-on-arm-reject', cli=fpga, env={'TEST_ARCH': 'aarch64'}, expect=2)
    run('arm-sim-on-x86-unchanged', 'build-arm64.sh', sim_cli, env={'TEST_ARCH': 'x86_64'})
    defaults = run('x86-user-defaults')['summary']['effective']
    run('arm-user-defaults', 'build-arm64.sh')
    run('arm-sim-explicit', 'build-arm64.sh', sim_cli)
    run('arm-cpu-defaults', 'build-arm64-cpu.sh')
    assert run('log-dependent-default', cli=('-DLOG_CYCLE=0',))['summary']['effective']['GGML_CPU_CYCLE_LOG'] == '0'
    assert run('log-dependent-explicit', cli=('-DLOG_CYCLE=0', '-DGGML_CPU_CYCLE_LOG=1'))['summary']['effective']['GGML_CPU_CYCLE_LOG'] == '1'
    run('cpu-explicit', cli=('-DGGML_GEMMINI_EXECUTION_BACKEND=HARDWARE', '-DGGML_GEMMINI_OPTION=CPU'))
    run('typed-cli-before-provision', 'build-arm64.sh', sim_cli + ('-DGGML_GEMMINI_DIM:STRING=16',))
    run('cli-over-env', cli=fpga + ('-DGGML_GEMMINI_DIM:STRING=16',), env={'GGML_GEMMINI_DIM': '64', 'GGML_GEMMINI_EXECUTION_BACKEND': 'IM2P_SIM'})
    run('env-over-cache', env={'LOG_DEBUG': '0'}, cache={'LOG_DEBUG': '1'})
    assert run('cache-over-defaults', cli=('-DGGML_GEMMINI_EXECUTION_BACKEND=HARDWARE',),
               cache={'GGML_GEMMINI_OPTION': 'CPU', 'LOG_DEBUG': '0'})['summary']['effective']['LOG_DEBUG'] == '0'
    cached_backend = 'IM2P_SIM' if defaults['GGML_GEMMINI_EXECUTION_BACKEND'] != 'IM2P_SIM' else 'FPGA_UART'
    assert run('backend-default-over-cache', cache={'GGML_GEMMINI_EXECUTION_BACKEND': cached_backend})['summary']['effective']['GGML_GEMMINI_EXECUTION_BACKEND'] == defaults['GGML_GEMMINI_EXECUTION_BACKEND']
    run('env-fpga', env={'GGML_GEMMINI_EXECUTION_BACKEND': 'FPGA_UART'})
    run('env-fpga-manifest-reject', env={'GGML_GEMMINI_EXECUTION_BACKEND': 'FPGA_UART',
        'GGML_GEMMINI_FPGA_SIM_MANIFEST': str(manifest)}, expect=2, error='is invalid for FPGA_UART')
    run('cache-fpga-manifest-reject', cli=fpga, cache={'GGML_GEMMINI_FPGA_SIM_MANIFEST': str(manifest)},
        expect=2, error='is invalid for FPGA_UART')
    run('alias-env', cli=sim_cli, env={'IM2P_DIM': '32'})
    run('alias-cli', cli=sim_cli + ('-DIM2P_DIM:STRING=32',))
    run('alias-cli-over-env', cli=sim_cli + ('-DGGML_GEMMINI_DIM=16',), env={'IM2P_DIM': '64'})
    run('alias-conflict', env={'IM2P_DIM': '32', 'GGML_GEMMINI_DIM': '16'}, expect=2)
    run('alias-cli-conflict', cli=('-DIM2P_DIM=32', '-DGGML_GEMMINI_DIM=16'), expect=2)
    for key, bad in (('GGML_GEMMINI_DIM', '64'), ('GGML_GEMMINI_ACTIVATION_BITS', '4'), ('GGML_GEMMINI_WEIGHT_BITS', '16'), ('GGML_GEMMINI_BLOCK_SIZE', '64'), ('GGML_GEMMINI_COMPUTE_TYPE', 'FLOAT'), ('GGML_GEMMINI_ACTIVATION_QUANT', 'UNKNOWN'), ('GGML_GEMMINI_OPTION', 'CPU'), ('GGML_GEMMINI_DEQUANT_FP_TEST', 'ON'), ('GGML_GEMMINI', 'OFF')):
        run('conflict-' + key, cli=fpga + (f'-D{key}={bad}',), expect=2)
    run('unknown-backend', cli=('-DGGML_GEMMINI_EXECUTION_BACKEND=UNKNOWN',), expect=2)
    run('provision-failure', cli=sim_cli, env={'TEST_MAKE_FAIL': '1'}, expect=7, provisioning_failure=True)
    run('bool-false', cli=fpga + ('-DGGML_GEMMINI_ENABLE_RMD:BOOL=false',))
    assert run('rmd-enabled', cli=fpga + ('-DGGML_GEMMINI_ENABLE_RMD:BOOL=ON',))['summary']['effective']['GGML_GEMMINI_ENABLE_RMD'] == 'ON'
    run('rmd-invalid', cli=fpga + ('-DGGML_GEMMINI_ENABLE_RMD=invalid',), expect=2)
    for script in ('build-x86.sh', 'build-arm64.sh'):
        run('stale-backend-' + script, script, fpga, cache={'GGML_GEMMINI_EXECUTION_BACKEND': 'IM2P_SIM'})
        run('stale-dim-' + script, script, fpga + ('-DGGML_GEMMINI_DIM=16',), cache={'GGML_GEMMINI_DIM': '64'})
        run('stale-dry-preserved-' + script, script, fpga + ('-DGGML_GEMMINI_DIM=16',),
            cache={'GGML_GEMMINI_EXECUTION_BACKEND': 'IM2P_SIM', 'GGML_GEMMINI_DIM': '64'}, dry=True)
    run('cached-invalid-fpga-dim', cli=fpga, cache={'GGML_GEMMINI_DIM': '64'},
        expect=2, error='FPGA_UART requires GGML_GEMMINI_DIM=16')
    run('reject-preset', cli=('--preset=unknown',), expect=2)
    run('reject-toolchain', cli=('-DCMAKE_TOOLCHAIN_FILE=arm.cmake',), expect=2)
    run('reject-cached-toolchain', cache={'CMAKE_TOOLCHAIN_FILE': 'arm.cmake'}, expect=2)
    run('env-default-over-cache', env={'GGML_GEMMINI_COMPUTE_TYPE_DEFAULT': 'INT'}, cache={'GGML_GEMMINI_COMPUTE_TYPE': 'INT'})
    run('reject-split-D', cli=('-D', 'GGML_GEMMINI_DIM=16'), expect=2)
    run('riscv-dry', 'build-riscv.sh', dry=True)
    run('riscv-static-dry', 'build-riscv.sh', ('static',), dry=True)
    run('riscv-fpga-reject', 'build-riscv.sh', fpga, expect=2)
    run('riscv-help', 'build-riscv.sh', ('--help',))
    run('darwin-control', 'build-arm64.sh', sim_cli, env={'TEST_UNAME': 'Darwin'})
    run('darwin-libomp-path', 'build-arm64.sh', sim_cli, env={'TEST_UNAME': 'Darwin', 'LIBOMP_PREFIX': '/tmp/lib omp', 'CMAKE_PREFIX_PATH': '/tmp/other prefix'})
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
