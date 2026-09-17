"""Current Gemmini runtime/export verification helpers.

This library reads external validation results and audits host artifacts; it does
not package historical approval campaigns or perform physical device operations.
"""

from __future__ import annotations


import hashlib


import json


import re


import shutil


import subprocess


from collections.abc import Mapping


from pathlib import Path


from typing import TypedDict, cast


MAC_GATES = (
    "DATAPATH_NUMERICAL",
    "UPSTREAM_CONTROLLER_CLOSURE",
    "UPSTREAM_WS_HP1_RUNTIME_INTEGRATION",
    "BACKING_LOAD_STORE_INTEGRATION",
    "LARGE_HOST_TILE_EXECUTION",
    "WS_DOUBLE_BUFFER_OWNERSHIP",
    "WS_OVERLAP_TEST",
    "LOGICAL_MATMUL_CYCLE_BOUNDARY",
    "NO_SIM_HOST_ARTIFACT",
    "EXPORT_SELECTED_TOP",
    "LAYOUT_BASELINE_DIFFERENTIAL",
)


ORCHESTRATION_SYMBOLS = (
    "im2p::gemmini::execute(",
    "ggml_gemmini_fpga_execute(",
    "im2p::gemmini_hp1::encode_work_plan_v1(",
    "im2p::gemmini_hp1::decode_work_plan_v1(",
)


RUN_RE = re.compile(
    r"^WS RTL (?P<mode>FULL|PIPELINE) rows=(?P<rows>\d+) row_begin=(?P<row_begin>\d+) "
    + r"N=(?P<n>\d+) K=(?P<k>\d+) cycles=(?P<cycles>\d+) "
    + r"tile=(?P<tile_i>\d+)/(?P<tile_j>\d+)/(?P<tile_k>\d+) "
    + r"load/ex/store=(?P<loads>\d+)/(?P<executes>\d+)/(?P<stores>\d+) "
    + r"contexts=(?P<contexts>\d+) committed_rows=(?P<committed_rows>\d+) "
    + r"scale_reads=(?P<scale_reads>\d+) reordered_reads=(?P<reordered_reads>\d+)$",
    re.MULTILINE,
)


DUAL_LOOP_RE = re.compile(
    r"^WS_DUAL_LOOP slots=(?P<slot0>\d+),(?P<slot1>\d+) "
    + r"overlap_loop_issue=(?P<overlap_loop_issue>\d+) "
    + r"load_execute_overlap=(?P<load_execute_overlap>\d+) "
    + r"slot0_reuse_blocked=(?P<slot0_reuse_blocked>\d+) "
    + r"delayed_done_cycles=(?P<delayed_done_cycles>\d+) "
    + r"local_halves=(?P<half0>\d+),(?P<half1>\d+) "
    + r"outputs=(?P<output0>-?\d+),(?P<output1>-?\d+) "
    + r"accepted_load_execute_store=(?P<loads>\d+),(?P<executes>\d+),(?P<stores>\d+) "
    + r"accepted_load_rows_store_rows_contexts=(?P<load_rows>\d+),(?P<store_rows>\d+),(?P<contexts>\d+)$",
    re.MULTILINE,
)


class RunEvidence(TypedDict):
    mode: str
    rows: int
    row_begin: int
    n: int
    k: int
    cycles: int
    tile_i: int
    tile_j: int
    tile_k: int
    loads: int
    executes: int
    stores: int
    contexts: int
    committed_rows: int
    scale_reads: int
    reordered_reads: int


class DualLoopEvidence(TypedDict):
    slots: list[int]
    overlap_loop_issue: int
    load_execute_overlap: int
    slot0_reuse_blocked: int
    delayed_done_cycles: int
    local_halves: list[int]
    outputs: list[int]
    accepted_load_execute_store: list[int]
    accepted_load_rows: int
    accepted_store_rows: int
    accepted_contexts: int


class RuntimeEvidence(TypedDict):
    large_full: RunEvidence
    pipeline_stripes: list[RunEvidence]
    outer_k: RunEvidence
    latency_cycles: list[int]
    loops: int
    load_execute_overlap: int
    dual_loop: DualLoopEvidence


class RmdRuntimeEvidence(TypedDict):
    rtl_callbacks: int
    scaled_exact: int
    lanes: int
    high_carry: int
    compose_exact: int
    merge_exact: int
    negative_tests: int
    missing_reject: int
    duplicate_reject: int
    high_exponent_scu: int
    sparse_k: int
    odd_k: int
    stripes: int
    slots: list[int]


class RmdBoundEvidence(TypedDict):
    full_exact: int
    pipeline_exact: int
    dense_calls: int
    scu_calls: int
    stripes: int
    slots: list[int]
    rollback: int
    public_entry: int


class LayoutEvidence(TypedDict):
    status: str
    classification: str
    failing_check: str
    layouts_equal: bool
    current: dict[str, object]
    baseline: dict[str, object]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rmd_runtime_evidence(text: str, profile: str) -> RmdRuntimeEvidence:
    match = re.fullmatch(r"a(?P<bits>[48])w(?P=bits)-d(?P<dim>16|32|64)-hp1", profile)
    if match is None:
        raise ValueError(f"invalid RMD profile: {profile}")
    lines = [line for line in text.splitlines() if line.startswith("WS_RMD_SCU ")]
    fields = (
        "rtl_callbacks", "scaled_exact", "lanes", "high_carry", "compose_exact",
        "merge_exact", "negative_tests", "missing_reject", "duplicate_reject",
        "high_exponent_scu", "sparse_k", "odd_k", "stripes",
    )
    pattern = rf"WS_RMD_SCU A{match['bits']}W{match['bits']}D{match['dim']} "
    pattern += " ".join(rf"{field}=(?P<{field}>\d+)" for field in fields)
    pattern += r" slots=0,1,0"
    marker = re.fullmatch(pattern, lines[0]) if len(lines) == 1 else None
    if marker is None:
        raise ValueError(f"RMD runtime marker missing or malformed: {profile}")
    values = {name: int(value) for name, value in marker.groupdict().items()}
    required_flags = (
        "high_carry", "missing_reject", "duplicate_reject", "high_exponent_scu",
        "sparse_k", "odd_k",
    )
    if (
        values["rtl_callbacks"] < 1
        or values["scaled_exact"] < values["rtl_callbacks"]
        or values["lanes"] != (9 if match["bits"] == "4" else 5)
        or values["compose_exact"] < 1
        or values["merge_exact"] != values["compose_exact"]
        or values["negative_tests"] < 3
        or values["stripes"] < 3
        or any(values[field] != 1 for field in required_flags)
    ):
        raise ValueError(f"incomplete RMD runtime evidence: {profile}")
    return RmdRuntimeEvidence(**values, slots=[0, 1, 0])


def rmd_bound_runtime_evidence(text: str, profile: str) -> RmdBoundEvidence:
    match = re.fullmatch(r"a(?P<bits>[48])w(?P=bits)-d(?P<dim>16|32|64)-hp1", profile)
    if match is None:
        raise ValueError(f"invalid RMD profile: {profile}")
    lines = [line for line in text.splitlines() if line.startswith("WS_RMD_BOUND ")]
    pattern = rf"WS_RMD_BOUND bits={match['bits']} DIM={match['dim']} "
    pattern += (
        r"full_exact=(?P<full_exact>\d+) pipeline_exact=(?P<pipeline_exact>\d+) "
        + r"dense_calls=(?P<dense_calls>\d+) scu_calls=(?P<scu_calls>\d+) "
        + r"stripes=(?P<stripes>\d+) slots=0,1,0 rollback=(?P<rollback>\d+) "
        + r"public_entry=(?P<public_entry>\d+)"
    )
    marker = re.fullmatch(pattern, lines[0]) if len(lines) == 1 else None
    if marker is None:
        raise ValueError(f"RMD public dispatch marker missing or malformed: {profile}")
    values = {name: int(value) for name, value in marker.groupdict().items()}
    if (
        values["full_exact"] != 27 or values["pipeline_exact"] != 27
        or values["dense_calls"] < 4 or values["scu_calls"] < 1
        or values["stripes"] != 3 or values["rollback"] != 2
        or values["public_entry"] != 1
    ):
        raise ValueError(f"incomplete RMD public dispatch evidence: {profile}")
    return RmdBoundEvidence(**values, slots=[0, 1, 0])


def _run_row(match: re.Match[str]) -> RunEvidence:
    fields = match.groupdict()
    return RunEvidence(
        mode=fields.pop("mode"), **{name: int(value) for name, value in fields.items()}
    )


def runtime_evidence(text: str, profile: str) -> RuntimeEvidence:
    match = re.fullmatch(r"a(?P<bits>[48])w(?P=bits)-d(?P<dim>16|32|64)-hp1", profile)
    if match is None:
        raise ValueError(f"invalid profile: {profile}")
    runs = [_run_row(row) for row in RUN_RE.finditer(text)]
    large = [
        row
        for row in runs
        if row["mode"] == "FULL"
        and (row["rows"], row["row_begin"], row["n"], row["k"]) == (129, 0, 129, 96)
    ]
    stripes = [
        row
        for row in runs
        if row["mode"] == "PIPELINE" and row["n"] == 129 and row["k"] == 96
    ]
    outer = [
        row
        for row in runs
        if row["mode"] == "FULL"
        and (row["rows"], row["row_begin"], row["n"], row["k"]) == (1, 0, 1, 8256)
    ]
    small = [
        row
        for row in runs
        if row["mode"] == "FULL"
        and (row["rows"], row["row_begin"], row["n"], row["k"]) == (2, 0, 3, 64)
    ]
    expected_stripes = [(64, 0), (64, 64), (1, 128)]
    checked = [*large, *stripes, *outer]
    if (
        len(large) != 1
        or [(row["rows"], row["row_begin"]) for row in stripes] != expected_stripes
        or len(outer) != 1
        or len(small) < 2
        or small[1]["cycles"] <= small[0]["cycles"]
        or any(
            not row["cycles"]
            or not row["loads"]
            or not row["executes"]
            or not row["stores"]
            or not row["scale_reads"]
            for row in checked
        )
    ):
        raise ValueError(f"incomplete integrated runtime evidence: {profile}")
    final = re.search(
        rf"^integrated upstream WS HP1 RTL passed A{match['bits']}W{match['bits']}D{match['dim']} "
        + r"loops=(\d+) load_execute_overlap=(\d+)$",
        text,
        re.MULTILINE,
    )
    if final is None or int(final[1]) < 1 or int(final[2]) < 1:
        raise ValueError(f"integrated runtime marker missing: {profile}")
    dual_match = DUAL_LOOP_RE.search(text)
    if dual_match is None:
        raise ValueError(f"dual-loop runtime marker missing: {profile}")
    dual_values = {name: int(value) for name, value in dual_match.groupdict().items()}
    dual = DualLoopEvidence(
        slots=[dual_values["slot0"], dual_values["slot1"]],
        overlap_loop_issue=dual_values["overlap_loop_issue"],
        load_execute_overlap=dual_values["load_execute_overlap"],
        slot0_reuse_blocked=dual_values["slot0_reuse_blocked"],
        delayed_done_cycles=dual_values["delayed_done_cycles"],
        local_halves=[dual_values["half0"], dual_values["half1"]],
        outputs=[dual_values["output0"], dual_values["output1"]],
        accepted_load_execute_store=[
            dual_values["loads"],
            dual_values["executes"],
            dual_values["stores"],
        ],
        accepted_load_rows=dual_values["load_rows"],
        accepted_store_rows=dual_values["store_rows"],
        accepted_contexts=dual_values["contexts"],
    )
    if (
        dual["slots"] != [0, 1]
        or dual["local_halves"] != [0, 1]
        or dual["outputs"] != [6, -16]
        or dual["accepted_load_execute_store"] != [7, 5, 3]
        or dual["accepted_load_rows"] != 2 * (int(match["dim"]) + 1)
        or dual["accepted_store_rows"] != 2
        or dual["accepted_contexts"] != 2
        or dual["overlap_loop_issue"] < 1
        or dual["load_execute_overlap"] < 1
        or dual["slot0_reuse_blocked"] != 1
        or dual["delayed_done_cycles"] < 1
    ):
        raise ValueError(f"invalid dual-loop runtime evidence: {profile}")
    return RuntimeEvidence(
        large_full=large[0],
        pipeline_stripes=stripes,
        outer_k=outer[0],
        latency_cycles=[small[0]["cycles"], small[1]["cycles"]],
        loops=int(final[1]),
        load_execute_overlap=int(final[2]),
        dual_loop=dual,
    )


def _probe(path: Path) -> dict[str, int]:
    try:
        text = path.read_text(encoding="utf-8")
        value = cast(object, json.loads(text))
    except (UnicodeDecodeError, json.JSONDecodeError):
        completed = subprocess.run(
            (str(path.resolve()),),
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
        )
        if completed.returncode != 0:
            raise ValueError(f"layout probe failed: {path}")
        value = cast(object, json.loads(completed.stdout))
    if not isinstance(value, dict):
        raise TypeError(f"invalid layout probe: {path}")
    values = cast(dict[object, object], value)
    if any(
        not isinstance(key, str) or not isinstance(item, int) or isinstance(item, bool)
        for key, item in values.items()
    ):
        raise ValueError(f"invalid layout probe: {path}")
    result = {
        key: item
        for key, item in values.items()
        if isinstance(key, str) and isinstance(item, int)
    }
    required = {
        "sizeof",
        "native_weight_bytes",
        "col_stride_f_out",
        "stride_f_out",
        "tile_I",
    }
    if set(result) != required:
        raise ValueError(f"incomplete layout probe: {path}")
    return result


def layout_differential(
    current: Path, baseline: Path, current_log: Path, baseline_log: Path
) -> LayoutEvidence:
    check = "native_weight_bytes offset remains unchanged"
    current_layout, baseline_layout = _probe(current), _probe(baseline)
    if current_layout != baseline_layout or any(
        f"FAIL: {check}" not in path.read_text(encoding="utf-8")
        for path in (current_log, baseline_log)
    ):
        raise ValueError("layout regression is not a reproduced baseline failure")
    return LayoutEvidence(
        status="PASS",
        classification="BASELINE_EXISTING_FAIL",
        failing_check=check,
        layouts_equal=True,
        current={
            "path": str(current.resolve()),
            "sha256": sha256(current),
            "layout": current_layout,
            "test_log": str(current_log.resolve()),
            "test_log_sha256": sha256(current_log),
        },
        baseline={
            "path": str(baseline.resolve()),
            "sha256": sha256(baseline),
            "layout": baseline_layout,
            "test_log": str(baseline_log.resolve()),
            "test_log_sha256": sha256(baseline_log),
        },
    )


def integration_ready(gates: Mapping[str, Mapping[str, object]]) -> bool:
    return all(gates.get(name, {}).get("status") == "PASS" for name in MAC_GATES)


def orchestration_symbols(text: str) -> tuple[str, ...]:
    missing = tuple(symbol for symbol in ORCHESTRATION_SYMBOLS if symbol not in text)
    if missing:
        raise ValueError(
            f"physical host lacks orchestration symbols: {', '.join(missing)}"
        )
    return ORCHESTRATION_SYMBOLS


def host_artifact_evidence(
    build_root: Path, name: str, audit: dict[str, object]
) -> dict[str, object]:
    artifact = (
        build_root / name / "host-build/gemmini_hp1_host_orchestration"
    ).resolve()
    audit_artifact = audit.get("artifact")
    if (
        audit.get("status") != "PASS"
        or audit.get("role") != "PHYSICAL_HOST"
        or audit.get("simulator_markers") != []
        or audit.get("physical_operations") != 0
        or not isinstance(audit_artifact, str)
        or Path(audit_artifact).resolve() != artifact
        or not artifact.is_file()
        or artifact.is_symlink()
        or audit.get("artifact_sha256") != sha256(artifact)
    ):
        raise RuntimeError(f"physical host artifact identity failed: {name}")
    executable = shutil.which("nm")
    if executable is None:
        raise RuntimeError("nm required for physical host symbol audit")
    inspected = subprocess.run(
        (executable, "-a", "-C", str(artifact)),
        text=True,
        capture_output=True,
        check=False,
    )
    if inspected.returncode != 0:
        raise RuntimeError(f"physical host symbol audit failed: {name}")
    symbols = orchestration_symbols(inspected.stdout)
    return {
        "path": str(artifact),
        "sha256": sha256(artifact),
        "role": "PHYSICAL_HOST",
        "symbols": list(symbols),
        "nm_output_sha256": hashlib.sha256(inspected.stdout.encode()).hexdigest(),
    }
