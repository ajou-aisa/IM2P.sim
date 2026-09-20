from __future__ import annotations

from pathlib import Path
from typing import TypeAlias

from scripts.gemmini_resolve_profile import JsonValue

JsonObject: TypeAlias = dict[str, JsonValue]


def complete_document(document: JsonObject, expected: dict[str, list[str]], library: Path,
                      execution_kind: str) -> JsonObject:
    from scripts.gemmini_replay_contract import hardware_contract, reference_memory_contract
    from sim.cycle.certificate_contract import finalize_certificate
    from sim.tests.cycle.rtl_hardening import PROFILES

    result: JsonObject = dict(document)
    result.update(schema='im2p-single-gemm-cycle-certificate', version=1,
                  profiles=list(PROFILES), framings=['regression-tiles', 'planner-blocks'],
                  scope='isolated-work-accounting', timing_profile='rtl-regression',
                  expected_cases={name: list(cases) for name, cases in expected.items()},
                  hardware_contracts={name: hardware_contract(name) for name in PROFILES},
                  reference_memory=reference_memory_contract(), execution_kind=execution_kind)
    if result['status'] == 'PASS':
        return finalize_certificate(result, library)
    return result
