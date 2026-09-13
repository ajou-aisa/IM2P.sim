#!/usr/bin/env python3
"""Create a new deployment workspace from the sealed SCU inputs; no devices."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[2]


def sha(path):
    return hashlib.file_digest(path.open('rb'), 'sha256').hexdigest()


def main():
    p = argparse.ArgumentParser()
    p.add_argument('run', type=Path)
    p.add_argument('--frozen', type=Path, required=True)
    p.add_argument('--migration', type=Path, required=True)
    a = p.parse_args()
    run = a.run.resolve()
    if run.exists():
        p.error('new RUN required')
    if os.statvfs(run.parent).f_bavail * os.statvfs(run.parent).f_frsize < 30 * 2**30:
        p.error('build filesystem requires operational 30 GiB free margin')
    spec = importlib.util.spec_from_file_location('native', ROOT / 'fpga/scu_migration/native_build.py')
    native = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(native)
    entries, identity = native.verify_frozen(a.frozen)
    run.mkdir()
    evidence = run / 'evidence'
    evidence.mkdir()
    for label, repo in [('core', ROOT), ('host', ROOT.parent / 'llama.cpp-gemmini'),
                        ('params', ROOT.parent / 'RISC-V-DynDNN-gemmini-include')]:
        commands = [('status', ['status', '--short']), ('branch', ['branch', '--show-current']),
                    ('head', ['rev-parse', 'HEAD']), ('log', ['log', '--oneline', '--decorate', '-10']),
                    ('stat', ['diff', '--stat']), ('check', ['diff', '--check']),
                    ('diff', ['diff', '--binary']), ('staged', ['diff', '--cached', '--binary']),
                    ('untracked', ['ls-files', '--others', '--exclude-standard'])]
        for name, argv in commands:
            result = subprocess.run(['git', '-C', str(repo), *argv], capture_output=True)
            (evidence / f'before-{label}-{name}.txt').write_bytes(result.stdout + result.stderr)
            if result.returncode:
                raise RuntimeError(f'git {label} {name} failed: {result.returncode}')
    original = json.loads((a.migration / 'evidence/preservation-before.json').read_text())
    (evidence / 'preservation-origin.json').write_text(json.dumps(original, indent=2) + '\n')
    for name in ('tmp', 'cache', 'cargo-home'):
        (run / name).mkdir()
    env = {k: str(run / 'tmp') for k in ('TMPDIR', 'TMP', 'TEMP')}
    env.update(XDG_CACHE_HOME=str(run / 'cache'), PYTHONPYCACHEPREFIX=str(run / 'cache/python'),
               CARGO_HOME=str(run / 'cargo-home'), CARGO_NET_OFFLINE='true',
               CMAKE_BUILD_PARALLEL_LEVEL='2', CARGO_BUILD_JOBS='2', LC_ALL='C', TZ='UTC')
    (evidence / 'environment.json').write_text(json.dumps(env, indent=2) + '\n')
    shutil.copy2(a.migration / 'evidence/run.py', evidence / 'run.py')
    vendor = a.migration / 'migration-package/host/toolchain/cargo-vendor'
    native.cargo_sources(a.migration / 'migration-package')
    (run / 'cargo-home/config.toml').write_text('[source.crates-io]\nreplace-with="vendor"\n[source.vendor]\ndirectory=' + json.dumps(str(vendor)) + '\n')
    native.materialize(a.frozen, run / 'baseline', entries, identity)
    shutil.copytree(run / 'baseline/host', run / 'host')
    (evidence / 'selection.json').write_text(json.dumps({
        'origin': str(a.frozen), 'source_sha256': native.SOURCE_SHA256,
        'selected_files': len(entries), 'copied_native_archives': 0,
        'host_candidate': 'host', 'source_baseline': 'baseline/source',
        'original_migration_archive_sha256': sha(a.migration / 'scu-h1-migration.tar.gz'),
        'space': {str(x): os.statvfs(x).f_bavail * os.statvfs(x).f_frsize for x in (run, Path('/'))},
    }, indent=2) + '\n')
    print(run)


if __name__ == '__main__':
    main()
