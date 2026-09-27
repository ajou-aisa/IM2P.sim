# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
# ─── How to run ───
# PYTHONPATH=. uv run pytest -q sim/tests/cycle/test_sequence_payload_v2.py
# ──────────────────
from __future__ import annotations

import json
from pathlib import Path

import pytest

from sim.cycle.npu_trace_schema import Record
from sim.cycle.reconstruct_graph import sha256
from sim.tests.cycle.compositional_sequence_v2_base import (
    STIMULUS_SOURCES,
    stimulus_source_hashes,
)
from sim.tests.cycle.compositional_sequence_v2_payload import payload_geometry
from sim.tests.cycle.compositional_sequence_v2_stream import compare_selected_events
from sim.tests.cycle.test_sequence_mutations import _boundary_log, _queue_edge_v1_log

RAW = Path(
    '/Users/zerogod/aisa-lab/build/im2p-gemmini/'
    'stateful-sequence-v2-20260924T033303Z/rtl/'
    'todo15-queue-payload-v2-emitter-20260926T040029Z/target-v2.raw'
)
ROOT = Path(__file__).resolve().parents[3]


def test_payload_helper_is_bound_into_fresh_stimulus_source_hashes() -> None:
    helper = ROOT / 'sim/tests/cycle/compositional_sequence_v2_payload.py'
    assert helper in STIMULUS_SOURCES
    assert stimulus_source_hashes()[str(helper.relative_to(ROOT))] == sha256(helper)


def _synthetic_v2(path: Path, profile: str, *, invalid: bool = False) -> Record:
    _queue_edge_v1_log(path)
    contract_path = ROOT / 'config/gemmini_host_memory_contracts' / f'{profile}.json'
    contract = json.loads(contract_path.read_text())
    source_key = str(contract_path.relative_to(ROOT))
    authority: Record = {
        'profile': profile, 'stimulus_sha256': '1' * 64,
        'hardware_contract': {
            'profile': profile,
            'facts': {'memory': {key: contract[key] for key in
                                 ('bank_count', 'bank_rows', 'accumulator_rows')}},
            'source_sha256': {source_key: sha256(contract_path)},
        },
    }
    geometry = payload_geometry(authority)
    lines = path.read_text().splitlines()
    for index, line in enumerate(lines):
        if not line.startswith(('RTL_QUEUE_EDGE_V1 ', 'MODEL_QUEUE_EDGE_V1 ')):
            continue
        kind, _, data = line.partition(' ')
        row = json.loads(data)
        row['queue_schema'] = 2
        row['old']['tag_payloads'] = []
        if kind.startswith('RTL'):
            row['next']['tags'] = [[1, 0, 0, 0]] if invalid else [[1, 1, 1, 7]]
            row['next']['tag_payloads'] = [
                [1, 0, 0, 0, 0, geometry.address_mask, 1, 1, 1, 1] if invalid
                else [1, 1, 7, 1, 1, 0, 1, 0, 0, 0]
            ]
        else:
            row['next']['tags'] = [[1, 0, 0, 0]] if invalid else [[1, 1, 1, 7]]
            row['next']['tag_payloads'] = [
                [1, 0, 0, 1, 0, (1 << 32) - 1, 0, 0, 0, 1, 0, 0] if invalid
                else [1, 1, 7, 1, 0, 0, 1, 1, 0, 1, 0, 0]
            ]
        lines[index] = kind.replace('_V1', '_V2') + ' ' + json.dumps(row, separators=(',', ':'))
    path.write_text('\n'.join(lines) + '\n')
    return authority


@pytest.mark.skipif(not RAW.is_file(), reason='sealed Todo15 V2 raw unavailable')
def test_source_bound_v2_raw_admitted_when_payload_required() -> None:
    # Given: the sealed A4W4-D16 V2 RTL/native raw.
    # When: strict payload admission is requested.
    counts = compare_selected_events(
        RAW, tuple(range(12)), require_counts=True, require_boundary_v2=True,
        require_queue_edges=True, require_queue_payload_v2=True,
    )
    # Then: every selected work is covered.
    assert counts == (72298, 82504, 72298, 72298, 72298, 12121,
                      72298, 110005, 72298, 72298, 72298, 12121)


@pytest.mark.parametrize(('field', 'offset'), (
    ('addr_data', 5), ('output_cols', 4), ('accumulate', 7),
))
@pytest.mark.skipif(not RAW.is_file(), reason='sealed Todo15 V2 raw unavailable')
def test_source_bound_v2_rejects_one_field_mutation(
        tmp_path: Path, field: str, offset: int) -> None:
    authority = json.loads((RAW.parent / 'fresh-stimulus.json').read_text())
    mutant = tmp_path / 'mutant.raw'
    changed = False
    with RAW.open('rb') as source, mutant.open('wb') as target:
        for line in source:
            if not changed and line.startswith(b'RTL_QUEUE_EDGE_V2 '):
                row = json.loads(line.removeprefix(b'RTL_QUEUE_EDGE_V2 '))
                row['next']['tag_payloads'][0][offset] += 1
                candidate = b'RTL_QUEUE_EDGE_V2 ' + json.dumps(
                    row, separators=(',', ':')).encode() + b'\n'
                assert len(candidate) == len(line)
                assert sum(left != right for left, right in zip(candidate, line, strict=True)) == 1
                line = candidate
                changed = True
            target.write(line)
    assert changed
    with pytest.raises(ValueError, match=rf'generation 1 cycle 358 .*{field}'):
        compare_selected_events(
            mutant, tuple(range(12)), require_counts=True, require_boundary_v2=True,
            require_queue_edges=True, require_queue_payload_v2=True,
            payload_stimulus=authority,
        )


def test_legacy_v1_rejects_unversioned_payload_extra_fields(tmp_path: Path) -> None:
    path = tmp_path / 'v1.raw'
    _queue_edge_v1_log(path)
    lines = path.read_text().splitlines()
    index = next(i for i, line in enumerate(lines) if line.startswith('RTL_QUEUE_EDGE_V1 '))
    row = json.loads(lines[index].partition(' ')[2])
    row['old'].update({'address': 0, 'cols': 16, 'accumulate': 0})
    lines[index] = 'RTL_QUEUE_EDGE_V1 ' + json.dumps(row, separators=(',', ':'))
    path.write_text('\n'.join(lines) + '\n')
    with pytest.raises(ValueError, match='accumulate missing or unexpected'):
        compare_selected_events(path, (0, 1), require_counts=True,
                                require_boundary_v2=True, require_queue_edges=True)


def test_legacy_boundary_extra_fields_only_pass_without_v2_requirement(tmp_path: Path) -> None:
    path = tmp_path / 'v1-boundary.raw'
    _boundary_log(path)
    lines = path.read_text().splitlines()
    index = next(i for i, line in enumerate(lines) if line.startswith('RTL_BOUNDARY '))
    row = json.loads(lines[index].partition(' ')[2])
    row.update({'tag_addr_data': 12345, 'tag_addr_accumulate': 1, 'tag_cols': 999})
    lines[index] = 'RTL_BOUNDARY ' + json.dumps(row, separators=(',', ':'))
    path.write_text('\n'.join(lines) + '\n')
    assert compare_selected_events(path, (0, 1), require_counts=True) == (2, 2)
    authority = _synthetic_v2(tmp_path / 'authority.raw', 'a4w4-d16-hp1')
    with pytest.raises(ValueError, match='required boundary v2 rejects legacy v1 row'):
        compare_selected_events(
            path, (0, 1), require_counts=True, require_boundary_v2=True,
            require_queue_edges=True, require_queue_payload_v2=True,
            payload_stimulus=authority,
        )


@pytest.mark.parametrize('profile', (
    'a4w4-d16-hp1', 'a4w4-d32-hp1', 'a4w4-d64-hp1',
    'a8w8-d16-hp1', 'a8w8-d32-hp1', 'a8w8-d64-hp1',
))
def test_synthetic_v2_admits_source_contract_profiles(tmp_path: Path, profile: str) -> None:
    path = tmp_path / 'v2.raw'
    authority = _synthetic_v2(path, profile)
    assert compare_selected_events(
        path, (0, 1), require_counts=True, require_boundary_v2=True,
        require_queue_edges=True, require_queue_payload_v2=True,
        payload_stimulus=authority,
    ) == (2, 2)


def test_synthetic_v2_admits_source_invalid_sentinels(tmp_path: Path) -> None:
    path = tmp_path / 'v2.raw'
    authority = _synthetic_v2(path, 'a4w4-d16-hp1', invalid=True)
    assert compare_selected_events(
        path, (0, 1), require_counts=True, require_boundary_v2=True,
        require_queue_edges=True, require_queue_payload_v2=True,
        payload_stimulus=authority,
    ) == (2, 2)


def test_synthetic_v2_rejects_invalid_tag_sentinel_change(tmp_path: Path) -> None:
    path = tmp_path / 'v2.raw'
    authority = _synthetic_v2(path, 'a4w4-d16-hp1', invalid=True)
    lines = path.read_text().splitlines()
    index = next(i for i, line in enumerate(lines) if line.startswith('RTL_QUEUE_EDGE_V2 '))
    row = json.loads(lines[index].partition(' ')[2])
    row['next']['tag_payloads'][0][5] -= 1
    lines[index] = 'RTL_QUEUE_EDGE_V2 ' + json.dumps(row, separators=(',', ':'))
    path.write_text('\n'.join(lines) + '\n')
    with pytest.raises(ValueError, match=r'cycle 2 tag_payloads\[0\]\.addr_data differs'):
        compare_selected_events(
            path, (0, 1), require_counts=True, require_boundary_v2=True,
            require_queue_edges=True, require_queue_payload_v2=True,
            payload_stimulus=authority,
        )


def test_synthetic_v2_rejects_unprovable_geometry(tmp_path: Path) -> None:
    path = tmp_path / 'v2.raw'
    authority = _synthetic_v2(path, 'a4w4-d16-hp1')
    hardware = authority['hardware_contract']
    assert isinstance(hardware, dict)
    sources = hardware['source_sha256']
    assert isinstance(sources, dict)
    sources['config/gemmini_host_memory_contracts/a4w4-d16-hp1.json'] = '0' * 64
    with pytest.raises(ValueError, match='profile geometry source differs'):
        compare_selected_events(
            path, (0, 1), require_counts=True, require_boundary_v2=True,
            require_queue_edges=True, require_queue_payload_v2=True,
            payload_stimulus=authority,
        )


def test_synthetic_v2_rejects_paired_payload_gap(tmp_path: Path) -> None:
    path = tmp_path / 'v2.raw'
    authority = _synthetic_v2(path, 'a4w4-d16-hp1')
    lines = path.read_text().splitlines()
    for kind, offset in (('RTL', 4), ('MODEL', 7)):
        index = next(i for i, line in enumerate(lines)
                     if line.startswith(f'{kind}_QUEUE_EDGE_V2 '))
        first = json.loads(lines[index].partition(' ')[2])
        old = json.loads(json.dumps(first['next']))
        old['cycle'] = 4
        old['tag_payloads'][0][offset] = 2
        nxt = json.loads(json.dumps(old))
        nxt['cycle'] = 5
        second = {'queue_schema': 2, 'generation': 1, 'old': old, 'next': nxt}
        lines.insert(index + 1, f'{kind}_QUEUE_EDGE_V2 ' + json.dumps(second, separators=(',', ':')))
    path.write_text('\n'.join(lines) + '\n')
    with pytest.raises(ValueError, match=r'generation 1 cycle 4 tag_payloads\[0\]\.output_cols gap'):
        compare_selected_events(
            path, (0, 1), require_counts=True, require_boundary_v2=True,
            require_queue_edges=True, require_queue_payload_v2=True,
            payload_stimulus=authority,
        )


@pytest.mark.parametrize(('mutation', 'message'), (
    ('schema', 'queue edge schema malformed'),
    ('width', r'tag_payloads\[0\] width malformed'),
    ('boolean', r'tag_payloads\[0\]\.accumulate malformed'),
    ('cycle', r'next\.cycle differs'),
    ('line_cap', 'queue edge line cap exceeded'),
    ('normalized_rob', r'tag_payloads\[0\]\.RTL\.rob_id'),
    ('missing_counterpart', 'MODEL counterpart coverage missing'),
    ('mixed_version', 'MODEL_QUEUE_EDGE_V2 queue edge malformed'),
    ('early_reset', 'reset origin differs'),
    ('duplicate_work', 'COMPOSITION_WORK order or selected events missing'),
    ('event_cycle', 'selected event divergence at work 0 cycle 0'),
))
def test_synthetic_v2_rejects_malformed_or_unpaired_edge(
        tmp_path: Path, mutation: str, message: str) -> None:
    path = tmp_path / 'v2.raw'
    authority = _synthetic_v2(path, 'a4w4-d16-hp1')
    lines = path.read_text().splitlines()
    if mutation == 'missing_counterpart':
        lines = [line for line in lines if not line.startswith('MODEL_QUEUE_EDGE_V2 ')]
    elif mutation == 'duplicate_work':
        index = next(i for i, line in enumerate(lines) if line.startswith('COMPOSITION_WORK '))
        lines.insert(index + 1, lines[index])
    elif mutation == 'event_cycle':
        index = next(i for i, line in enumerate(lines) if line.startswith('RTL_EVENT 0 0 0 '))
        lines[index] = lines[index].replace('RTL_EVENT 0 0 0 ', 'RTL_EVENT 0 0 1 ', 1)
    elif mutation == 'line_cap':
        index = next(i for i, line in enumerate(lines) if line.startswith('RTL_QUEUE_EDGE_V2 '))
        lines[index] += ' ' * 2048
    elif mutation == 'mixed_version':
        index = next(i for i, line in enumerate(lines) if line.startswith('MODEL_QUEUE_EDGE_V2 '))
        lines[index] = lines[index].replace('MODEL_QUEUE_EDGE_V2 ', 'MODEL_QUEUE_EDGE_V1 ', 1)
    else:
        kind = 'MODEL' if mutation in ('cycle', 'early_reset') else 'RTL'
        index = next(i for i, line in enumerate(lines)
                     if line.startswith(f'{kind}_QUEUE_EDGE_V2 '))
        row = json.loads(lines[index].partition(' ')[2])
        if mutation == 'schema':
            row['queue_schema'] = 1
        elif mutation == 'width':
            row['next']['tag_payloads'][0].pop()
        elif mutation == 'boolean':
            row['next']['tag_payloads'][0][7] = 2
        elif mutation == 'cycle':
            row['next']['cycle'] += 1
        elif mutation == 'normalized_rob':
            row['next']['tag_payloads'][0][2] = 6
        elif mutation == 'early_reset':
            row['old']['tag_enqueues'] = 1
            row['old']['tag_dequeues'] = 1
        lines[index] = f'{kind}_QUEUE_EDGE_V2 ' + json.dumps(row, separators=(',', ':'))
    path.write_text('\n'.join(lines) + '\n')
    with pytest.raises(ValueError, match=message):
        compare_selected_events(
            path, (0, 1), require_counts=True, require_boundary_v2=True,
            require_queue_edges=True, require_queue_payload_v2=True,
            payload_stimulus=authority,
        )
