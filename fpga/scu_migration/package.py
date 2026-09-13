#!/usr/bin/env python3
"""Copy the sealed selection, then seal/verify a relocatable preparation bundle."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import tarfile

SOURCE = '607827f129c61b60dffd9e9fb02fd0ceb4a7ab2abbeb25bd6b08d4d9b2782d69'
BIT = '2c5516e2bae4d5f960697537bb1501d42470b7971be5986b6e59ad8cc6586984'
DCP = '828c09cf0f718c535957c72c4a69bfc954ac306e06f44c467530ef4599004995'
RTL = '1833a6a4f53e48fa18f0afb188fbd2858a7584376701a333374025174d061c26'


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def relative(name):
    p = PurePosixPath(name)
    if not name or p.is_absolute() or '..' in p.parts or str(p) != name:
        raise ValueError('unsafe relative path: ' + name)
    return Path(*p.parts)


def regular(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError('regular file required: ' + str(path))


def assemble(candidate, approval, board, out):
    source_manifest = candidate / 'integration-sha256.json'
    if digest(source_manifest) != SOURCE:
        raise ValueError('frozen source identity mismatch')
    selected = json.loads(source_manifest.read_text())
    records = []
    for name, sha in selected.items():
        p = candidate / relative(name)
        if name.split('/')[0] not in ('source', 'host', 'params'):
            raise ValueError('unexpected source role')
        regular(p)
        if digest(p) != sha:
            raise ValueError('source mismatch: ' + name)
        records.append((p, 'host/frozen/' + name, sha, 'sealed-source'))
    manifest = json.loads((approval / 'candidate-manifest.json').read_text())
    for key, destination, expected in [
        ('bitstream', 'hardware/original.bit', BIT),
        ('routed_DCP', 'flash/x86/route.dcp', DCP),
        ('production_RTL', 'flash/x86/mkScuPipeline.v', RTL)]:
        item = manifest['artifacts'][key]
        p = Path(item['path']); regular(p)
        if item['sha256'] != expected or digest(p) != expected:
            raise ValueError('hardware mismatch: ' + key)
        records.append((p, destination, expected, 'original-hardware'))
    for folder, prefix in [(approval, 'manifests/original/package02'),
                           (board, 'manifests/original/board-correctness')]:
        for p in folder.iterdir():
            if p.is_file() and p.suffix in ('.json', '.md', '.txt'):
                regular(p)
                records.append((p, prefix + '/' + p.name, digest(p), 'historical-evidence'))
    for name in ('identity.json', 'integration-sha256.json'):
        p = candidate / name
        records.append((p, 'host/frozen/' + name, digest(p), 'original-source-manifest'))
    # Preserve the exact cycle file, including its original hardware pin.
    for name in ('full-cycle-reference.txt', 'full-cycle-provenance.json'):
        p = approval / name
        records.append((p, 'plans/' + name, digest(p), 'original-cycle-reference'))
    out.mkdir(parents=True, exist_ok=False)
    relocation = []
    for src, name, sha, role in records:
        dst = out / relative(name)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        if digest(dst) != sha:
            raise ValueError('copy mismatch: ' + name)
        relocation.append(dict(path=name, original_path=str(src), sha256=sha,
                               bytes=dst.stat().st_size, role=role))
    target = out / 'manifests/derived/relocation.json'
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(dict(status='PREPARATION_ONLY', source_manifest_sha256=SOURCE,
                                      selected_source_count=len(selected), files=relocation), indent=2)+'\n')
    return dict(selected_source_count=len(selected), copied_files=len(records),
                copied_bytes=sum((out / r['path']).stat().st_size for r in relocation))


def inventory(root):
    entries = {}
    for p in sorted(root.rglob('*')):
        if p.is_symlink():
            raise ValueError('package symlink forbidden: ' + str(p))
        if p.is_file() and p != root / 'manifest.json':
            entries[p.relative_to(root).as_posix()] = dict(sha256=digest(p), bytes=p.stat().st_size,
                                                          mode=p.stat().st_mode & 0o777)
    return entries


def seal(root, archive):
    if archive.resolve().is_relative_to(root.resolve()):
        raise ValueError('archive must be outside package tree')
    if (root / 'manifest.json').exists() or archive.exists():
        raise ValueError('fresh manifest/archive required')
    entries = inventory(root)
    (root / 'manifest.json').write_text(json.dumps(dict(schema=1, status='PREPARATION_ONLY',
        source_manifest_sha256=SOURCE, original_bitstream_sha256=BIT,
        board_operations_this_task=0, files=entries), indent=2)+'\n')
    with archive.open('xb') as raw:
        with tarfile.open(fileobj=raw, mode='w:gz') as tar:
            tar.add(root, arcname='migration-package', recursive=True)
    return dict(archive=str(archive), bytes=archive.stat().st_size, sha256=digest(archive),
                extracted_file_bytes=sum(x['bytes'] for x in entries.values())+(root/'manifest.json').stat().st_size,
                files=len(entries)+1, manifest_sha256=digest(root/'manifest.json'))


def verify(root):
    manifest = json.loads((root / 'manifest.json').read_text())
    if inventory(root) != manifest['files']:
        raise ValueError('package file/hash/mode inventory mismatch')
    frozen = root / 'host/frozen'
    if digest(frozen / 'integration-sha256.json') != SOURCE:
        raise ValueError('source manifest identity')
    for name, sha in json.loads((frozen / 'integration-sha256.json').read_text()).items():
        if digest(frozen / relative(name)) != sha:
            raise ValueError('frozen input mismatch: ' + name)
    if digest(root / 'hardware/original.bit') != BIT:
        raise ValueError('original bitstream identity')
    if digest(root / 'flash/x86/route.dcp') != DCP or digest(root / 'flash/x86/mkScuPipeline.v') != RTL:
        raise ValueError('routed DCP/production RTL identity')
    return dict(status='PASS', files=len(manifest['files'])+1, physical_operations=0)


def extract(archive, out):
    # Refuse links/devices/traversal instead of trusting tar extraction defaults.
    with tarfile.open(archive, 'r:gz') as tar:
        members = tar.getmembers()
        names = set()
        for member in members:
            name = relative(member.name)
            if name.parts[0] != 'migration-package' or member.name in names:
                raise ValueError('archive root or duplicate path')
            names.add(member.name)
            if not (member.isdir() or member.isfile()) or member.mode & 0o7000:
                raise ValueError('archive link/device/special mode')
        out.mkdir(parents=True, exist_ok=False)
        for member in members:
            dst = out / relative(member.name)
            if member.isdir():
                dst.mkdir(parents=True, exist_ok=True)
            else:
                dst.parent.mkdir(parents=True, exist_ok=True)
                with dst.open('xb') as f, tar.extractfile(member) as src:
                    shutil.copyfileobj(src, f)
                dst.chmod(member.mode & 0o777)
    return verify(out / 'migration-package')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    a = sub.add_parser('assemble')
    for name in ('candidate', 'approval', 'board', 'out'):
        a.add_argument('--'+name, type=Path, required=True)
    s = sub.add_parser('seal'); s.add_argument('root', type=Path); s.add_argument('archive', type=Path)
    v = sub.add_parser('verify'); v.add_argument('root', type=Path)
    e = sub.add_parser('extract'); e.add_argument('archive', type=Path); e.add_argument('out', type=Path)
    args = vars(p.parse_args()); command = args.pop('command')
    print(json.dumps(globals()[command](**args), indent=2))


if __name__ == '__main__':
    main()
