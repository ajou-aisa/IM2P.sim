# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
# ─── How to run ───
# IM2P_TODO14_G4_STIMULUS=<path> IM2P_TODO14_G4_MUTANT=<path> IM2P_TODO13_A4D16=<path> PYTHONPATH=. uv run pytest -q sim/tests/cycle/test_sequence_offer_grid.py
# ──────────────────
from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from sim.cycle.npu_trace_schema import Record, object_value
from sim.cycle.reconstruct_graph import array, sha256
from sim.tests.cycle import compositional_sequence_v2_stimulus as stimulus


def _loaded(name: str) -> Record:
    raw = os.environ.get(name)
    if raw is None:
        pytest.skip(f'{name} task-owned artifact required')
    return object_value(json.loads(Path(raw).read_text()))


def _current(row: Record) -> Record:
    updated = copy.deepcopy(row)
    updated['source_sha256'] = stimulus.stimulus_source_hashes()
    updated['stimulus_sha256'] = stimulus.stimulus_digest(updated)
    return updated


def _file_sha(row: Record) -> str:
    return hashlib.sha256((json.dumps(row, indent=2, sort_keys=True) + '\n').encode()).hexdigest()


def _source_plan(row: Record) -> Record:
    binding = object_value(object_value(row['producer_corpus'])['plan'])
    return object_value(json.loads(Path(str(binding['path'])).read_text()))


def _source_grid(row: Record) -> Record:
    binding = object_value(row['predeclared_offer_grid'])
    return object_value(json.loads(Path(str(binding['path'])).read_text()))


def _sealed(tmp_path: Path, row: Record, plan: Record,
            grid: Record | None = None) -> tuple[Record, Path]:
    changed = _current(row)
    changed_plan = copy.deepcopy(plan)
    if grid is not None:
        grid_path = tmp_path / 'grid.json'
        grid_path.write_text(json.dumps(grid, sort_keys=True) + '\n')
        digest = sha256(grid_path)
        changed_plan['predeclared_offer_grid_sha256'] = digest
        changed['predeclared_offer_grid'] = {'path': str(grid_path), 'sha256': digest}
    plan_path = tmp_path / 'plan.json'
    plan_path.write_text(json.dumps(changed_plan, sort_keys=True) + '\n')
    object_value(changed['producer_corpus'])['plan'] = {
        'path': str(plan_path), 'sha256': sha256(plan_path),
    }
    changed['stimulus_sha256'] = stimulus.stimulus_digest(changed)
    path = tmp_path / 'stimulus.json'
    path.write_text(json.dumps(changed, indent=2, sort_keys=True) + '\n')
    return changed, path


def test_original_directed_g4_remains_admissible(tmp_path: Path) -> None:
    # Given: the unmodified source-bound directed g4 case.
    original = _loaded('IM2P_TODO14_G4_STIMULUS')
    sealed, path = _sealed(tmp_path, original, _source_plan(original))

    # When: public admission checks it.
    stimulus.validate_stimulus(sealed, expected_stimulus_sha256=sha256(path))

    # Then: its work3 port still equals the predeclared grid value.
    assert object_value(array(sealed['works'])[3])['port_offer_cycle'] == 600007


def test_genuine_g4_requires_reviewed_file_sha() -> None:
    # Given: a genuine complete-parent manifest without caller-supplied freeze authority.
    original = _current(_loaded('IM2P_TODO14_G4_STIMULUS'))

    # When/Then: its self-declared digest cannot authorize admission.
    with pytest.raises(ValueError, match='expected genuine stimulus authority missing'):
        stimulus.validate_stimulus(original)


def test_old_todo13_full_array_grid_remains_admissible() -> None:
    # Given: the immutable historical A4W4-D16 full-array grid binding.
    old = _current(_loaded('IM2P_TODO13_A4D16'))

    # When: public admission checks a current-source copy with separately supplied file SHA.
    stimulus.validate_stimulus(old, expected_stimulus_sha256=_file_sha(old))

    # Then: all twelve source-bound works remain covered.
    assert len(array(old['works'])) == 12


def test_rejects_coordinated_g4_plan_and_stimulus_offer_mutation(tmp_path: Path) -> None:
    # Given: the reviewer's exact 600007-to-600006 copied plan and stimulus.
    changed = _current(_loaded('IM2P_TODO14_G4_MUTANT'))

    # When/Then: the unchanged grid overrules both self-consistent copies.
    with pytest.raises(ValueError, match='predeclared offer grid epochs differ'):
        stimulus.validate_stimulus(changed, expected_stimulus_sha256=_file_sha(changed))


def test_cli_rejects_coordinated_g4_before_output(tmp_path: Path) -> None:
    # Given: the same copied g4 stimulus with current source hashes.
    changed = _current(_loaded('IM2P_TODO14_G4_MUTANT'))
    path = tmp_path / 'mutant.json'
    path.write_text(json.dumps(changed, indent=2, sort_keys=True) + '\n')
    out = tmp_path / 'out'

    # When: the official static CLI receives the copied manifest.
    result = subprocess.run(
        [sys.executable, '-B', str(Path(__file__).with_name('compositional_sequence_work.py')),
         '--stimulus', str(path), '--case', str(changed['case_id']), '--out', str(out),
         '--expected-stimulus-sha256', sha256(path)],
        capture_output=True, text=True, check=False, timeout=30,
    )

    # Then: it exits nonzero without publishing a result.
    assert result.returncode == 1
    assert 'predeclared offer grid epochs differ' in result.stderr
    assert not out.exists()


def test_cli_requires_reviewed_sha_for_genuine_g4(tmp_path: Path) -> None:
    # Given: a valid current-source genuine g4 file but no separately reviewed SHA.
    original = _loaded('IM2P_TODO14_G4_STIMULUS')
    changed, path = _sealed(tmp_path, original, _source_plan(original))
    out = tmp_path / 'out'

    # When: the official static CLI is called without the authority flag.
    result = subprocess.run(
        [sys.executable, '-B', str(Path(__file__).with_name('compositional_sequence_work.py')),
         '--stimulus', str(path), '--case', str(changed['case_id']), '--out', str(out)],
        capture_output=True, text=True, check=False, timeout=30,
    )

    # Then: neither static report nor output directory is published.
    assert result.returncode == 1
    assert 'expected genuine stimulus authority missing' in result.stderr
    assert not out.exists()


@pytest.mark.parametrize('missing', ('producer_corpus', 'predeclared_offer_grid'))
def test_cli_rejects_removed_genuine_authority_with_reviewed_sha(
        tmp_path: Path, missing: str) -> None:
    # Given: a frozen original and a rehashed copy missing one required authority binding.
    original = _loaded('IM2P_TODO14_G4_STIMULUS')
    sealed, path = _sealed(tmp_path, original, _source_plan(original))
    reviewed_sha = sha256(path)
    del sealed[missing]
    sealed['stimulus_sha256'] = stimulus.stimulus_digest(sealed)
    mutant_path = tmp_path / 'missing-authority.json'
    mutant_path.write_text(json.dumps(sealed, indent=2, sort_keys=True) + '\n')
    out = tmp_path / 'out'

    # When: the official CLI retains the original reviewer's frozen SHA.
    result = subprocess.run(
        [sys.executable, '-B', str(Path(__file__).with_name('compositional_sequence_work.py')),
         '--stimulus', str(mutant_path), '--case', str(sealed['case_id']), '--out', str(out),
         '--expected-stimulus-sha256', reviewed_sha],
        capture_output=True, text=True, check=False, timeout=30,
    )

    # Then: the changed file cannot publish a static result.
    assert result.returncode == 1
    assert 'frozen stimulus file digest mismatch' in result.stderr
    assert not out.exists()


def test_rejects_coordinated_grid_plan_stimulus_offer_with_reviewed_sha(tmp_path: Path) -> None:
    # Given: a separately frozen original and a fully rehashed three-file copied mutation.
    original = _loaded('IM2P_TODO14_G4_STIMULUS')
    _, original_path = _sealed(tmp_path, original, _source_plan(original))
    mutant = _loaded('IM2P_TODO14_G4_MUTANT')
    grid = _source_grid(mutant)
    case = next(object_value(row) for row in array(grid['cases'])
                if object_value(row)['case_id'] == mutant['case_id'])
    object_value(case['extra_ports'])['3'] = 600006
    mutant_dir = tmp_path / 'coordinated'
    mutant_dir.mkdir()
    changed, _ = _sealed(mutant_dir, mutant, _source_plan(mutant), grid)

    # When/Then: the reviewed original SHA prevents the new input from replacing the freeze.
    with pytest.raises(ValueError, match='frozen stimulus file digest mismatch'):
        stimulus.validate_stimulus(changed, expected_stimulus_sha256=sha256(original_path))


def test_rejects_missing_required_grid_binding() -> None:
    # Given: a producer plan declares a frozen grid SHA but the stimulus drops its binding.
    changed = _current(_loaded('IM2P_TODO14_G4_STIMULUS'))
    del changed['predeclared_offer_grid']
    changed['stimulus_sha256'] = stimulus.stimulus_digest(changed)

    # When/Then: the missing required grid cannot be silently skipped.
    with pytest.raises(ValueError, match='predeclared offer grid missing'):
        stimulus.validate_stimulus(changed)


def test_rejects_coordinated_removal_of_both_grid_bindings(tmp_path: Path) -> None:
    # Given: the copied reviewer mutant drops grid SHA from its plan and grid binding from stimulus.
    original = _loaded('IM2P_TODO14_G4_MUTANT')
    plan = _source_plan(original)
    del plan['predeclared_offer_grid_sha256']
    changed, _ = _sealed(tmp_path, original, plan)
    del changed['predeclared_offer_grid']
    changed['stimulus_sha256'] = stimulus.stimulus_digest(changed)

    # When/Then: removing both authority declarations cannot disable source-grid admission.
    with pytest.raises(ValueError, match='predeclared offer grid missing'):
        stimulus.validate_stimulus(changed)


def test_rejects_removal_of_genuine_producer_corpus() -> None:
    # Given: a copied reviewer mutant drops its corpus binding but keeps the grid binding.
    changed = _current(_loaded('IM2P_TODO14_G4_MUTANT'))
    del changed['producer_corpus']
    changed['stimulus_sha256'] = stimulus.stimulus_digest(changed)

    # When/Then: genuine complete parents must remain bound to source corpus and grid.
    with pytest.raises(ValueError, match='producer corpus missing'):
        stimulus.validate_stimulus(changed)


def test_rejects_grid_path_and_sha_mutations(tmp_path: Path) -> None:
    # Given: a valid plan with a missing grid path or mismatched grid digest.
    original = _loaded('IM2P_TODO14_G4_STIMULUS')
    for changed_binding in (
            {'path': str(tmp_path / 'missing-grid.json'),
             'sha256': object_value(original['predeclared_offer_grid'])['sha256']},
            {'path': object_value(original['predeclared_offer_grid'])['path'],
             'sha256': '0' * 64}):
        changed = _current(original)
        changed['predeclared_offer_grid'] = changed_binding
        changed['stimulus_sha256'] = stimulus.stimulus_digest(changed)

        # When/Then: actual grid bytes must match the bound path and SHA.
        with pytest.raises(ValueError, match='predeclared offer grid'):
            stimulus.validate_stimulus(changed, expected_stimulus_sha256=_file_sha(changed))


def test_rejects_unknown_grid_schema_and_missing_case(tmp_path: Path) -> None:
    # Given: valid plan/stimulus vectors but a newly bound unknown or case-less grid.
    original = _loaded('IM2P_TODO14_G4_STIMULUS')
    plan = _source_plan(original)
    for variant in ('unknown-schema', 'missing-case'):
        grid = _source_grid(original)
        if variant == 'unknown-schema':
            grid['schema'] = 'unknown-grid-v1'
        else:
            grid['cases'] = [row for row in array(grid['cases'])
                             if object_value(row)['case_id'] != original['case_id']]
        folder = tmp_path / variant
        folder.mkdir()
        changed, _ = _sealed(folder, original, plan, grid)

        # When/Then: schema and exact case selection are mandatory.
        with pytest.raises(ValueError, match='predeclared offer grid'):
            stimulus.validate_stimulus(changed, expected_stimulus_sha256=_file_sha(changed))


def test_malformed_grid_has_typed_authority_error(tmp_path: Path) -> None:
    # Given: the grid binding is rehashed, but its required case array is absent.
    original = _loaded('IM2P_TODO14_G4_STIMULUS')
    grid = _source_grid(original)
    del grid['cases']
    changed, _ = _sealed(tmp_path, original, _source_plan(original), grid)

    # When/Then: malformed authority fails closed through the public typed error.
    with pytest.raises(ValueError, match='predeclared offer grid malformed'):
        stimulus.validate_stimulus(changed, expected_stimulus_sha256=_file_sha(changed))


def test_rejects_grid_epoch_and_prior_q0_mutations(tmp_path: Path) -> None:
    # Given: a rehashed compact grid changes one extra port or its Todo13 prior Q0.
    original = _loaded('IM2P_TODO14_G4_STIMULUS')
    plan = _source_plan(original)
    for variant in ('epoch', 'prior-q0'):
        grid = _source_grid(original)
        if variant == 'epoch':
            case = next(object_value(row) for row in array(grid['cases'])
                        if object_value(row)['case_id'] == original['case_id'])
            object_value(case['extra_ports'])['3'] = 600006
        else:
            grid['prior_q0'] = 44778
        folder = tmp_path / variant
        folder.mkdir()
        changed, _ = _sealed(folder, original, plan, grid)

        # When/Then: the vector or independent prior-report authority must differ.
        with pytest.raises(ValueError, match='predeclared offer grid|prior_q0'):
            stimulus.validate_stimulus(changed, expected_stimulus_sha256=_file_sha(changed))


def test_rejects_old_full_array_grid_epoch_mutation(tmp_path: Path) -> None:
    # Given: a rehashed historical grid alone changes work3 electrical offer.
    old = _loaded('IM2P_TODO13_A4D16')
    grid = _source_grid(old)
    case = next(object_value(row) for row in array(grid['cases'])
                if object_value(row)['profile'] == old['profile'])
    array(case['electrical_port_offer_cycles'])[3] = 600006
    changed, _ = _sealed(tmp_path, old, _source_plan(old), grid)

    # When/Then: full-array grid epochs remain authoritative too.
    with pytest.raises(ValueError, match='predeclared offer grid epochs differ'):
        stimulus.validate_stimulus(changed, expected_stimulus_sha256=_file_sha(changed))
