#!/usr/bin/env python3
"""Bounded native test build: reuse frozen archives, never BSC/Cargo/Vivado.

12 MiB disk ceiling includes private compiler TMPDIR. Compilation uses pipes
and a 1 MiB per-file limit; link has an 8 MiB limit. Logs are capped at 64 KiB
per stage. Preflight rejects insufficient space or a nonempty output directory.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import resource
import selectors
import shlex
import shutil
import signal
import subprocess
import time

LIMIT = 12 * 1024 * 1024


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def size(directory):
    return sum(path.stat().st_size for path in directory.rglob('*') if path.is_file())


def stage(command, out, name, file_limit, timeout):
    env = dict(os.environ, TMPDIR=str(out / 'tmp'))

    def limits():
        resource.setrlimit(resource.RLIMIT_FSIZE, (file_limit, file_limit))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               env=env, preexec_fn=limits, start_new_session=True)
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    deadline, captured = time.monotonic() + timeout, bytearray()
    try:
        with (out / (name + '.log')).open('xb') as log:
            while selector.get_map():
                if time.monotonic() > deadline or size(out) > LIMIT - 1024 * 1024:
                    raise RuntimeError(name + ' timeout/disk reserve exceeded')
                for key, event in selector.select(0.1):
                    chunk = os.read(key.fileobj.fileno(), 4096)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    if len(captured) + len(chunk) > 65536:
                        raise RuntimeError(name + ' log limit exceeded')
                    captured.extend(chunk)
                    log.write(chunk)
        code = process.wait(timeout=5)
        if code:
            raise RuntimeError(f'{name} exit={code}: {captured.decode(errors="replace")[-2000:]}')
        if size(out) > LIMIT:
            raise RuntimeError('12 MiB ceiling exceeded')
        return captured.decode(errors='replace')
    except BaseException:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        raise
    finally:
        selector.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    snapshot, out = args.snapshot.resolve(), args.out.resolve()
    build = snapshot / 'host-build'
    target = build / 'CMakeFiles/scu_frontend_final.dir'
    expected = root / 'tests/scu_block_scale/fixtures/ifr-inputs-signed-scu-sat-v2'
    if out.exists() or shutil.disk_usage(root).free < 2 * LIMIT:
        raise RuntimeError('preflight requires a new output path and at least 24 MiB free')
    flags = dict(line.split(' = ', 1) for line in (target / 'flags.make').read_text().splitlines() if ' = ' in line)
    libraries = [(build / value).resolve() for value in shlex.split((target / 'link.txt').read_text()) if value.endswith('.a')]
    source = Path(__file__).with_name('portable_replay.cpp')
    old_reader = root / 'fpga/full_replay/capture.cpp'
    text = old_reader.read_text()
    # Literal slices: preserve the original fields, pointer binding and restore
    # body. Do not include old ABI4 Execution callbacks or its capture/main.
    fields = text[text.index('struct Fixture {'):text.index('    void capture(')]
    restore = text[text.index('    void restore('):text.index('\nstruct Execution {')]
    header = fields + restore
    expected_manifest = json.loads((expected / 'manifest.json').read_text())
    inputs = [root / path for path in expected_manifest['input_sha256']]
    artifacts = [source, Path(__file__), old_reader, target / 'flags.make', target / 'link.txt',
                 snapshot / 'integration-sha256.json', *libraries, *inputs,
                 *[path for path in expected.rglob('*') if path.is_file()]]
    identities = {str(path): digest(path) for path in artifacts}
    for path, value in expected_manifest['input_sha256'].items():
        if digest(root / path) != value:
            raise RuntimeError('preserved fixture differs from independent golden provenance')
    for path, value in json.loads((expected / 'sha256.json').read_text()).items():
        if digest(expected / path) != value:
            raise RuntimeError('independent expected checksum mismatch')
    out.mkdir(parents=True)
    (out / 'tmp').mkdir()
    (out / 'ifx1_fixture_reader.hpp').write_text(header)
    shutil.copy2(source, out / source.name)
    (out / 'input-sha256.json').write_text(json.dumps(identities, indent=2) + '\n')
    compile_command = ['c++', '-std=c++20', '-O2', '-pipe', *shlex.split(flags['CXX_DEFINES']),
                       *shlex.split(flags['CXX_INCLUDES']), '-c', str(out / source.name), '-o', str(out / 'test.o')]
    link_command = ['c++', str(out / 'test.o'), '-o', str(out / 'test'), '-Wl,--start-group',
                    *map(str, libraries), '-Wl,--end-group', '-pthread', '-ldl', '-lm',
                    '-Wl,--wrap=im2p_execute_matmul_extended', '-Wl,--wrap=im2p_begin_striped_matmul']
    run_command = [str(out / 'test'), str(root), str(expected)]
    (out / 'commands.json').write_text(json.dumps(dict(compile=compile_command, link=link_command, run=run_command), indent=2) + '\n')
    try:
        stage(compile_command, out, 'compile', 1024 * 1024, 120)
        stage(link_command, out, 'link', 8 * 1024 * 1024, 120)
        log = stage(run_command, out, 'run', 1024 * 1024, 300)
        if (log.count('SCU_PORTABLE_RESULT ') != 16 or 'SCU_PORTABLE_PASS unique_inputs=8 complete_invocations=16' not in log or
                'SCU_PORTABLE_FAIL' in log):
            raise RuntimeError('actual numerical/count PASS marker missing')
        if any(digest(Path(path)) != value for path, value in identities.items()):
            raise RuntimeError('source/archive/fixture changed during test')
        status = dict(passed=True, unique_inputs=8, complete_invocations=16,
                      executable_sha256=digest(out / 'test'), source_reader_sha256=digest(old_reader),
                      extracted_reader_sha256=digest(out / 'ifx1_fixture_reader.hpp'),
                      bytes_before_status=size(out), maximum_bytes=LIMIT,
                      BSC_Cargo_Vivado_builds=0, physical_jobs=0)
        print(log, end='')
    except BaseException as error:
        status = dict(passed=False, error=str(error), bytes_before_status=size(out), maximum_bytes=LIMIT)
        (out / 'status.json').write_text(json.dumps(status, indent=2) + '\n')
        raise
    (out / 'status.json').write_text(json.dumps(status, indent=2) + '\n')
    assert size(out) <= LIMIT
    print(f'BOUNDED_NATIVE_REPLAY_PASS bytes={size(out)} ceiling={LIMIT} physical_jobs=0')


if __name__ == '__main__':
    main()
