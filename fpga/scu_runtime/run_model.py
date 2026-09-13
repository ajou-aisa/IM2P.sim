#!/usr/bin/env python3
"""Run an unchanged local model through an ordinary CLI and explicit production RTL.

Capture every executed native matmul and check block integers plus original
main reconstruction. UART PHY is omitted by the selected RTL plugin.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import time


def sha(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build', type=Path, required=True)
    parser.add_argument('--plugin', type=Path, required=True)
    parser.add_argument('--audit', type=Path, required=True)
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--prompt', default='Hello world')
    parser.add_argument('--predict', type=int, default=1)
    parser.add_argument('--mode', choices=['FULL', 'STRIPE_PIPELINE'], default='FULL')
    parser.add_argument('--context', type=int, default=128)
    parser.add_argument('--threads', type=int, default=4)
    args = parser.parse_args()
    if args.predict < 1 or args.context < 8 or args.threads < 1:
        parser.error('positive prediction/thread count and context >=8 required')
    build, plugin, audit, model = (p.resolve(strict=True) for p in
                                 (args.build, args.plugin, args.audit, args.model))
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    (out / 'captures').mkdir()
    cli = build / 'bin/llama-cli'
    library = build / 'bin/libggml-gemmini.so'
    result = dict(status='RUNNING', started=datetime.now(timezone.utc).isoformat(),
                  physical_access_count=0, uart_phy='omitted', commands=[],
                  model=dict(path=str(model), sha256=sha(model), bytes=model.stat().st_size),
                  inputs=dict(prompt=args.prompt, predict=args.predict, context=args.context,
                              threads=args.threads, mode=args.mode),
                  identities={str(p): sha(p) for p in (cli, library, plugin, audit)})

    def save():
        (out / 'result.json').write_text(json.dumps(result, indent=2) + '\n')

    def command(name, argv, env=None):
        record = dict(name=name, argv=list(map(str, argv)), cwd=str(out),
                      started=datetime.now(timezone.utc).isoformat())
        result['commands'].append(record)
        save()
        started = time.monotonic()
        with (out / (name + '.stdout')).open('wb') as stdout, (out / (name + '.stderr')).open('wb') as stderr:
            child = subprocess.Popen(record['argv'], cwd=out, env=env, stdout=stdout, stderr=stderr)
            record['pid'] = child.pid
            save()
            code = child.wait()
        record.update(exit=code, elapsed=time.monotonic() - started,
                      ended=datetime.now(timezone.utc).isoformat())
        save()
        if code:
            raise RuntimeError(f'{name}: exit {code}; {out / (name + ".stderr")}')

    try:
        entries = json.loads((build / 'compile_commands.json').read_text())
        entry = next(e for e in entries if e['file'].endswith('/ggml-gemmini-fpga.cpp'))
        tokens = shlex.split(entry['command'])
        flags = []
        skip = False
        for token in tokens[1:]:
            if skip:
                skip = False
                continue
            if token == '-o':
                skip = True
            elif token != '-c' and token != entry['file']:
                flags.append(token)
        source = Path(__file__).resolve().parent / 'tests/model_observer.cpp'
        shutil.copy2(source, out / 'model_observer.cpp')
        capture_header = source.with_name('model_capture.hpp')
        shutil.copy2(capture_header, out / capture_header.name)
        host_source = next(Path(e['file']) for e in entries
                           if e['file'].endswith('/ggml-gemmini/ggml-gemmini.cpp'))
        observer = out / 'model_observer.so'
        command('observer-build', [tokens[0], *flags, '-O2', '-fno-fast-math', '-shared', '-fPIC',
                '-I' + str(host_source.parents[3] / 'common'),
                out / 'model_observer.cpp', '-L' + str(build / 'bin'),
                '-Wl,-rpath,' + str(build / 'bin'), '-lggml', '-lggml-base',
                '-lggml-gemmini-utils', '-ldl', '-pthread', '-o', observer])
        result['observer'] = dict(path=str(observer), sha256=sha(observer), source_sha256=sha(out / 'model_observer.cpp'))
        result['observer']['capture_header_sha256'] = sha(out / capture_header.name)
        environment = {k: v for k, v in os.environ.items()
                       if not k.startswith(('IM2P_FPGA_', 'LLAMA_ARG_', 'GEMMINI_')) and k != 'LD_PRELOAD'}
        environment.update(IM2P_FPGA_DEVICE='rtl:' + str(plugin), IM2P_FPGA_REQUIRE_COMPLETION='1',
                           IM2P_FPGA_TIMEOUT_SECONDS='600', IM2P_TEST_SIM_FORBIDDEN='1',
                           IM2P_MODEL_CAPTURE_DIR=str(out / 'captures'),
                           GGML_BACKEND_PATH=str(library), GEMMINI_MATMUL_MODE=args.mode,
                           LD_PRELOAD=str(observer) + ':' + str(audit))
        result['environment'] = {k: v for k, v in environment.items()
                                 if k.startswith(('IM2P_', 'GEMMINI_', 'GGML_BACKEND_')) or k == 'LD_PRELOAD'}
        command('cli', [cli, '--device', 'GEMMINI', '-ngl', '99', '-m', model,
                '-p', args.prompt, '-n', str(args.predict), '-c', str(args.context),
                '-t', str(args.threads), '-b', str(args.context), '-ub', str(args.context),
                '--no-warmup', '--no-conversation', '--simple-io', '--no-display-prompt',
                '--seed', '1', '--temp', '0'], environment)
        stdout = (out / 'cli.stdout').read_text(errors='replace')
        stderr = (out / 'cli.stderr').read_text(errors='replace')
        log = stdout + '\n' + stderr
        captures = [json.loads(p.read_text()) for p in sorted((out / 'captures').glob('*.json'))]
        assigned = re.findall(r'FPGA_UART_ASSIGN node=(.*?) M=(\d+) N=(\d+) K=(\d+)', log)
        counters = re.findall(r'FPGA_UART_VERIFY assigned=(\d+) adapter_attempted=(\d+) completed=(\d+) failed=(\d+) status=(\w+)', log)
        if not captures or len(assigned) != len(captures) or not counters:
            raise RuntimeError('missing actual FPGA assignments, captures, or final counters')
        count = tuple(map(int, counters[-1][:4]))
        if count != (len(captures), len(captures), len(captures), 0) or counters[-1][4] != 'PASS':
            raise RuntimeError('incomplete actual FPGA assignment/completion counts')
        if 'SIMULATOR_AUDIT creates=0 fulls=0 streams=0' not in log:
            raise RuntimeError('simulator substitution audit missing or nonzero')
        if f'MODEL_OBSERVER_SUMMARY installed=1 completed={len(captures)} pending_records=0' not in log:
            raise RuntimeError('model observer completion summary missing')
        assignment = re.findall(r'MODEL_ASSIGNMENT_SUMMARY eligible=(\d+) assigned=(\d+) originally_cpu=(\d+) other=(\d+) unassigned_eligible=(\d+) numerical_fallback=(\d+)', log)
        if len(assignment) != 1:
            raise RuntimeError('actual scheduler assignment observation missing')
        eligible, scheduled, cpu, other, unassigned, fallback = map(int, assignment[0])
        if eligible != count[0] or scheduled != count[0] or unassigned or fallback:
            raise RuntimeError('eligible/assigned/completed FPGA nodes do not match')
        for capture in captures:
            node, m, n, k = assigned[capture['invocation']]
            if (capture['M'], capture['N'], capture['K']) != tuple(map(int, (m, n, k))):
                raise RuntimeError('actual graph assignment/captured invocation mismatch')
            capture['node'] = node
        if sha(model) != result['model']['sha256']:
            raise RuntimeError('local model changed during execution')
        for path, digest in result['identities'].items():
            if sha(Path(path)) != digest:
                raise RuntimeError('executable/library changed during execution: ' + path)
        result.update(status='PASS', invocations=captures,
                      counters=dict(assigned=count[0], attempted=count[1], completed=count[2], failed=count[3],
                                    eligible=eligible, originally_cpu=cpu, other_backend=other,
                                    unassigned_eligible=unassigned, numerical_fallback=fallback,
                                    simulator_create=0, simulator_full=0, simulator_stream=0),
                      capture_hashes={p.name: sha(p) for p in (out / 'captures').iterdir() if p.is_file()},
                      model_preservation='PASS', numerical_contract='main_external',
                      raw_exact=True, fout_exact=True)
    except Exception as error:
        result.update(status='FAIL', first_error=str(error))
        raise
    finally:
        result['ended'] = datetime.now(timezone.utc).isoformat()
        save()
    print('ACTUAL_MODEL_RTL_PASS ' + str(out), flush=True)


if __name__ == '__main__':
    main()
