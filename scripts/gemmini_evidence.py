#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import TypedDict, cast

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = Path(os.environ.get("IM2P_WORKSPACE_ROOT", ROOT.parent)).resolve()
PROFILES = tuple(
    f"a{bits}w{bits}-d{dim}-hp1" for bits in (4, 8) for dim in (16, 32, 64)
)
PLAN_SHA256 = "e2189267dfc63e9f773688292ce53eff3750e7cf1883bac612772d8dce89caa9"
StatusEntry = dict[str, str | None]
ProfileStatus = dict[str, StatusEntry]
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
    raw_exact: int
    lanes: int
    high_carry: int
    compose_exact: int
    merge_exact: int
    negative_tests: int
    missing_reject: int
    duplicate_reject: int
    overflow_reject: int
    sparse_k: int
    odd_k: int
    stripes: int
    slots: list[int]


class RmdBoundEvidence(TypedDict):
    full_exact: int
    pipeline_exact: int
    dense_calls: int
    raw_calls: int
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


class Arguments(argparse.Namespace):
    build_root: Path
    memory_probe: Path
    export_root: Path
    numerical_build: Path
    layout_current_probe: Path
    layout_baseline_probe: Path
    layout_current_test_log: Path
    layout_baseline_test_log: Path
    software_root: Path | None
    scala_log: Path | None
    failed_build: list[Path]
    out: Path

    def __init__(self) -> None:
        super().__init__()
        self.build_root = Path()
        self.memory_probe = Path()
        self.export_root = Path()
        self.numerical_build = Path()
        self.layout_current_probe = Path()
        self.layout_baseline_probe = Path()
        self.layout_current_test_log = Path()
        self.layout_baseline_test_log = Path()
        self.software_root = None
        self.scala_log = None
        self.failed_build = []
        self.out = Path()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rmd_runtime_evidence(text: str, profile: str) -> RmdRuntimeEvidence:
    match = re.fullmatch(r"a(?P<bits>[48])w(?P=bits)-d(?P<dim>16|32|64)-hp1", profile)
    if match is None:
        raise ValueError(f"invalid RMD profile: {profile}")
    lines = [line for line in text.splitlines() if line.startswith("WS_RMD ")]
    fields = (
        "rtl_callbacks", "raw_exact", "lanes", "high_carry", "compose_exact",
        "merge_exact", "negative_tests", "missing_reject", "duplicate_reject",
        "overflow_reject", "sparse_k", "odd_k", "stripes",
    )
    pattern = rf"WS_RMD A{match['bits']}W{match['bits']}D{match['dim']} "
    pattern += " ".join(rf"{field}=(?P<{field}>\d+)" for field in fields)
    pattern += r" slots=0,1,0"
    marker = re.fullmatch(pattern, lines[0]) if len(lines) == 1 else None
    if marker is None:
        raise ValueError(f"RMD runtime marker missing or malformed: {profile}")
    values = {name: int(value) for name, value in marker.groupdict().items()}
    required_flags = (
        "high_carry", "missing_reject", "duplicate_reject", "overflow_reject",
        "sparse_k", "odd_k",
    )
    if (
        values["rtl_callbacks"] < 1
        or values["raw_exact"] < values["rtl_callbacks"]
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
        + r"dense_calls=(?P<dense_calls>\d+) raw_calls=(?P<raw_calls>\d+) "
        + r"stripes=(?P<stripes>\d+) slots=0,1,0 rollback=(?P<rollback>\d+) "
        + r"public_entry=(?P<public_entry>\d+)"
    )
    marker = re.fullmatch(pattern, lines[0]) if len(lines) == 1 else None
    if marker is None:
        raise ValueError(f"RMD public dispatch marker missing or malformed: {profile}")
    values = {name: int(value) for name, value in marker.groupdict().items()}
    if (
        values["full_exact"] != 27 or values["pipeline_exact"] != 27
        or values["dense_calls"] < 4 or values["raw_calls"] < 1
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


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def git(repository: Path, *arguments: str) -> str:
    result = subprocess.run(
        ("git", "-C", str(repository), *arguments),
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(arguments)} failed: {repository}: {result.stderr.strip()}"
        )
    return result.stdout


def repository_state(repository: Path) -> dict[str, object]:
    return {
        "path": str(repository.resolve()),
        "head": git(repository, "rev-parse", "HEAD").strip(),
        "branch": git(repository, "branch", "--show-current").strip(),
        "remote": git(repository, "remote", "get-url", "origin").strip(),
        "status": git(repository, "status", "--short").splitlines(),
        "staged": git(repository, "diff", "--cached", "--name-status").splitlines(),
        "tracked_diff": git(repository, "diff", "--name-status").splitlines(),
        "untracked": git(
            repository, "ls-files", "--others", "--exclude-standard"
        ).splitlines(),
    }


def task_sources() -> list[Path]:
    selections = (
        ROOT / "src/gemmini",
        ROOT / "fpga/gemmini_hp1",
        ROOT / "config/gemmini_hp1_profiles.json",
        ROOT / "config/gemmini_host_memory_contracts",
        ROOT / "frontend/include/im2p_gemmini_frontend.hpp",
        ROOT / "frontend/src/im2p_gemmini_frontend.cpp",
        ROOT / "sim/include/im2p_sim.h",
    )
    files = [
        path
        for selected in selections
        if selected.exists()
        for path in ([selected] if selected.is_file() else selected.rglob("*"))
    ]
    files.extend((ROOT / "scripts").glob("gemmini_*"))
    files.extend((ROOT / "tests").glob("test_gemmini_*"))
    return sorted(
        {
            path
            for path in files
            if path.is_file()
            and not path.is_symlink()
            and not {"target", "__pycache__", ".bloop", ".bsp"}.intersection(path.parts)
        }
    )


def artifact_manifest(build_root: Path) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for profile in PROFILES:
        for path in sorted((build_root / profile / "rtl").rglob("*")):
            if path.is_file() and not path.is_symlink():
                result.append(
                    {
                        "profile": profile,
                        "path": path.relative_to(build_root).as_posix(),
                        "bytes": path.stat().st_size,
                        "sha256": sha256(path),
                    }
                )
    return result


def first_failure(build: Path) -> dict[str, object] | None:
    result_path = build / "result.json"
    if not result_path.is_file():
        return None
    result = read_object(result_path)
    for profile in named_rows(result).values():
        for command in command_rows(profile, "command_results"):
            if command.get("returncode") != 0:
                log_value = command.get("log")
                log = Path(log_value) if isinstance(log_value, str) else None
                return {
                    "build": str(build.resolve()),
                    "profile": profile.get("profile"),
                    "command": command,
                    "first_failure_log": log.read_text(encoding="utf-8")
                    if log and log.is_file()
                    else None,
                }
    return None


def rows(document: dict[str, object], key: str) -> list[dict[str, object]]:
    value = document.get(key)
    if not isinstance(value, list):
        raise TypeError(f"{key} is not an object array")
    values = cast(list[object], value)
    if not all(isinstance(row, dict) for row in values):
        raise RuntimeError(f"{key} is not an object array")
    return [cast(dict[str, object], row) for row in values if isinstance(row, dict)]


def named_rows(document: dict[str, object]) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for row in rows(document, "profiles"):
        name = row.get("profile")
        if not isinstance(name, str):
            raise TypeError("profile name missing")
        if name in result:
            raise RuntimeError(f"duplicate profile: {name}")
        result[name] = row
    return result


def read_object(path: Path) -> dict[str, object]:
    value = cast(object, json.loads(path.read_text(encoding="utf-8")))
    if not isinstance(value, dict):
        raise TypeError(f"JSON object required: {path}")
    return cast(dict[str, object], value)


def expected_identity(profile: str) -> dict[str, str]:
    match = re.fullmatch(r"a(?P<bits>[48])w(?P=bits)-d(?P<dim>16|32|64)-hp1", profile)
    if match is None:
        raise RuntimeError(f"invalid profile: {profile}")
    return {
        "selected_top": f"IM2PGemminiWSHP1A{match['bits']}W{match['bits']}D{match['dim']}",
        "controller_kind": "UPSTREAM_GEMMINI_WS",
        "backing_memory": "INTEGRATED",
        "cycle_scope": "logical_work_accept_to_final_backing_write_completion",
        "host_artifact_role": "HOST_COMMON_ORCHESTRATION",
        "host_audit_role": "PHYSICAL_HOST",
    }


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


def command_rows(row: dict[str, object], key: str) -> list[dict[str, object]]:
    value = row.get(key)
    if not isinstance(value, list):
        raise TypeError(f"{key} missing for {row.get('profile')}")
    values = cast(list[object], value)
    if not all(isinstance(item, dict) for item in values):
        raise RuntimeError(f"{key} missing for {row.get('profile')}")
    return [cast(dict[str, object], item) for item in values if isinstance(item, dict)]


def validate_profile(build_root: Path, row: dict[str, object]) -> dict[str, object]:
    name = row.get("profile")
    if not isinstance(name, str):
        raise TypeError("profile name missing")
    expected = expected_identity(name)
    manifest = read_object(build_root / name / "resolved-profile.json")
    if row.get("status") != "PASS" or any(
        row.get(key) != value or manifest.get(key) != value
        for key, value in expected.items()
    ):
        raise RuntimeError(f"integrated identity mismatch: {name}")
    planned, executed = (
        command_rows(row, "commands"),
        command_rows(row, "command_results"),
    )
    if len(planned) != len(executed) or any(
        planned_item.get("arguments") != result.get("arguments")
        or result.get("returncode") != 0
        for planned_item, result in zip(planned, executed)
    ):
        raise RuntimeError(f"host-test command evidence incomplete: {name}")
    rtl_builds: list[list[object]] = []
    audit_commands: list[list[str]] = []
    runtime_rows: list[dict[str, object]] = []
    for result in executed:
        arguments = result.get("arguments")
        values = cast(list[object], arguments) if isinstance(arguments, list) else []
        if (
            values
            and values[0] == "verilator"
            and any(Path(str(value)).name == "test_ws_rtl.cpp" for value in values)
        ):
            rtl_builds.append(values)
        if any(Path(str(value)).name == "gemmini_audit_host.py" for value in values):
            audit_commands.append([str(value) for value in values])
        if values and Path(str(values[0])).name == "VIM2PGemminiWSHP1RtlTest":
            runtime_rows.append(result)
    top = expected["selected_top"]
    top_index = (
        rtl_builds[0].index("--top-module")
        if len(rtl_builds) == 1 and "--top-module" in rtl_builds[0]
        else -1
    )
    if (
        len(rtl_builds) != 1
        or top_index < 0
        or len(rtl_builds[0]) <= top_index + 1
        or rtl_builds[0][top_index + 1] != top
    ):
        raise RuntimeError(f"integrated runtime build missing: {name}")
    if len(runtime_rows) != 1 or not isinstance(runtime_rows[0].get("log"), str):
        raise RuntimeError(f"integrated runtime log missing: {name}")
    if len(audit_commands) != 1:
        raise RuntimeError(f"physical host audit command missing: {name}")
    audit_command = audit_commands[0]
    role_index = audit_command.index("--role") if "--role" in audit_command else -1
    artifact_index = (
        audit_command.index("--artifact") if "--artifact" in audit_command else -1
    )
    if (
        role_index < 0
        or len(audit_command) <= role_index + 1
        or audit_command[role_index + 1] != "PHYSICAL_HOST"
        or artifact_index < 0
        or len(audit_command) <= artifact_index + 1
        or Path(audit_command[artifact_index + 1]).name
        != "gemmini_hp1_host_orchestration"
    ):
        raise RuntimeError(f"physical host audit command invalid: {name}")
    runtime_log = Path(str(runtime_rows[0]["log"])).resolve()
    if (
        not runtime_log.is_relative_to(build_root.resolve())
        or not runtime_log.is_file()
    ):
        raise RuntimeError(f"runtime log outside build: {name}")
    content = runtime_log.read_text(encoding="utf-8")
    runtime = runtime_evidence(content, name)
    rmd_raw = manifest.get("rmd_raw", False)
    if type(rmd_raw) is not bool:
        raise RuntimeError(f"RMD capability must be boolean: {name}")
    if rmd_raw and (
        manifest.get("rmd_numerical_revision") != "rmd-raw-k32-cpu-compose-v1"
        or manifest.get("work_kinds") != ["DENSE_HP1_FINAL", "RMD_RAW"]
        or any(row.get(field) != manifest.get(field) for field in (
            "rmd_raw", "rmd_numerical_revision", "work_kinds",
        ))
    ):
        raise RuntimeError(f"RMD resolved contract mismatch: {name}")
    rmd = rmd_runtime_evidence(content, name) if rmd_raw else None
    rmd_bound = rmd_bound_runtime_evidence(content, name) if rmd_raw else None
    if rmd_raw and not {
        "rmd_rtl_fixture.cpp", "bound_rmd_rtl_fixture.cpp",
    }.issubset({Path(str(value)).name for value in rtl_builds[0]}):
        raise RuntimeError(f"RMD runtime compilation closure missing: {name}")
    audit = read_object(build_root / name / "host-audit.json")
    host_artifact = host_artifact_evidence(build_root, name, audit)
    required = (
        "ExecuteController.sv",
        "LoadController.sv",
        "LoopMatmul.sv",
        "ReservationStation.sv",
        "StoreController.sv",
        "MeshWithDelays.sv",
        "SCU.sv",
        "UpstreamWsControl.sv",
        "UpstreamWsMemory.sv",
        f"{expected['selected_top']}.sv",
    )
    rtl = build_root / name / "rtl"
    missing = [source for source in required if not (rtl / source).is_file()]
    if missing or len(tuple(rtl.glob("MeshWithDelays.sv"))) != 1:
        raise RuntimeError(f"integrated RTL closure incomplete: {name}: {missing}")
    return {
        **expected,
        "runtime_log": str(runtime_log),
        "runtime_log_sha256": sha256(runtime_log),
        "runtime": runtime,
        "rmd_raw": rmd_raw,
        "rmd_status": "PASS" if rmd is not None else "NOT_RUN",
        "rmd": rmd,
        "rmd_bound": rmd_bound,
        "host_audit": audit,
        "host_artifact": host_artifact,
        "rtl": [
            {"path": source, "sha256": sha256(rtl / source)} for source in required
        ],
    }


def validate_export(export_root: Path) -> dict[str, dict[str, object]]:
    result = read_object(export_root / "result.json")
    manifest = read_object(export_root / "profile-manifest.json")
    result_rows, manifest_rows = named_rows(result), named_rows(manifest)
    if (
        result.get("export") != "PASS"
        or result.get("export_kind") != "INTEGRATED"
        or set(result_rows) != set(PROFILES)
        or result_rows != manifest_rows
    ):
        raise RuntimeError("integrated export result invalid")
    for name, row in result_rows.items():
        expected = expected_identity(name)
        if (
            any(row.get(key) != value for key, value in expected.items())
            or row.get("status") != "PASS"
            or row.get("runtime_status") != "PASS"
            or row.get("no_sim_host_artifact") != "PASS"
        ):
            raise RuntimeError(f"integrated export metadata invalid: {name}")
        root_value = row.get("root")
        if not isinstance(root_value, str):
            raise TypeError(f"integrated export root missing: {name}")
        profile_root = (export_root / root_value).resolve()
        if not profile_root.is_relative_to(export_root.resolve()):
            raise RuntimeError(f"integrated export root unsafe: {name}")
        exported = read_object(profile_root / "resolved-profile.json")
        if any(exported.get(key) != value for key, value in expected.items()):
            raise RuntimeError(f"exported profile identity mismatch: {name}")
        top = expected["selected_top"]
        if (
            not (profile_root / "rtl" / f"{top}.sv").is_file()
            or f"rtl/{top}.sv"
            not in (profile_root / "filelist.f")
            .read_text(encoding="utf-8")
            .splitlines()
        ):
            raise RuntimeError(f"export selected top missing: {name}")
    return result_rows


def passing_matrix(root: Path) -> dict[str, dict[str, object]]:
    document = read_object(root / "result.json")
    profiles = named_rows(document)
    if (
        document.get("status") != "PASS"
        or set(profiles) != set(PROFILES)
        or any(row.get("status") != "PASS" for row in profiles.values())
    ):
        raise RuntimeError(f"six-profile numerical PASS required: {root}")
    return profiles


def verify_relocation(export_root: Path) -> dict[str, object]:
    tool = ROOT / "scripts/gemmini_export.py"
    archive = export_root.with_name(export_root.name + ".tar.gz")
    commands = ((sys.executable, str(tool), "verify", str(export_root)),)
    with tempfile.TemporaryDirectory(
        prefix="gemmini-evidence-relocation-"
    ) as directory:
        extract = (
            sys.executable,
            str(tool),
            "extract",
            str(archive),
            "--out",
            str(Path(directory) / "package"),
        )
        results = [
            subprocess.run(command, text=True, capture_output=True, check=False)
            for command in (*commands, extract)
        ]
    if not archive.is_file() or any(result.returncode != 0 for result in results):
        raise RuntimeError("export hash or relocation verification failed")
    return {
        "path": str(archive.resolve()),
        "bytes": archive.stat().st_size,
        "sha256": sha256(archive),
        "relocation_verified": True,
    }


def status_table(
    runtime: dict[str, dict[str, object]],
    probe: dict[str, object],
    exported: dict[str, dict[str, object]],
) -> dict[str, ProfileStatus]:
    probe_names = set(named_rows(probe))
    return {
        name: {
            "elaboration": {"status": "PASS"},
            "rtl_numerical": {"status": "PASS"},
            "host_contract": {"status": "PASS"},
            "host_rtl_integration": {"status": "PASS"},
            "no_sim_host_artifact": {"status": "PASS", "reason": None},
            "memory_contract": {"status": "PASS" if name in probe_names else "FAIL"},
            "export": {"status": str(exported[name]["status"])},
            "route": {"status": "NOT_RUN", "reason": "PLATFORM"},
            "bitstream": {"status": "NOT_RUN", "reason": "PLATFORM_BOARD_REQUIRED"},
            "physical": {"status": "NOT_RUN", "reason": "OUT_OF_SCOPE"},
            "rmd": {
                "status": str(runtime[name].get("rmd_status", "NOT_RUN")),
                "reason": None if runtime[name].get("rmd_status") == "PASS"
                else "UNSUPPORTED_BEFORE_DISPATCH",
            },
        }
        for name in runtime
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    _ = parser.add_argument("--build-root", type=Path, required=True)
    _ = parser.add_argument("--memory-probe", type=Path, required=True)
    _ = parser.add_argument("--export-root", type=Path, required=True)
    _ = parser.add_argument("--numerical-build", type=Path, required=True)
    _ = parser.add_argument("--layout-current-probe", type=Path, required=True)
    _ = parser.add_argument("--layout-baseline-probe", type=Path, required=True)
    _ = parser.add_argument("--layout-current-test-log", type=Path, required=True)
    _ = parser.add_argument("--layout-baseline-test-log", type=Path, required=True)
    _ = parser.add_argument("--software-root", type=Path)
    _ = parser.add_argument("--scala-log", type=Path)
    _ = parser.add_argument("--failed-build", type=Path, action="append", default=[])
    _ = parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(namespace=Arguments())
    if os.path.lexists(args.out):
        raise SystemExit(f"new evidence directory required: {args.out}")
    build_path = args.build_root / "stage-host-test.json"
    if not build_path.is_file():
        build_path = args.build_root / "result.json"
    build = read_object(build_path)
    build_profiles = named_rows(build)
    if (
        build.get("stage") != "host-test"
        or build.get("status") != "PASS"
        or build.get("execution") != "sequential"
        or set(build_profiles) != set(PROFILES)
    ):
        raise SystemExit("passing sequential six-profile integrated host-test required")
    runtime = {
        name: validate_profile(args.build_root, build_profiles[name])
        for name in PROFILES
    }
    probe = read_object(args.memory_probe)
    probe_profiles = named_rows(probe)
    if (
        probe.get("status") != "PASS"
        or set(probe_profiles) != set(PROFILES)
        or any(
            row.get("shape") != {"m": 129, "n": 129, "k": 96}
            for row in probe_profiles.values()
        )
    ):
        raise SystemExit("exact six-profile M129/N129/K96 memory probe required")
    _ = passing_matrix(args.numerical_build)
    exported = validate_export(args.export_root)
    if any(
        runtime[name]["rmd_raw"] != (exported[name].get("rmd_raw") is True)
        or runtime[name]["rmd_status"] == "PASS"
        and exported[name].get("rmd_runtime_status") != "PASS"
        for name in PROFILES
    ):
        raise RuntimeError("export RMD capability does not match executed profile")
    relocation = verify_relocation(args.export_root)
    layout = layout_differential(
        args.layout_current_probe,
        args.layout_baseline_probe,
        args.layout_current_test_log,
        args.layout_baseline_test_log,
    )
    gates: dict[str, dict[str, object]] = {
        "DATAPATH_NUMERICAL": {"status": "PASS", "evidence": "stage-test.json"},
        "UPSTREAM_CONTROLLER_CLOSURE": {
            "status": "PASS",
            "evidence": "architecture-result.json",
        },
        "UPSTREAM_WS_HP1_RUNTIME_INTEGRATION": {
            "status": "PASS",
            "evidence": "runtime-evidence.json",
        },
        "BACKING_LOAD_STORE_INTEGRATION": {
            "status": "PASS",
            "evidence": "runtime-evidence.json#profiles.*.runtime.dual_loop.accepted_load_execute_store",
        },
        "LARGE_HOST_TILE_EXECUTION": {"status": "PASS", "shape": "M129/N129/K96"},
        "WS_DOUBLE_BUFFER_OWNERSHIP": {
            "status": "PASS",
            "pipeline_stripes": 3,
            "slots": "0,1,0",
            "local_halves": "0,1",
            "slot0_reuse_blocked": 1,
        },
        "WS_OVERLAP_TEST": {
            "status": "PASS",
            "evidence": "dual-loop issue and load/execute overlaps >0 per profile",
        },
        "LOGICAL_MATMUL_CYCLE_BOUNDARY": {
            "status": "PASS",
            "evidence": "runtime-evidence.json#profiles.*.runtime.dual_loop.delayed_done_cycles",
        },
        "NO_SIM_HOST_ARTIFACT": {
            "status": "PASS",
            "role": "PHYSICAL_HOST",
            "artifact": "gemmini_hp1_host_orchestration",
            "evidence": "runtime-evidence.json#profiles.*.host_artifact",
        },
        "EXPORT_SELECTED_TOP": {"status": "PASS", "evidence": "export-result.json"},
        "LAYOUT_BASELINE_DIFFERENTIAL": {
            "status": "PASS",
            "classification": "BASELINE_EXISTING_FAIL",
        },
        "LINUX_PRODUCTION_HOST": {"status": "NOT_RUN", "reason": "PLATFORM"},
        "VIVADO_ROUTE": {"status": "NOT_RUN", "reason": "PLATFORM_BOARD_REQUIRED"},
        "BITSTREAM": {"status": "NOT_RUN", "reason": "PLATFORM_BOARD_REQUIRED"},
        "PHYSICAL": {"status": "NOT_RUN", "reason": "OUT_OF_SCOPE"},
        "RMD_HARDWARE": {
            "status": "PASS" if all(row["rmd_status"] == "PASS" for row in runtime.values()) else "NOT_RUN",
            "scope": "GENERATED_RTL_RAW_DOT_CPU_COMPOSE_MERGE",
            "evidence": "runtime-evidence.json#profiles.*.rmd",
        },
    }
    ready = integration_ready(gates)

    args.out.mkdir(parents=True)
    repositories = {
        "im2p_sim": repository_state(ROOT),
        "llama_cpp_gemmini": repository_state(WORKSPACE_ROOT / "llama.cpp-gemmini"),
        "gemmini_include": repository_state(
            WORKSPACE_ROOT / "RISC-V-DynDNN-gemmini-include"
        ),
    }
    write_json(args.out / "baseline.json", repositories)
    agents = [
        path
        for path in (
            WORKSPACE_ROOT / "AGENTS.md",
            WORKSPACE_ROOT / "RISC-V-DynDNN-gemmini-include/AGENTS.md",
        )
        if path.is_file()
    ]
    write_json(
        args.out / "instruction-hashes.json",
        {
            "agents": [{"path": str(path), "sha256": sha256(path)} for path in agents],
            "plan": {
                "filename": "IM2P_Gemmini_HP1_Chipyard_1_13_0_Implementation_Plan_Mac.md",
                "availability": "NOT_AVAILABLE",
                "expected_sha256_from_user": PLAN_SHA256,
                "recomputed_sha256": None,
                "reason": "exact attachment bytes not present in workspace",
            },
        },
    )
    source_rows = [
        {
            "path": path.relative_to(ROOT).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
        }
        for path in task_sources()
    ]
    write_json(args.out / "task-source-manifest.json", {"files": source_rows})
    write_json(
        args.out / "rtl-artifact-manifest.json",
        {
            "files": artifact_manifest(args.build_root),
        },
    )
    _ = shutil.copy2(build_path, args.out / "stage-host-test.json")
    _ = shutil.copy2(args.memory_probe, args.out / "memory-probe.json")
    _ = shutil.copy2(
        args.build_root / PROFILES[0] / "tool-lock.json", args.out / "tool-lock.json"
    )
    _ = shutil.copy2(
        ROOT / "src/gemmini/UPSTREAM.lock.json", args.out / "upstream-lock.json"
    )
    _ = shutil.copy2(
        ROOT / "src/gemmini/vendor-manifest.json", args.out / "vendor-manifest.json"
    )
    _ = shutil.copy2(args.export_root / "result.json", args.out / "export-result.json")
    write_json(
        args.out / "architecture-result.json", {"status": "PASS", "profiles": runtime}
    )
    write_json(
        args.out / "runtime-evidence.json", {"status": "PASS", "profiles": runtime}
    )
    write_json(args.out / "layout-baseline-differential.json", layout)
    write_json(args.out / "gate-table.json", gates)
    _ = shutil.copy2(args.numerical_build / "result.json", args.out / "stage-test.json")
    if args.software_root is not None:
        _ = shutil.copytree(args.software_root, args.out / "software-tests")
    if args.scala_log is not None:
        _ = shutil.copy2(args.scala_log, args.out / "scala-full-test.log")
    write_json(args.out / "export-artifact.json", relocation)
    (args.out / "resolved-profiles").mkdir()
    (args.out / "host-audits").mkdir()
    for profile in PROFILES:
        _ = shutil.copytree(
            args.build_root / profile / "logs", args.out / "logs" / profile
        )
        _ = shutil.copy2(
            args.build_root / profile / "resolved-profile.json",
            args.out / "resolved-profiles" / f"{profile}.json",
        )
        _ = shutil.copy2(
            args.build_root / profile / "host-audit.json",
            args.out / "host-audits" / f"{profile}.json",
        )
    failures = [
        failure for path in args.failed_build if (failure := first_failure(path))
    ]
    write_json(args.out / "first-failures.json", failures)
    _ = (args.out / "im2p-final.diff").write_text(git(ROOT, "diff"), encoding="utf-8")
    llama = WORKSPACE_ROOT / "llama.cpp-gemmini"
    _ = (args.out / "llama-final.diff").write_text(git(llama, "diff"), encoding="utf-8")
    profiles = status_table(runtime, probe, exported)
    marker = "UPSTREAM_WS_HP1_6PROFILE_INTEGRATION_PASS"
    if ready:
        _ = (args.out / marker).write_text(marker + "\n", encoding="utf-8")
    final = {
        "schema_version": 2,
        "plan_revision": "mac-v2",
        "scope": "HP1_SHIFT_ONLY_A4W4_A8W8_D16_D32_D64",
        "profiles": profiles,
        "gate_table": gates,
        "integration_marker": marker if ready else None,
        "layout_regression": layout,
        "linux_vivado": {"status": "NOT_RUN", "reason": "PLATFORM"},
        "rmd_on_ready": gates["RMD_HARDWARE"]["status"] == "PASS",
        "hardware_access": {"uart": 0, "jtag": 0, "flash": 0},
        "git_commit_push": "NOT_RUN",
        "mac_implementation_ready": ready,
    }
    write_json(args.out / "final.json", final)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
