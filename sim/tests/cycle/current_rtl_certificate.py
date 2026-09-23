#!/usr/bin/env python3
"""Certify current-source isolated GEMMs, without archived RTL answer inputs.

Build first with scripts/gemmini_build.py --stage host-test. This tool passively
observes that exact numerical fixture, proves observation did not change its
stdout, then replays every captured shape/tile/timing tuple in a freshly reset
RTL adapter and the existing value-free cycle library. No tiler or timing model
is implemented here. Historical goldens and historical exclusion counts are not
used. An unresolved RTL/model mismatch stops the certificate immediately.
"""
from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shlex
import subprocess
import sys
from typing import Any
from collections.abc import Mapping, Sequence

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from scripts.gemmini_replay_contract import reference_memory_contract
from sim.cycle import cli
from sim.cycle.certificate_contract import number, object_value
from sim.tests.cycle import rtl_hardening as rtl
from sim.tests.cycle.passive_log import strip_device_host_call_records
from sim.tests.cycle.production_block_certificate import RESULT_MAP, TIMING_KEYS, first_event_difference

FRAMINGS = ('regression-tiles', 'planner-blocks')
SOURCE = ROOT / 'fpga/gemmini_hp1/host/test_ws_rtl.cpp'


def require_profiles(profiles: Sequence[Mapping[str, str]]) -> None:
    names = [profile['profile'] for profile in profiles]
    if len(names) != len(rtl.PROFILES) or set(names) != set(rtl.PROFILES):
        raise ValueError('current certificate requires each of the six profiles exactly once')


def event_comparison(model: list[tuple[int, str]], observed: list[tuple[int, str]]) -> dict[str, int | str]:
    return {
        'model_event_count': len(model), 'rtl_event_count': len(observed),
        'model_multiset_sha256': hashlib.sha256(json.dumps(sorted(model), separators=(',', ':')).encode()).hexdigest(),
        'rtl_multiset_sha256': hashlib.sha256(json.dumps(sorted(observed), separators=(',', ':')).encode()).hexdigest(),
    }


def write_json(path: Path, data: Any) -> None:
    with path.open('x') as stream:
        json.dump(data, stream, indent=2, sort_keys=True)
        stream.write('\n')


def replace(text: str, old: str, new: str) -> str:
    return rtl._replace_once(text, old, new, 'current-source observation')


def observed_source(framing: str, single: bool) -> str:
    text = SOURCE.read_text()
    # The retained observer predates clang-format's wrapping of this statement.
    anchor = ('  const auto old_contexts = state.contexts, old_raw = state.raw_rows, '
              'old_commits = state.commits;')
    text = replace(text, '  const auto old_contexts = state.contexts, old_raw = state.raw_rows,\n'
                        '             old_commits = state.commits;', anchor)
    text = replace(text, anchor, '''  const auto old_loops = state.loops;
  const auto old_lr = state.dut.io_loadRequests, old_la = state.dut.io_loadResponses;
  const auto old_sr = state.dut.io_storeRequests, old_sa = state.dut.io_storeResponses;
  const auto old_qr = state.dut.io_scaleReadRequests, old_qa = state.dut.io_scaleReadResponses;
''' + anchor)
    text = rtl._instrument_events(text)
    text = replace(text, 'static unsigned hardening_case = 0;',
                   'static unsigned hardening_case = 0;\n'
                   'static const char *hardening_provenance = "dense";')
    text = replace(text, "                     << unsigned(work.plan.kind == WorkKind::rmd_raw) << '\\n';",
                   "                     << unsigned(work.plan.kind == WorkKind::rmd_raw)\n"
                   "                     << ',' << hardening_provenance << '\\n';")
    text = replace(text, '            << " cycles=" << timing.done_cycle - timing.start_cycle\n', '''            << " cycles=" << timing.done_cycle - timing.start_cycle
            << " start=" << timing.start_cycle << " done=" << timing.done_cycle
            << " work_count=1 loop_count=" << state.loops - old_loops
            << " load_req=" << state.dut.io_loadRequests - old_lr
            << " load_resp=" << state.dut.io_loadResponses - old_la
            << " store_req=" << state.dut.io_storeRequests - old_sr
            << " store_resp=" << state.dut.io_storeResponses - old_sa
            << " scale_req=" << state.dut.io_scaleReadRequests - old_qr
            << " scale_resp=" << state.dut.io_scaleReadResponses - old_qa
''')
    text = replace(text, '    if (dut.io_events_loadIssued) hardening_event("load_issue");', r'''    if (dut.io_work_valid && dut.io_work_ready)
      hardening_observer << "OWNERSHIP," << hardening_case << ',' << dut.io_coreCycle
          << ',' << dut.io_work_bits_fragmentBase << ',' << dut.io_work_bits_scaleBase
          << ',' << unsigned(dut.io_work_bits_scaleGeneration)
          << ',' << dut.io_work_bits_maxJ << ',' << dut.io_work_bits_maxK << '\n';
    if (dut.io_scaleRelease_valid && dut.io_scaleRelease_ready)
      hardening_observer << "RELEASE," << hardening_case << ',' << dut.io_coreCycle
          << ',' << unsigned(dut.io_scaleRelease_bits_address)
          << ',' << unsigned(dut.io_scaleRelease_bits_column)
          << ',' << unsigned(dut.io_scaleRelease_bits_generation) << '\n';
    if (dut.io_events_loadIssued) hardening_event("load_issue");''')
    for function, provenance in (('execute_raw', 'raw_diagnostic'), ('execute_scu', 'residual_hp1')):
        start = text.index(f'int {function}(')
        end = text.index('\n}', start)
        body = text[start:end]
        call = '  const auto status = execute(opaque, work, output, timing);'
        body = replace(body, call, f'  hardening_provenance = "{provenance}";\n' + call +
                       '\n  hardening_provenance = "dense";')
        text = text[:start] + body + text[end:]
    if single:
        # Tiny synthetic probes need not each exercise corpus-level backpressure.
        text = replace(text, '  check(state.reordered_reads > old_reordered,\n'
                             '        "backing fixture did not exercise out-of-order IDs");\n', '')
        text = replace(text, '  check(state.read_backpressure_cycles > old_backpressure,\n'
                             '        "backing fixture did not exercise read-request backpressure");\n', '')
        if framing == 'planner-blocks':
            text = rtl._planner_block_traversal(text)
        text = rtl._single_case_main(text)
        text = replace(text, '    check(status == IM2P_OK, "hardening execute failed");', '''    check(status == IM2P_OK, "hardening execute failed");
    check(std::all_of(output.begin(), output.end(),
                      [k](std::int32_t value) { return value == static_cast<std::int32_t>(k); }),
          "constant-one RTL numerical oracle mismatch");''')
    elif framing != 'regression-tiles':
        raise ValueError('capture must preserve the original numerical fixture traversal')
    return text


def build_probe(profile: dict[str, Any], destination: Path, framing: str,
                single: bool) -> Path:
    from scripts.gemmini_rtl_build_binding import verify_build
    verify_build(Path(profile['resolved_profile']).parent, profile['profile'])
    destination.mkdir(parents=True, exist_ok=False)
    if profile['status'] != 'PASS':
        raise ValueError(f"current host-test failed: {profile['profile']}")
    command = next(row['arguments'] for row in profile['command_results']
                   if row['arguments'][0] == 'verilator' and '--cc' in row['arguments'])
    obj = Path(command[command.index('--Mdir') + 1])
    flags = shlex.split(command[command.index('-CFLAGS') + 1])
    ldflags = shlex.split(command[command.index('-LDFLAGS') + 1])
    top = command[command.index('--top-module') + 1]
    text = observed_source(framing, single)
    prefix = 'dut.rootp->' + top + '__DOT__control__DOT__'
    symbols = {
        'ROOT_EX_FUNCT': '_reservation_io_issue_ex_cmd_cmd_inst_funct',
        'ROOT_MESH_INPUT': 'execute__DOT__mesh__DOT__input_next_row_into_spatial_array',
        'ROOT_MESH_ID': 'execute__DOT__mesh__DOT__matmul_id',
        'ROOT_MESH_ROW': 'execute__DOT__mesh__DOT__fire_counter',
        'ROOT_RAW_COMPLETED': '_execute_io_completed_valid',
        'ROOT_RAW_ROB': '_execute_io_completed_bits',
    }
    header = (obj / 'VIM2PGemminiWSHP1RtlTest___024root.h').read_text()
    for token, suffix in symbols.items():
        symbol = prefix + suffix
        if symbol.split('->')[1] not in header:
            raise ValueError(f'missing passive observer symbol: {symbol}')
        text = text.replace(token, symbol)
    source = destination / 'probe.cpp'
    source.write_text(text)
    version = subprocess.check_output(['verilator', '-V'], text=True)
    match = re.search(r'VERILATOR_ROOT\s*=\s*(\S+)', version)
    if match is None:
        raise ValueError('Verilator include root missing')
    vroot = Path(match[1])
    retained = [obj / name for name in ('frontend_rtl_fixture.o', 'rmd_rtl_fixture.o',
        'bound_rmd_rtl_fixture.o', 'rmd-reference.o', 'VIM2PGemminiWSHP1RtlTest__ALL.a',
        'verilated.o', 'verilated_threads.o')]
    executable = destination / 'probe'
    args = ['c++', *flags, '-O2', '-I' + str(ROOT / 'sim/common'), '-isystem', str(obj),
            '-isystem', str(vroot / 'include'), '-isystem', str(vroot / 'include/vltstd'),
            str(source), str(ROOT / 'sim/common/gemmini_schedule.cpp'), *map(str, retained),
            *ldflags, *(['-Wl,-U,__Z13sc_time_stampv'] if platform.system() == 'Darwin' else []),
            '-pthread', '-o', str(executable)]
    if rtl.run_logged(destination, args, destination / 'build.log'):
        raise RuntimeError(f'probe build failed: {destination / "build.log"}')
    write_json(destination / 'provenance.json', {
        'profile': profile['profile'], 'framing': framing, 'single_reset_work': single,
        'current_fixture': str(SOURCE), 'current_fixture_sha256': rtl.sha256(SOURCE),
        'generated_probe_sha256': rtl.sha256(source),
        'schedule_sha256': rtl.sha256(ROOT / 'sim/common/gemmini_schedule.cpp'),
        'retained_objects_sha256': {p.name: rtl.sha256(p) for p in retained},
        'rtl_sha256': {p.name: rtl.sha256(p) for p in sorted((obj.parent / 'rtl').glob('*.sv'))},
        'rtl_source_modified': False, 'passive_observation_only': True,
    })
    return executable


def capture(profile: dict[str, Any], out: Path) -> list[dict[str, Any]]:
    executable = build_probe(profile, out / 'capture', 'regression-tiles', False)
    events = executable.parent / 'events.csv'
    log = executable.parent / 'run.log'
    env = dict(os.environ, IM2P_CYCLE_OBSERVER=str(events))
    code = rtl.run_logged(out, [str(executable)], log, env)
    original = strip_device_host_call_records(
        Path(profile['command_results'][-1]['log']).read_bytes().decode('utf-8'))
    stripped = strip_device_host_call_records(log.read_bytes().decode('utf-8'))
    stripped = re.sub(r'( cycles=\d+) start=\d+ done=\d+ work_count=1 loop_count=\d+'
                      r' load_req=\d+ load_resp=\d+ store_req=\d+ store_resp=\d+'
                      r' scale_req=\d+ scale_resp=\d+', r'\1', stripped)
    equal = stripped == original and code == 0
    write_json(executable.parent / 'observation-check.json', {
        'returncode': code, 'runtime_log_byte_identical_without_observer_columns': equal,
        'original_log': profile['command_results'][-1]['log'],
        'original_log_sha256': rtl.sha256(Path(profile['command_results'][-1]['log']))})
    if not equal:
        raise ValueError(f'passive observer changed current numerical fixture: {log}')
    with events.open() as stream:
        rows = [r for r in csv.reader(stream) if r and r[0] == 'CASE']
    if [int(r[1]) for r in rows] != list(range(1, len(rows) + 1)) or not rows:
        raise ValueError('captured work identity/count is not contiguous')
    result = [{'case': f'captured-{int(r[1]):03}', 'shape': list(map(int, r[2:5])),
               'tile': list(map(int, r[6:9])), 'timing': list(map(int, r[9:14])),
               'raw': bool(int(r[15])), 'provenance': r[16], 'captured_case': int(r[1])}
              for r in rows]
    write_json(out / 'captured-corpus.json', result)
    return result


def ownership(events: Path, dim: int) -> dict[str, Any]:
    with events.open() as stream:
        rows = list(csv.reader(stream))
    owned = [r for r in rows if r and r[0] == 'OWNERSHIP']
    released = [r for r in rows if r and r[0] == 'RELEASE']
    done = [int(r[1]) for r in rows if r and r[0].isdigit() and r[2] == 'loop_done']
    details = []
    for index, row in enumerate(owned):
        _, case, epoch, fragment, base, generation, js, ks = row
        f, b, g, j, k = map(int, (fragment, base, generation, js, ks))
        fpb = max(1, 32 // dim)
        count = ((f + k - 1) // fpb - f // fpb + 1) * j
        end_epoch = int(owned[index + 1][2]) if index + 1 < len(owned) else 1 << 64
        actual = [(int(r[3]), int(r[4]), int(r[5])) for r in released
                  if r[1] == case and int(epoch) <= int(r[2]) < end_epoch]
        expected = [(address, lane, g) for address in range(b, b + count) for lane in range(dim)]
        completion = next((cycle for cycle in done if int(epoch) <= cycle < end_epoch), None)
        release_cycles = [int(r[2]) for r in released
                          if r[1] == case and int(epoch) <= int(r[2]) < end_epoch]
        valid = (0 < g < 256 and b + count <= 256 and actual == expected and
                 completion is not None and bool(release_cycles) and min(release_cycles) > completion)
        details.append({'fragment_base': f, 'physical_first_row': b, 'physical_row_count': count,
                        'generation': g, 'released_row_lanes': len(actual), 'status': 'PASS' if valid else 'FAIL'})
    return {'status': 'PASS' if details and all(r['status'] == 'PASS' for r in details) else 'FAIL',
            'submissions': len(details), 'max_fragment_base': max((r['fragment_base'] for r in details), default=None),
            'max_physical_end_row': max((r['physical_first_row'] + r['physical_row_count'] for r in details), default=None),
            'generation_wraps': sum(b['generation'] < a['generation'] for a, b in zip(details, details[1:])),
            'details': details}


def exact_result(differences: dict[str, Any], model_events: list[tuple[int, str]],
                 rtl_events: list[tuple[int, str]]) -> bool:
    return not differences and bool(model_events) and model_events == rtl_events


def compare_case(executable: Path, profile: str, framing: str, case: dict[str, Any],
                 library: Path) -> dict[str, Any]:
    m, n, k = case['shape']
    ti, tj, tk = case['tile']
    observed = rtl.run_probe(executable.parent, executable, case['case'], m=m, n=n, k=k,
                            tile_i=ti, tile_j=tj, tile_k=tk, timing=case['timing'], raw=case['raw'])
    result = {'profile': profile, 'framing': framing, **case,
              'rtl_admitted': observed['returncode'] == 0,
              'model_attempted': False, 'model_admitted': False,
              'status': 'FAIL', 'rtl_log': observed['log'], 'rtl_events': observed['events_path']}
    if observed['returncode']:
        result['reason'] = 'CURRENT_RTL_ADAPTER_FAILURE'
        return result
    if len(observed['cases']) != 1 or not observed['work_events']:
        raise ValueError('probe is missing exactly one accepted work record')
    data = observed['cases'][0]
    reference = reference_memory_contract()
    accepted = number(reference['accepted_cycle'], 'reference accepted cycle')
    offset = number(object_value(reference['timing'], 'reference timing')['backing_cycle_offset'],
                    'reference backing offset')
    request = {'profile': profile, 'timing_profile': 'rtl-regression',
               'request': {'m': m, 'n': n, 'k': k, 'tile_i': ti, 'tile_j': tj, 'tile_k': tk,
                           'accepted_cycle': accepted, 'submission': framing, 'record_events': 1},
               'timing': dict(zip(TIMING_KEYS, (*case['timing'], offset)))}
    directory = Path(observed['log']).parent
    write_json(directory / 'model-request.json', request)
    result['model_attempted'] = True
    try:
        model = cli.estimate(library, request)
    except ValueError as error:
        result['reason'] = str(error)
        return result
    result['model_admitted'] = True
    write_json(directory / 'model-result.json', model)
    pairs = {name: field for name, field in RESULT_MAP.items() if name in observed['summary']}
    if not set(pairs) >= set(RESULT_MAP) - {'planner_loop_count', 'fragment_count'}:
        raise ValueError('missing endpoint/traffic counter evidence')
    differences = {name: {'rtl': observed['summary'][name], 'model': model['result'][field]}
                   for name, field in pairs.items()
                   if observed['summary'][name] != model['result'][field]}
    for name, actual, expected in (('accepted_cycle', int(observed['work_events'][0][1]), accepted),
                                   ('backing_cycle_offset', int(data[14]), offset)):
        if actual != expected:
            differences[name] = {'rtl': actual, 'model': expected}
    model_events = rtl.normalized_model_events(model['events'])
    event_difference = first_event_difference(model_events, observed['selected_events'])
    _, dim = rtl.profile_bits_dim(profile)
    scale = ownership(Path(observed['events_path']), dim)
    write_json(directory / 'scale-ownership.json', scale)
    exact = exact_result(differences, model_events, observed['selected_events']) and scale['status'] == 'PASS'
    result.update(status='PASS' if exact else 'FAIL', reason=None if exact else 'CURRENT_RTL_MODEL_MISMATCH',
                  differences=differences, first_event_difference=event_difference,
                  rtl_summary=observed['summary'], model_summary=model['result'],
                  delta_cycles=model['result']['total_cycles'] - observed['summary']['cycles'],
                  selected_event_multiset_exact=not event_difference,
                  event_comparison=event_comparison(model_events, observed['selected_events']),
                  scale_ownership={key: value for key, value in scale.items() if key != 'details'})
    return result


def mutation_test(case: dict[str, Any]) -> dict[str, Any]:
    directory = Path(case['rtl_log']).parent
    model = json.loads((directory / 'model-result.json').read_text())
    events = rtl.normalized_model_events(model['events'])
    with Path(case['rtl_events']).open() as stream:
        expected = sorted((int(r[1]), r[2]) for r in csv.reader(stream)
                          if r and r[0].isdigit() and r[2] in rtl.SELECTED_EVENTS)
    if not exact_result({}, events, expected):
        raise ValueError('mutation control must already be exact')
    index = next(i for i, (_cycle, kind) in enumerate(events) if kind == 'load_issue')
    variants = {}
    changed = copy.copy(events); changed[index] = (changed[index][0] + 1, changed[index][1])
    variants['shift_non_endpoint_event'] = sorted(changed)
    variants['delete_event'] = events[:index] + events[index + 1:]
    variants['duplicate_event'] = sorted([*events, events[index]])
    checks = {name: not exact_result({}, value, expected) for name, value in variants.items()}
    return {'status': 'PASS' if all(checks.values()) else 'FAIL', 'control_exact': True,
            'endpoints_and_counters_unchanged': True, 'mutations_rejected': checks,
            'selected_events': sorted(rtl.SELECTED_EVENTS)}


def certify(build_root: Path, library: Path, out: Path) -> dict[str, Any]:
    if out.is_relative_to(ROOT):
        raise ValueError('certificate output must be external to IM2P.sim')
    out.mkdir(parents=True, exist_ok=False)
    manifest = json.loads((build_root / 'result.json').read_text())
    if manifest.get('status') != 'PASS' or manifest.get('stage') != 'host-test':
        raise ValueError('a fresh passing host-test build is required')
    profiles = manifest['profiles']
    require_profiles(profiles)
    from sim.cycle.corpus_authority import authority, authority_reference
    sources = [profile.get('llama_source') for profile in profiles]
    if not all(source == sources[0] for source in sources) or not isinstance(sources[0], dict):
        raise ValueError('current candidate package identity missing or inconsistent')
    llama_source = sources[0]
    if set(llama_source) != {'root', 'base_head', 'source_manifest_sha256', 'dependency_lock_sha256'}:
        raise ValueError('current candidate package identity differs')
    source_root = llama_source['root']
    if not isinstance(source_root, str) or not source_root:
        raise ValueError('current candidate llama source root missing')
    reviewed = authority('v4', fixture_root=ROOT, llama_root=Path(source_root))
    source_manifest = Path(source_root).parents[2] / 'source-manifest.json'
    if (not source_manifest.is_file() or
            hashlib.sha256(source_manifest.read_bytes()).hexdigest() != llama_source['source_manifest_sha256']):
        raise ValueError('current candidate source manifest differs')
    if (llama_source['base_head'] != reviewed['producer_base_head'] or
            llama_source['dependency_lock_sha256'] != reviewed['dependency_lock_sha256']):
        raise ValueError('current candidate package identity differs')
    from scripts.gemmini_rtl_build_binding import verify_build
    bindings = {p['profile']: verify_build(Path(p['resolved_profile']).parent, p['profile']) for p in profiles}
    results: list[dict[str, Any]] = []
    captures = {}
    corpora = {}
    first_mismatch = None
    for profile in profiles:
        name = profile['profile']
        profile_out = out / name
        profile_out.mkdir()
        corpus = capture(profile, profile_out)
        captures[name] = len(corpus)
        # Additional explicit synthetic coverage, never mislabeled model trace.
        extra = [{'case': f'large-k-{k}', 'shape': [1, 1, k], 'tile': [1, 1, 3],
                  'timing': [3, 13, 17, 11, 5], 'raw': False,
                  'provenance': 'synthetic_large_k'} for k in (32, 64, 96, 3072, 8256)]
        corpora[name] = corpus + extra
    expected = {name: [case['case'] for case in cases] for name, cases in corpora.items()}
    write_json(out / 'expected-corpus.json', expected)
    for profile in profiles:
        name = profile['profile']
        profile_out = out / name
        for framing in FRAMINGS:
            executable = build_probe(profile, profile_out / framing, framing, True)
            for case in corpora[name]:
                row = compare_case(executable, name, framing, case, library)
                results.append(row)
                print(name, framing, case['case'], row['status'], flush=True)
                if row['status'] != 'PASS':
                    first_mismatch = row
                    break
            if first_mismatch:
                break
        if first_mismatch:
            break
    good = next((r for r in results if r['status'] == 'PASS'), None)
    mutation = mutation_test(good) if good else {'status': 'NOT_RUN'}
    write_json(out / 'event-hard-gate-mutation.json', mutation)
    summaries = {}
    for framing in FRAMINGS:
        subset = [r for r in results if r['framing'] == framing]
        summaries[framing] = {
            'cases_attempted': len(subset),
            'cases_rtl_admitted': sum(r['rtl_admitted'] for r in subset),
            'cases_model_admitted': sum(r['model_admitted'] for r in subset),
            'cases_exact': sum(r['status'] == 'PASS' for r in subset),
            'max_abs_delta_cycles': max((abs(r['delta_cycles']) for r in subset if 'delta_cycles' in r), default=None)}
    from sim.tests.cycle.certificate_document import complete_document
    result = {'status': 'PASS' if not first_mismatch and mutation['status'] == 'PASS' else 'FAIL',
              'captured_corpus_counts': captures, 'summaries': summaries, 'first_mismatch': first_mismatch,
              'observed_corpus_counts': dict(captures),
              'moved_historical_residual_case_ids': reviewed['moved_historical_residual_case_ids'],
              'selected_events': sorted(rtl.SELECTED_EVENTS), 'event_mutation': mutation,
              'build_manifest_sha256': rtl.sha256(build_root / 'result.json'),
              'model_library_sha256': rtl.sha256(library),
              'rtl_build_bindings': bindings,
              'llama_source': llama_source, 'corpus_authority': authority_reference('v4'),
              'hardware_contracts': {name: binding['hardware_contract'] for name, binding in bindings.items()},
              'historical_goldens_used': False, 'historical_exclusions_used': False,
              'cases': results}
    result = complete_document(result, expected, library, 'FRESH_RUN')
    write_json(out / 'current-certificate.json', result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--build-root', type=Path)
    source.add_argument('--reuse-verified-evidence', type=Path,
                        help='historical evidence root with checksums, source inventory, and raw comparisons')
    parser.add_argument('--library', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    if args.reuse_verified_evidence is not None:
        from sim.tests.cycle.reaggregate_certificate import reaggregate
        result = reaggregate(args.reuse_verified_evidence.resolve(), args.library.resolve(), args.out.resolve())
    else:
        result = certify(args.build_root.resolve(), args.library.resolve(), args.out.resolve())
    print(json.dumps({k: v for k, v in result.items() if k != 'cases'}, indent=2))
    return 0 if result['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
