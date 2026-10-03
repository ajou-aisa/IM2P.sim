# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
# ─── How to run ───
# PYTHONPATH=. uv run pytest -q sim/tests/cycle/test_sequence_absolute_offer.py
# ──────────────────
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.gemmini_resolve_profile import JsonValue
from sim.cycle.npu_trace_schema import INPUT_KEYS, Record, integer, object_value
from sim.tests.cycle import compositional_sequence_work as sequence


def _sealed_manifest(tmp_path: Path, second_port_offer_cycle: int = 6) -> Record:
    sources: Record = {}
    for name in ('trace', 'lifecycle', 'semantic_graph'):
        path = tmp_path / f'{name}.jsonl'
        path.write_text('{}\n')
        sources[name] = {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    works: list[Record] = []
    parents: list[Record] = []
    for ordinal in range(2):
        work_id = ordinal + 11
        parent_id = ordinal + 21
        works.append({'ordinal': ordinal, 'work_id': work_id, 'scope': 'stripe',
                      'slot': ordinal, 'work_binding': f'synthetic-{work_id}',
                      'request_available_cycle': ordinal + 5,
                      'port_offer_cycle': 5 if ordinal == 0 else second_port_offer_cycle,
                      'input': {key: 1 for key in INPUT_KEYS}, 'original_k': None,
                      'runs': [], 'trace_record': {'work_id': work_id,
                                                   'parent_id': parent_id,
                                                   'host_slot': ordinal,
                                                   'call_id': ordinal + 31,
                                                   'stripe_id': 0,
                                                   'row_begin': 0,
                                                   'parent_m': 1}})
        parents.append({'parent_id': parent_id, 'required_work_ids': [work_id],
                        'fence_required_work_ids': [work_id],
                        'fence_call_id': ordinal + 31})
    payload: Record = {
        'schema': 'im2p-compositional-sequence-stimulus', 'version': 2,
        'case_id': 'synthetic-two-work', 'offer_policy': 'absolute-offer-one-pending-v2',
        'profile': 'a8w8-d16-hp1', 'selected_parent_indices': [0, 1],
        'source_sha256': sequence.stimulus_source_hashes(),
        'cycle_library_sha256': '0' * 64,
        'hardware_contract': {'synthetic_control': True},
        'npu_summary': {'synthetic_control': True},
        'producer_artifacts': sources, 'pipeline_parents': list[JsonValue](parents),
        'pipeline_owners': [], 'works': list[JsonValue](works),
    }
    payload['stimulus_sha256'] = hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
        allow_nan=False).encode()).hexdigest()
    return payload


def _cli(manifest: Path, out: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, '-B', str(Path(sequence.__file__)), '--stimulus',
         str(manifest), '--case', 'synthetic-two-work', '--out', str(out)],
        capture_output=True, text=True, check=False,
    )


def _coherent_log(stimulus: Record, first_resource_ready: int = 6,
                  model_first_resource_ready: int | None = None) -> str:
    run = {"instance_count": 1, "reset_count": 1, "period": 5,
           "work_count": 2, "stimulus_sha256": stimulus["stimulus_sha256"]}
    lines = [f'COMPOSITION_RUN {json.dumps(run)}',
             f'MODEL_RUN {json.dumps(run | {"generation": 1})}']
    counts = ('submissions', 'scale_read_requests', 'scale_read_responses',
              'scale_release_count', 'load_requests', 'load_responses',
              'store_requests', 'store_responses')
    works = stimulus['works']
    assert isinstance(works, list)
    for ordinal in range(2):
        requested = object_value(works[ordinal])
        cycle = integer(requested, 'port_offer_cycle')
        work = {'ordinal': ordinal, 'work_id': ordinal + 11,
                'work_binding': f'synthetic-{ordinal + 11}', 'slot': ordinal,
                'request_available_cycle': requested['request_available_cycle'],
                'port_offer_cycle': cycle,
                'offered': cycle, 'accepted': cycle, 'result_ready': cycle,
                'final_scale_release': cycle,
                'resource_ready': first_resource_ready if ordinal == 0 else cycle + 1,
                'initial_scratchpad_half': 0, 'initial_accumulator_half': 0,
                'next_scratchpad_half': 0, 'next_accumulator_half': 0,
                **{key: 0 for key in counts}}
        model = work | {'planner_loop_count': 0, 'fragment_count': 0, 'event_count': 1}
        if ordinal == 0 and model_first_resource_ready is not None:
            model['resource_ready'] = model_first_resource_ready
        lines.extend((f'OFFER_EDGE {ordinal} {cycle} 1 1 1 1 1',
                      f'MODEL_OFFER_EDGE {ordinal} {cycle} 1 1 1 1 1',
                      'COMPOSITION_WORK ' + json.dumps({**work, 'numeric_pass': True}),
                      'MODEL_WORK ' + json.dumps(model),
                      f'RTL_EVENT {ordinal} {ordinal + 11} {cycle} work',
                      f'MODEL_EVENT {ordinal} {ordinal + 11} {cycle} work'))
        if ordinal == 0:
            available = integer(object_value(works[1]), 'request_available_cycle')
            ready_cycle = integer(work, 'resource_ready')
            lines.extend(f'AVAIL_EDGE 1 {edge} 1 0 {int(edge == ready_cycle)} '
                         f'{int(edge == ready_cycle)} 0'
                         for edge in range(available, ready_cycle + 1))
    return '\n'.join(lines)


def test_static_cli_accepts_sealed_absolute_offer_when_sources_match(tmp_path: Path) -> None:
    # Given: a predeclared two-work schedule and source files.
    path = tmp_path / 'stimulus.json'
    path.write_text(json.dumps(_sealed_manifest(tmp_path)))

    # When: the public CLI consumes the sealed input.
    result = _cli(path, tmp_path / 'out')

    # Then: only static artifacts are published until native execution exists.
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['status'] == 'STATIC_VALIDATED_RUNTIME_PENDING'
    assert (tmp_path / 'out' / 'projection.txt').is_file()


def test_static_cli_rejects_offer_mutation_without_new_digest(tmp_path: Path) -> None:
    # Given: one absolute port offer changed after sealing.
    document = _sealed_manifest(tmp_path)
    works = document['works']
    assert isinstance(works, list)
    first = works[0]
    assert isinstance(first, dict)
    first['port_offer_cycle'] = 6
    path = tmp_path / 'changed.json'
    path.write_text(json.dumps(document))

    # When: the changed input reaches the public CLI.
    result = _cli(path, tmp_path / 'out')

    # Then: it fails before writing an execution output.
    assert result.returncode != 0
    assert 'stimulus digest' in result.stderr
    assert not (tmp_path / 'out').exists()


def test_static_cli_rejects_changed_producer_file(tmp_path: Path) -> None:
    # Given: a sealed schedule whose producer trace changes afterward.
    document = _sealed_manifest(tmp_path)
    trace = object_value(object_value(document['producer_artifacts'])['trace'])
    Path(str(trace['path'])).write_text('{"changed":true}\n')
    path = tmp_path / 'stale-source.json'
    path.write_text(json.dumps(document))

    # When: the CLI checks its bound producer sources.
    result = _cli(path, tmp_path / 'out')

    # Then: stale input cannot publish a static report.
    assert result.returncode != 0
    assert 'producer trace digest mismatch' in result.stderr
    assert not (tmp_path / 'out').exists()


def test_static_cli_rejects_duplicate_json_key(tmp_path: Path) -> None:
    # Given: JSON with a repeated case identifier.
    raw = json.dumps(_sealed_manifest(tmp_path))
    raw = raw.replace('"case_id": "synthetic-two-work"',
                      '"case_id": "synthetic-two-work", "case_id": "synthetic-two-work"', 1)
    path = tmp_path / 'duplicate.json'
    path.write_text(raw)

    # When: the CLI parses the manifest.
    result = _cli(path, tmp_path / 'out')

    # Then: the duplicate key cannot hide a changed offer or source.
    assert result.returncode != 0
    assert 'duplicate JSON key' in result.stderr
    assert not (tmp_path / 'out').exists()


def test_runtime_cli_rejects_stale_cycle_library_before_output(tmp_path: Path) -> None:
    # Given: a sealed expected library identity and different bytes at --library.
    document = _sealed_manifest(tmp_path)
    document['cycle_library_sha256'] = hashlib.sha256(b'frozen').hexdigest()
    document['stimulus_sha256'] = sequence.stimulus_digest(document)
    manifest = tmp_path / 'stimulus.json'
    manifest.write_text(json.dumps(document))
    library = tmp_path / 'stale.dylib'
    library.write_bytes(b'stale')
    out = tmp_path / 'out'

    # When: the public CLI starts a runtime run with stale native bytes.
    result = subprocess.run(
        [sys.executable, '-B', str(Path(sequence.__file__)), '--stimulus', str(manifest),
         '--case', 'synthetic-two-work', '--out', str(out),
         '--rtl-build', str(tmp_path / 'unused-build'), '--library', str(library)],
        capture_output=True, text=True, check=False,
    )

    # Then: it fails before compilation and leaves no output directory.
    assert result.returncode != 0
    assert 'cycle library digest mismatch' in result.stderr
    assert not out.exists()


def test_generated_d32_probe_admits_v2_observers_before_reset(tmp_path: Path) -> None:
    binary = os.environ.get('IM2P_D32_COMPOSITIONAL_PROBE')
    if binary is None:
        pytest.skip('set IM2P_D32_COMPOSITIONAL_PROBE to a generated D32 probe')
    manifest = tmp_path / 'd32-empty.txt'
    manifest.write_text('IM2P_COMPOSITIONAL_SEQUENCE_V2 a4w4-d32-hp1\n'
                        'PERIOD 5\nWORKS 0\n')
    for flags in (('--tag-observer-v2',), ('--boundary-schema=2',),
                  ('--tag-observer-v2', '--boundary-schema=2')):
        result = subprocess.run([binary, str(manifest), *flags],
                                capture_output=True, text=True, check=False, timeout=10)
        assert result.returncode == 1
        assert 'absolute composition needs bounded works' in result.stderr
        assert 'COMPOSITION_RUN' not in result.stdout


def test_required_boundary_v2_routes_official_probe_through_stream_parser(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stimulus = _sealed_manifest(tmp_path)
    stimulus['required_observations'] = ['FRAGMENTS', 'TAG_STATE', 'BOUNDARY_V2']
    library = tmp_path / 'libcycle.so'
    library.write_bytes(b'current-cycle-library')
    stimulus['cycle_library_sha256'] = hashlib.sha256(library.read_bytes()).hexdigest()
    stimulus['stimulus_sha256'] = sequence.stimulus_digest(stimulus)
    manifest = tmp_path / 'required-v2.json'
    manifest.write_text(json.dumps(stimulus))

    static = _cli(manifest, tmp_path / 'static')
    assert static.returncode == 0, static.stderr
    assert json.loads((tmp_path / 'static' / 'report.json').read_text())[
        'required_observations'] == ['FRAGMENTS', 'TAG_STATE', 'BOUNDARY_V2']

    raw = ['COMPOSITION_RUN {}']
    for ordinal, work_id in enumerate((11, 12)):
        raw += [f'RTL_EVENT {ordinal} {work_id} {ordinal} work',
                f'COMPOSITION_WORK {json.dumps({"ordinal": ordinal, "work_id": work_id})}',
                f'RTL_SELECTED_EVENT_COUNT {ordinal} 1']
    raw += ['MODEL_RUN {}']
    for ordinal, work_id in enumerate((11, 12)):
        raw += [f'MODEL_EVENT {ordinal} {work_id} {ordinal} work',
                f'MODEL_WORK {json.dumps({"ordinal": ordinal, "work_id": work_id})}',
                f'MODEL_SELECTED_EVENT_COUNT {ordinal} 1']
    probe = tmp_path / 'probe'
    raw_text = '\n'.join(raw)
    probe.write_text('#!/usr/bin/env python3\nimport sys\n'
                     "print('PROBE_ARGV ' + repr(sys.argv[1:]))\n"
                     f'for line in {raw_text!r}.splitlines():\n'
                     "    if line.startswith(('RTL_SELECTED_EVENT_COUNT ', "
                     "'MODEL_SELECTED_EVENT_COUNT ')) and '--tag-observer-v2' not in sys.argv[1:]:\n"
                     '        continue\n'
                     '    print(line)\n')
    probe.chmod(0o700)
    monkeypatch.setattr(sequence, 'compile_probe', lambda *_args: probe)

    out = tmp_path / 'runtime'
    with pytest.raises(ValueError, match='required boundary v2 coverage missing'):
        sequence.run_absolute(manifest, str(stimulus['case_id']), out,
                              tmp_path / 'build', library)
    argv_row = (out / 'rtl.log').read_text().splitlines()[0]
    assert argv_row.count("'--tag-observer-v2'") == 1
    assert argv_row.count("'--boundary-schema=2'") == 1
    assert not (out / 'report.json').exists()
    with pytest.raises(ValueError, match='boundary v2.*stream'):
        sequence.validate_absolute_log(_coherent_log(stimulus), stimulus)


def test_required_boundary_v2_rejects_missing_queue_edges_before_scalar_checks(
        tmp_path: Path) -> None:
    from sim.tests.cycle.test_sequence_mutations import _queue_edge_v1_log

    # Given: a source-bound v2 declaration and a copied paired raw with only queue rows removed.
    stimulus = _sealed_manifest(tmp_path)
    works = stimulus['works']
    parents = stimulus['pipeline_parents']
    assert isinstance(works, list) and isinstance(parents, list)
    for ordinal, (work, parent) in enumerate(zip(works, parents, strict=True)):
        row = object_value(work)
        row['work_id'] = ordinal
        object_value(row['trace_record'])['work_id'] = ordinal
        object_value(parent)['required_work_ids'] = [ordinal]
        object_value(parent)['fence_required_work_ids'] = [ordinal]
    stimulus['required_observations'] = ['FRAGMENTS', 'TAG_STATE', 'BOUNDARY_V2']
    digest = sequence.stimulus_digest(stimulus)
    stimulus['stimulus_sha256'] = digest
    source = tmp_path / 'paired.raw'
    _queue_edge_v1_log(source)
    copied = tmp_path / 'missing-queue.raw'
    copied.write_text('\n'.join(line.replace('1' * 64, digest)
                                for line in source.read_text().splitlines()
                                if not line.startswith(('RTL_QUEUE_EDGE_V1 ',
                                                        'MODEL_QUEUE_EDGE_V1 '))) + '\n')

    # When: the official streaming runtime consumes the declared observation scope.
    with pytest.raises(ValueError, match='required queue edge coverage missing'):
        # Then: absent transitions fail before work summaries can publish a result.
        sequence.validate_absolute_log_stream(
            copied, stimulus, (0, 1), expected_stimulus_sha256=None,
            expected_repeats=None, require_boundary_v2=True)


def test_comparator_rejects_acceptance_before_raw_ready(tmp_path: Path) -> None:
    # Given: a matching manifest but an RTL fire on a !ready edge.
    stimulus = _sealed_manifest(tmp_path)
    raw = _coherent_log(stimulus).replace('OFFER_EDGE 0 5 1 1 1 1 1',
                                          'OFFER_EDGE 0 5 1 1 0 1 1', 1)

    # When: the strict v2 comparator checks independent raw ready/fire.
    with pytest.raises(ValueError, match='ready'):
        # Then: the claimed acceptance is rejected at its edge.
        sequence.validate_absolute_log(raw, stimulus)


def test_comparator_accepts_independent_ready_streams_when_edges_match(tmp_path: Path) -> None:
    # Given: separate RTL and model edge streams at predeclared epochs.
    stimulus = _sealed_manifest(tmp_path)

    # When: each side fires at its own earliest eligible ready edge.
    works = sequence.validate_absolute_log(_coherent_log(stimulus), stimulus)

    # Then: the two producer works have the declared acceptance epochs.
    assert [work['accepted'] for work in works] == [5, 6]


def test_comparator_fails_closed_when_native_evidence_is_missing(tmp_path: Path) -> None:
    # Given: legal RTL evidence with no native run, work, edge, or event rows.
    stimulus = _sealed_manifest(tmp_path)
    rtl_only = '\n'.join(line for line in _coherent_log(stimulus).splitlines()
                         if not line.startswith('MODEL_'))

    # When: the v2 comparator checks the independently recorded native stream.
    with pytest.raises(ValueError, match='model'):
        # Then: incomplete native evidence fails closed with a domain error.
        sequence.validate_absolute_log(rtl_only, stimulus)


def test_comparator_requires_native_run_binding(tmp_path: Path) -> None:
    # Given: otherwise coherent work evidence without its native run identity.
    stimulus = _sealed_manifest(tmp_path)
    raw = '\n'.join(line for line in _coherent_log(stimulus).splitlines()
                    if not line.startswith('MODEL_RUN '))

    # When: the comparator checks the source-bound run.
    with pytest.raises(ValueError, match='model run'):
        # Then: work rows alone cannot establish an independent model run.
        sequence.validate_absolute_log(raw, stimulus)


@pytest.mark.parametrize(('field', 'changed'), [
    ('work_id', 99), ('work_binding', 'wrong-binding'), ('request_available_cycle', 99),
    ('port_offer_cycle', 99), ('offered', 99), ('accepted', 99), ('result_ready', 99),
    ('final_scale_release', 99), ('resource_ready', 99), ('submissions', 99),
    ('scale_read_requests', 99), ('scale_read_responses', 99), ('scale_release_count', 99),
    ('load_requests', 99), ('load_responses', 99), ('store_requests', 99),
    ('store_responses', 99), ('initial_scratchpad_half', 1),
    ('initial_accumulator_half', 1), ('next_scratchpad_half', 1), ('next_accumulator_half', 1),
])
def test_comparator_rejects_independent_native_work_mutation(
        tmp_path: Path, field: str, changed: JsonValue) -> None:
    # Given: one changed native field while RTL and the sealed input stay fixed.
    stimulus = _sealed_manifest(tmp_path)
    rows = _coherent_log(stimulus).splitlines()
    for index, row in enumerate(rows):
        if row.startswith('MODEL_WORK '):
            model = json.loads(row.removeprefix('MODEL_WORK '))
            if model['ordinal'] == 1:
                model[field] = changed
                rows[index] = 'MODEL_WORK ' + json.dumps(model)
                break

    # When: the comparator checks the altered native report.
    with pytest.raises(ValueError):
        # Then: each work binding, epoch, counter, or half mismatch fails.
        sequence.validate_absolute_log('\n'.join(rows), stimulus)


@pytest.mark.parametrize(('old', 'changed'), [
    ('MODEL_OFFER_EDGE 1 6 1 1 1 1 1', 'MODEL_OFFER_EDGE 1 7 1 1 1 1 1'),
    ('MODEL_EVENT 1 12 6 work', 'MODEL_EVENT 1 12 7 work'),
])
def test_comparator_rejects_independent_native_edge_or_event_mutation(
        tmp_path: Path, old: str, changed: str) -> None:
    # Given: exactly one native ready/fire/epoch/event row changed.
    stimulus = _sealed_manifest(tmp_path)
    raw = _coherent_log(stimulus).replace(old, changed, 1)

    # When: the comparator checks the independent native stream.
    with pytest.raises(ValueError):
        # Then: the altered claim cannot inherit RTL success.
        sequence.validate_absolute_log(raw, stimulus)


@pytest.mark.parametrize(('old', 'changed'), [
    ('AVAIL_EDGE 1 6 1 0 0 0 0', ''),
    ('AVAIL_EDGE 1 7 1 0 0 0 0', 'AVAIL_EDGE 1 7 1 1 0 0 0'),
])
def test_comparator_rejects_missing_or_early_valid_availability(
        tmp_path: Path, old: str, changed: str) -> None:
    # Given: B is pending from cycle 6 while A holds the policy credit to cycle 10.
    stimulus = _sealed_manifest(tmp_path, second_port_offer_cycle=10)
    raw = _coherent_log(stimulus, first_resource_ready=10)

    # When: an availability row is removed or falsely asserts DUT valid.
    with pytest.raises(ValueError, match='AVAIL_EDGE'):
        # Then: external pending evidence cannot be silently omitted or called electrical valid.
        sequence.validate_absolute_log(raw.replace(old, changed, 1), stimulus)


def test_comparator_reports_first_selected_event_divergence(tmp_path: Path) -> None:
    # Given: one native selected event moves one cycle later.
    stimulus = _sealed_manifest(tmp_path)
    raw = _coherent_log(stimulus).replace('MODEL_EVENT 1 12 6 work',
                                          'MODEL_EVENT 1 12 7 work', 1)

    # When: the event multiset differs at the original cycle.
    with pytest.raises(ValueError, match=r'event.*cycle 6.*window='):
        # Then: the first divergent cycle and its nearby events are exposed.
        sequence.validate_absolute_log(raw, stimulus)


def test_comparator_accepts_available_early_with_later_electrical_port(tmp_path: Path) -> None:
    # Given: B is available at 6, while A owns the resource until 10.
    stimulus = _sealed_manifest(tmp_path, second_port_offer_cycle=10)

    # When: each stream drives electrical valid at B's frozen port edge 10.
    works = sequence.validate_absolute_log(_coherent_log(stimulus, first_resource_ready=10), stimulus)

    # Then: availability remains distinct from the actual port offer.
    assert [(work['offered'], work['accepted']) for work in works] == [(5, 5), (10, 10)]


def test_comparator_rejects_model_port_before_its_prior_resource_ready(tmp_path: Path) -> None:
    # Given: RTL can offer B at 6, but the model still owns its resource through 7.
    stimulus = _sealed_manifest(tmp_path)
    raw = _coherent_log(stimulus, model_first_resource_ready=7)

    # When: the comparator checks the model's own prior resource epoch.
    with pytest.raises(ValueError, match='MODEL_OFFER_EDGE port offer preceded prior resource_ready'):
        # Then: the model stream cannot inherit RTL credit.
        sequence.validate_absolute_log(raw, stimulus)


def test_comparator_rejects_model_acceptance_before_its_ready(tmp_path: Path) -> None:
    # Given: only the model edge claims fire while raw ready is low.
    stimulus = _sealed_manifest(tmp_path)
    raw = _coherent_log(stimulus).replace('MODEL_OFFER_EDGE 1 6 1 1 1 1 1',
                                          'MODEL_OFFER_EDGE 1 6 1 1 0 1 1', 1)

    # When: the comparator checks the independent model edge.
    with pytest.raises(ValueError, match='MODEL_OFFER_EDGE accepted before ready'):
        # Then: the model's accepted-before-ready claim fails.
        sequence.validate_absolute_log(raw, stimulus)


def test_comparator_rejects_logical_pending_as_electrical_port(tmp_path: Path) -> None:
    # Given: B arrives and declares electrical port 6 while A owns the resource through 10.
    stimulus = _sealed_manifest(tmp_path)
    raw = _coherent_log(stimulus, first_resource_ready=10)
    raw = raw.replace('"offered": 6, "accepted": 6', '"offered": 6, "accepted": 10')
    for prefix in ('OFFER_EDGE', 'MODEL_OFFER_EDGE'):
        early = f'\n{prefix} 1 6 1 1 1 1 1'
        held = '\n'.join(f'{prefix} 1 {cycle} 1 0 1 0 0' for cycle in range(6, 10))
        raw = raw.replace(early, '\n' + held + f'\n{prefix} 1 10 1 1 1 1 1')

    # When: the comparator checks the declared electrical port edge.
    with pytest.raises(ValueError, match='port offer preceded prior resource_ready'):
        # Then: it rejects the early port instead of moving valid to cycle 10.
        sequence.validate_absolute_log(raw, stimulus)
