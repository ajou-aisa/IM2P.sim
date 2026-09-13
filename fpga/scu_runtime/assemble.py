#!/usr/bin/env python3
"""Materialize sealed SCU sources and a reviewed host-only deployment overlay."""
import argparse
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import shutil
import tarfile
import sys

_HELPER = Path(__file__).resolve().parents[1] / 'scu_migration/native_build.py'
_spec = importlib.util.spec_from_file_location('scu_native_materialize', _HELPER)
native = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(native)
digest = native.digest
regular_file = native.regular_file
write_json = native.write_json


def overlay_files(overlay):
    if overlay.is_symlink() or not overlay.is_dir():
        raise ValueError('regular overlay directory required')
    files = {}
    for path in sorted(overlay.rglob('*')):
        relative = path.relative_to(overlay).as_posix()
        if '__pycache__' in path.parts or path.suffix == '.pyc':
            continue
        if path.is_symlink():
            raise ValueError('overlay symlink rejected: ' + relative)
        if path.is_dir():
            continue
        path = regular_file(overlay, relative)
        if path.suffix in ('.o', '.a', '.so', '.bit', '.dcp', '.gguf', '.bin'):
            raise ValueError('binary/build artifact in host overlay: ' + relative)
        data = path.read_bytes()
        if len(data) > 8 * 1024 * 1024 or b'\0' in data:
            raise ValueError('unexpected binary/large overlay file: ' + relative)
        data.decode('utf-8')
        files[relative] = {'sha256': digest(path), 'bytes': len(data), 'mode': path.stat().st_mode & 0o777}
    if not files:
        raise ValueError('empty host overlay')
    return files


def apply_overlay(frozen, overlay, host, evidence):
    entries, identity = native.verify_frozen(frozen)
    changes = overlay_files(overlay)
    if host.is_symlink() or not host.is_dir():
        raise ValueError('ordinary fresh host tree required')
    if host.resolve().is_relative_to(frozen.resolve()):
        raise ValueError('refusing to edit the sealed candidate')
    # Check the entire selected host before touching any file, not only changed paths.
    for name, expected in entries.items():
        if name.startswith('host/') and digest(regular_file(host, name[5:])) != expected:
            raise ValueError('fresh host differs from sealed selection: ' + name)
    before = {}
    for name in changes:
        expected = entries.get('host/' + name)
        if os.path.lexists(host / name) and expected is None:
            raise ValueError('overlay would replace an unselected existing host file: ' + name)
        if expected is not None:
            before[name] = expected
    evidence.mkdir(parents=True, exist_ok=False)
    for name in before:
        dst = evidence / 'host-before' / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(regular_file(host, name), dst)
    write_json(evidence / 'before.json', {'files': before, 'source_manifest_sha256': native.SOURCE_SHA256})
    for name, row in changes.items():
        dst = host / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        if any(parent.is_symlink() for parent in (dst, *dst.parents)):
            raise ValueError('symlink in overlay destination: ' + str(dst))
        shutil.copy2(regular_file(overlay, name), dst)
        if digest(dst) != row['sha256']:
            raise ValueError('overlay copy mismatch: ' + name)
    selected = {}
    for name, expected in entries.items():
        if name.startswith('host/'):
            rel = name[5:]
            selected[rel] = {'sha256': digest(regular_file(host, rel)), 'origin': 'host-overlay' if rel in changes else 'sealed-host', 'baseline_sha256': expected}
    for name, row in changes.items():
        if name not in selected:
            selected[name] = dict(sha256=row['sha256'], origin='host-overlay-added', baseline_sha256=None)
    result = {'schema': 1, 'status': 'HOST_OVERLAY_APPLIED_NOT_BUILT',
              'time_utc': datetime.now(timezone.utc).isoformat(),
              'source_manifest_sha256': native.SOURCE_SHA256,
              'host_pin': identity['host_pin'], 'numerical': identity['numerical_revision'],
              'abi': 5, 'protocol': 'IFR3', 'domain': 2,
              'overlay_files': changes, 'host_files': selected,
              'binary_artifacts_copied': 0, 'physical_operations': 0,
              'before_blobs': 'host-before', 'source_root_is_ordinary_host_tree': True}
    write_json(evidence / 'after.json', result)
    return {'status': result['status'], 'host_files': len(selected), 'overlay_files': len(changes),
            'after_manifest_sha256': digest(evidence / 'after.json'), 'evidence': str(evidence)}


def assemble(frozen, overlay, out):
    entries, identity = native.verify_frozen(frozen)
    overlay_files(overlay)
    if os.path.lexists(out) or out.resolve().is_relative_to(frozen.resolve()) or out.resolve().is_relative_to(overlay.resolve()):
        raise ValueError('nonexistent output outside frozen/overlay required')
    native.materialize(frozen, out, entries, identity)
    result = apply_overlay(frozen, overlay, out / 'host', out / 'provenance/host-overlay')
    # Carry the small existing materializer dependency, not an experiment path.
    helpers = [(Path(__file__), 'tools/scu_runtime/assemble.py'),
               (_HELPER, 'tools/scu_migration/native_build.py')]
    for name in ('README.md', 'build-options-baseline.json', 'user-build-x86-before.patch'):
        path = Path(__file__).resolve().parent / name
        if path.is_file():
            helpers.append((path, 'tools/scu_runtime/' + name))
    # Keep new tests at their original source-relative location. Their existing
    # imports resolve source/fpga/scu_block_scale, never an experiment directory.
    runtime = Path(__file__).resolve().parent
    runtime_tools = ('assemble.py', 'README.md', 'build-options-baseline.json',
                     'user-build-x86-before.patch', 'build_tests.py', 'run_rtl.py', 'run_graph_cohort.py',
                     'test_assemble.py', 'test_build_options.py', 'test_dl_test_targets.py',
                     'test_cmake_contract.py', 'test_standard_fail_closed.py',
                     'test_fingerprint.py', 'test_cli_selection.py', 'test_ppl_warmup.py',
                     'repro_stock_configure.py', 'runtime-audit.md',
                     'runtime-source-audit.json', 'rmd-off-availability-fix.json',
                     'explicit-fpga-split-fix.json', 'ppl-warmup-fix.json')
    for name in runtime_tools:
        path = regular_file(runtime, name)
        helpers.append((path, 'source/fpga/scu_runtime/' + name))
    for name in ('full-cycle-reference.txt', 'full-cycle-provenance.json', 'origin.json'):
        path = regular_file(runtime, 'references/' + name)
        helpers.append((path, 'source/fpga/scu_runtime/references/' + name))
    for path in sorted((runtime / 'tests').glob('*')):
        if path.suffix in ('.cpp', '.py'):
            helpers.append((regular_file(runtime, 'tests/' + path.name),
                            'source/fpga/scu_runtime/tests/' + path.name))
    after = json.loads((out / 'provenance/host-overlay/after.json').read_text())
    for name, row in overlay_files(overlay).items():
        path = regular_file(overlay, name)
        if row['sha256'] != after['overlay_files'][name]['sha256']:
            raise ValueError('overlay changed during source assembly: ' + name)
        helpers.append((path, 'source/fpga/scu_runtime/host-overlay/' + name))
    helpers.append((_HELPER, 'source/fpga/scu_migration/native_build.py'))
    for source, relative in helpers:
        target = out / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    write_json(out / 'selected-files.json', {'source_manifest_sha256': native.SOURCE_SHA256, 'files': inventory(out)})
    return dict(result, selected_root=str(out), preserved_frozen_files=len(entries),
                note='integration-sha256.json remains the original baseline; use provenance/host-overlay/after.json for modified host selection')



def inventory(root):
    files = {}
    for path in sorted(root.rglob('*')):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise ValueError('package symlink rejected: ' + relative)
        if not path.is_file() or relative == 'deployment-manifest.json':
            continue
        if path.suffix in ('.o', '.a', '.so', '.bit', '.dcp', '.pyc') or '__pycache__' in path.parts:
            raise ValueError('source package contains generated/binary output: ' + relative)
        path = regular_file(root, relative)
        files[relative] = {'sha256': digest(path), 'bytes': path.stat().st_size, 'mode': path.stat().st_mode & 0o777}
    return files


def verify(root):
    manifest = json.loads(regular_file(root, 'deployment-manifest.json').read_text())
    if manifest.get('source_manifest_sha256') != native.SOURCE_SHA256:
        raise ValueError('deployment source origin mismatch')
    if inventory(root) != manifest['files']:
        raise ValueError('deployment path/hash/size/mode inventory mismatch')
    if digest(regular_file(root, 'integration-sha256.json')) != native.SOURCE_SHA256:
        raise ValueError('baseline source manifest mismatch')
    original = json.loads(regular_file(root, 'integration-sha256.json').read_text())
    host = json.loads(regular_file(root, 'provenance/host-overlay/after.json').read_text())
    for name, expected in original.items():
        selected = host['host_files'][name[5:]]['sha256'] if name.startswith('host/') else expected
        if digest(regular_file(root, name)) != selected:
            raise ValueError('selected source mismatch: ' + name)
    for name, row in host['host_files'].items():
        if digest(regular_file(root, 'host/' + name)) != row['sha256']:
            raise ValueError('selected overlay host mismatch: ' + name)
    return {'status': 'PASS', 'files': len(manifest['files']) + 1,
            'source_manifest_sha256': native.SOURCE_SHA256,
            'deployment_manifest_sha256': digest(root / 'deployment-manifest.json'),
            'binary_artifacts_copied': 0, 'physical_operations': 0}


def seal(root, archive):
    if root.is_symlink() or os.path.lexists(archive) or (root / 'deployment-manifest.json').exists():
        raise ValueError('ordinary source root and fresh manifest/archive required')
    if archive.resolve().is_relative_to(root.resolve()):
        raise ValueError('archive must be outside the source root')
    files = inventory(root)
    selected = json.loads(regular_file(root, 'selected-files.json').read_text())
    actual = {name: row for name, row in files.items() if name != 'selected-files.json'}
    if selected.get('source_manifest_sha256') != native.SOURCE_SHA256 or actual != selected.get('files'):
        raise ValueError('selected source changed or new build output added; assemble a fresh source package')
    write_json(root / 'deployment-manifest.json', {
        'schema': 1, 'status': 'SOURCE_RECIPE_PREPARED',
        'source_manifest_sha256': native.SOURCE_SHA256,
        'host_selection_manifest': 'provenance/host-overlay/after.json',
        'ABI': 5, 'protocol': 'IFR3', 'numerical': 'signed-scu-sat-v2', 'domain': 2,
        'FPGA': 'H1 only, A8/W8/D16, RMD OFF',
        'ARM64_build': 'NOT_RUN', 'ARM64_runtime': 'NOT_RUN',
        'files': files})
    verify(root)
    with archive.open('xb') as output:
        with tarfile.open(fileobj=output, mode='w:gz') as tar:
            tar.add(root, arcname='llama-fpga-source')
    return {'status': 'SEALED_SOURCE_ONLY', 'archive': str(archive),
            'sha256': digest(archive), 'bytes': archive.stat().st_size,
            'extracted_file_bytes': sum(row['bytes'] for row in files.values()) + (root / 'deployment-manifest.json').stat().st_size,
            'manifest_sha256': digest(root / 'deployment-manifest.json'), 'files': len(files) + 1}


def extract(archive, out):
    # Validate every member before creating a destination. No links, devices,
    # duplicate paths, parent traversal, or permission elevation are accepted.
    if os.path.lexists(out):
        raise ValueError('extraction output must not exist')
    with tarfile.open(archive, 'r:gz') as tar:
        members = tar.getmembers()
        names = set()
        for member in members:
            name = Path(member.name)
            if (not member.name or name.is_absolute() or '..' in name.parts or
                name.as_posix() != member.name or name.parts[0] != 'llama-fpga-source' or
                member.name in names or not (member.isdir() or member.isfile()) or member.mode & 0o7000):
                raise ValueError('unsafe archive member: ' + member.name)
            names.add(member.name)
        out.mkdir(parents=True, exist_ok=False)
        for member in members:
            target = out / member.name
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open('xb') as output, tar.extractfile(member) as source:
                    shutil.copyfileobj(source, output)
                target.chmod(member.mode & 0o777)
    return verify(out / 'llama-fpga-source')

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for command in ('assemble', 'apply-overlay'):
        item = sub.add_parser(command)
        item.add_argument('--frozen', type=Path, required=True)
        item.add_argument('--overlay', type=Path, required=True)
        if command == 'assemble':
            item.add_argument('--out', type=Path, required=True)
        else:
            item.add_argument('--host', type=Path, required=True)
            item.add_argument('--evidence', type=Path, required=True)
    item = sub.add_parser('seal')
    item.add_argument('--root', type=Path, required=True)
    item.add_argument('--archive', type=Path, required=True)
    item = sub.add_parser('verify')
    item.add_argument('--root', type=Path, required=True)
    item = sub.add_parser('extract')
    item.add_argument('--archive', type=Path, required=True)
    item.add_argument('--out', type=Path, required=True)
    args = vars(parser.parse_args(argv))
    command = args.pop('command')
    try:
        result = {'assemble': assemble, 'apply-overlay': apply_overlay, 'seal': seal, 'verify': verify, 'extract': extract}[command](**args)
        print(json.dumps(result, indent=2))
        return 0
    except (OSError, ValueError, UnicodeError, KeyError) as error:
        print('ASSEMBLE_STOP: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
