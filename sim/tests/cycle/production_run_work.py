from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import NotRequired, TypedDict

from scripts.gemmini_resolve_profile import JsonValue
from sim.cycle import cli
from sim.cycle.certificate_contract import array_value, number, read_document
from sim.cycle.npu_trace_schema import object_value, text
from sim.tests.cycle.rtl_hardening import PROFILES

ROOT = Path(__file__).resolve().parents[3]
WORKSPACE = ROOT.parent
PRODUCTION_MANIFEST = ROOT / 'sim/tests/cycle/production_run_aware_corpus.json'
PRODUCTION_MANIFEST_SHA256 = '6d387e98194c6c18e203e4a80c9716e1be72b0ef07594267b6b77fc66f9befe6'
PRODUCTION_CASE_NAMES = ('gap_0_3', 'gap_0_3_k22', 'gap_0_3_k37', 'one_run',
                         'boundary_1_31', 'tail_mn', 'radix_lane')
PRODUCTION_SOURCE_FILES = (
    'IM2P.sim/sim/cycle/c_api.cpp',
    'IM2P.sim/sim/cycle/control_engine.cpp',
    'IM2P.sim/sim/cycle/scheduled_work.cpp',
    'IM2P.sim/sim/cycle/timing_profile.hpp',
    'IM2P.sim/sim/common/gemmini_schedule.cpp',
    'IM2P.sim/sim/include/im2p_cycle_model.h',
    'IM2P.sim/sim/include/im2p_geometry.h',
    'IM2P.sim/sim/cycle/cli.py',
    'IM2P.sim/scripts/gemmini_rtl_build_binding.py',
    'IM2P.sim/sim/cycle/run_aware_certificate.py',
    'IM2P.sim/sim/cycle/run_aware_production_evidence.py',
    'IM2P.sim/sim/tests/cycle/certify_production_run_aware.py',
    'IM2P.sim/sim/tests/cycle/production_run_work.py',
    'IM2P.sim/fpga/gemmini_hp1/host/CMakeLists.txt',
    'IM2P.sim/fpga/gemmini_hp1/host/rmd.cpp',
    'IM2P.sim/fpga/gemmini_hp1/host/test_run_aware_rtl.cpp',
    'IM2P.sim/frontend/src/im2p_cpu_functional.cpp',
    'llama.cpp-gemmini/ggml/src/ggml-gemmini/residual/rmd/rmd-builder.cpp',
    'llama.cpp-gemmini/ggml/src/ggml-gemmini/residual/rmd/rmd-compose.cpp',
    'llama.cpp-gemmini/ggml/src/ggml-gemmini/residual/rmd/rmd-im2p-executor.cpp',
    'llama.cpp-gemmini/ggml/src/ggml-gemmini/residual/rmd/rmd-run-aware.cpp',
    'llama.cpp-gemmini/tests/CMakeLists.txt',
    'llama.cpp-gemmini/tests/test-gemmini-rmd-im2p-provider.cpp',
) + tuple(f'IM2P.sim/config/gemmini_host_memory_contracts/a{bits}w{bits}-d{dim}-hp1.json'
          for bits in (4, 8) for dim in (16, 32, 64))


class WorkFixture(TypedDict):
    descriptor: list[int]
    geometry: list[int]
    runs_header: list[int]
    runs: list[list[int]]
    row_map: list[list[int]]
    shape: list[int]
    tile: list[int]
    original_k: int


class Case(WorkFixture):
    profile: str
    case: str
    fixture_sha256: str
    timing: dict[str, int]
    framing: str


class Manifest(TypedDict):
    schema_version: int
    artifact_role: str
    producer_source: str
    producer_source_sha256: str
    profiles: list[str]
    case_count: int
    cases: list[Case]


class EventDifference(TypedDict):
    index: int
    model: tuple[int, str] | None
    rtl: tuple[int, str] | None


class EventComparison(TypedDict):
    exact: bool
    first_difference: EventDifference | None
    model_count: int
    rtl_count: int
    model_sha256: str
    rtl_sha256: str


class Artifact(TypedDict):
    path: str
    sha256: str


class CertificateCase(TypedDict):
    profile: str
    case: str
    shape: list[int]
    tile: list[int]
    original_k: int
    runs: list[list[int]]
    row_map: list[list[int]]
    timing: dict[str, int]
    framing: str
    status: str
    rtl_admitted: bool
    model_admitted: bool
    rtl_summary: dict[str, int]
    model_summary: dict[str, int]
    selected_event_multiset: bool
    selected_event_comparison: EventComparison
    differences: dict[str, dict[str, int]]
    delta_cycles: int
    artifacts: NotRequired[dict[str, Artifact]]


class Certificate(TypedDict):
    status: str
    artifact_role: str
    production_one_logical_cross_block_gemm: str
    expected: int
    attempted: int
    rtl_admitted: int
    model_admitted: int
    exact: int
    selected_event_multisets_exact: int
    event_plus_one_mutation_rejected: bool
    event_plus_one_mutation_case: str
    max_abs_delta_cycles: int
    first_mismatch: CertificateCase | None
    manifest_sha256: str
    library_sha256: str
    source_sha256: dict[str, str]
    cases: list[CertificateCase]


class CycleModel(TypedDict):
    result: dict[str, int]
    events: list[dict[str, int | str]]


def load_manifest() -> Manifest:
    if sha256(PRODUCTION_MANIFEST.read_bytes()).hexdigest() != PRODUCTION_MANIFEST_SHA256:
        raise ValueError("production corpus manifest changed")

    def integers(value: JsonValue, label: str) -> list[int]:
        return [number(item, label) for item in array_value(value, label)]

    def pairs(value: JsonValue, label: str) -> list[list[int]]:
        return [integers(item, label) for item in array_value(value, label)]

    source = read_document(PRODUCTION_MANIFEST)
    parsed: list[Case] = []
    for value in array_value(source.get("cases"), "production cases"):
        row = object_value(value)
        timing = object_value(row.get("timing"))
        parsed.append({"profile": text(row, "profile"), "case": text(row, "case"),
                       "fixture_sha256": text(row, "fixture_sha256"),
                       "descriptor": integers(row.get("descriptor"), "descriptor"),
                       "geometry": integers(row.get("geometry"), "geometry"),
                       "runs_header": integers(row.get("runs_header"), "runs header"),
                       "runs": pairs(row.get("runs"), "runs"),
                       "row_map": pairs(row.get("row_map"), "row map"),
                       "shape": integers(row.get("shape"), "shape"),
                       "tile": integers(row.get("tile"), "tile"),
                       "original_k": number(row.get("original_k"), "original K"),
                       "timing": {key: number(item, key) for key, item in timing.items()},
                       "framing": text(row, "framing")})
    manifest: Manifest = {
        "schema_version": number(source.get("schema_version"), "schema version"),
        "artifact_role": text(source, "artifact_role"),
        "producer_source": text(source, "producer_source"),
        "producer_source_sha256": text(source, "producer_source_sha256"),
        "profiles": [text({"profile": item}, "profile") for item in
                     array_value(source.get("profiles"), "profiles")],
        "case_count": number(source.get("case_count"), "case count"),
        "cases": parsed}
    cases = manifest["cases"]
    keys = [(case["profile"], case["case"]) for case in cases]
    if (manifest["schema_version"] != 1 or manifest["artifact_role"] != "PRODUCTION_GENERATED" or
            manifest["producer_source_sha256"] !=
            sha256((WORKSPACE / manifest["producer_source"]).read_bytes()).hexdigest() or
            tuple(manifest["profiles"]) != PROFILES or manifest["case_count"] != 42 or
            set(keys) != {(profile, name) for profile in PROFILES
                          for name in PRODUCTION_CASE_NAMES} or len(keys) != 42 or
            any(case["framing"] != "planner-blocks" for case in cases)):
        raise ValueError("production corpus incomplete")
    return manifest


def estimate_case(library: Path, case: Case) -> CycleModel:
    request = {"profile": case["profile"], "timing_profile": "rtl-regression",
               "request": {"m": case["shape"][0], "n": case["shape"][1],
                           "k": case["shape"][2], "tile_i": case["tile"][0],
                           "tile_j": case["tile"][1], "tile_k": case["tile"][2],
                           "accepted_cycle": 1, "logical_work_id": 1,
                           "submission": case["framing"], "record_events": 1},
               "original_k": case["original_k"],
               "runs": [dict(zip(("original_block_id", "original_k_mask",
                                   "compact_k_begin", "compact_k_count"), run))
                        for run in case["runs"]], "timing": case["timing"]}
    answer = cli.estimate(library, request)
    return {"result": answer["result"], "events": answer["events"]}


def read_work_fixture(path: Path) -> WorkFixture:
    lines = path.read_text().splitlines()
    if not lines or lines[0] != "RMD_RUN_WORK_V1":
        raise ValueError("unsupported producer work fixture")
    position = 1

    def record(label: str, count: int) -> list[int]:
        nonlocal position
        if position >= len(lines):
            raise ValueError("truncated producer work fixture")
        tokens = lines[position].split()
        position += 1
        if tokens[0] != label or len(tokens) != count + 1:
            raise ValueError(f"malformed {label} record")
        return [int(value) for value in tokens[1:]]

    descriptor = record("descriptor", 23)
    geometry = record("geometry", 16)
    header = record("runs", 4)
    runs = [record("run", 4) for _ in range(header[3])]
    row_count = record("ROW_MAP", 1)[0]
    row_map: list[list[int]] = []
    for _ in range(row_count):
        if position >= len(lines):
            raise ValueError("truncated ROW_MAP")
        values = [int(value) for value in lines[position].split()]
        position += 1
        if len(values) != 2:
            raise ValueError("malformed ROW_MAP pair")
        row_map.append(values)
    arrays: dict[str, list[int]] = {}
    for label in ("A", "B", "CARRIERS", "OUTPUT"):
        length = record(label, 1)[0]
        if position >= len(lines):
            raise ValueError(f"truncated {label}")
        values = [int(value) for value in lines[position].split()]
        position += 1
        if len(values) != length:
            raise ValueError(f"wrong {label} length")
        arrays[label] = values
    if position != len(lines):
        raise ValueError("trailing producer work records")
    m, n, k = geometry[6:9]
    if descriptor[6:9] != [m, n, k] or any(value <= 0 for value in geometry[9:12]):
        raise ValueError("producer descriptor/geometry mismatch")
    if (row_count != m or len({tuple(pair) for pair in row_map}) != m or
            len(arrays["A"]) != m * k or len(arrays["B"]) != k * n or
            len(arrays["CARRIERS"]) != len(runs) * n or len(arrays["OUTPUT"]) != m * n or
            header[:2] != [1, 32] or sum(run[3] for run in runs) != k):
        raise ValueError("producer work geometry/count mismatch")
    return {"descriptor": descriptor, "geometry": geometry, "runs_header": header,
            "runs": runs, "row_map": row_map, "shape": [m, n, k],
            "tile": geometry[9:12], "original_k": header[2]}
