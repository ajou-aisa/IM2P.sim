"""Admission of NPU model results for the execution lifecycle and IR cores.

The lifecycle and IR transformations take already-admitted result rows; which results are admitted is the
caller's authority. `certified_results` is the CURRENT_CERTIFIED admission of the official entrypoints. Other
authorities (sim.cycle.nano_local_execution) supply their own admission and never relabel results.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from typing import Final, TypeAlias

from sim.cycle.execution_ir import ensure
from sim.cycle.npu_trace_schema import Record

CURRENT_CERTIFIED: Final = 'CURRENT_CERTIFIED'
# (result rows, rejection detail) -> the same rows, each checked lazily before the core consumes it.
ResultAdmission: TypeAlias = Callable[[Iterable[Record], str], Iterator[Record]]


def certified_results(rows: Iterable[Record], detail: str) -> Iterator[Record]:
    for row in rows:
        ensure(row.get('cycle_model_validation') == CURRENT_CERTIFIED, detail)
        yield row
