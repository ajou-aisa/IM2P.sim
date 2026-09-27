"""Unit-only native provenance graphs and capture contexts; never execution proof."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.gemmini_resolve_profile import JsonValue
from scripts.real_lib_manifest import sha256
from sim.cycle import current_run_capture as capture
from sim.cycle.npu_trace_schema import Record, object_value


def save(path: Path, document: Record) -> Record:
    path.write_text(json.dumps(document))
    return {'path': str(path), 'sha256': sha256(path)}


def graph(build: Path) -> tuple[Path, Path]:
    target = build / 'tests/CMakeFiles' / (capture.EXECUTABLE + '.dir')
    target.mkdir(parents=True)
    (build / 'bin').mkdir()
    (build / 'bin' / capture.EXECUTABLE).write_bytes(b'unit-only binary')
    obj = target / 'producer.cpp.o'
    obj.write_bytes(b'unit-only object')
    (target / 'flags.make').write_text('unit-only flags')
    (target / 'link.txt').write_text(f'c++ CMakeFiles/{capture.EXECUTABLE}.dir/producer.cpp.o -o ../bin/{capture.EXECUTABLE}\n')
    source = capture.WORKSPACE / capture.PRODUCER
    Path(str(obj) + '.d').write_text(f'{obj.relative_to(build)}: {source}\n')
    (build / 'CMakeCache.txt').write_text('unit-only cache')
    (build / 'compile_commands.json').write_text(json.dumps([
        {'directory': str(build / 'tests'), 'file': str(source),
         'command': f'c++ -o {obj} -c {source}'}]))
    return obj, target


class CurrentRunCaptureBindingTest(unittest.TestCase):
    def test_unselected_depfile_cannot_attest_selected_producer(self) -> None:
        # Given only an unrelated depfile lists the real producer source.
        with tempfile.TemporaryDirectory() as directory:
            build = Path(directory).resolve()
            (build / 'unrelated.o.d').write_text(f'unrelated.o: {capture.WORKSPACE / capture.PRODUCER}\n')
            # When checking selected producer dependencies, then reject this union.
            with self.assertRaises(ValueError):
                capture.dependency_paths(build)

    def test_selected_depfile_must_name_its_object_and_actual_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            build = Path(directory).resolve()
            obj, _ = graph(build)
            depfile = Path(str(obj) + '.d')
            depfile.write_text(f'unrelated.o: {capture.WORKSPACE / capture.PRODUCER}\n')
            with self.assertRaises(ValueError):
                capture.dependency_paths(build)

    def test_unselected_archive_is_not_part_of_executable_closure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            build = Path(directory).resolve()
            graph(build)
            unused = build / 'unselected.a'
            unused.write_bytes(b'unit-only unrelated archive')
            self.assertNotIn(unused, capture.build_artifacts(build))

    def test_selected_depfile_cannot_omit_compiled_translation_unit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            build = Path(directory).resolve()
            obj, _ = graph(build)
            unrelated = build / 'other.cpp'
            unrelated.write_text('unit-only source')
            Path(str(obj) + '.d').write_text(f'{obj.relative_to(build)}: {unrelated}\n')
            with self.assertRaisesRegex(ValueError, 'omits actual translation unit'):
                capture.dependency_paths(build)

    def test_compile_database_cannot_name_a_different_actual_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            build = Path(directory).resolve()
            obj, _ = graph(build)
            unrelated = build / 'other.cpp'
            unrelated.write_text('unit-only source')
            (build / 'compile_commands.json').write_text(json.dumps([
                {'directory': str(build), 'file': str(capture.WORKSPACE / capture.PRODUCER),
                 'command': f'c++ -o {obj} -c {unrelated}'}]))
            with self.assertRaisesRegex(ValueError, 'source differs'):
                capture.dependency_paths(build)

    def test_selected_link_graph_includes_transitive_archive_and_objects(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            build = Path(directory).resolve()
            obj, target = graph(build)
            nested = build / 'lib/CMakeFiles/helper.dir'
            nested.mkdir(parents=True)
            helper_obj = nested / 'helper.cpp.o'
            helper_obj.write_bytes(b'unit-only helper object')
            Path(str(helper_obj) + '.d').write_text(f'{helper_obj.relative_to(build)}: {capture.WORKSPACE / capture.PRODUCER}\n')
            (nested / 'flags.make').write_text('unit-only helper flags')
            archive = build / 'lib/helper.a'
            archive.write_bytes(b'unit-only archive')
            (nested / 'link.txt').write_text('ar qc helper.a CMakeFiles/helper.dir/helper.cpp.o\nranlib helper.a\n')
            (target / 'link.txt').write_text(f'c++ {obj} ../lib/helper.a -o ../bin/{capture.EXECUTABLE}\n')
            closure = capture.build_artifacts(build)
            self.assertTrue({obj, helper_obj, archive, nested / 'link.txt'} <= closure)

    def test_pre_capture_context_binds_binary_cwd_and_runtime_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            binary = root / capture.EXECUTABLE
            binary.write_bytes(b'unit-only binary, never executed')
            fixtures = root / 'fixtures'
            fixtures.mkdir()
            command: list[JsonValue] = [str(binary), '--case', 'production-fixtures']
            environment: Record = {'GEMMINI_LOG_DIR': str(fixtures)}
            pre: Record = {'binary_sha256': sha256(binary), 'command': command,
                           'cwd': str(capture.ROOT), 'environment': environment,
                           'build_artifacts': {}, 'runtime_dependencies': {}}
            receipt = save(root / 'receipt.json', {'command': command})
            row: Record = {'fixture_root': str(fixtures), 'capture_receipt': receipt,
                           'executable': {'path': str(binary), 'sha256': sha256(binary)},
                           'build_artifacts': {}, 'runtime_dependencies': {}}
            changes: tuple[Record, ...] = ({'binary_sha256': '0' * 64}, {'cwd': str(root)},
                       {'command': ['true']}, {'runtime_dependencies': {'changed': 'bytes'}},
                       {'build_artifacts': {'changed': 'bytes'}},
                       {'environment': {'GEMMINI_LOG_DIR': str(root)}})
            for index, change in enumerate(changes):
                context: Record = {'cwd': str(capture.ROOT), 'environment': environment,
                                   'capture_receipt_sha256': receipt['sha256'],
                                   'pre_capture_context': save(root / f'pre-{index}.json', {**pre, **change})}
                row['capture_context'] = save(root / f'context-{index}.json', context)
                with self.subTest(index=index), self.assertRaises(ValueError):
                    capture.capture_root(row)
            context = {'cwd': str(capture.ROOT), 'environment': environment,
                       'capture_receipt_sha256': receipt['sha256'],
                       'pre_capture_context': save(root / 'pre-good.json', pre)}
            row['capture_context'] = save(root / 'context-good.json', context)
            self.assertEqual(capture.capture_root(row), fixtures)
            object_value(context['pre_capture_context'])['sha256'] = '0' * 64
            row['capture_context'] = save(root / 'context-stale.json', context)
            with self.assertRaises(ValueError):
                capture.capture_root(row)


if __name__ == '__main__':
    unittest.main()
