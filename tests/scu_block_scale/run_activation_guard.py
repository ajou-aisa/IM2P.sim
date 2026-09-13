#!/usr/bin/env python3
"""Fresh-source or frozen-S2 M9 gate with an independent BSV layout oracle."""
import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import datetime
import time


from activation_guard_support import (
    changed_inputs, changed_source_inputs, digest, discover_toolchain,
    source_inputs, warning_inventory,
)


def validate(text, monitored):
    assert not re.search(r'assertion failed|RESIDENT .* FAIL|unexpected.*finish|^\s*(?:%Error|%Fatal|FATAL:)', text, re.I | re.M)
    rows = [json.loads(line) for line in text.splitlines() if line.startswith('{')]
    cases = [row for row in rows if 'case' in row]
    summaries = [row for row in rows if row.get('summary') is True]
    assert [row['case'] for row in cases] == [
        'int20_widen_int32_multiply', 'fragment_shift_boundary',
        'random_full_range_shift', 'matched_host_benchmark_shift']
    assert len(summaries) == 1
    summary = summaries[0]
    assert summary['status'] == 'PASS' and summary['monitor'] == int(monitored)
    assert all(summary[key] == 4 for key in ('expected_jobs', 'completed_jobs', 'verified_jobs'))
    assert summary['correct_outputs'] == summary['expected_outputs'] == 36
    assert all(row['pass'] and (row['m'], row['n'], row['k']) == (9, 1, 1)
               and row['numeric_elements'] == 9 and row['fragments'] == 1 and row['rtl_cycles'] > 0
               for row in cases)
    assert text.splitlines().count('RESIDENT SHAPE PASS: 36 outputs, 4 autonomous jobs') == 1
    lines = re.findall(r'^MONITOR (.+)$', text, re.M)
    assert len(lines) == 1
    stats = dict((key, int(value)) for key, value in (field.split('=') for field in lines[0].split()))
    if monitored:
        assert stats['returns'] == stats['publications'] == stats['feeds'] == 36
        assert stats['completed'] == 4 and stats['edges'] > 0
        assert stats['strict_pending_violations'] == stats['legal_turnovers'] == stats['data_unobserved'] == 0
    else:
        assert all(value == 0 for value in stats.values())
    return {'jobs': 4, 'verified_outputs': 36, 'comparisons_including_three_hold_cycles': 108,
            'rtl_cycles': [row['rtl_cycles'] for row in cases], 'monitor': stats,
            'runtime_assertion_failures': 0, 'unexpected_finish': 0}


def main():
    parser = argparse.ArgumentParser()
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument('--snapshot', type=Path, help='Historical sealed S2 snapshot')
    selection.add_argument('--source', type=Path, help='Current repository source; always rebuild production RTL')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--jobs', type=int, default=2)
    parser.add_argument('--production-build', type=Path,
                        help='Reuse preserved production RTL/primitives, never its observer binary')
    args = parser.parse_args()
    if not __debug__:
        parser.error('Python assertions are required; do not run this validation with -O')
    out = args.out.resolve()
    if args.jobs <= 0:
        parser.error('--jobs must be positive')
    if args.source and args.production_build:
        parser.error('--source cannot reuse --production-build; fresh RTL is required')
    if out.exists():
        parser.error('--out must be a new directory; previous evidence is never overwritten')
    try:
        tools = discover_toolchain()
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    snapshot = args.snapshot.resolve() if args.snapshot else None
    source = snapshot / 'source' if snapshot else args.source.resolve()
    if out == source or any(out.is_relative_to(source / name)
                            for name in ('src', 'synth', 'sim', 'config', 'tests')):
        parser.error('--out must not overwrite or enter the source input directories')
    # The historical path still verifies its original integration seal. The
    # current-source path hashes the actual branch inputs, never an old archive.
    if snapshot:
        manifest = json.loads((snapshot / 'integration-sha256.json').read_text())
        hashes = {str(snapshot / name): expected for name, expected in manifest.items()}
        assert not changed_inputs(hashes), 'frozen integration seal mismatch'
    else:
        hashes = source_inputs(source)
    selected_source_hashes = source_inputs(source)
    hashes.update(selected_source_hashes)
    core = source / 'src/core/IM2PCore.bsv'
    guard = ') if ((activationRequestValidReg || lookaheadActivationRequestValidReg)\n            && !activationResponsePendingReg);'
    assert guard in core.read_text(), 'selected activation-response capacity guard missing'
    assert 'signed-scu-sat-v2' in (source / 'sim/ffi/im2p_config.h').read_text()
    test_dir = Path(__file__).with_name('activation_guard')
    hashes.update({str(path.resolve()): digest(path) for path in test_dir.iterdir() if path.is_file()})
    hashes[str(Path(__file__).resolve())] = digest(Path(__file__))
    support = Path(__file__).with_name('activation_guard_support.py').resolve()
    hashes[str(support)] = digest(support)
    if args.production_build:
        args.production_build = args.production_build.resolve()
        previous = json.loads((args.production_build.parent / 'summary.json').read_text())
        assert previous['core_sha256'] == digest(core), 'reused production core identity mismatch'
        previous_inputs = json.loads((args.production_build.parent / 'input-sha256.json').read_text())
        assert all(previous_inputs.get(str(snapshot / name)) == expected for name, expected in manifest.items())
        rtl_hash = digest(args.production_build / 'rtl/mkResidentP0.v')
        assert any(row['variant'] == 'production' and row['generated_rtl_sha256'] == rtl_hash
                   for row in previous['records']), 'reused production RTL not previously recorded'
        for directory in ('rtl', 'primitives'):
            hashes.update({str(path): digest(path) for path in (args.production_build / directory).glob('*.v')})
    out.mkdir(parents=True, exist_ok=False)
    shutil.copytree(test_dir, out / 'test-source')
    shutil.copy2(__file__, out / Path(__file__).name)
    (out / 'input-sha256.json').write_text(json.dumps(hashes, indent=2) + '\n')
    shutil.copy2(support, out / support.name)
    bsc = Path(tools['bsc'])
    primitive_dir = Path(tools['bsc_verilog'])
    identity = {name: {'realpath': tools[name], 'sha256': digest(Path(tools[name]))}
                for name in ('bsc', 'verilator')}
    identity['bsc_verilog'] = str(primitive_dir)
    identity['primitives'] = {name: digest(primitive_dir / name)
                              for name in ('RegFile.v', 'FIFO2.v', 'BRAM1.v', 'BRAM2.v')}
    for name in ('bsc', 'verilator'):
        hashes[tools[name]] = identity[name]['sha256']
    for name, expected in identity['primitives'].items():
        hashes[str(primitive_dir / name)] = expected
    (out / 'input-sha256.json').write_text(json.dumps(hashes, indent=2) + '\n')
    # Preserve the exact input bytes, but compile the selected source itself.
    for name in selected_source_hashes:
        relative = Path(name).relative_to(source)
        target = out / 'source-inputs' / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(name, target)
    (out / 'toolchain.json').write_text(json.dumps(identity, indent=2) + '\n')
    (out / 'source-selection.json').write_text(json.dumps({
        'mode': 'frozen-snapshot' if snapshot else 'current-source',
        'source': str(source), 'snapshot': str(snapshot) if snapshot else None,
        'reuse_production_requested': args.production_build is not None,
        'input_hashes_sha256': digest(out / 'input-sha256.json'),
    }, indent=2) + '\n')
    commands, records = [], []

    def run(command, cwd, log, timeout):
        record = {'argv': list(map(str, command)), 'cwd': str(cwd),
                  'start_utc': datetime.datetime.now(datetime.timezone.utc).isoformat()}
        commands.append(record)
        (out / 'commands.json').write_text(json.dumps(commands, indent=2) + '\n')
        start = time.monotonic()
        try:
            with log.open('x') as stream:
                result = subprocess.run(command, cwd=cwd, stdout=stream, stderr=subprocess.STDOUT, timeout=timeout)
            record['exit'] = result.returncode
        except BaseException as exc:
            record.update(exit=None, error=type(exc).__name__ + ': ' + str(exc))
            raise
        finally:
            record.update(seconds=time.monotonic() - start,
                          end_utc=datetime.datetime.now(datetime.timezone.utc).isoformat())
            (out / 'commands.json').write_text(json.dumps(commands, indent=2) + '\n')
        assert result.returncode == 0, f'{log}: exit {result.returncode}'
        return log.read_text()

    error = None
    try:
        run([str(bsc), '-help'], out, out / 'bsc-help.log', 30)
        run([tools['verilator'], '-V'], out, out / 'verilator-version.log', 30)
        layout = out / 'layout'
        layout.mkdir()
        search = '+:src/common:src/io:src/array:src/vector:src/accumulator:src/control:src/core:synth'
        common = [str(bsc), '-sim', '-p', search, '-bdir', str(layout), '-simdir', str(layout),
                  '-info-dir', str(layout)]
        run(common + ['-u', '-g', 'mkTbWorkLayout', str(out / 'test-source/TbWorkLayout.bsv')],
            source, layout / 'compile.log', 120)
        run(common + ['-e', 'mkTbWorkLayout', '-o', str(layout / 'pack-layout')],
            source, layout / 'link.log', 120)
        oracle = run([str(layout / 'pack-layout')], layout, layout / 'oracle.log', 30)
        vectors = re.findall(r'^WORK_LAYOUT_VECTOR \d+ (\d+) ', oracle, re.M)
        assert len(vectors) == 3 and len(set(vectors)) == 1, 'incomplete BSV layout oracle'
        record_bits = int(vectors[0])
        run(['c++', '-std=c++17', '-O1', '-Wall', '-Wextra', '-Werror',
             str(out / 'test-source/test_work_layout.cpp'), '-o', str(layout / 'test_work_layout')],
            layout, layout / 'decoder-build.log', 120)
        checked = run([str(layout / 'test_work_layout'), str(layout / 'oracle.log')],
                      layout, layout / 'check.log', 30)
        assert 'vectors=3 field_comparisons=24 old_layout_mismatches=9 expected_rejections=24 monitor_positive=1' in checked
        assert checked.count('WORK_LAYOUT_PASS ') == 1
        for variant in ('production', 'assertion'):
            build = out / variant
            for name in ('bsc', 'info', 'rtl', 'primitives'):
                (build / name).mkdir(parents=True)
            command = [str(bsc), '-u', '-verilog', '-keep-fires', '-show-schedule',
                       '-p', '+:src/common:src/io:src/array:src/vector:src/accumulator:src/control:src/core:synth',
                       '-steps', '4000000', '-steps-warn-interval', '1000000', '-steps-max-intervals', '20',
                       '+RTS', '-K256M', '-RTS', '-bdir', str(build / 'bsc'), '-info-dir', str(build / 'info'),
                       '-vdir', str(build / 'rtl'), '-g', 'mkResidentP0', str(out / 'test-source/ResidentP0.bsv')]
            if variant == 'assertion': command.insert(3, '-check-assert')
            if variant == 'production' and args.production_build:
                for directory in ('rtl', 'primitives'):
                    for path in (args.production_build / directory).glob('*.v'):
                        shutil.copy2(path, build / directory / path.name)
                assert digest(build / 'rtl/mkResidentP0.v') == rtl_hash
            else:
                run(command, source, build / 'bsc.log', 600)
                for name in ('FIFO2.v', 'BRAM1.v', 'BRAM2.v', 'RegFile.v'):
                    shutil.copy2(primitive_dir / name, build / 'primitives' / name)
            rtl = (build / 'rtl/mkResidentP0.v').read_text()
            widths = re.findall(r'reg\s+\[\s*(\d+)\s*:\s*0\s*\]\s+core_core_core_matrixWorkReg\s*;', rtl)
            assert widths == [str(record_bits - 1)], 'generated RTL / BSV pack record width mismatch'
            for high, low in ((706, 675), (674, 643), (66, 64)):
                assert f'core_core_core_matrixWorkReg[{high}:{low}]' in rtl, 'generated consumer slice mismatch'
            (build / 'layout-gate.json').write_text(json.dumps({
                'bsv_record_bits': record_bits, 'generated_reg_bits': int(widths[0]) + 1,
                'oracle_sha256': digest(layout / 'oracle.log'),
                'decoder_sha256': digest(out / 'test-source/work_layout.hpp'),
                'rtl_sha256': digest(build / 'rtl/mkResidentP0.v'),
                'consumer_slices': {'iCount': [706, 675], 'jCount': [674, 643], 'vectorOp': [66, 64]},
                'identity_fields': 'independent BSV pack; optimized out of this provider consumption',
            }, indent=2) + '\n')
            command = [tools['verilator'], '--cc', '--exe', '--build', '-j', str(args.jobs), '--timing', '--assert',
                       '--public-flat-rw', '--trace', '--trace-depth', '1', '-Wno-fatal', '--top-module', 'mkResidentP0',
                       '--Mdir', str(build / 'obj_dir'), '--output-split', '20000', '--output-split-cfuncs', '500',
                       '-CFLAGS', f'-std=c++20 -O1 -g0 -DASSERTED=1 -DMATMUL_WORK_RECORD_BITS={record_bits} -I' + str(out / 'test-source'),
                       str(build / 'rtl/mkResidentP0.v'),
                       *map(str, sorted((build / 'primitives').glob('*.v'))),
                       str(out / 'test-source/resident_shape.cpp')]
            run(command, out, build / 'verilator.log', 600)
            header = (build / 'obj_dir/VmkResidentP0___024root.h').read_text()
            assert 'WILL_FIRE_RL_core_core_core_publishMatrixActivationResponse' in header
            for monitored in (False, True):
                folder = build / ('passive' if monitored else 'plain')
                folder.mkdir()
                command = [str(build / 'obj_dir/VmkResidentP0'), '--shape', '9', '1', '1', '--no-wave', '--trace']
                if monitored: command += ['--monitor']
                text = run(command, folder, folder / 'run.log', 120)
                record = validate(text, monitored)
                record.update(variant=variant, passive_monitor=monitored,
                              bsc_assertions=variant == 'assertion', clock_period_ns=40,
                              generated_rtl_sha256=digest(build / 'rtl/mkResidentP0.v'))
                (folder / 'validated.json').write_text(json.dumps(record, indent=2) + '\n')
                records.append(record)
            assert (build / 'plain/trace.jsonl').read_bytes() == (build / 'passive/trace.jsonl').read_bytes()
    except BaseException as exc:
        error = type(exc).__name__ + ': ' + str(exc)
        raise
    finally:
        changed = sorted(set(changed_inputs(hashes)) |
                         set(changed_source_inputs(source, selected_source_hashes)))
        warnings = {str(path.relative_to(out)): warning_inventory(path.read_text())
                    for path in sorted(out.glob('*/bsc.log'))}
        (out / 'summary.json').write_text(json.dumps({
            'status': 'PASS' if error is None and not changed and len(records) == 4 else 'FAIL', 'error': error,
            'source_mode': 'frozen-snapshot' if snapshot else 'current-source',
            'source_root': str(source), 'completed_cohorts': len(records),
            'warnings': warnings, 'production_reused': args.production_build is not None,
            'records': records, 'frozen_inputs': len(hashes), 'changed_inputs': changed,
            'core_sha256': digest(core), 'activation_guard_unchanged': True,
            'source_guard': guard, 'physical_jobs': 0,
            'scope': 'raw-core M9/N1/K1; not native-Q8 host format support',
        }, indent=2) + '\n')
        assert not changed, 'frozen inputs changed'
    print('SCU_ACTIVATION_GUARD_PASS cohorts=4 jobs=16 verified_outputs=144 physical_jobs=0')


if __name__ == '__main__':
    main()
