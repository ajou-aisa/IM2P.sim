#!/usr/bin/env python3
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import package


class PackageTests(unittest.TestCase):
    def test_paths(self):
        for name in ('../x', '/x', 'a/../x', '', 'a//b'):
            with self.assertRaises(ValueError):
                package.relative(name)
        self.assertEqual(package.relative('a/b'), Path('a/b'))

    def test_roundtrip_and_tamper(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'package'; root.mkdir()
            frozen = root / 'host/frozen'; frozen.mkdir(parents=True)
            source = frozen / 'source/a'; source.parent.mkdir(); source.write_bytes(b'source')
            (frozen/'integration-sha256.json').write_text(json.dumps({'source/a': package.digest(source)}))
            bit = root/'hardware/original.bit'; bit.parent.mkdir(); bit.write_bytes(b'bit')
            dcp = root/'flash/x86/route.dcp'; dcp.parent.mkdir(parents=True); dcp.write_bytes(b'dcp')
            rtl = root/'flash/x86/mkScuPipeline.v'; rtl.write_bytes(b'rtl')
            with patch.object(package, 'SOURCE', package.digest(frozen/'integration-sha256.json')), \
                 patch.object(package, 'BIT', package.digest(bit)), \
                 patch.object(package, 'DCP', package.digest(dcp)), \
                 patch.object(package, 'RTL', package.digest(rtl)):
                archive = Path(folder)/'bundle.tar.gz'
                with self.assertRaises(ValueError):
                    package.seal(root, root/'recursive.tar.gz')
                sealed = package.seal(root, archive)
                self.assertEqual(sealed['files'], 6)
                self.assertEqual(package.extract(archive, Path(folder)/'relocated')['status'], 'PASS')
                source.write_bytes(b'changed')
                with self.assertRaises(ValueError):
                    package.verify(root)
                with self.assertRaises(ValueError):
                    package.seal(root, archive)

    def test_archive_link_rejected_before_extract(self):
        with tempfile.TemporaryDirectory() as folder:
            archive = Path(folder)/'bad.tar.gz'
            with tarfile.open(archive, 'w:gz') as tar:
                link = tarfile.TarInfo('migration-package/link')
                link.type = tarfile.SYMTYPE; link.linkname = '/etc/passwd'; tar.addfile(link)
            out = Path(folder)/'out'
            with self.assertRaises(ValueError):
                package.extract(archive, out)
            self.assertFalse(out.exists())

    def test_symlink_inventory(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root/'link').symlink_to('/etc/passwd')
            with self.assertRaises(ValueError):
                package.inventory(root)


if __name__ == '__main__':
    unittest.main()
