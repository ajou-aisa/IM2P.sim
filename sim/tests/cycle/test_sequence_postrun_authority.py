# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
# ─── How to run ───
# IM2P_TODO14_G4_STIMULUS=<frozen-g4.json> IM2P_TODO14_G4_RAW=<saved-rtl.log> PYTHONPATH=. uv run pytest -q sim/tests/cycle/test_sequence_postrun_authority.py
# ──────────────────
from __future__ import annotations

import copy
import json
import os
import shlex
from pathlib import Path

import pytest

from sim.cycle.npu_trace_schema import object_value
from sim.cycle.reconstruct_graph import array, sha256
from sim.tests.cycle import compositional_sequence_work as sequence


def test_genuine_postrun_reaches_raw_digest_check_with_reviewed_sha(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: a current-source copy of the frozen g4 and its immutable historical RTL raw.
    stimulus_name = os.environ.get('IM2P_TODO14_G4_STIMULUS')
    raw_name = os.environ.get('IM2P_TODO14_G4_RAW')
    if stimulus_name is None or raw_name is None:
        pytest.skip('task-owned frozen g4 and historical raw required')
    raw_path = Path(raw_name)
    stimulus = copy.deepcopy(object_value(json.loads(Path(stimulus_name).read_text())))
    stimulus['source_sha256'] = sequence.stimulus_source_hashes()
    stimulus['required_observations'] = ['FRAGMENTS', 'TAG_STATE', 'TAG_FULL_PRESSURE']
    stimulus['stimulus_sha256'] = sequence.stimulus_digest(stimulus)
    stimulus_path = tmp_path / 'current-stimulus.json'
    stimulus_path.write_text(json.dumps(stimulus, indent=2, sort_keys=True) + '\n')
    preflight = object_value(json.loads((raw_path.parent.parent / 'preflight.json').read_text()))
    argv = [str(value) for value in array(preflight['argv'])]
    build = Path(argv[argv.index('--rtl-build') + 1])
    library = Path(argv[argv.index('--library') + 1])
    replay = tmp_path / 'historical-raw-replay.sh'
    replay.write_text(f'#!/bin/sh\nexec /bin/cat {shlex.quote(str(raw_path))}\n')
    replay.chmod(0o700)
    monkeypatch.setattr(sequence, 'compile_probe', lambda *_args: replay)
    with raw_path.open() as source:
        recorded = object_value(json.loads(source.readline().removeprefix('COMPOSITION_RUN ')))
    assert recorded['stimulus_sha256'] != stimulus['stimulus_sha256']

    # When: the official runner validates the saved raw after admission with reviewed SHA.
    out = tmp_path / 'run'
    with pytest.raises(sequence.AbsoluteOfferError,
                       match='absolute offer run/digest/work evidence missing'):
        sequence.run_absolute(stimulus_path, str(stimulus['case_id']), out, build, library,
                              expected_stimulus_sha256=sha256(stimulus_path))

    # Then: it reached the semantic raw check and published no normal report.
    assert not (out / 'report.json').exists()
