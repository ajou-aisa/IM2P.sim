from __future__ import annotations

from hashlib import sha256
from pathlib import Path

from scripts.gemmini_rtl_build_binding import verify_build
from sim.cycle.certificate_contract import (
    CertificateError,
    array_value,
    digest_value,
    number,
    read_document,
)
from sim.cycle.npu_trace_schema import Record, object_value, require, text
from sim.cycle.run_aware_production_evidence import (
    compare_case,
    compare_events,
    parse_rtl_events,
    parse_rtl_log,
)
from sim.tests.cycle.production_run_work import (
    PRODUCTION_CASE_NAMES,
    PRODUCTION_MANIFEST,
    PRODUCTION_MANIFEST_SHA256,
    PRODUCTION_SOURCE_FILES,
    CertificateCase,
    estimate_case,
    load_manifest,
    read_work_fixture,
)

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / 'sim/tests/cycle/run_aware_corpus.json'


def case_evidence_matches(row: Record, recomputed: CertificateCase) -> bool:
    return (recomputed['status'] == 'PASS' and
            row.get('rtl_summary') == recomputed['rtl_summary'] and
            row.get('model_summary') == recomputed['model_summary'] and
            row.get('selected_event_comparison') == recomputed['selected_event_comparison'] and
            row.get('selected_event_multiset') == recomputed['selected_event_multiset'] and
            row.get('differences') == recomputed['differences'] and
            row.get('delta_cycles') == recomputed['delta_cycles'])


def _validate_production(certificate: Record, library: Path) -> str:
    require(certificate.get('status') == 'PASS' and
            certificate.get('production_one_logical_cross_block_gemm') == 'READY' and
            certificate.get('first_mismatch') is None,
            'production run-aware certificate failed')
    require(sha256(PRODUCTION_MANIFEST.read_bytes()).hexdigest() == PRODUCTION_MANIFEST_SHA256 and
            certificate.get('manifest_sha256') == PRODUCTION_MANIFEST_SHA256,
            'production corpus authority mismatch')
    manifest = read_document(PRODUCTION_MANIFEST)
    profiles = tuple(text({'profile': value}, 'profile') for value in
                     array_value(manifest.get('profiles'), 'profiles'))
    require(profiles == tuple(f'a{bits}w{bits}-d{dim}-hp1' for bits in (4, 8)
                              for dim in (16, 32, 64)) and
            manifest.get('artifact_role') == 'PRODUCTION_GENERATED',
            'production corpus profile/scope mismatch')
    producer_source = text(manifest, 'producer_source')
    require(producer_source == 'llama.cpp-gemmini/tests/test-gemmini-rmd-im2p-provider.cpp' and
            manifest.get('producer_source_sha256') ==
            sha256((ROOT.parent / producer_source).read_bytes()).hexdigest(),
            'producer source changed')
    expected = {(text(case, 'profile'), text(case, 'case')): case for value in
                array_value(manifest.get('cases'), 'cases') if (case := object_value(value))}
    count = number(manifest.get('case_count'), 'production case count')
    require(count == len(profiles) * len(PRODUCTION_CASE_NAMES) == 42 and
            set(expected) == {(profile, case) for profile in profiles
                              for case in PRODUCTION_CASE_NAMES},
            'production corpus incomplete')
    require(certificate.get('library_sha256') == sha256(library.read_bytes()).hexdigest(),
            'production run-aware library mismatch')
    sources = object_value(certificate.get('source_sha256'))
    require(set(sources) == set(PRODUCTION_SOURCE_FILES), 'production source closure incomplete')
    for name, digest in sources.items():
        require(isinstance(digest, str) and
                sha256((ROOT.parent / name).read_bytes()).hexdigest() == digest,
                'production source changed: ' + name)
    typed_cases = {(case['profile'], case['case']): case for case in load_manifest()['cases']}
    rows = array_value(certificate.get('cases'), 'production cases')
    require(len(rows) == count, 'production certificate corpus incomplete')
    observed: set[tuple[str, str]] = set()
    verified_roots: dict[str, Path] = {}
    for value in rows:
        row = object_value(value)
        key = (text(row, 'profile'), text(row, 'case'))
        require(key in expected and key not in observed, 'production case missing or duplicate')
        case = expected[key]
        observed.add(key)
        for name in ('shape', 'tile', 'original_k', 'runs', 'row_map', 'timing', 'framing'):
            require(row.get(name) == case.get(name), 'production case metadata changed: ' + name)
        require(row.get('status') == 'PASS' and row.get('rtl_admitted') is True and
                row.get('model_admitted') is True and row.get('selected_event_multiset') is True and
                row.get('differences') == {} and row.get('delta_cycles') == 0,
                'production case inexact')
        comparison = object_value(row.get('selected_event_comparison'))
        event_count = number(comparison.get('model_count'), 'model event count')
        require(comparison.get('exact') is True and comparison.get('first_difference') is None and
                event_count > 0 and event_count == number(comparison.get('rtl_count'), 'RTL event count') and
                digest_value(comparison.get('model_sha256'), 'model events') ==
                digest_value(comparison.get('rtl_sha256'), 'RTL events'),
                'production selected event multiset inexact')
        rtl = object_value(row.get('rtl_summary'))
        model = object_value(row.get('model_summary'))
        cycles = number(rtl.get('cycles'), 'RTL cycles')
        require(set(rtl) == {'start', 'done', 'cycles', 'loops', 'loads', 'executes',
                             'stores', 'commits', 'scale_reads', 'scale_responses',
                             'completions'} and rtl == model and rtl.get('start') == 1 and
                cycles > 0 and number(rtl.get('done'), 'RTL done') == cycles + 1,
                'production endpoints/counters inexact')
        artifacts = object_value(row.get('artifacts'))
        require(set(artifacts) == {'fixture', 'rtl_log', 'events_csv', 'rtl_binary',
                                   'resolved_profile', 'rtl_build_binding'},
                'production artifacts incomplete')
        for name, value in artifacts.items():
            artifact = object_value(value)
            path = Path(text(artifact, 'path'))
            require(path.is_absolute() and artifact.get('sha256') == sha256(path.read_bytes()).hexdigest(),
                    'production artifact changed: ' + name)
        resolved_path = Path(text(object_value(artifacts['resolved_profile']), 'path'))
        profile_root = resolved_path.parent
        binary_path = Path(text(object_value(artifacts['rtl_binary']), 'path'))
        binding_path = Path(text(object_value(artifacts['rtl_build_binding']), 'path'))
        binary_rel = Path('host-build/run-aware-rtl/VIM2PGemminiWSHP1RtlTest')
        require(resolved_path == profile_root / 'resolved-profile.json' and
                binary_path == profile_root / binary_rel and
                binding_path == profile_root / 'rtl-build-binding.json' and
                (key[0] not in verified_roots or verified_roots[key[0]] == profile_root),
                'production RTL build route changed')
        if key[0] not in verified_roots:
            binding = verify_build(profile_root, key[0])
            hashes = object_value(binding.get('artifact_sha256'))
            require(binding.get('execution_kind') == 'FRESH_BUILD' and
                    hashes.get(binary_rel.as_posix()) ==
                    object_value(artifacts['rtl_binary']).get('sha256'),
                    'production RTL executable not in fresh build binding')
            verified_roots[key[0]] = profile_root
        fixture = object_value(artifacts['fixture'])
        require(fixture.get('sha256') == case.get('fixture_sha256'),
                'producer-captured fixture changed')
        typed_case = typed_cases[key]
        work = read_work_fixture(Path(text(fixture, 'path')))
        log = Path(text(object_value(artifacts['rtl_log']), 'path'))
        events_path = Path(text(object_value(artifacts['events_csv']), 'path'))
        rtl_observed = parse_rtl_log(log, typed_case, work)
        selected = parse_rtl_events(events_path, typed_case, work)
        model_observed = estimate_case(library, typed_case)
        recomputed = compare_case(typed_case, work, rtl_observed, selected, model_observed)
        require(case_evidence_matches(row, recomputed),
                'production raw RTL/model evidence differs from certificate')
        if key == (profiles[0], PRODUCTION_CASE_NAMES[0]):
            index = next((i for i, (_, kind) in enumerate(selected) if kind == 'load_issue'), None)
            if index is None:
                raise CertificateError('production mutation event missing')
            changed = list(selected)
            cycle, kind = changed[index]
            changed[index] = (cycle + 1, kind)
            require(compare_events(selected, changed)['exact'] is False,
                    'production +1 event mutation was admitted')
    require(observed == set(expected) and all(certificate.get(name) == count for name in
            ('expected', 'attempted', 'rtl_admitted', 'model_admitted', 'exact',
             'selected_event_multisets_exact')) and certificate.get('max_abs_delta_cycles') == 0,
            'production certificate summary incomplete')
    require(certificate.get('event_plus_one_mutation_rejected') is True and
            certificate.get('event_plus_one_mutation_case') ==
            profiles[0] + '/' + PRODUCTION_CASE_NAMES[0],
            'production event mutation gate missing')
    return 'PRODUCTION_GENERATED'


def validate_run_certificate(path: Path, library: Path) -> str:
    certificate = read_document(path)
    match certificate.get('artifact_role'):
        case 'PRODUCTION_GENERATED':
            return _validate_production(certificate, library)
        case 'FIXTURE_ONLY':
            pass
        case _:
            require(False, 'unsupported run-aware certificate scope')
    manifest = read_document(MANIFEST)
    profiles = tuple(text({'profile': value}, 'profile') for value in
                     array_value(manifest.get('profiles'), 'profiles'))
    cases = array_value(manifest.get('cases'), 'cases')
    require(certificate.get('status') == 'PASS' and
            certificate.get('artifact_role') == 'FIXTURE_ONLY' and
            certificate.get('production_one_logical_cross_block_gemm') == 'NOT_READY' and
            certificate.get('first_mismatch') is None, 'run-aware certificate failed or scope changed')
    require(certificate.get('library_sha256') == sha256(library.read_bytes()).hexdigest(),
            'run-aware certificate library mismatch')
    require(certificate.get('manifest_sha256') == sha256(MANIFEST.read_bytes()).hexdigest() and
            certificate.get('case_key_sha256') == manifest['case_key_sha256'],
            'run-aware corpus authority mismatch')
    expected = {(profile, text(object_value(case), 'name')) for profile in profiles for case in cases}
    observed: set[tuple[str, str]] = set()
    rows = array_value(certificate.get('cases'), 'run-aware cases')
    require(len(rows) == len(expected) == manifest['case_count'],
            'run-aware certificate corpus incomplete')
    for value in rows:
        row: Record = object_value(value)
        key = (text(row, 'profile'), text(row, 'case'))
        require(key in expected and key not in observed and row.get('status') == 'PASS' and
                row.get('rtl_admitted') is True and row.get('model_admitted') is True and
                row.get('selected_event_multiset') is True and row.get('delta_cycles') == 0 and
                row.get('differences') == {}, 'run-aware case missing, duplicate, or inexact')
        observed.add(key)
    require(observed == expected and all(certificate.get(name) == len(expected) for name in
            ('expected', 'attempted', 'rtl_admitted', 'model_admitted', 'exact', 'selected_event_multisets_exact')) and
            certificate.get('max_abs_delta_cycles') == 0, 'run-aware summary incomplete')
    sources = object_value(certificate.get('cycle_source_sha256'))
    require(bool(sources), 'run-aware cycle source binding missing')
    for name, digest in sources.items():
        require(isinstance(digest, str) and
                sha256((ROOT / name).read_bytes()).hexdigest() == digest,
                'run-aware cycle source changed: ' + str(name))
    return 'FIXTURE_ONLY'
