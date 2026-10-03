# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
# ─── How to run ───
# PYTHONPATH=. uv run --offline pytest -q sim/tests/cycle/test_sequence_todo12_values.py
# ──────────────────
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from sim.cycle.npu_trace_schema import Record, object_value
from sim.cycle.reconstruct_graph import array
from sim.tests.cycle import compositional_sequence_work as sequence
from sim.tests.cycle.test_certify_production_run_aware import WORK
from sim.tests.cycle.test_sequence_absolute_offer import (
    _cli,
    _coherent_log,
    _sealed_manifest,
)


def _with_fixture(tmp_path: Path) -> Record:
    manifest = _sealed_manifest(tmp_path)
    fixture = tmp_path / 'run-work.txt'
    fixture.write_text(WORK.replace(' 19\ngeometry', ' 12\ngeometry', 1))
    manifest['value_fixture'] = {
        'schema': 'RMD_RUN_WORK_V1', 'path': str(fixture),
        'sha256': hashlib.sha256(fixture.read_bytes()).hexdigest(), 'work_id': 12,
    }
    manifest['stimulus_sha256'] = sequence.stimulus_digest(manifest)
    return manifest


def _matching_residual(tmp_path: Path) -> Record:
    manifest = _with_fixture(tmp_path)
    work = object_value(array(manifest['works'])[1])
    work['scope'] = 'residual_compact'
    work['original_k'] = 128
    work['runs'] = [
        {'original_block_id': 0, 'original_k_mask': 1,
         'compact_k_begin': 0, 'compact_k_count': 1},
        {'original_block_id': 3, 'original_k_mask': 2,
         'compact_k_begin': 1, 'compact_k_count': 1},
    ]
    object_value(work['input']).update({
        'm': 2, 'n': 1, 'k': 2,
        'tile_i_count': 1, 'tile_j_count': 1, 'tile_k_count': 1,
        'activation_stride_bytes': 2, 'weight_stride_bytes': 1,
        'output_stride_bytes': 4, 'scale_stride_elements': 1,
    })
    manifest['stimulus_sha256'] = sequence.stimulus_digest(manifest)
    return manifest


def test_no_value_fixture_keeps_v2_static_report_shape(tmp_path: Path) -> None:
    # Given: the existing v2 stimulus without any value-sidecar declaration.
    path = tmp_path / 'plain.json'
    path.write_text(json.dumps(_sealed_manifest(tmp_path)))

    # When: the public static CLI runs with the default manifest.
    result = _cli(path, tmp_path / 'plain-out')

    # Then: the historical status and report shape remain unchanged.
    assert result.returncode == 0, result.stderr
    report = json.loads((tmp_path / 'plain-out/report.json').read_text())
    assert report['status'] == 'STATIC_VALIDATED_RUNTIME_PENDING'
    assert 'value_fixture' not in report


def test_missing_sealed_value_sidecar_is_rejected_before_output(tmp_path: Path) -> None:
    # Given: a re-sealed value binding whose file does not exist.
    manifest = _with_fixture(tmp_path)
    object_value(manifest['value_fixture'])['path'] = str(tmp_path / 'absent.txt')
    manifest['stimulus_sha256'] = sequence.stimulus_digest(manifest)
    path = tmp_path / 'missing.json'
    path.write_text(json.dumps(manifest))

    # When: the public CLI validates its sealed optional sidecar.
    result = _cli(path, tmp_path / 'missing-out')

    # Then: it fails closed without publishing a static report.
    assert result.returncode == 1
    assert 'value fixture missing' in result.stderr
    assert not (tmp_path / 'missing-out').exists()


def test_stale_value_sidecar_digest_is_rejected_before_output(tmp_path: Path) -> None:
    # Given: existing fixture bytes with a stale declared SHA256.
    manifest = _with_fixture(tmp_path)
    object_value(manifest['value_fixture'])['sha256'] = '0' * 64
    manifest['stimulus_sha256'] = sequence.stimulus_digest(manifest)
    path = tmp_path / 'stale.json'
    path.write_text(json.dumps(manifest))

    # When: the public CLI validates its sealed optional sidecar.
    result = _cli(path, tmp_path / 'stale-out')

    # Then: it rejects the stale bytes before creating output.
    assert result.returncode == 1
    assert 'value fixture digest mismatch' in result.stderr
    assert not (tmp_path / 'stale-out').exists()


def test_stale_numeric_parser_header_is_rejected_before_output(tmp_path: Path) -> None:
    # Given: a re-sealed manifest that lies about the current RTL value-parser header.
    manifest = _matching_residual(tmp_path)
    source = object_value(manifest['source_sha256'])
    source['fpga/gemmini_hp1/host/run_aware_numeric_fixture.hpp'] = '0' * 64
    manifest['stimulus_sha256'] = sequence.stimulus_digest(manifest)
    path = tmp_path / 'stale-header.json'
    path.write_text(json.dumps(manifest))

    # When: the public CLI validates current source binding.
    result = _cli(path, tmp_path / 'stale-header-out')

    # Then: a changed parser cannot execute behind a valid sidecar digest.
    assert result.returncode == 1
    assert 'stimulus source digest mismatch' in result.stderr
    assert not (tmp_path / 'stale-header-out').exists()


def test_value_sidecar_wrong_logical_work_is_rejected_before_output(tmp_path: Path) -> None:
    # Given: valid fixture bytes bound to a work ID absent from the manifest.
    manifest = _with_fixture(tmp_path)
    object_value(manifest['value_fixture'])['work_id'] = 999
    manifest['stimulus_sha256'] = sequence.stimulus_digest(manifest)
    path = tmp_path / 'wrong-work.json'
    path.write_text(json.dumps(manifest))

    # When: the public CLI resolves the sidecar's selected logical work.
    result = _cli(path, tmp_path / 'wrong-work-out')

    # Then: unrelated work bytes cannot be attached to this sequence.
    assert result.returncode == 1
    assert 'value fixture work ID' in result.stderr
    assert not (tmp_path / 'wrong-work-out').exists()


def test_value_sidecar_requires_residual_selected_work(tmp_path: Path) -> None:
    # Given: valid fixture bytes but a selected stripe work.
    manifest = _with_fixture(tmp_path)
    path = tmp_path / 'stripe.json'
    path.write_text(json.dumps(manifest))

    # When: the public CLI resolves the sidecar's selected logical work.
    result = _cli(path, tmp_path / 'stripe-out')

    # Then: values cannot be silently forwarded to a dense work.
    assert result.returncode == 1
    assert 'value fixture requires residual work' in result.stderr
    assert not (tmp_path / 'stripe-out').exists()


def test_exact_residual_value_fixture_is_admissible(tmp_path: Path) -> None:
    # Given: a selected residual whose request, geometry and ordered runs match the sidecar.
    manifest = _matching_residual(tmp_path)

    # When: the sealed stimulus is validated.
    sequence.validate_stimulus(manifest)

    # Then: exact original-coordinate sidecar metadata may be admitted.
    assert object_value(manifest['value_fixture'])['work_id'] == 12


def test_exact_residual_value_fixture_static_report_is_pending(tmp_path: Path) -> None:
    # Given: a sealed exact residual fixture and no RTL build invocation.
    manifest = _matching_residual(tmp_path)
    path = tmp_path / 'numeric-static.json'
    path.write_text(json.dumps(manifest))

    # When: the public CLI publishes static input validation.
    result = _cli(path, tmp_path / 'numeric-static-out')

    # Then: the fixture digest and work are bound, but numerical PASS is not claimed.
    assert result.returncode == 0, result.stderr
    report = json.loads((tmp_path / 'numeric-static-out/report.json').read_text())
    bound = object_value(report['value_fixture'])
    assert bound['sha256'] == object_value(manifest['value_fixture'])['sha256']
    assert (bound['work_id'], bound['expected_count'], bound['status']) == \
           (12, 2, 'RUNTIME_PENDING')


def test_value_sidecar_rejects_stride_mismatch(tmp_path: Path) -> None:
    # Given: a selected residual whose activation stride differs from the sidecar.
    manifest = _matching_residual(tmp_path)
    work = object_value(array(manifest['works'])[1])
    object_value(work['input'])['activation_stride_bytes'] = 3
    manifest['stimulus_sha256'] = sequence.stimulus_digest(manifest)

    # When: the sealed stimulus is validated.
    with pytest.raises(ValueError, match='value fixture request differs'):
        # Then: a same-shape but wrong physical request cannot be used.
        sequence.validate_stimulus(manifest)


def test_value_sidecar_rejects_output_byte_stride_mismatch(tmp_path: Path) -> None:
    # Given: the producer sidecar stores one int32 element per row, but work asks for eight bytes.
    manifest = _matching_residual(tmp_path)
    work = object_value(array(manifest['works'])[1])
    object_value(work['input'])['output_stride_bytes'] = 8
    manifest['stimulus_sha256'] = sequence.stimulus_digest(manifest)

    # When: the physical byte-stride binding is validated.
    with pytest.raises(ValueError, match='value fixture request differs'):
        # Then: bytes and int32 elements cannot be conflated.
        sequence.validate_stimulus(manifest)


def test_value_sidecar_rejects_geometry_mismatch(tmp_path: Path) -> None:
    # Given: a selected residual whose tile count differs from the sidecar.
    manifest = _matching_residual(tmp_path)
    work = object_value(array(manifest['works'])[1])
    object_value(work['input'])['tile_k_count'] = 2
    manifest['stimulus_sha256'] = sequence.stimulus_digest(manifest)

    # When: the sealed stimulus is validated.
    with pytest.raises(ValueError, match='value fixture request differs'):
        # Then: geometry cannot be relabeled after value capture.
        sequence.validate_stimulus(manifest)


def test_value_sidecar_rejects_ordered_run_mismatch(tmp_path: Path) -> None:
    # Given: a selected residual with one changed original-block mask.
    manifest = _matching_residual(tmp_path)
    work = object_value(array(manifest['works'])[1])
    object_value(array(work['runs'])[1])['original_k_mask'] = 4
    manifest['stimulus_sha256'] = sequence.stimulus_digest(manifest)

    # When: the sealed stimulus is validated.
    with pytest.raises(ValueError, match='value fixture runs differ'):
        # Then: the sidecar cannot silently supply values for another run mapping.
        sequence.validate_stimulus(manifest)


def test_value_sidecar_rejects_descriptor_work_context_mismatch(tmp_path: Path) -> None:
    # Given: exact shape and runs but producer descriptor context 19 for selected work 12.
    manifest = _matching_residual(tmp_path)
    fixture = Path(str(object_value(manifest['value_fixture'])['path']))
    fixture.write_text(WORK)
    object_value(manifest['value_fixture'])['sha256'] = hashlib.sha256(fixture.read_bytes()).hexdigest()
    manifest['stimulus_sha256'] = sequence.stimulus_digest(manifest)

    # When: the sidecar identity is bound to the selected logical work.
    with pytest.raises(ValueError, match='value fixture work ID differs'):
        # Then: identical geometry cannot attach values from another work context.
        sequence.validate_stimulus(manifest)


def _numeric_log(manifest: Record, row: Record) -> str:
    return _coherent_log(manifest) + '\nNUMERIC_WORK ' + json.dumps(row)


def test_value_sidecar_accepts_exact_numeric_result_row(tmp_path: Path) -> None:
    # Given: one selected-work row with matching output count and first value.
    manifest = _matching_residual(tmp_path)
    raw = _numeric_log(manifest, {'ordinal': 1, 'work_id': 12,
                                  'expected_count': 2, 'actual_count': 2,
                                  'first_expected': 3, 'first_actual': 3,
                                  'numeric_pass': True})

    # When: the value-bound comparator reads the completed row.
    works = sequence.validate_absolute_log(raw, manifest)

    # Then: both logical works retain their original timing reports.
    assert len(works) == 2


def test_value_sidecar_requires_numeric_result_row(tmp_path: Path) -> None:
    # Given: an exact value-bound residual and otherwise coherent RTL/native timing log.
    manifest = _matching_residual(tmp_path)

    # When: no independent numerical result row is present.
    with pytest.raises(ValueError, match='numeric fixture evidence missing'):
        # Then: ordinary timing PASS cannot masquerade as numerical PASS.
        sequence.validate_absolute_log(_coherent_log(manifest), manifest)


def test_value_sidecar_rejects_wrong_work_numeric_row(tmp_path: Path) -> None:
    # Given: a numerical row marked PASS for another logical work.
    manifest = _matching_residual(tmp_path)
    raw = _numeric_log(manifest, {'ordinal': 1, 'work_id': 999,
                                  'expected_count': 2, 'actual_count': 2,
                                  'first_expected': 3, 'first_actual': 3,
                                  'numeric_pass': True})

    # When: the value-bound comparator reads the row.
    with pytest.raises(ValueError, match='numeric fixture work ID differs'):
        # Then: success text cannot substitute for the selected work identity.
        sequence.validate_absolute_log(raw, manifest)


def test_value_sidecar_rejects_false_numeric_pass(tmp_path: Path) -> None:
    # Given: the selected work has a numerical row with a false pass flag.
    manifest = _matching_residual(tmp_path)
    raw = _numeric_log(manifest, {'ordinal': 1, 'work_id': 12,
                                  'expected_count': 2, 'actual_count': 2,
                                  'first_expected': 3, 'first_actual': 3,
                                  'numeric_pass': False})

    # When: the value-bound comparator reads the row.
    with pytest.raises(ValueError, match='numeric fixture failed'):
        # Then: a normal endpoint PASS cannot override failed numerical output.
        sequence.validate_absolute_log(raw, manifest)


def test_value_sidecar_rejects_duplicate_numeric_rows(tmp_path: Path) -> None:
    # Given: two identical numerical PASS rows for one selected work.
    manifest = _matching_residual(tmp_path)
    row: Record = {'ordinal': 1, 'work_id': 12,
                   'expected_count': 2, 'actual_count': 2,
                   'first_expected': 3, 'first_actual': 3, 'numeric_pass': True}
    raw = _numeric_log(manifest, row) + '\nNUMERIC_WORK ' + json.dumps(row)

    # When: the value-bound comparator reads the duplicated evidence.
    with pytest.raises(ValueError, match='numeric fixture evidence missing'):
        # Then: duplicate success rows cannot be counted as one exact result.
        sequence.validate_absolute_log(raw, manifest)


def test_value_sidecar_rejects_duplicate_numeric_pass_key(tmp_path: Path) -> None:
    # Given: one real-shaped row whose false pass key is hidden by a later true key.
    manifest = _matching_residual(tmp_path)
    raw = _numeric_log(manifest, {'ordinal': 1, 'work_id': 12,
                                  'expected_count': 2, 'actual_count': 2,
                                  'first_expected': 3, 'first_actual': 3,
                                  'numeric_pass': True})
    prefix, numeric = raw.rsplit('\nNUMERIC_WORK ', 1)
    raw = prefix + '\nNUMERIC_WORK ' + numeric.replace(
        '"numeric_pass": true}', '"numeric_pass": false, "numeric_pass": true}', 1)

    # When: the value-bound comparator parses the ambiguous JSON row.
    with pytest.raises(ValueError, match='numeric fixture evidence malformed'):
        # Then: last-key-wins parsing cannot turn numerical failure into PASS.
        sequence.validate_absolute_log(raw, manifest)


def test_value_sidecar_rejects_false_first_output_under_pass_flag(tmp_path: Path) -> None:
    # Given: the probe row claims PASS but its first observed result differs.
    manifest = _matching_residual(tmp_path)
    raw = _numeric_log(manifest, {'ordinal': 1, 'work_id': 12,
                                  'expected_count': 2, 'actual_count': 2,
                                  'first_expected': 3, 'first_actual': 4,
                                  'numeric_pass': True})

    # When: the value-bound comparator reads the row.
    with pytest.raises(ValueError, match='numeric fixture failed'):
        # Then: misleading success text cannot hide the visible numerical mismatch.
        sequence.validate_absolute_log(raw, manifest)
