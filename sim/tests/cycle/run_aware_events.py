from __future__ import annotations

from collections import Counter
import csv

from sim.tests.cycle.rtl_hardening import SELECTED_EVENTS
from sim.tests.cycle.run_aware_fixture import Corpus, expected_loops


def parse_events_csv(text: str, corpus: Corpus, profile: str) -> tuple[tuple[tuple[int, str], ...], ...]:
    if profile not in corpus.profiles:
        raise ValueError(f"unexpected event profile: {profile}")
    cases: dict[int, tuple[str, ...]] = {}
    events: dict[int, list[tuple[int, str]]] = {i: [] for i in range(1, 9)}
    loops: dict[int, list[tuple[int, ...]]] = {i: [] for i in range(1, 9)}
    releases: Counter[int] = Counter()
    for row in csv.reader(text.splitlines()):
        if not row:
            raise ValueError("empty RTL event row")
        if row[0] == "CASE":
            if len(row) != 16:
                raise ValueError("malformed CASE metadata")
            index = int(row[1])
            if index in cases or index not in events:
                raise ValueError("duplicate or unknown CASE")
            cases[index] = tuple(row[2:])
        elif row[0] == "LOOP":
            if len(row) != 13 or int(row[1]) not in events:
                raise ValueError("malformed LOOP metadata")
            loops[int(row[1])].append(tuple(int(value) for value in row[2:]))
        else:
            if len(row) != 6:
                raise ValueError("malformed selected event")
            index = int(row[0])
            if index not in events or row[2] not in SELECTED_EVENTS | {"scale_release"}:
                raise ValueError("unknown case or event kind")
            if row[2] in SELECTED_EVENTS:
                events[index].append((int(row[1]), row[2]))
            else:
                releases[index] += 1
    if set(cases) != set(events):
        raise ValueError("missing event CASE metadata")
    normalized: list[tuple[tuple[int, str], ...]] = []
    dim = int(profile.split("-d", 1)[1].split("-", 1)[0])
    for index, case in enumerate(corpus.cases, 1):
        metadata = cases[index]
        expected = (case.name, "1", "1", str(case.k), str(case.original_k),
                    "1", "1", "2", "3", "13", "17", "11", "5", "5")
        if metadata != expected:
            raise ValueError(f"event geometry or timing differs: {profile}/{case.name}")
        work = [cycle for cycle, kind in events[index] if kind == "work"]
        expected_rows = expected_loops(case, dim)
        if (len(work) != len(expected_rows) or len(loops[index]) != len(expected_rows) or
                releases[index] != dim * len(expected_rows)):
            raise ValueError(f"RTL work/release event coverage differs: {profile}/{case.name}")
        for ordinal, (row, expected_loop) in enumerate(zip(loops[index], expected_rows)):
            expected_tail = (
                int(ordinal == 0), int(ordinal == len(expected_rows) - 1), int(ordinal > 0),
                expected_loop["fragment_base"], 0, 0, expected_loop["generation"],
                expected_loop["original_block"], expected_loop["compact_k_begin"],
                expected_loop["compact_k_count"],
            )
            if row[1:] != expected_tail or row[0] != work[ordinal]:
                raise ValueError(f"RTL run ownership differs: {profile}/{case.name}")
        normalized.append(tuple(sorted((cycle - min(work) + 1, kind)
                                       for cycle, kind in events[index])))
    return tuple(normalized)


def events_exact(model: list[tuple[int, str]], rtl: list[tuple[int, str]]) -> bool:
    return bool(model and rtl) and sorted(model) == sorted(rtl)
