# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
# ─── How to run ───
# IM2P_TODO14_FROZEN32=<path> IM2P_TODO14_FROZEN32_SHA256=<sha> PYTHONPATH=. uv run pytest -q sim/tests/cycle/test_sequence_repeat_admission.py
# ──────────────────
from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from sim.cycle.npu_trace_schema import Record, object_value
from sim.cycle.reconstruct_graph import array, sha256
from sim.tests.cycle import compositional_sequence_v2_stimulus as stimulus


@pytest.fixture
def frozen32() -> tuple[Path, Record, str]:
    raw_path = os.environ.get('IM2P_TODO14_FROZEN32')
    expected_sha = os.environ.get('IM2P_TODO14_FROZEN32_SHA256')
    if raw_path is None or expected_sha is None:
        pytest.skip('task-owned frozen32 path and independently reviewed SHA required')
    path = Path(raw_path)
    assert sha256(path) == expected_sha
    return path, object_value(json.loads(path.read_text())), expected_sha


def _shrink(source: Record, path: Path) -> Record:
    changed = copy.deepcopy(source)
    changed['works'] = array(changed['works'])[:24]
    changed['synthetic_parent_occurrences'] = array(changed['synthetic_parent_occurrences'])[:4]
    changed['repeat_count'] = 2
    changed['stimulus_sha256'] = stimulus.stimulus_digest(changed)
    path.write_text(json.dumps(changed, indent=2, sort_keys=True) + '\n')
    return changed


def _offer_change(source: Record, path: Path) -> None:
    changed = copy.deepcopy(source)
    object_value(array(changed['works'])[23])['port_offer_cycle'] = 4_600_006
    changed['stimulus_sha256'] = stimulus.stimulus_digest(changed)
    path.write_text(json.dumps(changed, indent=2, sort_keys=True) + '\n')


def _static_cli(path: Path, out: Path, case_id: str,
                flags: tuple[str, ...] = ()) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, '-B', str(Path(__file__).with_name('compositional_sequence_work.py')),
         '--stimulus', str(path), '--case', case_id, '--out', str(out), *flags],
        capture_output=True, text=True, check=False, timeout=30,
    )


def test_public_rejects_rehashed_32_to_2_without_external_authority(
        frozen32: tuple[Path, Record, str], tmp_path: Path) -> None:
    # Given: a task-owned frozen32 copied to 24 works/4 parents with the same case ID.
    _, source, _ = frozen32
    changed = _shrink(source, tmp_path / 'shrunken.json')

    # When/Then: public admission requires external authority, even with a valid inner digest.
    with pytest.raises(ValueError, match='expected repeat authority'):
        stimulus.validate_stimulus(changed)


def test_cli_rejects_rehashed_32_to_2_without_external_authority(
        frozen32: tuple[Path, Record, str], tmp_path: Path) -> None:
    # Given: the same task-owned shrunken manifest submitted through the official CLI.
    _, source, _ = frozen32
    changed_path = tmp_path / 'shrunken.json'
    _shrink(source, changed_path)
    out = tmp_path / 'out'

    # When: the static CLI tries to publish a result.
    result = _static_cli(changed_path, out, str(source['case_id']))

    # Then: no report or output directory is published.
    assert result.returncode == 1
    assert 'expected repeat authority' in result.stderr
    assert not out.exists()


def test_public_binds_exact_frozen32_sha_and_count(
        frozen32: tuple[Path, Record, str], tmp_path: Path) -> None:
    # Given: exact frozen32 bytes and a self-rehashed 32-to-2 copy.
    _, source, expected_sha = frozen32
    changed = _shrink(source, tmp_path / 'shrunken.json')

    # When: the caller supplies the reviewed SHA and repeat denominator.
    stimulus.validate_stimulus(source, expected_stimulus_sha256=expected_sha,
                               expected_repeats=32)

    # Then: the same authority rejects the shorter copy.
    with pytest.raises(ValueError, match='frozen stimulus file digest mismatch'):
        stimulus.validate_stimulus(changed, expected_stimulus_sha256=expected_sha,
                                   expected_repeats=32)


def test_cli_rejects_shrink_with_own_sha_but_expected_32(
        frozen32: tuple[Path, Record, str], tmp_path: Path) -> None:
    # Given: the shorter copy has its own valid file SHA but the reviewed denominator is 32.
    _, source, _ = frozen32
    changed_path = tmp_path / 'shrunken.json'
    _shrink(source, changed_path)
    out = tmp_path / 'out'

    # When: the official CLI receives that SHA with expected_repeats=32.
    result = _static_cli(changed_path, out, str(source['case_id']),
                         ('--expected-stimulus-sha256', sha256(changed_path),
                          '--expected-repeats', '32'))

    # Then: the independently supplied count blocks denominator shrinkage.
    assert result.returncode == 1
    assert 'expected repeat work/parent coverage differs' in result.stderr
    assert not out.exists()


def test_cli_rejects_rehashed_offer_with_original_frozen_sha(
        frozen32: tuple[Path, Record, str], tmp_path: Path) -> None:
    # Given: one source offer changed while the caller retains the reviewed frozen32 SHA.
    _, source, expected_sha = frozen32
    changed_path = tmp_path / 'offer.json'
    _offer_change(source, changed_path)
    out = tmp_path / 'out'

    # When: the official CLI receives the independent SHA and 32-repeat denominator.
    result = _static_cli(changed_path, out, str(source['case_id']),
                         ('--expected-stimulus-sha256', expected_sha,
                          '--expected-repeats', '32'))

    # Then: the raw file binding rejects the mutation before output publication.
    assert result.returncode == 1
    assert 'frozen stimulus file digest mismatch' in result.stderr
    assert not out.exists()


def test_cli_accepts_exact_frozen32_with_external_authority(
        frozen32: tuple[Path, Record, str], tmp_path: Path) -> None:
    # Given: exact task-owned frozen bytes and a separately supplied reviewed SHA/count.
    path, source, expected_sha = frozen32
    out = tmp_path / 'out'

    # When: the official static CLI admits the exact 32-repeat artifact.
    result = _static_cli(path, out, str(source['case_id']),
                         ('--expected-stimulus-sha256', expected_sha,
                          '--expected-repeats', '32'))

    # Then: publication retains the full denominator and synthetic-parent scope.
    assert result.returncode == 0
    report = object_value(json.loads((out / 'report.json').read_text()))
    assert report['status'] == 'STATIC_VALIDATED_RUNTIME_PENDING'
    assert report['work_count'] == 384
    assert object_value(report['repeat_authority'])['expected_parent_occurrences'] == 64
