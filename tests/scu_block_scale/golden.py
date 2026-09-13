#!/usr/bin/env python3
"""Independent integer/float oracle. No simulator, frontend, or DUT output imports."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import struct

ZERO = 0x80000000
REVISION = 'signed-scu-sat-v2'
STAGING_REVISION = 'scu-wrap-staging-v1'


def clamp(value, width):
    if width not in (32, 64):
        raise ValueError('architectural width must be 32 or 64')
    return min((1 << (width - 1)) - 1, max(-(1 << (width - 1)), value))


def h1_metadata(code, residual):
    if not 0 <= code <= 255 or not 0 <= residual <= 65535:
        raise ValueError('H1 stored metadata range')
    return code + residual


def hp1_metadata(exponent):
    if exponent == -32768:
        return ZERO
    if not 0 <= exponent <= 32767:
        raise ValueError('HP1 stored exponent range')
    return exponent


def g1(partials, metadata, operation, width):
    if not partials or len(partials) != len(metadata):
        raise ValueError('one metadata value per nonempty fragment')
    accumulator = None
    trace = []
    for partial, value in zip(partials, metadata):
        if operation == 4:
            if not 0 <= value <= 65790:
                raise ValueError('reserved unsigned scale carrier')
            exact = partial * value
        elif operation == 5:
            if value != ZERO and not 0 <= value <= 32767:
                raise ValueError('reserved shift carrier')
            exact = 0 if value == ZERO or partial == 0 else partial * (1 << value)
        else:
            raise ValueError('SCU operation must be 4 or 5')
        scaled = clamp(exact, width)
        sum_exact = scaled if accumulator is None else accumulator + scaled
        accumulator = clamp(sum_exact, width)
        trace.append({'partial': partial, 'metadata': value, 'scaled': scaled,
                      'scu_saturated': scaled != exact, 'accumulator': accumulator,
                      'accumulator_saturated': accumulator != sum_exact})
    return accumulator, trace


def g2(integer, shared_scale, activation_scale):
    # Python float is binary64. Keep both operations distinct and in this order.
    first = float(integer) * float(shared_scale)
    second = first * float(activation_scale)
    try:
        return struct.unpack('<I', struct.pack('<f', second))[0]
    except OverflowError:
        return 0xff800000 if second < 0 else 0x7f800000


def self_check():
    assert h1_metadata(0, 0) == 0 and h1_metadata(255, 65535) == 65790
    assert hp1_metadata(0) == 0 and hp1_metadata(-32768) == ZERO
    assert g1([16] * 4, [1, 1, 256, 256], 4, 32)[0] == 8224
    assert g2(8224, 1 / 256, 1) == 0x42008000
    assert g2(-8224, 1 / 256, 1) == 0xc2008000
    for width in (32, 64):
        high, low = (1 << (width - 1)) - 1, -(1 << (width - 1))
        assert g1([high, 1, -1], [1, 1, 1], 4, width)[0] == high - 1
        assert g1([low, -1, 1], [1, 1, 1], 4, width)[0] == low + 1
        assert g1([1, -1], [32767, 32767], 5, width)[0] == -1
        assert g1([-1], [width - 1], 5, width)[0] == low
        for exponent in (0, 15, 16, 31, 32, 63, 64, 32767):
            assert g1([0], [exponent], 5, width)[0] == 0
        assert g1([high, low], [ZERO, ZERO], 5, width)[0] == 0
    for function, arguments in [(h1_metadata, (-1, 0)), (h1_metadata, (256, 0)),
                                (h1_metadata, (0, 65536)), (hp1_metadata, (-1,)),
                                (hp1_metadata, (32768,)),
                                (g1, ([1], [65791], 4, 32)),
                                (g1, ([1], [0x80000001], 5, 64))]:
        try:
            function(*arguments)
        except ValueError:
            continue
        raise AssertionError('invalid encoding accepted')


def cases():
    def make(name, route='h1', codes=(0, 255), residual=1, scale=1 / 256,
             activation=(1, 1, 1, 1), weight=(1, 1, 1, 1), theta=0, mutate=0):
        return dict(name=name, route=route, codes=list(codes), residual=residual, scale=scale,
                    activation=list(activation), weight=list(weight), theta=theta, mutate=mutate)
    result = [make('h1_boundary'), make('h1_beta_changes_integer', residual=2),
              make('h1_shared_s_only_changes_float', scale=1 / 128),
              make('h1_negative_partial', activation=(-1, -1, -1, -1)),
              make('h1_zero_factor', codes=(0, 0), residual=0),
              make('h1_zero_channel', codes=(0, 0), residual=0, scale=0),
              make('h1_max_factor', codes=(255, 255), residual=65535),
              make('h1_owned_metadata', mutate=1),
              make('h1_activation_scale_only', theta=-3),
              make('h1_fragment_saturation', codes=(255, 255), residual=65535,
                   activation=(-128, -128, 127, 127), weight=(-128,) * 4)]
    for exponent in (0, 15, 16, 31, 32, 63, 32767, -32768):
        result.append(make('hp1_m' + str(exponent).replace('-', 'neg'), route='hp1',
                           codes=(exponent, exponent), residual=0))
    result += [make('hp1_negative_saturation', route='hp1', codes=(32767, 32767),
                    residual=0, activation=(-1,) * 4),
               make('hp1_zero_partial_large_exponent', route='hp1', codes=(32767, 32767),
                    residual=0, activation=(0,) * 4),
               make('hp1_zero_then_nonzero', route='hp1', codes=(-32768, 0), residual=0),
               make('hp1_owned_metadata', route='hp1', codes=(0, 4), residual=0, mutate=1)]
    return result


def generate(out, overflow_free_only, carrier_edges=False):
    out.mkdir(parents=True, exist_ok=False)
    expected, rows = {}, []
    selected = cases()
    if carrier_edges:
        selected = [dict(name=f'h1_c{code}_r{offset}_sign{sign}', route='h1',
                         codes=[code, code], residual=offset, scale=1 / 256,
                         activation=[sign] * 4, weight=[1] * 4, theta=0, mutate=0)
                    for code in (0, 1, 127, 128, 200, 255)
                    for offset in (0, 1, 256, 65535) for sign in (1, -1)]
    for case in selected:
        scale = struct.unpack('<f', struct.pack('<f', case['scale']))[0]
        assert math.isfinite(scale) and scale >= 0
        carriers = [h1_metadata(code, case['residual']) if case['route'] == 'h1'
                    else hp1_metadata(code) for code in case['codes']]
        partials = [16 * a * w for a, w in zip(case['activation'], case['weight'])]
        metadata = [carriers[fragment // 2] for fragment in range(4)]
        integer, trace = g1(partials, metadata, 4 if case['route'] == 'h1' else 5, 32)
        overflow_free = not any(row['scu_saturated'] or row['accumulator_saturated'] for row in trace)
        if overflow_free_only and not overflow_free:
            continue
        expected[case['name']] = {'integer': integer, 'float32_bits': g2(integer, scale, 2.0 ** case['theta']),
                                  'output_domain': 2, 'callbacks': 1, 'metadata': carriers,
                                  'operation': 4 if case['route'] == 'h1' else 5,
                                  'overflow_free': overflow_free, 'trace': trace}
        rows.append(' '.join([case['name'], case['route'], ','.join(map(str, case['codes'])),
                    str(case['residual']), repr(scale), str(case['theta']),
                    ','.join(map(str, case['activation'])), ','.join(map(str, case['weight'])),
                    str(case['mutate'])]))
    (out / 'cases.txt').write_text('\n'.join(rows) + '\n')
    (out / 'expected.json').write_text(json.dumps({'numerical_revision': REVISION,
        'allowed_dut_revisions': [STAGING_REVISION, REVISION] if overflow_free_only else [REVISION],
        'source': 'independent Python arbitrary integers and binary64, never DUT results',
        'overflow_free_only': overflow_free_only, 'cases': expected}, indent=2) + '\n')
    manifest = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in out.iterdir()}
    (out / 'sha256.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(f'INDEPENDENT_GOLDEN_CREATED cases={len(rows)} revision={REVISION}')


def check(expected_file, log):
    document = json.loads(expected_file.read_text())
    expected = document['cases']
    observed = {}
    text = log.read_text()
    assert 'SCU_FRONTEND_FAIL ' not in text, 'DUT failure marker'
    completion = []
    for line in text.splitlines():
        if line.startswith('SCU_FRONTEND_PASS '):
            completion.append(dict(field.split('=', 1) for field in line.split()[1:]))
        if not line.startswith('SCU_FRONTEND_RESULT '):
            continue
        fields = dict(field.split('=', 1) for field in line.split()[1:])
        name = fields['case']
        assert name not in observed and name in expected, 'duplicate or unexpected case'
        wanted = expected[name]
        for field, key in [('raw', 'integer'), ('f_out_bits', 'float32_bits'),
                           ('domain', 'output_domain'), ('callbacks', 'callbacks'), ('operation', 'operation')]:
            assert int(fields[field]) == wanted[key], (name, field, fields[field], wanted[key])
        assert [int(value) for value in fields['metadata'].split(',')] == wanted['metadata'], (name, 'metadata')
        assert fields['metadata_blocks'] == '0,1', (name, 'metadata block identity')
        assert fields['padding_preserved'] == '1' and fields['logical_invocations'] == '1'
        observed[name] = fields
    assert observed.keys() == expected.keys(), 'missing expected cases'
    assert len(completion) == 1 and int(completion[0]['cases']) == len(expected)
    assert completion[0]['rejected_metadata'] == '6'
    accepted_revision = {REVISION}
    if document['overflow_free_only']:
        accepted_revision.add(STAGING_REVISION)
    assert set(document['allowed_dut_revisions']) == accepted_revision
    assert completion[0]['numerical_revision'] in accepted_revision, 'unexpected numerical revision'
    print(f'INDEPENDENT_GOLDEN_EXACT_PASS cases={len(observed)} raw={len(observed)} f_out={len(observed)}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path)
    parser.add_argument('--overflow-free-only', action='store_true')
    parser.add_argument('--carrier-edges', action='store_true')
    parser.add_argument('--expected', type=Path)
    parser.add_argument('--check', type=Path)
    args = parser.parse_args()
    self_check()
    if args.out:
        generate(args.out, args.overflow_free_only, args.carrier_edges)
    elif args.check and args.expected:
        check(args.expected, args.check)
    elif args.check or args.expected:
        parser.error('--expected and --check are required together')
    else:
        print('INDEPENDENT_GOLDEN_SELF_CHECK_PASS widths=32,64')
