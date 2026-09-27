from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Final

from scripts.gemmini_replay_contract import canonical_json
from sim.cycle.npu_trace_schema import Record
from sim.cycle.reconstruct_graph import sha256
from sim.tests.cycle import compositional_sequence_v2_corpus as corpus
from sim.tests.cycle import compositional_sequence_v2_values as values

ROOT: Final = Path(__file__).resolve().parents[3]
SOURCE: Final = ROOT / 'sim/tests/cycle/compositional_sequence_probe.cpp'
BASE: Final = ROOT / 'sim/tests/cycle/production_sequence_probe.cpp'
RUNNER: Final = ROOT / 'sim/tests/cycle/compositional_sequence_work.py'
STIMULUS_SOURCES: Final = (
    SOURCE, BASE, RUNNER, Path(__file__).resolve(),
    ROOT / 'sim/tests/cycle/compositional_sequence_v2_stimulus.py',
    ROOT / 'sim/tests/cycle/compositional_sequence_v2_runtime.py',
    ROOT / 'sim/tests/cycle/compositional_sequence_v1_runtime.py',
    ROOT / 'sim/tests/cycle/compositional_sequence_v2_compile.py',
    ROOT / 'sim/tests/cycle/compositional_sequence_v2_text.py',
    ROOT / 'sim/tests/cycle/compositional_sequence_repeat.py',
    ROOT / 'sim/tests/cycle/compositional_sequence_v2_stream.py',
    ROOT / 'sim/tests/cycle/compositional_sequence_v2_payload.py',
    ROOT / 'sim/tests/cycle/tag_pressure_observer.hpp',
    *corpus.CORPUS_SOURCES, *values.NUMERIC_SOURCES,
)
POLICY: Final = 'public-ready-or-later-arrival-v1'
ABSOLUTE_POLICY: Final = 'absolute-offer-one-pending-v2'
STIMULUS_SCHEMA: Final = 'im2p-compositional-sequence-stimulus'


class AbsoluteOfferError(ValueError):
    detail: str

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


def stimulus_digest(stimulus: Record) -> str:
    return hashlib.sha256(canonical_json({key: value for key, value in stimulus.items()
                                          if key != 'stimulus_sha256'}).encode()).hexdigest()


def stimulus_source_hashes() -> Record:
    return {str(path.relative_to(ROOT)): sha256(path) for path in STIMULUS_SOURCES}
