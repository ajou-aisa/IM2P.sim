#!/usr/bin/env python3
"""New small host-quantized migration fixture; historical board payloads are not reconstructed.

Uses existing IFX1 reader, independent G1/G2 and IFR3 packet helper. No device or
matrix execution. ARM64 capture/comparison is a future explicit invocation.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import shlex
import struct
import subprocess
import sys
import time

from native_build import SOURCE_SHA256, verify_frozen

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = {'schema': 'im2p.migration_fixture.v1', 'abi': 5, 'protocol': 'IFR3',
            'profile': 'A8/W8/D16', 'numerical_revision': 'signed-scu-sat-v2',
            'output_domain': 2, 'route': 'Q8_H1/EXSIA/RMD_OFF', 'shape': [16, 16, 64]}
FILES = ('input-a-f32.bin', 'input-w-f32.bin', 'fixture.bin', 'activation-i8.bin',
         'weight-qs-i8.bin', 'h1-code-u8.bin', 'h1-offset-u16le.bin', 'channel-scale-f32le.bin',
         'theta-i16le.bin', 'beta-u32le.bin', 'expected-final-raw.bin', 'expected-fout.bin',
         'wire-a.bin', 'wire-w.bin', 'wire-scale.bin', 'full-run.request.bin', 'geometry.json')
HELPERS = {
    'tests/scu_block_scale/golden.py': '0b644b0acff95e78f7bb12407771d72b81e9b191b6b6425e54df6fba18b803b8',
    'tests/scu_block_scale/replay_golden.py': 'abfdf99c4ade4ef24751fda9a09b02f97e4bf9217ff263104514131b60f1ed28',
    'fpga/scu_block_scale/packets.py': '088fd51eaa08a513e81febd80d62f57cb157603c110b5dfd5a6f4f19edec34d4',
}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def helpers(root=ROOT):
    root = Path(root)
    for name, expected in HELPERS.items():
        if sha(root / name) != expected:
            raise ValueError('pinned portable helper SHA256 mismatch: ' + name)
    def load(name, relative):
        path = root / relative
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        # Compile the verified source bytes, never an unrelated sys.modules or pyc entry.
        exec(compile(path.read_bytes(), str(path), 'exec'), module.__dict__)
        return module
    previous = sys.modules.get('golden')
    try:
        sys.modules['golden'] = load('golden', 'tests/scu_block_scale/golden.py')
        replay = load('migration_replay_golden', 'tests/scu_block_scale/replay_golden.py')
    finally:
        if previous is None:
            sys.modules.pop('golden', None)
        else:
            sys.modules['golden'] = previous
    return replay, load('migration_ifr3_packets', 'fpga/scu_block_scale/packets.py')


def native_architecture(path):
    # Match native_build's existing executable / first-object-member ELF gate.
    # This is not an all-member proof for a deliberately mixed-architecture archive.
    path = Path(path)
    expected = {'x86_64': 62, 'aarch64': 183}.get(platform.machine())
    if expected is None:
        raise ValueError('unsupported native build architecture: ' + platform.machine())
    member = None
    if path.suffix == '.a':
        members = subprocess.check_output(['ar', 't', str(path)], universal_newlines=True, timeout=10).splitlines()
        member = next((name for name in members if name.endswith('.o')), None)
        if member is None:
            raise ValueError('archive has no object member: ' + str(path))
        header = subprocess.check_output(['ar', 'p', str(path), member], timeout=10)[:20]
    else:
        with path.open('rb') as stream:
            header = stream.read(20)
    if header[:6] != b'\x7fELF\x02\x01' or int.from_bytes(header[18:20], 'little') != expected:
        raise ValueError('ELF64 little-endian native architecture mismatch: ' + str(path))
    return {'machine': platform.machine(), 'ELF_machine': expected,
            'checked_scope': 'first_object_member' if member else 'executable', 'member': member}


def run(argv, out, label):
    began = time.monotonic()
    env = os.environ.copy()
    env.update({name: str(out / 'tmp') for name in ('TMPDIR', 'TMP', 'TEMP')})
    result = subprocess.run(list(map(str, argv)), cwd=out, env=env, timeout=120,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
    record = {'argv': list(map(str, argv)), 'cwd': str(out), 'exit': result.returncode,
              'seconds': time.monotonic() - began, 'stdout': result.stdout, 'stderr': result.stderr}
    (out / (label + '.json')).write_text(json.dumps(record, indent=2) + '\n')
    if result.returncode:
        raise RuntimeError('{} failed; original output retained'.format(label))
    return record


def build_exporter(snapshot, out, cxx):
    snapshot, out = Path(snapshot).resolve(), Path(out).resolve()
    compiler = Path(cxx)
    if not compiler.is_absolute() or not compiler.is_file() or not os.access(str(compiler), os.X_OK):
        raise ValueError('--cxx requires an absolute existing compiler executable')
    verify_frozen(snapshot)  # Fixed 607827... manifest, 1746 files, ABI5/profile and host/include pins.
    build = snapshot / 'host-build'
    target = build / 'CMakeFiles/scu_frontend_final.dir'
    flags = dict(line.split(' = ', 1) for line in (target / 'flags.make').read_text().splitlines() if ' = ' in line)
    libraries = [(build / value).resolve() for value in shlex.split((target / 'link.txt').read_text()) if value.endswith('.a')]
    if not libraries or any(snapshot not in path.parents for path in libraries):
        raise ValueError('library must belong to the explicitly selected snapshot')
    architecture = {str(path): native_architecture(path) for path in [compiler, *libraries]}
    source = Path(__file__).with_name('portable_fixture_export.cpp')
    inputs = [source, Path(__file__), Path(__file__).with_name('native_build.py'), target / 'flags.make', target / 'link.txt',
              snapshot / 'integration-sha256.json', *libraries]
    before = {str(path): sha(path) for path in inputs}
    out.mkdir(parents=True, exist_ok=False)
    (out / 'tmp').mkdir()
    (out / source.name).write_bytes(source.read_bytes())
    executable = out / 'portable-fixture-export'
    argv = [str(compiler), '-std=c++20', '-O2', '-pipe', *shlex.split(flags['CXX_DEFINES']),
            *shlex.split(flags['CXX_INCLUDES']), str(out / source.name), '-o', str(executable),
            '-Wl,--start-group', *map(str, libraries), '-Wl,--end-group', '-pthread', '-ldl', '-lm']
    run(argv, out, 'compile')
    architecture[str(executable)] = native_architecture(executable)
    if any(sha(path) != digest for path, digest in before.items()):
        raise ValueError('build input changed during compilation')
    report = {'kind': 'test-only host quantizer exporter', 'contract': CONTRACT,
              'build_machine': platform.machine(), 'snapshot': str(snapshot),
              'input_sha256': before, 'executable_sha256': sha(executable),
              'selected_source_sha256': SOURCE_SHA256, 'architecture': architecture,
              'execution': 'not run during build', 'device_access': False}
    (out / 'manifest.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


def fp32_inputs():
    # Exact dyadic FP32 values; same formula family as Graph::prepare, new seed73.
    m, n, k, seed = 16, 16, 64, 73
    a = [(int((i * 17 + seed * 13) % 251) - 125) / 32 for i in range(m * k)]
    w = [(int((j * 11 + l * 7 + seed * 5) % 253) - 126) * (1 + (l // 32) * 3) / 128
         for j in range(n) for l in range(k)]
    return struct.pack('<{}f'.format(len(a)), *a), struct.pack('<{}f'.format(len(w)), *w)


def derive(directory):
    """Field extraction + independent expected/wire creation; not a quantizer."""
    directory = Path(directory)
    replay, packets = helpers()
    fixture = replay.decode(directory)
    m, n, k = (fixture[name] for name in ('I', 'J', 'K'))
    if [m, n, k] != CONTRACT['shape']:
        raise ValueError('only the explicit M16/N16/K64 migration fixture is supported')
    raw, floats, unused_legacy, counts = replay.expected(fixture)
    if counts['zero_final_values'] == m * n:
        raise ValueError('vacuous all-zero final-integer fixture')
    a = bytes(value & 255 for value in fixture['A'])
    w = bytes(value & 255 for channel in fixture['weights'] for block in channel for value in block['qs'])
    codes = bytes(block['code'] for channel in fixture['weights'] for block in channel)
    offsets = b''.join(struct.pack('<H', channel[0]['residual']) for channel in fixture['weights'])
    shared = b''.join(struct.pack('<f', channel[0]['scale']) for channel in fixture['weights'])
    theta = struct.pack('<{}h'.format(len(fixture['theta'])), *fixture['theta'])
    beta = b''.join(struct.pack('<I', fixture['weights'][col][block]['beta']) for block in range(k // 32) for col in range(n))
    wire_a, wire_w, wire_scale = bytearray(m * 128), bytearray(k * 64), bytearray((k // 32) * 256)
    for row in range(m):
        wire_a[row * 128:row * 128 + k] = a[row * k:(row + 1) * k]
    for row in range(k):
        for col in range(n):
            wire_w[row * 64 + col] = fixture['weights'][col][row // 32]['qs'][row % 32] & 255
    for block in range(k // 32):
        wire_scale[block * 256:block * 256 + n * 4] = beta[block * n * 4:(block + 1) * n * 4]
    files = {'activation-i8.bin': a, 'weight-qs-i8.bin': w, 'h1-code-u8.bin': codes,
             'h1-offset-u16le.bin': offsets, 'channel-scale-f32le.bin': shared, 'theta-i16le.bin': theta,
             'beta-u32le.bin': beta, 'expected-final-raw.bin': raw, 'expected-fout.bin': floats,
             'wire-a.bin': bytes(wire_a), 'wire-w.bin': bytes(wire_w), 'wire-scale.bin': bytes(wire_scale),
             'full-run.request.bin': packets.packet(1, run=1, generation=1, m=m, n=n, k=k,
                                                   payload=wire_a + wire_w + wire_scale)}
    for name, data in files.items():
        with (directory / name).open('xb') as stream:
            stream.write(data)
    geometry = {name: fixture[name] for name in ('I', 'J', 'K', 'tile_I', 'tile_J', 'tile_K',
                'stripe_rows', 'row_offset', 'a_stride', 'output_stride', 'column_stride', 'theta_count', 'e_s', 'rho', 'sigma')}
    with (directory / 'geometry.json').open('x') as stream:
        stream.write(json.dumps(geometry, sort_keys=True, indent=2) + '\n')
    # The helper also calculates legacy dots; this capture does not compare them.
    counts.pop('legacy_block_dots_checked', None)
    return counts


def capture(export_build, out):
    export_build, out = Path(export_build).resolve(), Path(out).resolve()
    manifest = json.loads((export_build / 'manifest.json').read_text())
    executable = export_build / 'portable-fixture-export'
    if manifest['contract'] != CONTRACT or sha(executable) != manifest['executable_sha256']:
        raise ValueError('exporter contract/hash mismatch')
    wrapper_changes = {}
    for name, expected in manifest['input_sha256'].items():
        actual = sha(name)
        if actual != expected:
            if Path(name).resolve() != Path(__file__).resolve():
                raise ValueError('compiled exporter input changed: ' + name)
            wrapper_changes[name] = {'build_time_sha256': expected, 'capture_time_sha256': actual}
    verify_frozen(Path(manifest['snapshot']))
    native_architecture(executable)
    helpers()  # Reject changed Python oracle/packet inputs before creating or executing anything.
    out.mkdir(parents=True, exist_ok=False)
    (out / 'tmp').mkdir()
    a, w = fp32_inputs()
    (out / 'input-a-f32.bin').write_bytes(a)
    (out / 'input-w-f32.bin').write_bytes(w)
    result = run([executable, out / 'input-a-f32.bin', out / 'input-w-f32.bin', out / 'quantized'], out, 'export')
    if result['stdout'].count('PORTABLE_HOST_QUANTIZATION_PASS ') != 1:
        raise ValueError('actual host quantizer completion marker missing')
    (out / 'fixture.bin').write_bytes((out / 'quantized/fixture.bin').read_bytes())
    counts = derive(out)
    report = {'contract': CONTRACT, 'origin': 'new explicit FP32 seed73; not reconstructed historical board payload',
              'capture_machine': platform.machine(), 'exporter_manifest_sha256': sha(export_build / 'manifest.json'),
              'capture_wrapper_derivation': {'changes': wrapper_changes,
                  'policy': 'only this Python wrapper may differ; C++ source, flags and linked archives remain exact'},
              'exporter_sha256': sha(executable), 'quantizer_execution': 'actual native Q8_H1 and EXSIA host routines',
              'numerical_expected': 'existing independent Python bigint G1 and ordered binary64/float32 G2',
              'SCU_matrix_execution': 'NOT_RUN', 'ARM64_comparison': 'NOT_RUN', 'device_access': False,
              'counts': counts, 'sha256': {name: sha(out / name) for name in FILES},
              'helper_sha256': {str(path.relative_to(ROOT)): sha(path) for path in (
                  Path(__file__), Path(__file__).with_name('portable_fixture_export.cpp'),
                  Path(__file__).with_name('native_build.py'),
                  ROOT / 'tests/scu_block_scale/replay_golden.py', ROOT / 'tests/scu_block_scale/golden.py',
                  ROOT / 'fpga/scu_block_scale/packets.py')}}
    (out / 'manifest.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


def compare(left, right):
    left, right = Path(left), Path(right)
    for directory in (left, right):
        manifest = json.loads((directory / 'manifest.json').read_text())
        if manifest['contract'] != CONTRACT or set(manifest['sha256']) != set(FILES):
            raise ValueError('fixture schema/profile/file-set mismatch')
        if any(sha(directory / name) != digest for name, digest in manifest['sha256'].items()):
            raise ValueError('fixture integrity mismatch: {}'.format(directory))
    differences = []
    for name in FILES:
        a, b = (left / name).read_bytes(), (right / name).read_bytes()
        if a != b:
            first = next((at for at, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
            differences.append({'file': name, 'left_bytes': len(a), 'right_bytes': len(b), 'first_different_byte': first})
    return {'status': 'PASS' if not differences else 'FAIL', 'files_compared': len(FILES), 'differences': differences,
            'scope': 'captured codes/metadata/expected/wire byte equality; no matrix or device execution',
            'left': str(left.resolve()), 'right': str(right.resolve())}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='stage')
    build = commands.add_parser('build-exporter')
    build.add_argument('--snapshot', type=Path, required=True)
    build.add_argument('--out', type=Path, required=True)
    build.add_argument('--cxx', required=True)
    create = commands.add_parser('capture')
    create.add_argument('--export-build', type=Path, required=True)
    create.add_argument('--out', type=Path, required=True)
    comparison = commands.add_parser('compare')
    comparison.add_argument('left', type=Path)
    comparison.add_argument('right', type=Path)
    args = parser.parse_args()
    if args.stage is None:
        parser.error('select build-exporter, capture, or compare')
    if args.stage == 'build-exporter': result = build_exporter(args.snapshot, args.out, args.cxx)
    elif args.stage == 'capture': result = capture(args.export_build, args.out)
    else: result = compare(args.left, args.right)
    print(json.dumps(result, indent=2))
    return int(result.get('status') == 'FAIL')


if __name__ == '__main__':
    sys.exit(main())
