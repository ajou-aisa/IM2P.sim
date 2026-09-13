#!/usr/bin/env python3
"""New G1/G2 expected values from unchanged IFX1 host capture inputs.

The little-endian field layout follows fpga/full_replay/capture.cpp Fixture,
not sizeof(block_q8_h1). No quantizer, simulator, frontend, or DUT is executed.
Legacy expected files are parser cross-checks/comparisons, never oracle input.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import struct

from golden import REVISION, g1, g2, h1_metadata, self_check


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def decode(directory):
    data = (directory / 'fixture.bin').read_bytes()
    cursor = 0

    def take(format):
        nonlocal cursor
        size = struct.calcsize(format)
        if size > len(data) - cursor:
            raise ValueError('truncated IFX1 field')
        values = struct.unpack_from(format, data, cursor)
        cursor += size
        return values

    if take('<I')[0] != 0x31584649:
        raise ValueError('not IFX1')
    values = take('<13Q')
    names = ('I', 'J', 'K', 'tile_I', 'tile_J', 'tile_K', 'stripe_rows',
             'row_offset', 'a_stride', 'output_stride', 'column_stride',
             'run_id', 'theta_count')
    f = dict(zip(names, values))
    m, n, k = f['I'], f['J'], f['K']
    if not (0 < m <= 336 and 0 < n <= 48 and 0 < k <= 96 and k % 32 == 0):
        raise ValueError('IFX1 capacity/alignment')
    if not (f['row_offset'] == 0 and f['a_stride'] == k and
            0 < f['column_stride'] <= 2 and
            n * f['column_stride'] <= f['output_stride'] <= 128 and
            f['stripe_rows'] > 0 and 0 < f['theta_count'] <= 32):
        raise ValueError('IFX1 layout')
    f['e_s'], f['rho'], f['sigma'] = take('<hhi')
    f['theta'] = take('<' + 'h' * f['theta_count'])
    if f['theta_count'] != (m + f['stripe_rows'] - 1) // f['stripe_rows']:
        raise ValueError('IFX1 stripe metadata count')
    for theta in f['theta']:
        if theta == -32768 or not math.isfinite(math.ldexp(1.0, theta)):
            raise ValueError('invalid captured theta')
    f['A'] = take('<' + 'b' * (m * k))
    weights = []
    for column in range(n):
        channel = []
        for block in range(k // 32):
            qs = take('<32b')
            code, scale, residual = take('<BfH')
            beta = h1_metadata(code, residual)
            if not math.isfinite(scale) or scale < 0:
                raise ValueError('invalid native H1 scale')
            channel.append(dict(qs=qs, code=code, scale=scale, residual=residual, beta=beta))
        if len({x['residual'] for x in channel}) != 1 or len({
                struct.pack('<f', x['scale']) for x in channel}) != 1:
            raise ValueError('H1 channel shared R/S contract')
        weights.append(channel)
    if cursor != len(data):
        raise ValueError('trailing IFX1 data')
    f['weights'] = weights
    return f


def expected(f):
    m, n, k = (f[key] for key in ('I', 'J', 'K'))
    raw = bytearray()
    floats = bytearray(struct.pack('<f', 17.0) * (m * f['output_stride']))
    legacy_block_dots = [0] * (m * n * (k // 32))
    counts = dict(logical_values=m * n, padding_values=m * (f['output_stride'] - n),
                  fragments_evaluated=0, scu_saturations=0, commit_saturations=0,
                  zero_final_values=0, legacy_block_dots_checked=len(legacy_block_dots))
    for row in range(m):
        activation_scale = math.ldexp(1.0, f['theta'][row // f['stripe_rows']])
        for column in range(n):
            partials, metadata = [], []
            for block, w in enumerate(f['weights'][column]):
                block_dot = 0
                for fragment in range(2):
                    start = fragment * 16
                    # Python arbitrary integers preserve exact signed A8/W8 dot.
                    dot = sum(f['A'][row * k + block * 32 + start + lane] * w['qs'][start + lane]
                              for lane in range(16))
                    if not -(1 << 19) <= dot < (1 << 19):
                        raise ValueError('A8/D16 INT20 PE partial range')
                    partials.append(dot)
                    metadata.append(w['beta'])
                    block_dot += dot
                legacy_block_dots[(block * m + row) * n + column] = block_dot
            integer, trace = g1(partials, metadata, 4, 32)
            counts['fragments_evaluated'] += len(trace)
            counts['scu_saturations'] += sum(x['scu_saturated'] for x in trace)
            counts['commit_saturations'] += sum(x['accumulator_saturated'] for x in trace)
            counts['zero_final_values'] += integer == 0
            raw += struct.pack('<i', integer)
            bits = g2(integer, f['weights'][column][0]['scale'], activation_scale)
            offset = 4 * (row * f['output_stride'] + column * f['column_stride'])
            struct.pack_into('<I', floats, offset, bits)
    legacy_raw = struct.pack('<' + 'i' * len(legacy_block_dots), *legacy_block_dots)
    return bytes(raw), bytes(floats), legacy_raw, counts


def generate(root, output):
    self_check()
    # Six IFR1 inputs plus the two distinct long IFR2 inputs. The IFR2 small
    # input duplicates IFR1, but supplies the original live producer F32 input.
    inputs = sorted((root / 'fpga/full_replay/fixtures').glob('*/fixture.bin'))
    inputs += sorted((root / 'fpga/dense_pipeline/fixtures').glob('m321*/fixture.bin'))
    if len(inputs) != 8 or len({digest(path) for path in inputs}) != 8:
        raise ValueError('expected exactly eight unique preserved inputs')
    identities = {}
    for path in inputs:
        for name in ('fixture.bin', 'manifest.json', 'expected-raw.bin', 'expected-fout.bin', 'input-f32.bin'):
            source = path.with_name(name)
            if source.is_file():
                identities[str(source.relative_to(root))] = digest(source)
    small = root / 'fpga/dense_pipeline/fixtures/m16n16k32'
    if digest(small / 'fixture.bin') != digest(root / 'fpga/full_replay/fixtures/m16n16k32/fixture.bin'):
        raise ValueError('small dense fixture alias differs')
    identities[str((small / 'fixture.bin').relative_to(root))] = digest(small / 'fixture.bin')
    identities[str((small / 'input-f32.bin').relative_to(root))] = digest(small / 'input-f32.bin')
    generated = []
    for path in inputs:
        fixture = decode(path.parent)
        raw, floats, legacy_raw, counts = expected(fixture)
        if legacy_raw != path.with_name('expected-raw.bin').read_bytes():
            raise ValueError('independent captured K32 dots disagree with legacy expected')
        old_floats = path.with_name('expected-fout.bin').read_bytes()
        if len(old_floats) != len(floats):
            raise ValueError('legacy output layout length')
        differing = sum(floats[at:at + 4] != old_floats[at:at + 4] for at in range(0, len(floats), 4))
        counts['legacy_fout_different_values'] = differing
        generated.append((path, fixture, raw, floats, counts))
    # Validate all inputs before creating any output; old captures are read only.
    output.mkdir(parents=True, exist_ok=False)
    report = dict(schema=1, numerical_revision=REVISION, profile='A8/W8/DIM16',
                  route='native Q8_H1', residual='OFF', output_domain=2,
                  input_policy='unchanged original IFX1 payload; no requantization',
                  oracle='Python bigint G1 per K16 fragment, G2 binary64 then float32',
                  execution='golden generation only; actual native DUT replay NOT RUN',
                  unique_inputs=8, logical_invocations_executed=0,
                  old_expected_policy='preserved; K32 dots parser cross-check, fout comparison only',
                  source_sha256={str(Path(__file__).relative_to(root)): digest(Path(__file__)),
                                 'tests/scu_block_scale/golden.py': digest(Path(__file__).with_name('golden.py'))},
                  input_sha256=identities, fixtures=[])
    total_bytes = 0
    for path, fixture, raw, floats, counts in generated:
        directory = output / path.parent.name
        directory.mkdir()
        (directory / 'expected-final-raw.bin').write_bytes(raw)
        (directory / 'expected-fout.bin').write_bytes(floats)
        total_bytes += len(raw) + len(floats)
        report['fixtures'].append(dict(name=path.parent.name,
            source=str(path.relative_to(root)), shape=[fixture[x] for x in ('I', 'J', 'K')],
            tile_factors=[fixture[x] for x in ('tile_I', 'tile_J', 'tile_K')],
            stripe_rows=fixture['stripe_rows'], theta=fixture['theta'],
            output_row_stride=fixture['output_stride'], output_column_stride=fixture['column_stride'],
            raw_layout='row-major final signed32 little-endian', counts=counts))
    report['generated_payload_bytes'] = total_bytes
    (output / 'manifest.json').write_text(json.dumps(report, indent=2) + '\n')
    hashes = {str(path.relative_to(output)): digest(path) for path in sorted(output.rglob('*')) if path.is_file()}
    (output / 'sha256.json').write_text(json.dumps(hashes, indent=2) + '\n')
    if sum(path.stat().st_size for path in output.rglob('*') if path.is_file()) > 1024 * 1024:
        raise ValueError('bounded output exceeds 1 MiB')
    if any(digest(root / path) != value for path, value in identities.items()):
        raise ValueError('original fixture changed during generation')
    print('IFX1_G1_G2_GOLDEN_PASS unique_inputs=8 DUT_invocations=0 '
          f'logical_expected_values={sum(x[4]["logical_values"] for x in generated)} '
          f'payload_bytes={total_bytes} original_inputs_preserved=1')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    generate(args.root.resolve(), args.out.resolve())
