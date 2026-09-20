#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
# noqa: SIZE_OK - one archive create/verify/extract lifecycle
#
# ─── How to run ───
# 1. Install uv (if not installed):
#      curl -LsSf https://astral.sh/uv/install.sh | sh
# 2. Run directly (no venv, no pip install needed):
#      uv run scripts/gemmini_export.py create --source-root . --build-root BUILD --out NEW_DIRECTORY
# 3. Or make executable and run:
#      chmod +x scripts/gemmini_export.py && ./scripts/gemmini_export.py --help
# ─────────────────

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Final, TypeAlias, assert_never

JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]

ROOT = Path(__file__).resolve().parents[1]
if __name__ == "__main__":
    sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT))
from scripts.gemmini_board import BoardError, load_board_manifest
from scripts.gemmini_evidence import rmd_bound_runtime_evidence, rmd_runtime_evidence
from scripts.gemmini_replay_contract import HARDWARE_PATHS
from scripts.im2p_paths import resolve_gemmini_work_root

SOURCE_SUFFIXES: Final = frozenset({
    ".bsv", ".c", ".cc", ".cmake", ".cpp", ".h", ".hpp", ".json", ".md", ".patch", ".properties",
    ".py", ".sbt", ".scala", ".sh", ".sv", ".svh", ".tcl", ".txt", ".v", ".vh", ".xdc",
})
RTL_SUFFIXES: Final = frozenset({".hex", ".mem", ".sv", ".svh", ".v", ".vh", ".vhd", ".vhdl"})
BUILD_METADATA: Final = frozenset({
    "filelist.f", "host-contract.json", "resolved-profile.json", "source-manifest.json",
    "tool-lock.json", "upstream-lock.json", "result.json", "host-audit.json", "final.json",
    "memory-probe.json", "dependency-lock.json", "profile-manifest.json",
})
EXCLUDED_PARTS: Final = frozenset({
    ".bloop", ".bsp", ".cache", ".git", ".metals", ".omo", ".omx", ".vscode", ".zed", "Testing",
    "__pycache__", "build", "cache", "export", "graphify-out", "models", "target",
    "test_run_dir",
})
FORBIDDEN_SUFFIXES: Final = frozenset({
    ".a", ".bit", ".dcp", ".dylib", ".elf", ".gguf", ".o", ".onnx", ".pdi",
    ".pt", ".pth", ".so", ".safetensors", ".xclbin",
})
MACHO_MAGICS: Final = frozenset({bytes.fromhex(value) for value in
                                 ("feedface", "cefaedfe", "feedfacf", "cffaedfe", "cafebabe", "bebafeca",
                                  "cafebabf", "bfbafeca")})
PROFILE_FIELDS: Final = (
    "selected_top", "controller_kind", "backing_memory", "cycle_scope",
)
INTEGRATED_PROFILE_FIELDS: Final = ("host_artifact_role", "host_audit_role")
RMD_PROFILE_FIELDS: Final = (
    "rmd_enabled", "rmd_datapath", "rmd_raw", "rmd_numerical_revision",
    "host_integer_block_multiply", "work_kinds", "diagnostic_work_kinds",
)
RMD_DATAPATH: Final = "NORMAL_HP1_SCALED"
RMD_NUMERICAL_REVISION: Final = "rmd-hp1-scu-sat32-radix-v1"
RMD_PRODUCTION_WORK_KINDS: Final = ("DENSE_HP1_FINAL",)
RMD_DIAGNOSTIC_WORK_KINDS: Final = ("RMD_RAW",)
INTEGRATED_CONTROLLER: Final = "UPSTREAM_GEMMINI_WS"
INTEGRATED_MEMORY: Final = "INTEGRATED"
INTEGRATED_CYCLE_SCOPE: Final = "logical_work_accept_to_final_backing_write_completion"
INTEGRATED_ARTIFACT_ROLE: Final = "HOST_COMMON_ORCHESTRATION"
INTEGRATED_AUDIT_ROLE: Final = "PHYSICAL_HOST"
UNRESOLVED_BYPASS_FLAGS: Final = (
    "--allow-shlib-undefined", "--unresolved-symbols=ignore", "-undefined dynamic_lookup",
    "-Wl,-undefined,dynamic_lookup",
)
INTEGRATED_SOURCE_FILES: Final = (
    "source/src/gemmini/control/src/main/scala/im2p/gemmini/UpstreamWsHp1Top.scala",
    "source/src/gemmini/control/src/main/scala/im2p/gemmini/UpstreamWsMemory.scala",
    "source/src/gemmini/control/src/main/scala/im2p/gemmini/ScaleBackingLoader.scala",
    "source/src/gemmini/src/main/scala/im2p/gemmini/BackingMemoryPort.scala",
    "source/src/gemmini/src/main/scala/im2p/gemmini/SCU.scala",
    "source/fpga/gemmini_hp1/host/test_ws_rtl.cpp",
)
RMD_LLAMA_SOURCES: Final = (
    "ggml/src/ggml-gemmini/residual/rmd/rmd-builder.cpp",
    "ggml/src/ggml-gemmini/residual/rmd/rmd-builder.hpp",
    "ggml/src/ggml-gemmini/residual/rmd/rmd-compose.cpp",
    "ggml/src/ggml-gemmini/residual/rmd/rmd-compose.hpp",
    "ggml/src/ggml-gemmini/residual/rmd/rmd-executor.cpp",
    "ggml/src/ggml-gemmini/residual/rmd/rmd-executor.hpp",
    "ggml/src/ggml-gemmini/residual/rmd/rmd-im2p-executor.cpp",
    "ggml/src/ggml-gemmini/residual/rmd/rmd-im2p-executor.hpp",
    "ggml/src/ggml-gemmini/residual/rmd/rmd-reference.cpp",
    "ggml/src/ggml-gemmini/residual/rmd/rmd-reference.hpp",
    "ggml/src/ggml-gemmini/residual/rmd/rmd-types.hpp",
    "ggml/src/ggml-gemmini/quants/common/hp1_scu.hpp",
    "ggml/src/ggml-gemmini/quants/common/weight_reader.cpp",
    "ggml/src/ggml-gemmini/quants/common/weight_reader.hpp",
    "ggml/src/ggml-gemmini/quants/common/weight_route.hpp",
    "ggml/src/ggml-gemmini/quants/common/dequant.cpp",
    "ggml/src/ggml-gemmini/quants/act/dispatch.cpp",
    "ggml/src/ggml.c",
)
RMD_HOST_SOURCES: Final = (
    "rmd.cpp", "rmd.hpp", "rmd_rtl_fixture.cpp", "rmd_rtl_fixture.hpp",
    "bound_rmd_rtl_fixture.cpp", "bound_rmd_rtl_fixture.hpp",
)
REQUIRED_SOURCE_FILES: Final = (
    "sim/include/im2p_sim.h", "sim/include/im2p_geometry.h", "sim/include/im2p_cycle_model.h",
    "frontend/include/im2p_gemmini_frontend.hpp",
    "frontend/include/im2p_production_trace.hpp",
    "frontend/src/im2p_gemmini_frontend.cpp",
    "scripts/im2p_paths.py", "scripts/im2p_config.py",
    "scripts/real_matrix_fingerprint.py", "scripts/real_lib_manifest.py",
    "scripts/gemmini_replay_contract.py",
    "sim/common/gemmini_schedule.hpp", "sim/common/gemmini_schedule.cpp",
    "sim/backends/gemmini_hp1/geometry.cpp",
    "sim/cycle/CMakeLists.txt", "sim/cycle/c_api.cpp", "sim/cycle/timing_profile.hpp",
    "sim/cycle/control_engine.cpp", "sim/cycle/control_engine.hpp",
    "sim/cycle/cycle_model.hpp", "sim/cycle/execute_engine.cpp",
    "sim/cycle/scheduled_work.cpp", "sim/cycle/scheduled_work.hpp",
    "sim/cycle/timing_events.hpp", "sim/cycle/cli.py", "sim/cycle/optrace.py",
    "sim/cycle/optrace_schema.py", "sim/cycle/optrace_parents.py",
    "sim/cycle/certificate_contract.py", "sim/cycle/corpus_authority.py",
    "sim/cycle/corpus-authority-v1.json", "scripts/gemmini_rtl_build_binding.py",
    "sim/tests/cycle/probe.cpp", "sim/tests/cycle/test_cycle_model.cpp",
    "sim/tests/cycle/test_c_api.c", "sim/tests/cycle/current_rtl_certificate.py",
    "sim/tests/cycle/rtl_hardening.py", "sim/tests/cycle/production_block_certificate.py",
    "sim/tests/cycle/certificate_document.py", "sim/tests/cycle/reaggregate_certificate.py",
    "config/im2p_profiles.json", "sim/ffi/im2p_config.h", "src/common/Config.bsv",
    "docs/GEMMINI_CYCLE_MODEL.md", "docs/VERIFICATION.md",
) + HARDWARE_PATHS
HOST_LLAMA_SOURCES: Final = RMD_LLAMA_SOURCES + (
    "ggml/src/ggml-gemmini-utils/CMakeLists.txt",
    "ggml/src/ggml-gemmini-utils/src/optrace.cpp",
    "ggml/src/ggml-gemmini-utils/src/debug.cpp",
    "ggml/src/ggml-gemmini-utils/src/cycle.cpp",
    "ggml/src/ggml-gemmini-utils/src/log-capi.cpp",
    "ggml/src/ggml-gemmini-utils/src/cycle_reader-capi.cpp",
    "ggml/src/ggml-gemmini-utils/src/cycle_reader_aarch64.cpp",
    "ggml/src/ggml-gemmini-utils/src/cycle_reader_internal.h",
    "ggml/src/ggml-gemmini-utils/src/semantic.cpp",
    "ggml/src/ggml-gemmini-utils/include/gemmini/optrace.hpp",
    "ggml/src/ggml-gemmini-utils/include/gemmini/cpu_log_context.hpp",
    "ggml/src/ggml-gemmini-utils/include/gemmini/semantic.hpp",
    "ggml/src/ggml-gemmini-utils/include/gemmini/semantic.h",
    "scripts/optrace-build-info.py",
)


@dataclass(frozen=True, slots=True)
class ExportError(Exception):
    message: str

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True, slots=True)
class ExportRequest:
    source_root: Path
    build_root: Path | None
    output: Path
    board: Path | None


@dataclass(frozen=True, slots=True)
class ExportResult:
    status: str
    archive: Path
    files: int
    generated_rtl: int
    relocation_verified: bool


@dataclass(frozen=True, slots=True)
class VerifyResult:
    status: str
    files: int


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def safe_relative(name: str) -> Path:
    pure = PurePosixPath(name)
    if not name or pure.is_absolute() or ".." in pure.parts or pure.as_posix() != name:
        raise ExportError(f"unsafe relative path: {name}")
    return Path(*pure.parts)


def regular_file(path: Path) -> Path:
    if path.is_symlink() or not path.is_file():
        raise ExportError(f"regular file required: {path}")
    return path


def is_macho(path: Path) -> bool:
    with path.open("rb") as source:
        return source.read(4) in MACHO_MAGICS


def source_files(root: Path) -> list[tuple[Path, Path]]:
    selections = [root / "src/gemmini", root / "config/gemmini_hp1_profiles.json",
                  root / "config/gemmini_host_memory_contracts", root / "fpga/gemmini_hp1",
                  *(regular_file(root / name) for name in REQUIRED_SOURCE_FILES)]
    candidates = [path for selected in selections if selected.exists()
                  for path in ([selected] if selected.is_file() else selected.rglob("*"))]
    candidates.extend((root / "scripts").glob("gemmini_*"))
    result: list[tuple[Path, Path]] = []
    for path in sorted(set(candidates)):
        relative = path.relative_to(root)
        if path.is_symlink() or not path.is_file() or EXCLUDED_PARTS.intersection(relative.parts):
            continue
        if path.suffix.lower() not in SOURCE_SUFFIXES and path.name not in {"LICENSE", "LICENSE.upstream"}:
            continue
        result.append((path, Path("source") / relative))
    return result


def rmd_dependency_sources(workspace: Path) -> list[tuple[Path, Path]]:
    llama = workspace / "llama.cpp-gemmini"
    sources = {regular_file(llama / name) for name in HOST_LLAMA_SOURCES}
    sources.update(
        path for path in (llama / "ggml").rglob("*")
        if path.is_file() and not path.is_symlink()
        and path.suffix.lower() in {".h", ".hpp", ".inc", ".inl"}
        and not EXCLUDED_PARTS.intersection(path.relative_to(llama).parts)
    )
    return [
        (path, Path("dependency/source/llama_cpp_gemmini") / path.relative_to(llama))
        for path in sorted(sources)
    ]


def generated_files(root: Path | None) -> list[tuple[Path, Path]]:
    if root is None:
        return []
    if not root.is_dir():
        raise ExportError(f"build root is not a directory: {root}")
    result: list[tuple[Path, Path]] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if path.is_symlink() or not path.is_file() or EXCLUDED_PARTS.intersection(relative.parts):
            continue
        is_log = path.suffix.lower() == ".log" and "logs" in relative.parts
        is_host_param = path.name == "gemmini_params.h" and "host-params" in relative.parts
        is_host_command_log = path.name == "link.txt" and "gemmini_hp1_host_orchestration.dir" in relative.parts
        is_upstream_overlay = path.suffix.lower() == ".scala" and "upstream-overlay" in relative.parts
        if path.suffix.lower() not in RTL_SUFFIXES and path.name not in BUILD_METADATA and not is_log and not is_host_param and not is_host_command_log and not is_upstream_overlay and not (
            (path.name.startswith("stage-") or path.name.startswith("memory-probe"))
            and path.suffix == ".json"
        ):
            continue
        if path.suffix.lower() in FORBIDDEN_SUFFIXES or is_macho(path):
            continue
        result.append((path, Path("generated") / relative))
    return result


def write_json(path: Path, value: dict[str, JsonValue]) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ("git", "-C", str(repository), *arguments), text=True,
        capture_output=True, check=False,
    )
    if completed.returncode != 0:
        raise ExportError(f"git {' '.join(arguments)} failed in {repository}: {completed.stderr.strip()}")
    return completed.stdout


def git_is_ancestor(repository: Path, ancestor: str, descendant: str) -> bool:
    completed = subprocess.run(
        ("git", "-C", str(repository), "merge-base", "--is-ancestor", ancestor, descendant),
        text=True, capture_output=True, check=False,
    )
    if completed.returncode not in (0, 1):
        raise ExportError(f"git merge-base failed in {repository}: {completed.stderr.strip()}")
    return completed.returncode == 0


def require_clean_tracked_state(repository: Path, name: str) -> list[JsonValue]:
    status: list[JsonValue] = list(git(repository, "status", "--short").splitlines())
    tracked = [entry for entry in status if isinstance(entry, str) and not entry.startswith("?? ")]
    if tracked:
        raise ExportError(f"dependency tracked state is dirty: {name}: {tracked[0]}")
    return status


def approved_head(repository: Path, name: str, baseline: str, descendants_allowed: bool) -> str:
    head = git(repository, "rev-parse", "HEAD").strip()
    if head == baseline:
        return head
    if descendants_allowed and git_is_ancestor(repository, baseline, head):
        return head
    raise ExportError(f"dependency HEAD mismatch: {name}: {head}")


def dependency_snapshot(source_root: Path, output: Path) -> dict[str, JsonValue]:
    try:
        upstream = json.loads((source_root / "src/gemmini/UPSTREAM.lock.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"schema_version": 1, "status": "NOT_RUN", "reason": "UPSTREAM_LOCK_MISSING"}
    if not isinstance(upstream, dict) or not isinstance(upstream.get("chipyard"), dict) or \
       not isinstance(upstream.get("gemmini"), dict):
        return {"schema_version": 1, "status": "NOT_RUN", "reason": "UPSTREAM_LOCK_INCOMPLETE"}
    workspace = Path(os.environ.get("IM2P_WORKSPACE_ROOT", source_root.parent)).resolve()
    work_root = resolve_gemmini_work_root(source_root)
    chipyard = work_root / "deps/chipyard-1.13.0"
    llama = workspace / "llama.cpp-gemmini"
    include = workspace / "RISC-V-DynDNN-gemmini-include"
    repositories = {
        "chipyard": (chipyard, "69eba860a352343e4ac6b6df0f3638a79a86ec78", False),
        "gemmini": (chipyard / "generators/gemmini", "25809f78323a729ef76fb68f3cedd8a24da2942b", False),
        "llama_cpp_gemmini": (llama, "257753bf77500081844598a28352b56ab505bd9a", True),
        "gemmini_include": (include, "cd2caed8d93e49d3f8873bd6c4e340fcad54b9e0", False),
    }
    repository_rows: dict[str, dict[str, JsonValue]] = {}
    patch_root = output / "dependency"
    patch_root.mkdir(parents=True, exist_ok=True)
    for name, (repository, baseline, descendants_allowed) in repositories.items():
        if not repository.is_dir():
            raise ExportError(f"dependency checkout missing: {repository}")
        head = approved_head(repository, name, baseline, descendants_allowed)
        status: list[JsonValue] = list(git(repository, "status", "--short").splitlines())
        if name != "llama_cpp_gemmini":
            status = require_clean_tracked_state(repository, name)
        allowed = tuple(path.relative_to(repository).as_posix()
                        for path, _ in rmd_dependency_sources(workspace)) \
            if name == "llama_cpp_gemmini" else ()
        diff = git(repository, "diff", "HEAD", "--binary", "--", *allowed)
        patch = patch_root / f"{name}.patch"
        patch.write_text(diff, encoding="utf-8")
        overlays: list[JsonValue] = []
        for relative_name in git(repository, "ls-files", "--others", "--exclude-standard").splitlines():
            relative = Path(relative_name)
            if name == "llama_cpp_gemmini" and relative_name not in allowed:
                continue
            if not relative.parts or relative.name == "AGENTS.md" or \
               relative.suffix.lower() not in SOURCE_SUFFIXES or \
               any(part.startswith("build") for part in relative.parts) or \
               EXCLUDED_PARTS.intersection(relative.parts) or \
               (name == "llama_cpp_gemmini" and relative.parts[0] not in {
                   "cmake", "ggml", "include", "scripts", "src", "tests",
               }):
                continue
            source = repository / relative
            if source.is_symlink() or not source.is_file():
                continue
            destination = patch_root / "overlays" / name / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            overlays.append({
                "source": relative.as_posix(),
                "package": destination.relative_to(output).as_posix(),
                "sha256": digest(destination),
                "bytes": destination.stat().st_size,
            })
        repository_rows[name] = {
            "head": head,
            "branch": git(repository, "branch", "--show-current").strip(),
            "baseline": baseline,
            "descendants_allowed": descendants_allowed,
            "remote": git(repository, "remote", "get-url", "origin").strip(),
            "tracked_patch": patch.relative_to(output).as_posix(),
            "tracked_patch_sha256": digest(patch),
            "tracked_patch_bytes": patch.stat().st_size,
            "tracked_patch_base": head,
            "allowed_overlay_paths": list(allowed),
            "untracked_source_overlay": overlays,
            "status": status,
        }
    repository_rows["chipyard"]["gemmini_gitlink"] = git(
        chipyard, "ls-tree", "HEAD", "generators/gemmini",
    ).split()[2]
    submodules: list[JsonValue] = list(git(chipyard, "submodule", "status").splitlines())
    repository_document: dict[str, JsonValue] = {}
    for name, row in repository_rows.items():
        repository_document[name] = row
    return {
        "schema_version": 1,
        "status": "PASS",
        "repositories": repository_document,
        "chipyard_submodules": submodules,
    }


def linux_instructions(dependencies: dict[str, JsonValue]) -> str:
    repositories = dependencies.get("repositories")
    if repositories is None:
        return "# Linux/Vivado handoff\n\nDependency lock unavailable for this fixture.\n"
    if not isinstance(repositories, dict):
        raise ExportError("dependency repository document is invalid")
    lines = ["# Linux/Vivado handoff", "", "Verify `SHA256SUMS`, then recreate exact checkouts:", ""]
    for name in ("chipyard", "llama_cpp_gemmini", "gemmini_include"):
        entry = repositories[name]
        if not isinstance(entry, dict):
            raise ExportError(f"dependency record missing: {name}")
        lines.extend((
            f"- `{name}`: `{entry['remote']}` at `{entry['head']}`",
            f"  apply `{entry['tracked_patch']}` when its byte count is nonzero.",
            "  copy every `untracked_source_overlay` file to its recorded source path.",
        ))
    lines.extend((
        "",
        "Initialize Chipyard's pinned Gemmini, Rocket Chip, HardFloat, diplomacy, CDE and FireSim submodules without `--remote`.",
        "Set `IM2P_WORKSPACE_ROOT` and `IM2P_GEMMINI_WORK_ROOT`, run `source/scripts/gemmini_vendor.py --verify`, then use `source/scripts/gemmini_build.sh`.",
        "For integrated exports, select one profile's `filelist` from `profile-manifest.json`; no aggregate root filelist is runnable.",
        "All exports include the official host's compiled llama sources, utility CMake target and GGML headers under `dependency/source/llama_cpp_gemmini`; restore those paths into the pinned llama checkout together with its recorded patch/overlays before rebuilding host tests.",
        "Provide a validated board manifest and XDC only for synth, route or bitstream stages.",
        "",
        "## Minimum host rebuild (no simulator or hardware operation)",
        "",
        "Use a new workspace outside the original checkout. Set `package` to this extracted package's absolute path and `work` to the new workspace. Restore only llama and Gemmini headers at the locked commits for this minimum build; Chipyard is needed for the separate RTL flow, not this host build.",
        "",
        "```sh",
        'export IM2P_WORKSPACE_ROOT="$work"',
        'export IM2P_GEMMINI_WORK_ROOT="$work/gemmini-work"',
        'export PYTHONDONTWRITEBYTECODE=1',
        "unset CPATH C_INCLUDE_PATH CPLUS_INCLUDE_PATH PYTHONPATH",
        'python3 "$package/source/scripts/gemmini_export.py" verify "$package"',
        'cp -R "$package/dependency/source/llama_cpp_gemmini/." "$work/llama.cpp-gemmini/"',
        'python3 "$package/source/scripts/gemmini_build.py" --stage plan --a-bits 8 --w-bits 8 --dim 16 --scu hp1-left-shift --memory-contract "$package/source/config/gemmini_host_memory_contracts/a8w8-d16-hp1.json" --out "$work/plan"',
        'cmake -S "$package/source/fpga/gemmini_hp1/host" -B "$work/host-build" -DCMAKE_EXPORT_COMPILE_COMMANDS=ON -DIM2P_GEMMINI_RESOLVED_PROFILE="$work/plan/resolved-profile.json" -DIM2P_LLAMA_ROOT="$work/llama.cpp-gemmini" -DIM2P_GEMMINI_INCLUDE_ROOT="$work/RISC-V-DynDNN-gemmini-include"',
        'cmake --build "$work/host-build" --parallel 2',
        'ctest --test-dir "$work/host-build" --output-on-failure',
        "```",
        "",
        "Restore dependencies from their recorded remote and exact HEAD (a verified immutable Git-object archive is also acceptable), then apply nonempty tracked patches and allowed overlays before the source copy above. `allowed_overlay_paths` limits the llama patch to the host compilation closure; unrelated local scripts/editor/model files are excluded. `source-manifest.json` records the copied source snapshot.",
        "This minimum target is external-executor-only. Trace OFF works without Git metadata; explicit production tracing is unsupported and fails closed. The ordinary IM2P_SIM trace ON producer build is a separate runtime-dependent flow, not certified by this host rebuild.",
        "The exported host contract uses CYCLE_SIM=0. Semantic metadata helpers are included because existing CPU-log sources depend on them; this does not export or certify the PoTal collector. CYCLE_SIM=1 requires a complete llama root build and its CPU-functional source closure, and is rejected by this standalone host build.",
        "Checksum verification proves content identity, not dependency closure. The export verifier separately requires public/frontend headers, Python helpers and all utility implementation files. Preserve compile dependency files and Python module origins when checking relocation. macOS execution does not certify a Linux binary.",
        "",
        "## Value-free replay tools",
        "",
        "The package also contains the standalone cycle library and official replay/certificate Python source closure. Build it independently:",
        "",
        "```sh",
        'cmake -S "$package/source/sim/cycle" -B "$work/cycle-build"',
        'cmake --build "$work/cycle-build" --parallel 2',
        'ctest --test-dir "$work/cycle-build" --output-on-failure --no-tests=error',
        'python3 "$package/source/sim/cycle/optrace.py" --help',
        'python3 "$package/source/sim/tests/cycle/current_rtl_certificate.py" --help',
        "```",
        "",
        "A newly built library needs its own admitted certificate and independent producer manifest. Building or hashing it does not certify it. Fresh RTL certificate generation additionally needs the separately restored RTL dependencies/build artifacts. Verified reaggregation needs the original raw evidence plus matching source/artifact proofs; those large external inputs are not bundled. Historical traces are not promoted to the current schema by export.",
        "See `source/docs/GEMMINI_CYCLE_MODEL.md` for the certificate/replay commands and current JSON contracts, and `source/docs/VERIFICATION.md` for verification scope.",
    ))
    return "\n".join(lines) + "\n"


def inventory(root: Path) -> dict[str, tuple[str, int]]:
    return {path.relative_to(root).as_posix(): (digest(path), path.stat().st_size)
            for path in sorted(root.rglob("*")) if path.is_file() and path.name != "SHA256SUMS"}


def expected_selected_top(profile: str, prefix: str) -> str:
    parts = profile.split("-")
    if len(parts) != 3 or parts[2] != "hp1" or not parts[0].startswith("a") or not parts[1].startswith("d"):
        raise ExportError(f"invalid profile name: {profile}")
    widths = parts[0][1:].split("w", 1)
    if len(widths) != 2 or not widths[0].isdigit() or not widths[1].isdigit() or not parts[1][1:].isdigit():
        raise ExportError(f"invalid profile name: {profile}")
    return f"{prefix}A{widths[0]}W{widths[1]}D{parts[1][1:]}"


def read_object(path: Path) -> dict[str, JsonValue]:
    document = json.loads(regular_file(path).read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ExportError(f"JSON object required: {path}")
    return document


def rmd_enabled(profile: Mapping[str, JsonValue]) -> bool:
    return profile.get("rmd_enabled") is True


def require_current_rmd_contract(profile: Mapping[str, JsonValue], label: str) -> None:
    enabled = profile.get("rmd_enabled")
    if enabled is None:
        return
    if type(enabled) is not bool:
        raise ExportError(f"RMD capability must be boolean: {label}")
    if not enabled:
        if any(field in profile for field in RMD_PROFILE_FIELDS[1:]):
            raise ExportError(f"disabled RMD profile carries production RMD metadata: {label}")
        return
    expected: dict[str, JsonValue] = {
        "rmd_datapath": RMD_DATAPATH,
        "rmd_raw": False,
        "rmd_numerical_revision": RMD_NUMERICAL_REVISION,
        "host_integer_block_multiply": False,
        "work_kinds": list(RMD_PRODUCTION_WORK_KINDS),
        "diagnostic_work_kinds": list(RMD_DIAGNOSTIC_WORK_KINDS),
    }
    if any(profile.get(field) != value for field, value in expected.items()):
        raise ExportError(f"resolved profile RMD contract mismatch: {label}")


def filelist_entries(root: Path) -> list[Path]:
    entries: list[Path] = []
    for name in regular_file(root / "filelist.f").read_text(encoding="utf-8").splitlines():
        relative = safe_relative(name)
        _ = regular_file(root / relative)
        if relative.suffix.lower() not in RTL_SUFFIXES:
            raise ExportError(f"invalid RTL filelist entry: {name}")
        if relative in entries:
            raise ExportError(f"duplicate RTL filelist entry: {name}")
        entries.append(relative)
    return entries


def require_unique_top(root: Path, top: str) -> None:
    definitions: dict[str, Path] = {}
    for relative in filelist_entries(root):
        text = regular_file(root / relative).read_text(encoding="utf-8")
        for module in re.findall(r"(?m)^\s*module\s+(?:automatic\s+)?([A-Za-z_][A-Za-z0-9_$]*)", text):
            if module in definitions:
                if module == top:
                    raise ExportError(f"selected top must resolve to exactly one RTL module: {top}")
                raise ExportError(f"duplicate RTL module in profile filelist: {module}")
            definitions[module] = relative
    if top not in definitions:
        raise ExportError(f"selected top must resolve to exactly one RTL module: {top}")


def command_arguments(document: object, label: str) -> list[str]:
    if not isinstance(document, dict) or not isinstance(document.get("arguments"), list) or not all(
        isinstance(argument, str) for argument in document["arguments"]
    ):
        raise ExportError(f"invalid {label} command record")
    return [argument for argument in document["arguments"] if isinstance(argument, str)]


def option_value(arguments: list[str], option: str) -> str:
    positions = [index for index, argument in enumerate(arguments) if argument == option]
    if len(positions) != 1 or positions[0] + 1 >= len(arguments):
        raise ExportError(f"invalid command option: {option}")
    return arguments[positions[0] + 1]


def require_runtime_log(profile: Mapping[str, JsonValue], content: str) -> None:
    label = expected_selected_top(str(profile["profile"]), "IM2PGemminiWSHP1").removeprefix(
        "IM2PGemminiWSHP1",
    )
    if re.search(
        rf"(?m)^integrated upstream WS HP1 RTL passed {re.escape(label)} "
        r"loops=[1-9][0-9]* load_execute_overlap=[1-9][0-9]*$",
        content,
    ) is None:
        raise ExportError(f"integrated RTL runtime log lacks PASS evidence: {profile['profile']}")
    if rmd_enabled(profile):
        if profile.get("rmd_raw") is not False:
            raise ExportError(f"production RMD profile is not normal HP1 scaled: {profile['profile']}")
        try:
            _ = rmd_runtime_evidence(content, str(profile["profile"]))
            _ = rmd_bound_runtime_evidence(content, str(profile["profile"]))
        except ValueError as error:
            raise ExportError(str(error)) from error


def require_host_test_profile(
    profile: dict[str, JsonValue], evidence: object, audit: dict[str, JsonValue], profile_root: Path | None,
) -> tuple[str, str, str, str]:
    name = str(profile["profile"])
    if not isinstance(evidence, dict) or evidence.get("profile") != name or evidence.get("status") != "PASS":
        raise ExportError(f"integrated host-test profile did not pass: {name}")
    resolved_profile = evidence.get("resolved_profile")
    if not isinstance(resolved_profile, str):
        raise ExportError(f"integrated host-test resolved profile is missing: {name}")
    evidence_profile_root = Path(resolved_profile).parent
    if profile_root is not None and evidence_profile_root.resolve() != profile_root.resolve():
        raise ExportError(f"integrated host-test resolved profile path mismatch: {name}")
    required_fields = (*PROFILE_FIELDS, *INTEGRATED_PROFILE_FIELDS)
    if rmd_enabled(profile):
        required_fields += RMD_PROFILE_FIELDS
    for field in required_fields:
        if evidence.get(field) != profile[field]:
            raise ExportError(f"integrated host-test metadata mismatch for {name}: {field}")
    commands = evidence.get("commands")
    results = evidence.get("command_results")
    if not isinstance(commands, list) or not isinstance(results, list) or not results or len(commands) != len(results):
        raise ExportError(f"integrated host-test command evidence incomplete: {name}")
    command_rows = [command_arguments(command, "planned") for command in commands]
    result_rows = [command_arguments(result, "executed") for result in results]
    if command_rows != result_rows or any(
        not isinstance(result, dict)
        or not isinstance(result.get("returncode"), int)
        or isinstance(result.get("returncode"), bool)
        or result.get("returncode") != 0
        for result in results
    ):
        raise ExportError(f"integrated host-test command failed or was not executed: {name}")
    for command, result in zip(commands, results):
        if not isinstance(command, dict) or not isinstance(result, dict):
            raise ExportError(f"integrated host-test command record is invalid: {name}")
        cwd = command.get("cwd")
        log = result.get("log")
        if not isinstance(cwd, str) or not cwd or result.get("cwd") != cwd or not isinstance(log, str):
            raise ExportError(f"integrated host-test cwd/log evidence incomplete: {name}")
        if profile_root is not None:
            if not Path(cwd).is_dir():
                raise ExportError(f"integrated host-test cwd does not exist: {name}")
            log_path = regular_file(Path(log))
            if not log_path.resolve().is_relative_to((profile_root / "logs").resolve()):
                raise ExportError(f"integrated host-test log is outside profile root: {name}")
    top = str(profile["selected_top"])
    rtl_builds = [arguments for arguments in result_rows if arguments and arguments[0] == "verilator" and
                  any(Path(argument).name == "test_ws_rtl.cpp" for argument in arguments)]
    if len(rtl_builds) != 1 or option_value(rtl_builds[0], "--top-module") != top:
        raise ExportError(f"integrated RTL runtime build evidence missing: {name}")
    if rmd_enabled(profile) and not {
        "rmd_rtl_fixture.cpp", "bound_rmd_rtl_fixture.cpp", "rmd-reference.cpp",
    }.issubset({Path(argument).name for argument in rtl_builds[0]}):
        raise ExportError(f"RMD runtime compilation closure missing: {name}")
    if rmd_enabled(profile):
        linked = shlex.split(option_value(rtl_builds[0], "-LDFLAGS"))
        for archive in ("libgemmini_hp1_host_common.a", "libgemmini_hp1_ggml_numeric.a"):
            expected = str(evidence_profile_root / "host-build" / archive)
            if expected not in linked:
                raise ExportError(f"RMD runtime host archive missing: {name}: {archive}")
            if profile_root is not None:
                _ = regular_file(Path(expected))
    runtime_indices = [index for index, arguments in enumerate(result_rows) if arguments and
                       Path(arguments[0]).name == "VIM2PGemminiWSHP1RtlTest"]
    if len(runtime_indices) != 1:
        raise ExportError(f"integrated RTL runtime execution evidence missing: {name}")
    runtime_artifact = Path(result_rows[runtime_indices[0]][0])
    expected_runtime = evidence_profile_root / "rtl-test-obj/VIM2PGemminiWSHP1RtlTest"
    if runtime_artifact.resolve() != expected_runtime.resolve():
        raise ExportError(f"integrated RTL runtime artifact path mismatch: {name}")
    runtime_result = results[runtime_indices[0]]
    if not isinstance(runtime_result, dict) or not isinstance(runtime_result.get("log"), str):
        raise ExportError(f"integrated RTL runtime log missing: {name}")
    runtime_log = runtime_result["log"]
    if not isinstance(runtime_log, str):
        raise ExportError(f"integrated RTL runtime log missing: {name}")
    if profile_root is not None:
        runtime_artifact_sha256 = digest(regular_file(runtime_artifact))
        require_runtime_log(profile, regular_file(Path(runtime_log)).read_text(encoding="utf-8"))
    else:
        runtime_artifact_sha256 = profile.get("rtl_test_artifact_sha256")
        if not isinstance(runtime_artifact_sha256, str) or \
           re.fullmatch(r"[0-9a-f]{64}", runtime_artifact_sha256) is None:
            raise ExportError(f"integrated RTL runtime artifact identity is invalid: {name}")
    audit_commands = [arguments for arguments in result_rows if arguments and
                      Path(arguments[0]).name.startswith("python") and
                      any(Path(argument).name == "gemmini_audit_host.py" for argument in arguments)]
    if len(audit_commands) != 1 or option_value(audit_commands[0], "--role") != INTEGRATED_AUDIT_ROLE:
        raise ExportError(f"integrated no-SIM host audit command evidence missing: {name}")
    physical_operations = audit.get("physical_operations")
    if audit.get("status") != "PASS" or audit.get("role") != INTEGRATED_AUDIT_ROLE or \
       audit.get("simulator_markers") != [] or not isinstance(physical_operations, int) or \
       isinstance(physical_operations, bool) or physical_operations != 0:
        raise ExportError(f"integrated host audit did not pass: {name}")
    audit_output = Path(option_value(audit_commands[0], "--out"))
    artifact = Path(option_value(audit_commands[0], "--artifact"))
    command_log = Path(option_value(audit_commands[0], "--command-log"))
    audit_artifact = audit.get("artifact")
    artifact_sha256 = audit.get("artifact_sha256")
    if not isinstance(audit_artifact, str) or Path(audit_artifact).resolve() != artifact.resolve() or \
       not isinstance(artifact_sha256, str) or re.fullmatch(r"[0-9a-f]{64}", artifact_sha256) is None:
        raise ExportError(f"integrated host audit artifact identity is invalid: {name}")
    if audit_output.resolve() != (evidence_profile_root / "host-audit.json").resolve() or \
       artifact.resolve() != (evidence_profile_root / "host-build/gemmini_hp1_host_orchestration").resolve() or \
       command_log.resolve() != (
           evidence_profile_root / "host-build/CMakeFiles/gemmini_hp1_host_orchestration.dir/link.txt"
       ).resolve():
        raise ExportError(f"integrated host audit artifact identity mismatch: {name}")
    if profile_root is not None and digest(regular_file(artifact)) != artifact_sha256:
        raise ExportError(f"integrated host audit artifact identity mismatch: {name}")
    if profile_root is not None:
        if command_log.is_symlink() or not command_log.is_file():
            raise ExportError(f"integrated host command log is missing: {name}")
        command_text = regular_file(command_log).read_text(encoding="utf-8")
        if not command_text.strip() or any(flag in command_text for flag in UNRESOLVED_BYPASS_FLAGS):
            raise ExportError(f"integrated host command log is invalid: {name}")
    return runtime_log, artifact_sha256, str(command_log), runtime_artifact_sha256


def attach_integrated_evidence(
    build_root: Path, profiles: list[dict[str, JsonValue]],
) -> list[dict[str, JsonValue]]:
    evidence_path = build_root / "stage-host-test.json"
    if not evidence_path.is_file():
        fallback = build_root / "result.json"
        evidence_path = fallback if fallback.is_file() else evidence_path
    if not evidence_path.is_file():
        raise ExportError("integrated export requires a host-test result")
    document = read_object(evidence_path)
    rows = document.get("profiles")
    if document.get("stage") != "host-test" or document.get("status") != "PASS" or \
       document.get("execution") != "sequential" or not isinstance(rows, list):
        raise ExportError("integrated export requires a passing sequential host-test result")
    evidence_by_name: dict[str, object] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ExportError("integrated host-test profile evidence is invalid or duplicated")
        profile_name = row.get("profile")
        if not isinstance(profile_name, str) or profile_name in evidence_by_name:
            raise ExportError("integrated host-test profile evidence is invalid or duplicated")
        evidence_by_name[profile_name] = row
    names = {str(profile["profile"]) for profile in profiles}
    if set(evidence_by_name) != names:
        raise ExportError("integrated profiles do not match host-test matrix")
    result: list[dict[str, JsonValue]] = []
    for profile in profiles:
        name = str(profile["profile"])
        profile_root = build_root / safe_relative(str(profile["root"])).relative_to("generated")
        require_unique_top(profile_root, str(profile["selected_top"]))
        audit_path = profile_root / "host-audit.json"
        runtime_log, artifact_sha256, command_log, runtime_artifact_sha256 = require_host_test_profile(
            profile, evidence_by_name[name], read_object(audit_path), profile_root,
        )
        runtime_log_path = Path(runtime_log)
        command_log_path = Path(command_log)
        result.append({
            **profile,
            **({"rmd_runtime_status": "PASS"} if rmd_enabled(profile) else {}),
            "status": "PASS",
            "runtime_status": "PASS",
            "no_sim_host_artifact": "PASS",
            "host_test_result": (Path("generated") / evidence_path.relative_to(build_root)).as_posix(),
            "host_audit": (Path("generated") / audit_path.relative_to(build_root)).as_posix(),
            "filelist": (Path(str(profile["root"])) / "filelist.f").as_posix(),
            "runtime_log": (
                Path("generated") / runtime_log_path.resolve().relative_to(build_root.resolve())
            ).as_posix(),
            "host_artifact_sha256": artifact_sha256,
            "rtl_test_artifact_sha256": runtime_artifact_sha256,
            "host_command_log": (
                Path("generated") / command_log_path.resolve().relative_to(build_root.resolve())
            ).as_posix(),
            "host_command_log_sha256": digest(command_log_path),
        })
    return result


def resolved_profiles(build_root: Path | None) -> list[dict[str, JsonValue]]:
    if build_root is None:
        return []
    profiles: list[dict[str, JsonValue]] = []
    for manifest in sorted(build_root.rglob("resolved-profile.json")):
        if EXCLUDED_PARTS.intersection(manifest.relative_to(build_root).parts):
            continue
        document = read_object(manifest)
        if not isinstance(document.get("profile"), str):
            raise ExportError(f"invalid resolved profile: {manifest}")
        for field in PROFILE_FIELDS:
            if not isinstance(document.get(field), str):
                raise ExportError(f"resolved profile missing {field}: {manifest}")
        require_current_rmd_contract(document, str(manifest))
        rtl_count = sum(
            path.is_file() and not path.is_symlink() and path.suffix.lower() in RTL_SUFFIXES
            for path in manifest.parent.rglob("*")
        )
        profiles.append({
            "profile": document["profile"],
            **{field: document[field] for field in PROFILE_FIELDS},
            **{field: document[field] for field in INTEGRATED_PROFILE_FIELDS if field in document},
            **{field: document[field] for field in RMD_PROFILE_FIELDS if field in document},
            "status": "PASS" if rtl_count else "FAIL",
            "generated_rtl": rtl_count,
            "root": (Path("generated") / manifest.parent.relative_to(build_root)).as_posix(),
        })
    return profiles


def export_kind(profiles: list[dict[str, JsonValue]]) -> str:
    if not profiles:
        return "SOURCE_ONLY"
    names = [str(profile["profile"]) for profile in profiles]
    if len(names) != len(set(names)):
        raise ExportError("duplicate resolved profile names cannot share one export")
    controllers = {str(profile["controller_kind"]) for profile in profiles}
    if len(controllers) != 1:
        raise ExportError("mixed controller kinds cannot share one export")
    controller = next(iter(controllers))
    match controller:
        case "UPSTREAM_GEMMINI_WS":
            for profile in profiles:
                if profile["backing_memory"] != INTEGRATED_MEMORY or profile["cycle_scope"] != INTEGRATED_CYCLE_SCOPE:
                    raise ExportError(f"invalid integrated resolved profile: {profile['profile']}")
                if profile.get("host_artifact_role") != INTEGRATED_ARTIFACT_ROLE or \
                   profile.get("host_audit_role") != INTEGRATED_AUDIT_ROLE:
                    raise ExportError(f"invalid integrated host artifact identity: {profile['profile']}")
                expected = expected_selected_top(str(profile["profile"]), "IM2PGemminiWSHP1")
                if profile["selected_top"] != expected:
                    raise ExportError(f"invalid integrated selected top: {profile['selected_top']}")
            return "INTEGRATED"
        case _:
            raise ExportError(f"unsupported controller kind: {controller}")


def json_profiles(profiles: list[dict[str, JsonValue]]) -> list[JsonValue]:
    values: list[JsonValue] = []
    values.extend(profiles)
    return values


def verify_integrated_closure(root: Path, profiles: list[dict[str, JsonValue]]) -> None:
    if (root / "filelist.f").exists():
        raise ExportError("integrated export must not contain an aggregate root filelist")
    evidence_names = {str(profile.get("host_test_result", "")) for profile in profiles}
    if len(evidence_names) != 1:
        raise ExportError("integrated profiles must share one host-test matrix result")
    evidence_path = root / safe_relative(next(iter(evidence_names)))
    evidence = read_object(evidence_path)
    rows = evidence.get("profiles")
    if evidence.get("stage") != "host-test" or evidence.get("status") != "PASS" or \
       evidence.get("execution") != "sequential" or not isinstance(rows, list):
        raise ExportError("packaged host-test matrix evidence is invalid")
    row_names = [row.get("profile") for row in rows if isinstance(row, dict)]
    profile_names = [str(profile["profile"]) for profile in profiles]
    if len(row_names) != len(rows) or not all(isinstance(name, str) for name in row_names) or \
       len(row_names) != len({str(name) for name in row_names}) or \
       {str(name) for name in row_names} != set(profile_names):
        raise ExportError("packaged host-test matrix profiles do not match export")
    for profile in profiles:
        top = str(profile["selected_top"])
        if profile.get("status") != "PASS" or profile.get("runtime_status") != "PASS" or \
           profile.get("no_sim_host_artifact") != "PASS":
            raise ExportError(f"integrated profile lacks passing runtime gates: {profile['profile']}")
        if rmd_enabled(profile) and profile.get("rmd_runtime_status") != "PASS":
            raise ExportError(f"integrated profile lacks RMD runtime gate: {profile['profile']}")
        profile_relative = safe_relative(str(profile["root"]))
        profile_root = root / profile_relative
        resolved = read_object(profile_root / "resolved-profile.json")
        if any(profile.get(field) != resolved.get(field) for field in RMD_PROFILE_FIELDS):
            raise ExportError(f"packaged RMD capability differs from resolved profile: {profile['profile']}")
        expected_filelist = (profile_relative / "filelist.f").as_posix()
        if profile.get("filelist") != expected_filelist:
            raise ExportError(f"integrated profile filelist mismatch: {profile['profile']}")
        require_unique_top(profile_root, top)
        matches = [row for row in rows if isinstance(row, dict) and row.get("profile") == profile["profile"]]
        if len(matches) != 1:
            raise ExportError(f"packaged host-test profile evidence is ambiguous: {profile['profile']}")
        expected_audit = (profile_relative / "host-audit.json").as_posix()
        if profile.get("host_audit") != expected_audit:
            raise ExportError(f"packaged host audit path mismatch: {profile['profile']}")
        audit_path = root / expected_audit
        _, artifact_sha256, _, rtl_test_sha256 = require_host_test_profile(
            profile, matches[0], read_object(audit_path), None,
        )
        if profile.get("host_artifact_sha256") != artifact_sha256:
            raise ExportError(f"packaged host artifact identity mismatch: {profile['profile']}")
        if profile.get("rtl_test_artifact_sha256") != rtl_test_sha256:
            raise ExportError(f"packaged RTL test artifact identity mismatch: {profile['profile']}")
        runtime_relative = safe_relative(str(profile.get("runtime_log", "")))
        if not runtime_relative.is_relative_to(profile_relative / "logs"):
            raise ExportError(f"packaged RTL runtime log path mismatch: {profile['profile']}")
        runtime_log = root / runtime_relative
        require_runtime_log(profile, regular_file(runtime_log).read_text(encoding="utf-8"))
        command_relative = profile_relative / \
            "host-build/CMakeFiles/gemmini_hp1_host_orchestration.dir/link.txt"
        if profile.get("host_command_log") != command_relative.as_posix():
            raise ExportError(f"packaged host command log path mismatch: {profile['profile']}")
        command_log = root / command_relative
        command_text = regular_file(command_log).read_text(encoding="utf-8")
        if profile.get("host_command_log_sha256") != digest(command_log) or not command_text.strip() or \
           any(flag in command_text for flag in UNRESOLVED_BYPASS_FLAGS):
            raise ExportError(f"packaged host command log is invalid: {profile['profile']}")
    mesh_sources = tuple(root.glob("source/src/gemmini/upstream/**/MeshWithDelays.scala"))
    if len(mesh_sources) != 1 or "class MeshWithDelays" not in regular_file(mesh_sources[0]).read_text(encoding="utf-8"):
        raise ExportError("exactly one MeshWithDelays source snapshot is required")
    for name in INTEGRATED_SOURCE_FILES:
        _ = regular_file(root / safe_relative(name))
    if any(rmd_enabled(profile) for profile in profiles):
        for name in RMD_HOST_SOURCES:
            _ = regular_file(root / "source/fpga/gemmini_hp1/host" / name)
        for name in RMD_LLAMA_SOURCES:
            _ = regular_file(root / "dependency/source/llama_cpp_gemmini" / name)
        _ = regular_file(root / "dependency/source/llama_cpp_gemmini/ggml/include/ggml.h")
    if not tuple((root / "source/src/gemmini/patches").glob("*.patch")):
        raise ExportError("upstream overlay patch missing")
    if not tuple((root / "source/src/gemmini/upstream").rglob("*.scala")):
        raise ExportError("upstream source snapshots missing")
    _ = regular_file(root / "source/src/gemmini/UPSTREAM.lock.json")


def verify_export(root: Path) -> VerifyResult:
    if not root.is_dir():
        raise ExportError(f"export directory missing: {root}")
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ExportError(f"package symlink forbidden: {path}")
        if path.is_file() and (path.suffix.lower() in FORBIDDEN_SUFFIXES or is_macho(path)):
            raise ExportError(f"forbidden binary/model artifact: {path}")
    expected: dict[str, tuple[str, int]] = {}
    for line in regular_file(root / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        fields = line.split("  ", 1)
        if len(fields) != 2 or len(fields[0]) != 64 or any(character not in "0123456789abcdef" for character in fields[0]):
            raise ExportError("malformed SHA256SUMS entry")
        sha256, name = fields
        relative = safe_relative(name)
        expected[relative.as_posix()] = (sha256, (root / relative).stat().st_size)
    if expected != inventory(root):
        raise ExportError("package SHA256 inventory mismatch")
    required = tuple(f"source/{name}" for name in REQUIRED_SOURCE_FILES) + tuple(
        f"dependency/source/llama_cpp_gemmini/{name}" for name in HOST_LLAMA_SOURCES
    )
    for name in required:
        if not (root / name).is_file():
            raise ExportError(f"required source closure missing: {name}")
    source_manifest = read_object(root / "source-manifest.json")
    source_rows = source_manifest.get("files")
    if source_manifest.get("schema_version") != 1 or not isinstance(source_rows, dict):
        raise ExportError("source closure manifest is invalid")
    for name in required:
        if name not in source_rows:
            raise ExportError(f"required source closure manifest entry missing: {name}")
    for name, row in source_rows.items():
        if not name.startswith(("source/", "dependency/source/")):
            continue
        source = root / safe_relative(name)
        if not isinstance(row, dict) or not source.is_file() or \
           row.get("sha256") != digest(source) or row.get("bytes") != source.stat().st_size:
            raise ExportError(f"source closure manifest mismatch: {name}")
    result = json.loads(regular_file(root / "result.json").read_text(encoding="utf-8"))
    if not isinstance(result, dict) or result.get("export") != "PASS":
        raise ExportError("export result does not report PASS")
    if result.get("route") != "NOT_RUN" or result.get("bitstream") != "NOT_RUN":
        raise ExportError("export must not claim route or bitstream completion")
    kind = result.get("export_kind")
    profile_manifest = json.loads(regular_file(root / "profile-manifest.json").read_text(encoding="utf-8"))
    if not isinstance(profile_manifest, dict) or not isinstance(profile_manifest.get("profiles"), list):
        raise ExportError("profile manifest is invalid")
    profiles = profile_manifest["profiles"]
    if not all(isinstance(profile, dict) for profile in profiles):
        raise ExportError("profile manifest is invalid")
    typed_profiles: list[dict[str, JsonValue]] = [profile for profile in profiles if isinstance(profile, dict)]
    if result.get("profiles") != typed_profiles or kind != export_kind(typed_profiles):
        raise ExportError("result profile metadata does not match package manifest")
    if kind == "INTEGRATED":
        verify_integrated_closure(root, typed_profiles)
    else:
        _ = filelist_entries(root)
    return VerifyResult("PASS", len(expected) + 1)


def make_archive(root: Path, archive: Path) -> None:
    if archive.exists():
        raise ExportError(f"archive already exists: {archive}")
    with tarfile.open(archive, "x:gz", format=tarfile.PAX_FORMAT) as package:
        for path in sorted(root.rglob("*")):
            info = package.gettarinfo(str(path), arcname=f"gemmini-hp1-export/{path.relative_to(root).as_posix()}")
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mtime = 0
            if path.is_file():
                with path.open("rb") as source:
                    package.addfile(info, source)
            else:
                package.addfile(info)


def extract_export(archive: Path, output: Path) -> VerifyResult:
    if os.path.lexists(output):
        raise ExportError(f"fresh extraction directory required: {output}")
    with tarfile.open(regular_file(archive), "r:gz") as package:
        members = package.getmembers()
        for member in members:
            relative = safe_relative(member.name)
            if relative.parts[0] != "gemmini-hp1-export" or not (member.isdir() or member.isfile()):
                raise ExportError(f"unsafe archive member: {member.name}")
        output.mkdir(parents=True)
        for member in members:
            destination = output / safe_relative(member.name)
            if member.isdir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            source = package.extractfile(member)
            if source is None:
                raise ExportError(f"archive member unreadable: {member.name}")
            with source, destination.open("xb") as target:
                shutil.copyfileobj(source, target)
    return verify_export(output / "gemmini-hp1-export")


def create_export(request: ExportRequest) -> ExportResult:
    source_root = request.source_root.resolve()
    output = request.output.resolve()
    if not source_root.is_dir() or os.path.lexists(output):
        raise ExportError("source root must exist and output must be new")
    build_root = request.build_root.resolve() if request.build_root is not None else None
    selected = source_files(source_root) + generated_files(build_root)
    profiles = resolved_profiles(build_root)
    kind = export_kind(profiles)
    workspace = Path(os.environ.get("IM2P_WORKSPACE_ROOT", source_root.parent)).resolve()
    selected.extend(rmd_dependency_sources(workspace))
    if kind == "INTEGRATED":
        if build_root is None:
            raise ExportError("integrated export requires a build root")
        profiles = attach_integrated_evidence(build_root, profiles)
    board = load_board_manifest(request.board) if request.board is not None else None
    output.mkdir(parents=True)
    rows: dict[str, dict[str, str | int]] = {}
    for source, relative in selected:
        destination = output / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        rows[relative.as_posix()] = {"sha256": digest(destination), "bytes": destination.stat().st_size}
    dependencies = dependency_snapshot(source_root, output)
    write_json(output / "dependency-lock.json", dependencies)
    (output / "LINUX_BUILD.md").write_text(
        linux_instructions(dependencies), encoding="utf-8",
    )
    if board is not None:
        copied_xdc: list[JsonValue] = []
        for index, source in enumerate(board.xdc):
            relative = Path("board/constraints") / f"{index:02}-{source.name}"
            (output / relative).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, output / relative)
            copied_xdc.append(relative.as_posix())
        board_payload: dict[str, JsonValue] = {"schema_version": 1, "board_id": board.board_id, "part": board.part,
            "top": board.top, "clock_port": board.clock_port, "clock_mhz": board.clock_mhz,
            "xdc": copied_xdc,
            "pin_constraints": copied_xdc[board.xdc.index(board.pin_constraints)],
            "memory_interface": board.memory_interface, "deployment_artifact": board.deployment_artifact}
        write_json(output / "board/resolved-board.json", board_payload)
    rtl = sorted(relative.as_posix() for _, relative in selected if relative.suffix.lower() in RTL_SUFFIXES)
    if kind != "INTEGRATED":
        (output / "filelist.f").write_text("".join(f"{name}\n" for name in rtl), encoding="utf-8")
    (output / "source-manifest.json").write_text(json.dumps({"schema_version": 1, "files": rows}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_json(output / "profile-manifest.json", {
        "schema_version": 1, "export_kind": kind, "profiles": json_profiles(profiles),
    })
    reason = "NOT_RUN_BY_EXPORT" if board is not None else "BOARD_REQUIRED"
    write_json(output / "result.json", {"schema_version": 1, "export": "PASS", "route": "NOT_RUN",
        "bitstream": "NOT_RUN", "physical": "NOT_RUN", "reason": reason,
        "generated_rtl": len(rtl), "board_included": board is not None,
        "export_kind": kind, "profiles": json_profiles(profiles)})
    sums = inventory(output)
    (output / "SHA256SUMS").write_text("".join(f"{sha}  {name}\n" for name, (sha, _) in sums.items()), encoding="utf-8")
    verified = verify_export(output)
    archive = output.with_name(output.name + ".tar.gz")
    make_archive(output, archive)
    with tempfile.TemporaryDirectory(prefix="gemmini-export-relocation-") as directory:
        relocated = extract_export(archive, Path(directory) / "relocated")
    return ExportResult("PASS", archive, verified.files, len(rtl), relocated.status == "PASS")


def main() -> int:
    parser = argparse.ArgumentParser(description="Create and verify relocatable Gemmini HP1 Linux handoff packages")
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create")
    create.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[1])
    create.add_argument("--build-root", type=Path)
    create.add_argument("--out", type=Path, required=True)
    create.add_argument("--board", type=Path)
    verify = subparsers.add_parser("verify")
    verify.add_argument("package", type=Path)
    extract = subparsers.add_parser("extract")
    extract.add_argument("archive", type=Path)
    extract.add_argument("--out", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        match arguments.command:
            case "create":
                result = create_export(ExportRequest(arguments.source_root, arguments.build_root, arguments.out, arguments.board))
                print(json.dumps({**asdict(result), "archive": str(result.archive)}, indent=2))
            case "verify":
                print(json.dumps(asdict(verify_export(arguments.package)), indent=2))
            case "extract":
                print(json.dumps(asdict(extract_export(arguments.archive, arguments.out)), indent=2))
            case _ as unreachable:
                assert_never(unreachable)
    except (BoardError, ExportError, OSError, json.JSONDecodeError, tarfile.TarError) as error:
        print(f"GEMMINI_EXPORT_FAILED: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
