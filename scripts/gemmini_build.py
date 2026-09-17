#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
# noqa: SIZE_OK - one exhaustive stage orchestrator; splitting duplicates state

# ─── How to run ───
# 1. Install uv (if not installed):
#      curl -LsSf https://astral.sh/uv/install.sh | sh
# 2. Run directly (no venv, no pip install needed):
#      uv run scripts/gemmini_build.py --help
# 3. Or make executable and run:
#      chmod +x scripts/gemmini_build.py && ./scripts/gemmini_build.py --help
# ─────────────────

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum, unique
from pathlib import Path
from typing import Final, TypeAlias, assert_never

if __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.gemmini_tools import build_environment, collect_tool_lock
from scripts.im2p_paths import resolve_gemmini_work_root
from scripts.gemmini_hardware_contract import write_hardware_contract

ROOT: Final = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT: Final = Path(os.environ.get("IM2P_WORKSPACE_ROOT", ROOT.parent))
LLAMA_ROOT: Final = WORKSPACE_ROOT / "llama.cpp-gemmini"
GEMMINI_INCLUDE_ROOT: Final = WORKSPACE_ROOT / "RISC-V-DynDNN-gemmini-include"
DEFAULT_CATALOG: Final = ROOT / "config" / "gemmini_hp1_profiles.json"
RESOLVER: Final = ROOT / "scripts" / "gemmini_resolve_profile.py"
BOARD_RESOLVER: Final = ROOT / "scripts" / "gemmini_board.py"
VENDOR: Final = ROOT / "scripts" / "gemmini_vendor.py"
WORK_ROOT: Final = resolve_gemmini_work_root(ROOT)
MACOS_FIRTOOL_BIN: Final = (
    WORK_ROOT / "deps" / "firtool-1.62.0-macos-x64"
    / "org.chipsalliance" / "llvm-firtool" / "macos-x64" / "bin"
)
JsonValue: TypeAlias = (
    str | int | float | bool | None
    | Sequence["JsonValue"] | Mapping[str, "JsonValue"]
)
HARDWARE_STAGES: Final = frozenset(("synth", "route", "bitstream"))
RTL_SUFFIXES: Final = frozenset((".sv", ".svh", ".v", ".vh", ".vhd", ".vhdl"))


@unique
class FailureReason(StrEnum):
    VALIDATION = "VALIDATION"
    OUTPUT_EXISTS = "OUTPUT_EXISTS"
    IO = "IO"
    DEPENDENCY = "DEPENDENCY"
    TOOL_FAILURE = "TOOL_FAILURE"
    DEFERRED_PLATFORM = "DEFERRED_PLATFORM"
    BOARD_REQUIRED = "BOARD_REQUIRED"


@unique
class Scu(StrEnum):
    HP1_LEFT_SHIFT = "hp1-left-shift"


@unique
class Stage(StrEnum):
    PLAN = "plan"
    RTL = "rtl"
    TEST = "test"
    HOST_TEST = "host-test"
    EXPORT = "export"
    SYNTH = "synth"
    ROUTE = "route"
    BITSTREAM = "bitstream"


@unique
class Top(StrEnum):
    INTEGRATED = "integrated"


@dataclass(frozen=True, slots=True)
class BuildFailure(Exception):
    reason: FailureReason
    detail: str

    def __str__(self) -> str:
        return self.detail

    def to_document(self) -> Mapping[str, JsonValue]:
        return {"status": "FAIL", "reason": self.reason.value, "detail": self.detail}


@dataclass(frozen=True, slots=True)
class ProfileSelection:
    activation_bits: int
    weight_bits: int
    dim: int
    scu: Scu

    def __post_init__(self) -> None:
        if self.activation_bits not in (4, 8) or self.weight_bits not in (4, 8):
            raise BuildFailure(FailureReason.VALIDATION, "operand width outside 4/8")
        if self.activation_bits != self.weight_bits or self.dim not in (16, 32, 64):
            raise BuildFailure(FailureReason.VALIDATION, "unsupported profile geometry")

    @property
    def name(self) -> str:
        return f"a{self.activation_bits}w{self.weight_bits}-d{self.dim}-hp1"


@dataclass(frozen=True, slots=True)
class BuildRequest:
    stage: Stage
    selections: tuple[ProfileSelection, ...]
    catalog_path: Path
    memory_contract: Path | None
    memory_contract_dir: Path | None
    board: Path | None
    clock_mhz: float | None
    top: Top
    output: Path
    dry_run: bool


@dataclass(frozen=True, slots=True)
class BuildCase:
    selection: ProfileSelection
    resolved: Mapping[str, JsonValue]
    output: Path
    manifest: Path


@dataclass(frozen=True, slots=True)
class ResolvedBoard:
    part: str
    top: str
    clock_port: str
    clock_mhz: float
    xdc: tuple[Path, ...]


@dataclass(frozen=True, slots=True)
class Command:
    cwd: Path
    arguments: tuple[str, ...]

    def to_document(self) -> Mapping[str, JsonValue]:
        return {"cwd": str(self.cwd), "arguments": list(self.arguments)}


@dataclass(frozen=True, slots=True)
class CommandResult:
    command: Command
    returncode: int
    log: str | None
    reason: FailureReason | None

    def to_document(self) -> Mapping[str, JsonValue]:
        return {
            **self.command.to_document(),
            "returncode": self.returncode,
            "log": self.log,
            "reason": self.reason.value if self.reason is not None else None,
        }


def _selection_arguments(namespace: argparse.Namespace) -> tuple[ProfileSelection, ...]:
    has_matrix = namespace.matrix is not None or namespace.dims is not None
    has_single = any(value is not None for value in (namespace.a_bits, namespace.w_bits, namespace.dim))
    if has_matrix == has_single:
        raise BuildFailure(FailureReason.VALIDATION, "choose one complete single profile or matrix")
    if has_single:
        if None in (namespace.a_bits, namespace.w_bits, namespace.dim):
            raise BuildFailure(FailureReason.VALIDATION, "single profile is incomplete")
        return (ProfileSelection(namespace.a_bits, namespace.w_bits, namespace.dim, Scu(namespace.scu)),)
    if namespace.matrix is None or namespace.dims is None:
        raise BuildFailure(FailureReason.VALIDATION, "matrix and dims must be paired")
    width_pairs = tuple(namespace.matrix.split(","))
    try:
        dimensions = tuple(int(value) for value in namespace.dims.split(","))
    except ValueError as error:
        raise BuildFailure(FailureReason.VALIDATION, "matrix DIM is not an integer") from error
    if len(set(width_pairs)) != len(width_pairs) or len(set(dimensions)) != len(dimensions):
        raise BuildFailure(FailureReason.VALIDATION, "matrix contains duplicate entries")
    known_pairs = {"a4w4": 4, "a8w8": 8}
    if not width_pairs or not dimensions or any(pair not in known_pairs for pair in width_pairs):
        raise BuildFailure(FailureReason.VALIDATION, "unsupported matrix entry")
    return tuple(
        ProfileSelection(known_pairs[pair], known_pairs[pair], dim, Scu(namespace.scu))
        for pair in width_pairs
        for dim in dimensions
    )


def _parse_request(arguments: Sequence[str]) -> BuildRequest:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profiles", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--a-bits", type=int)
    parser.add_argument("--w-bits", type=int)
    parser.add_argument("--dim", type=int)
    parser.add_argument("--matrix")
    parser.add_argument("--dims")
    parser.add_argument("--scu", choices=tuple(Scu), required=True)
    contracts = parser.add_mutually_exclusive_group(required=True)
    contracts.add_argument("--memory-contract", type=Path)
    contracts.add_argument("--memory-contract-dir", type=Path)
    parser.add_argument("--board", type=Path)
    parser.add_argument("--clock-mhz", type=float)
    parser.add_argument("--top", choices=tuple(Top), default=Top.INTEGRATED)
    parser.add_argument("--stage", choices=tuple(Stage), required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    namespace = parser.parse_args(arguments)
    return BuildRequest(
        Stage(namespace.stage), _selection_arguments(namespace), namespace.profiles,
        namespace.memory_contract, namespace.memory_contract_dir, namespace.board,
        namespace.clock_mhz, Top(namespace.top), namespace.out, namespace.dry_run,
    )


def _validate_request(request: BuildRequest) -> None:
    if platform.system() == "Darwin" and request.stage.value in HARDWARE_STAGES:
        raise BuildFailure(
            FailureReason.DEFERRED_PLATFORM,
            "Vivado implementation requires Linux; export this build on macOS",
        )
    if os.path.lexists(request.output) and request.stage is not Stage.EXPORT:
        raise BuildFailure(FailureReason.OUTPUT_EXISTS, f"output exists: {request.output}")
    if request.stage is Stage.EXPORT and (
        os.path.lexists(request.output / "export")
        or os.path.lexists(request.output / "export.tar.gz")
    ):
        raise BuildFailure(FailureReason.OUTPUT_EXISTS, "export output already exists")
    if request.memory_contract is not None and len(request.selections) != 1:
        raise BuildFailure(FailureReason.VALIDATION, "matrix requires a memory contract directory")
    if request.clock_mhz is not None and request.clock_mhz <= 0:
        raise BuildFailure(FailureReason.VALIDATION, "clock MHz must be positive")
    if request.stage.value in HARDWARE_STAGES:
        if request.board is None or request.clock_mhz is None:
            raise BuildFailure(FailureReason.BOARD_REQUIRED, "board and clock are required")
        if not request.board.is_file():
            raise BuildFailure(FailureReason.BOARD_REQUIRED, f"board manifest missing: {request.board}")


def _resolve_document(
    selection: ProfileSelection, catalog: Path, contract: Path,
) -> Mapping[str, JsonValue]:
    if not RESOLVER.is_file():
        raise BuildFailure(FailureReason.DEPENDENCY, f"resolver missing: {RESOLVER}")
    with tempfile.TemporaryDirectory(prefix="im2p-gemmini-resolve-") as temporary:
        output = Path(temporary) / "resolved-profile.json"
        completed = subprocess.run(
            [
                sys.executable, str(RESOLVER), "--profiles", str(catalog),
                "--a-bits", str(selection.activation_bits),
                "--w-bits", str(selection.weight_bits), "--dim", str(selection.dim),
                "--scu", selection.scu.value, "--memory-contract", str(contract),
                "--out", str(output),
            ],
            cwd=ROOT, text=True, capture_output=True, check=False,
        )
        if completed.returncode != 0:
            raise BuildFailure(FailureReason.VALIDATION, completed.stderr.strip())
        try:
            document = json.loads(output.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise BuildFailure(FailureReason.IO, f"invalid resolver output: {error}") from error
    match document:  # noqa: MATCH_OK - subprocess JSON needs typed validation failure
        case dict():
            return document
        case _:
            raise BuildFailure(FailureReason.VALIDATION, "resolver output is not an object")


def _resolve_cases(request: BuildRequest) -> tuple[BuildCase, ...]:
    cases: list[BuildCase] = []
    matrix = len(request.selections) > 1
    for selection in request.selections:
        if request.memory_contract is not None:
            contract = request.memory_contract
        else:
            contract_dir = request.memory_contract_dir
            if contract_dir is None:
                raise BuildFailure(FailureReason.VALIDATION, "memory contract source missing")
            contract = contract_dir / f"{selection.name}.json"
        case_output = request.output / selection.name if matrix else request.output
        resolved = {
            **_resolve_document(selection, request.catalog_path, contract),
            **_top_metadata(request.top, selection),
        }
        cases.append(BuildCase(selection, resolved, case_output, case_output / "resolved-profile.json"))
    return tuple(cases)


def _top_metadata(top: Top, selection: ProfileSelection) -> Mapping[str, JsonValue]:
    match top:
        case Top.INTEGRATED:
            return {
                "controller_kind": "UPSTREAM_GEMMINI_WS",
                "backing_memory": "INTEGRATED",
                "cycle_scope": "logical_work_accept_to_final_backing_write_completion",
                "host_artifact_role": "HOST_COMMON_ORCHESTRATION",
                "host_audit_role": "PHYSICAL_HOST",
                "rmd_enabled": True,
                "rmd_datapath": "NORMAL_HP1_SCALED",
                "rmd_raw": False,
                "rmd_numerical_revision": "rmd-hp1-scu-sat32-radix-v1",
                "host_integer_block_multiply": False,
                "work_kinds": ["DENSE_HP1_FINAL"],
                "diagnostic_work_kinds": ["RMD_RAW"],
                "selected_top": (
                    f"IM2PGemminiWSHP1A{selection.activation_bits}"
                    f"W{selection.weight_bits}D{selection.dim}"
                ),
            }
        case unreachable:
            assert_never(unreachable)


def _resolved_board(request: BuildRequest) -> ResolvedBoard:
    if request.board is None or request.clock_mhz is None or not BOARD_RESOLVER.is_file():
        raise BuildFailure(FailureReason.BOARD_REQUIRED, "board resolver inputs missing")
    completed = subprocess.run(
        [sys.executable, str(BOARD_RESOLVER), "--manifest", str(request.board)],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )
    if completed.returncode != 0:
        raise BuildFailure(FailureReason.BOARD_REQUIRED, completed.stderr.strip())
    try:
        document = json.loads(completed.stdout)
        board = ResolvedBoard(
            str(document["part"]), str(document["top"]), str(document["clock_port"]),
            float(document["clock_mhz"]), tuple(Path(str(path)) for path in document["xdc"]),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise BuildFailure(FailureReason.BOARD_REQUIRED, f"invalid board resolver output: {error}") from error
    if abs(board.clock_mhz - request.clock_mhz) > 0.000_001:
        raise BuildFailure(FailureReason.VALIDATION, "clock MHz differs from board manifest")
    return board


def _rtl_commands(request: BuildRequest, case: BuildCase) -> tuple[Command, ...]:
    rtl_output = case.output / "rtl"
    memory = case.resolved.get("memory")
    scratchpad_bank_rows = memory.get("bank_rows") if isinstance(memory, dict) else None
    accumulator_rows = memory.get("accumulator_rows") if isinstance(memory, dict) else None
    if not isinstance(scratchpad_bank_rows, int) or isinstance(scratchpad_bank_rows, bool):
        raise BuildFailure(FailureReason.VALIDATION, "resolved scratchpad bank rows missing")
    if not isinstance(accumulator_rows, int) or isinstance(accumulator_rows, bool):
        raise BuildFailure(FailureReason.VALIDATION, "resolved accumulator rows missing")
    lint = Command(ROOT, (
        "verilator", "--lint-only", "--timing", "-Wall", "-Wno-fatal",
        "-F", str(case.output / "filelist.f"),
    ))
    overlay = case.output / "upstream-overlay"
    main = "runMain im2p.gemmini.ElaborateUpstreamWsHp1"
    options = f"--resolved-hardware {case.output / 'resolved-hardware.properties'} "
    return (
        Command(ROOT, (sys.executable, str(VENDOR), "--overlay", str(overlay))),
        Command(ROOT / "src" / "gemmini", (
            "sbt", "-J-Xmx6G", "--batch", f"-Dim2p.gemmini.overlay={overlay.resolve()}",
            f"{main} --a-bits {case.selection.activation_bits} "
            f"--w-bits {case.selection.weight_bits} --dim {case.selection.dim} "
            f"{options}--out {rtl_output}",
        )),
        lint,
    )


def _rtl_test_commands(request: BuildRequest, case: BuildCase) -> tuple[Command, ...]:
    memory = case.resolved.get("memory")
    if not isinstance(memory, dict):
        raise BuildFailure(FailureReason.VALIDATION, "resolved memory contract missing")
    bank_rows = memory.get("bank_rows")
    accumulator_rows = memory.get("accumulator_rows")
    if not isinstance(bank_rows, int) or isinstance(bank_rows, bool):
        raise BuildFailure(FailureReason.VALIDATION, "resolved scratchpad bank rows missing")
    if not isinstance(accumulator_rows, int) or isinstance(accumulator_rows, bool):
        raise BuildFailure(FailureReason.VALIDATION, "resolved accumulator rows missing")
    metadata = _top_metadata(request.top, case.selection)
    top = str(metadata["selected_top"])
    object_dir = case.output / "rtl-test-obj"
    host = ROOT / "fpga" / "gemmini_hp1" / "host"
    host_params = case.output / "host-params"
    flags = " ".join((
        "-std=c++20", "-Wall", "-Wextra", "-Wpedantic", "-fno-fast-math",
        "-DIM2P_RTL_TEST_BUILD=1",
        f"-DIM2P_DIM={case.selection.dim}",
        f"-DIM2P_OPERAND_BITS={case.selection.activation_bits}",
        f"-DIM2P_ACTIVATION_BITS={case.selection.activation_bits}",
        f"-DIM2P_BANK_ROWS={bank_rows}",
        f"-DIM2P_ACC_ROWS={accumulator_rows}",
        "-DIM2P_GEMMINI_EXTERNAL_EXECUTOR_ONLY=1",
        f"-DIM2P_GEMMINI_FRONTEND_ACTIVATION_BITS={case.selection.activation_bits}",
        f"-DIM2P_GEMMINI_FRONTEND_EXPECTED_DIM={case.selection.dim}",
        f"-DGGML_GEMMINI_CONFIGURED_DIM={case.selection.dim}",
        f"-DGGML_GEMMINI_ACTIVATION_BITS={case.selection.activation_bits}",
        f"-DGGML_GEMMINI_WEIGHT_BITS={case.selection.weight_bits}",
        "-DGGML_GEMMINI_ENABLE_RMD=1",
        "-DGGML_GEMMINI_EXECUTION_BACKEND_FPGA_UART=1",
        "-DIM2P_FPGA_ARCH_GEMMINI_HP1=1",
        f"-I{host_params}",
        f"-I{host}",
        f"-I{ROOT / 'frontend' / 'include'}",
        f"-I{ROOT / 'sim' / 'include'}",
        f"-I{ROOT / 'sim' / 'ffi'}",
        f"-I{LLAMA_ROOT / 'ggml' / 'src' / 'ggml-gemmini'}",
        f"-I{LLAMA_ROOT / 'ggml' / 'src' / 'ggml-gemmini-utils' / 'include'}",
        f"-I{LLAMA_ROOT / 'ggml' / 'include'}",
        f"-I{LLAMA_ROOT / 'ggml' / 'src'}",
        f"-I{LLAMA_ROOT / 'common'}",
        f"-I{GEMMINI_INCLUDE_ROOT}",
    ))
    prefix = "VIM2PGemminiWSHP1RtlTest"
    source = "test_ws_rtl.cpp"
    executable = object_dir / prefix
    sources = (str(host / "rmd_rtl_fixture.cpp"), str(host / "bound_rmd_rtl_fixture.cpp"),
               str(LLAMA_ROOT / "ggml/src/ggml-gemmini/residual/rmd/rmd-reference.cpp"))
    link_flags = ("-LDFLAGS", " ".join((
        str(case.output / "host-build" / "libgemmini_hp1_host_common.a"),
        str(case.output / "host-build" / "libgemmini_hp1_ggml_numeric.a"),
        "-Wl,-dead_strip" if platform.system() == "Darwin" else "-Wl,--gc-sections -pthread",
    )))
    return (
        Command(ROOT, (
            "verilator", "--cc", "--exe", "--build", "--assert", "--timing",
            "-Wall", "-Wno-fatal", "-j", "2", "--top-module", top,
            "--prefix", prefix, "--Mdir", str(object_dir),
            "-CFLAGS", flags, "-F", str(case.output / "filelist.f"),
            str(host / source), str(host / "frontend_rtl_fixture.cpp"),
            *sources, *link_flags,
        )),
        Command(ROOT, (str(executable),)),
    )


def _host_validation_commands(
    request: BuildRequest, case: BuildCase, rtl_commands: tuple[Command, ...],
) -> tuple[Command, ...]:
    source = ROOT / "fpga" / "gemmini_hp1" / "host"
    build = case.output / "host-build"
    return (
        *rtl_commands,
        Command(ROOT, (
            "cmake", "-S", str(source), "-B", str(build),
            f"-DIM2P_GEMMINI_RESOLVED_PROFILE={case.manifest}",
            f"-DIM2P_LLAMA_ROOT={LLAMA_ROOT}",
            f"-DIM2P_GEMMINI_INCLUDE_ROOT={GEMMINI_INCLUDE_ROOT}",
            "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON",
        )),
        Command(ROOT, ("cmake", "--build", str(build))),
        Command(ROOT, ("ctest", "--test-dir", str(build), "--output-on-failure")),
        Command(ROOT, (
            sys.executable, str(ROOT / "scripts" / "gemmini_audit_host.py"),
            "--artifact", str(build / "gemmini_hp1_host_orchestration"),
            "--role", "PHYSICAL_HOST",
            "--command-log", str(
                build / "CMakeFiles" / "gemmini_hp1_host_orchestration.dir" / "link.txt"
            ),
            "--out", str(case.output / "host-audit.json"),
        )),
        *_rtl_test_commands(request, case),
    )


def _commands(request: BuildRequest, case: BuildCase) -> tuple[Command, ...]:
    scala_root = ROOT / "src" / "gemmini"
    manifest = str(case.manifest)
    match request.stage:
        case Stage.PLAN:
            return ()
        case Stage.RTL:
            return _rtl_commands(request, case)
        case Stage.TEST:
            memory = case.resolved.get("memory")
            if not isinstance(memory, dict):
                raise BuildFailure(FailureReason.VALIDATION, "resolved memory contract missing")
            scratchpad_bank_rows = memory.get("bank_rows")
            accumulator_rows = memory.get("accumulator_rows")
            if not isinstance(scratchpad_bank_rows, int) or isinstance(scratchpad_bank_rows, bool):
                raise BuildFailure(FailureReason.VALIDATION, "resolved scratchpad bank rows missing")
            if not isinstance(accumulator_rows, int) or isinstance(accumulator_rows, bool):
                raise BuildFailure(FailureReason.VALIDATION, "resolved accumulator rows missing")
            overlay = case.output / "upstream-overlay"
            common = (
                "sbt", "-J-Xmx6G", f"-Dim2p.resolvedProfile={manifest}",
                f"-Dim2p.resolvedHardware={case.output / 'resolved-hardware.properties'}",
                f"-Dim2p.testProfile={case.selection.name}", "--batch",
                f"-Dim2p.scratchpadBankRows={scratchpad_bank_rows}",
                f"-Dim2p.accumulatorRows={accumulator_rows}",
                f"-Dim2p.gemmini.overlay={overlay.resolve()}",
            )
            target = "test"
            return (
                Command(ROOT, (sys.executable, str(VENDOR), "--overlay", str(overlay))),
                Command(scala_root, (*common, target)),
            )
        case Stage.HOST_TEST:
            return _host_validation_commands(request, case, _rtl_commands(request, case))
        case Stage.EXPORT:
            commands = _rtl_commands(request, case)
            selected_top = str(_top_metadata(request.top, case.selection)["selected_top"])
            existing_top = case.output / "rtl" / f"{selected_top}.sv"
            reusable = existing_top.is_file() and not existing_top.is_symlink()
            rtl_commands = commands[-1:] if reusable else commands
            return _host_validation_commands(request, case, rtl_commands)
        case Stage.SYNTH | Stage.ROUTE | Stage.BITSTREAM:
            board = _resolved_board(request)
            xdc_arguments = tuple(value for path in board.xdc for value in ("--xdc", str(path)))
            flow = Command(ROOT, (
                "vivado", "-mode", "batch", "-source",
                str(ROOT / "fpga" / "gemmini_hp1" / "flow" / "vivado_flow.tcl"),
                "-tclargs", "--stage", request.stage.value, "--part", board.part,
                "--top", board.top, "--clock-port", board.clock_port,
                "--clock-mhz", str(board.clock_mhz), *xdc_arguments,
                "--filelist", str(case.output / "filelist.f"),
                "--out", str(case.output / "vivado"),
            ))
            return (*_rtl_commands(request, case), flow)
        case unreachable:
            assert_never(unreachable)


def _export_command(request: BuildRequest) -> Command:
    return Command(ROOT, (
        sys.executable, str(ROOT / "scripts" / "gemmini_export.py"), "create",
        "--source-root", str(ROOT), "--build-root", str(request.output),
        "--out", str(request.output / "export"),
    ) + (() if request.board is None else ("--board", str(request.board))))


def _execute(command: Command, log_path: Path) -> CommandResult:
    executable = command.arguments[0]
    available = Path(executable).is_file() if "/" in executable else shutil.which(executable) is not None
    if not command.cwd.is_dir() or not available:
        return CommandResult(command, 127, None, FailureReason.DEPENDENCY)
    environment: Mapping[str, str] | None = None
    if executable == "sbt" and platform.system() == "Darwin":
        environment = build_environment(WORK_ROOT)
        if not (Path(environment["CHISEL_FIRTOOL_PATH"]) / "firtool").is_file():
            return CommandResult(command, 127, None, FailureReason.DEPENDENCY)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as log:
        completed = subprocess.run(
            command.arguments, cwd=command.cwd, text=True,
            stdout=log, stderr=subprocess.STDOUT, env=environment, check=False,
        )
    reason = None if completed.returncode == 0 else FailureReason.TOOL_FAILURE
    return CommandResult(command, completed.returncode, str(log_path), reason)


def _write_filelist(case: BuildCase) -> None:
    rtl_root = case.output / "rtl"
    sources = tuple(
        path for path in sorted(rtl_root.rglob("*"))
        if path.is_file() and not path.is_symlink() and path.suffix.lower() in RTL_SUFFIXES
    )
    if not sources:
        raise BuildFailure(FailureReason.TOOL_FAILURE, "elaboration emitted no RTL")
    (case.output / "filelist.f").write_text(
        "".join(f"{path.relative_to(case.output).as_posix()}\n" for path in sources),
        encoding="utf-8",
    )


def _write_host_params(case: BuildCase) -> None:
    source = GEMMINI_INCLUDE_ROOT / "gemmini_params.h"
    try:
        content = source.read_text(encoding="utf-8")
    except OSError as error:
        raise BuildFailure(FailureReason.DEPENDENCY, f"host parameter source missing: {source}") from error
    memory = case.resolved.get("memory")
    if not isinstance(memory, dict):
        raise BuildFailure(FailureReason.VALIDATION, "resolved memory contract missing")
    replacements = {
        "DIM": case.selection.dim,
        "BANK_NUM": memory.get("bank_count"),
        "BANK_ROWS": memory.get("bank_rows"),
        "ACC_ROWS": memory.get("accumulator_rows"),
    }
    for macro, value in replacements.items():
        if not isinstance(value, int) or isinstance(value, bool):
            raise BuildFailure(FailureReason.VALIDATION, f"resolved {macro} missing")
        content, count = re.subn(
            rf"(?m)^#define {macro} [^\n]+$", f"#define {macro} {value}", content,
        )
        if count != 1:
            raise BuildFailure(FailureReason.DEPENDENCY, f"host parameter macro missing: {macro}")
    target = case.output / "host-params" / "gemmini_params.h"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def _case_document(
    request: BuildRequest,
    case: BuildCase,
    commands: tuple[Command, ...],
    results: tuple[CommandResult, ...] | None = None,
) -> Mapping[str, JsonValue]:
    status = (
        "NOT_RUN" if results is None
        else "PASS" if all(result.returncode == 0 for result in results)
        else "FAIL"
    )
    return {
        **_top_metadata(request.top, case.selection),
        "profile": case.selection.name,
        "status": status,
        "reason": next(
            (
                result.reason.value
                for result in (() if results is None else results)
                if result.reason is not None
            ),
            None,
        ),
        "resolved_profile": str(case.manifest),
        "commands": [command.to_document() for command in commands],
        "command_results": [] if results is None else [result.to_document() for result in results],
    }


def _write_json(path: Path, document: Mapping[str, JsonValue]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except OSError as error:
        raise BuildFailure(FailureReason.IO, f"cannot write result: {path}: {error}") from error


def run(request: BuildRequest) -> Mapping[str, JsonValue]:
    _validate_request(request)
    cases = _resolve_cases(request)
    command_sets = tuple(_commands(request, case) for case in cases)
    if request.dry_run:
        return {
            "schema_version": 1, "stage": request.stage.value, "status": "DRY_RUN",
            "execution": "sequential",
            "profiles": [
                _case_document(request, case, commands)
                for case, commands in zip(cases, command_sets)
            ],
            "handoff": _export_command(request).to_document() if request.stage is Stage.EXPORT else None,
        }
    request.output.mkdir(parents=True, exist_ok=request.stage is Stage.EXPORT)
    tool_lock = None if request.stage is Stage.PLAN else collect_tool_lock(
        WORK_ROOT, build_environment(WORK_ROOT),
    )
    profile_documents: list[Mapping[str, JsonValue]] = []
    for case, commands in zip(cases, command_sets):
        _write_json(case.manifest, case.resolved)
        try:
            write_hardware_contract(case.resolved, case.output)
        except (OSError, ValueError) as error:
            raise BuildFailure(FailureReason.VALIDATION, str(error)) from error
        if request.stage in (Stage.HOST_TEST, Stage.EXPORT):
            _write_host_params(case)
        if tool_lock is not None and not (case.output / "tool-lock.json").exists():
            _write_json(case.output / "tool-lock.json", tool_lock)
        results: list[CommandResult] = []
        for index, command in enumerate(commands, start=1):
            try:
                if command.arguments[0] == "verilator":
                    _write_filelist(case)
                result = _execute(command, case.output / "logs" / f"{index:02d}.log")
            except BuildFailure as error:
                result = CommandResult(command, 1, None, error.reason)
            results.append(result)
            if result.returncode != 0:
                break
        profile_documents.append(_case_document(request, case, commands, tuple(results)))
    status = "PASS" if all(profile["status"] == "PASS" for profile in profile_documents) else "FAIL"
    if request.stage is Stage.EXPORT:
        _write_json(request.output / "stage-host-test.json", {
            "schema_version": 1, "stage": Stage.HOST_TEST.value, "status": status,
            "execution": "sequential", "profiles": profile_documents, "handoff": None,
        })
    handoff: Mapping[str, JsonValue] | None = None
    if request.stage is Stage.EXPORT and status == "PASS":
        command = _export_command(request)
        result = _execute(command, request.output / "export.log")
        handoff = result.to_document()
        if result.returncode != 0:
            status = "FAIL"
    document: Mapping[str, JsonValue] = {
        "schema_version": 1, "stage": request.stage.value, "status": status,
        "execution": "sequential", "profiles": profile_documents, "handoff": handoff,
    }
    _write_json(request.output / "result.json", document)
    _write_json(request.output / f"stage-{request.stage.value}.json", document)
    return document


def main(arguments: Sequence[str] | None = None) -> int:
    try:
        request = _parse_request(sys.argv[1:] if arguments is None else arguments)
        document = run(request)
    except BuildFailure as error:
        print(json.dumps(error.to_document(), sort_keys=True), file=sys.stderr)
        return 2
    if request.dry_run:
        print(json.dumps(document, indent=2, sort_keys=True))
    return 0 if document["status"] in ("PASS", "DRY_RUN") else 1


if __name__ == "__main__":
    raise SystemExit(main())
