#!/usr/bin/env python3
"""Small source-layout tests; no compiler or hardware, and all attempts retained."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import subprocess
import sys
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('assemble', Path(__file__).with_name('assemble.py'))
assemble = importlib.util.module_from_spec(spec)
spec.loader.exec_module(assemble)


def main():
    out = Path(tempfile.mkdtemp(prefix='scu-runtime-assemble-test-'))
    frozen, overlay = out / 'frozen', out / 'overlay'
    files = {'host/build-x86.sh': b'#!/bin/bash\necho original\n', 'host/a.txt': b'host',
             'source/a.bsv': b'core', 'params/a.h': b'params'}
    for name, data in files.items():
        path = frozen / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    (frozen / 'host/build-x86.sh').chmod(0o755)
    selected = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
    (frozen / 'integration-sha256.json').write_text(json.dumps(selected))
    source_hash = assemble.digest(frozen / 'integration-sha256.json')
    (frozen / 'identity.json').write_text(json.dumps(dict(assemble.native.SEMANTICS,
        source_sha256=source_hash, sim_archive='/forbidden/old.a', sim_archive_sha256='old')))
    overlay.mkdir()
    (overlay / 'build-x86.sh').write_text('#!/bin/bash\necho changed\n')
    (overlay / 'build-x86.sh').chmod(0o755)
    (overlay / 'new.py').write_text('print("new")\n')
    (overlay / '__pycache__').mkdir()
    (overlay / '__pycache__/ignored.pyc').write_bytes(b'ignored generated file')
    checks = []

    def rejects(label, action):
        try:
            action()
        except (OSError, ValueError, KeyError):
            checks.append(label)
        else:
            raise AssertionError('expected rejection: ' + label)

    with patch.object(assemble.native, 'SOURCE_SHA256', source_hash), patch.object(assemble.native, 'SOURCE_FILES', len(files)):
        root = out / 'selected'
        result = assemble.assemble(frozen, overlay, root)
        assert result['overlay_files'] == 2
        assert (root / 'source/a.bsv').read_bytes() == b'core'
        assert (root / 'provenance/host-overlay/host-before/build-x86.sh').read_bytes() == files['host/build-x86.sh']
        assert not (root / 'host/__pycache__').exists()
        assert json.loads((root / 'identity.json').read_text())['sim_archive'] is None
        checks.append('assemble-before-blobs-no-binaries')
        rejects('existing-assemble-output', lambda: assemble.assemble(frozen, overlay, root))
        rejects('sealed-source-is-not-editable', lambda: assemble.apply_overlay(frozen, overlay, frozen / 'host', out / 'bad-evidence'))
        rejects('duplicate-overlay', lambda: assemble.apply_overlay(frozen, overlay, root / 'host', out / 'again-evidence'))
        rejects('archive-inside-root', lambda: assemble.seal(root, root / 'bad.tar.gz'))
        archive = out / 'source.tar.gz'
        assembled_hashes = assemble.inventory(root)
        assemble.seal(root, archive)
        assert assemble.extract(archive, out / 'extracted')['status'] == 'PASS'
        assert assemble.inventory(out / 'extracted/llama-fpga-source') == assembled_hashes
        checks.append('seal-extract-byte-mode-equality')
        relocated = out / 'extracted/llama-fpga-source'
        for tool in ('assemble.py', 'test_standard_fail_closed.py', 'build_tests.py',
                     'test_cmake_contract.py', 'run_rtl.py', 'test_fingerprint.py',
                     'test_cli_selection.py', 'test_ppl_warmup.py'):
            command = [sys.executable, str(relocated / 'source/fpga/scu_runtime' / tool), '--help']
            probe = subprocess.run(command, text=True, capture_output=True)
            assert probe.returncode == 0, (tool, probe.stderr)
        checks.append('relocated-tool-help-no-experiment-imports')
        assert (relocated / 'source/fpga/scu_runtime/explicit-fpga-split-fix.json').is_file()
        checks.append('explicit-device-fix-provenance-retained')
        assert (relocated / 'source/fpga/scu_runtime/ppl-warmup-fix.json').is_file()
        checks.append('ppl-warmup-fix-provenance-retained')
        rejects('existing-extract-output', lambda: assemble.extract(archive, out / 'extracted'))
        rejects('existing-seal-manifest', lambda: assemble.seal(root, out / 'again.tar.gz'))
        (root / 'host/new.py').write_text('tampered')
        rejects('source-tamper', lambda: assemble.verify(root))
        (overlay / 'stale.a').write_bytes(b'archive')
        rejects('overlay-binary', lambda: assemble.overlay_files(overlay))
        bad_overlay = out / 'symlink-overlay'; bad_overlay.mkdir()
        (bad_overlay / 'bad').symlink_to('/etc/passwd')
        rejects('overlay-symlink', lambda: assemble.overlay_files(bad_overlay))
        for index, (name, kind) in enumerate((('../escape', 'file'), ('llama-fpga-source/link', 'link'), ('/absolute', 'file'))):
            bad_archive = out / f'bad-{index}.tar.gz'
            with tarfile.open(bad_archive, 'w:gz') as tar:
                member = tarfile.TarInfo(name)
                if kind == 'link':
                    member.type = tarfile.SYMTYPE; member.linkname = '/etc/passwd'; tar.addfile(member)
                else:
                    member.size = 1; tar.addfile(member, io.BytesIO(b'x'))
            target = out / f'bad-extract-{index}'
            rejects('archive-invalid-' + str(index), lambda: assemble.extract(bad_archive, target))
            assert not target.exists()
        assert all(assemble.digest(frozen / name) == expected for name, expected in selected.items())
        checks.append('original-frozen-preserved')
    (out / 'result.json').write_text(json.dumps({'status': 'PASS', 'checks': checks,
        'test_kind': 'isolated small test source; not production 1746-file validation'}, indent=2) + '\n')
    print(json.dumps({'status': 'PASS', 'checks': len(checks), 'out': str(out)}))


if __name__ == '__main__':
    main()
