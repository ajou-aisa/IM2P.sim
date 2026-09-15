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
import shutil
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Final, TypeAlias, assert_never

JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.gemmini_board import BoardError, load_board_manifest

SOURCE_SUFFIXES: Final = frozenset({
    ".c", ".cc", ".cmake", ".cpp", ".h", ".hpp", ".json", ".md", ".patch", ".properties",
    ".py", ".sbt", ".scala", ".sh", ".sv", ".svh", ".tcl", ".txt", ".v", ".vh", ".xdc",
})
RTL_SUFFIXES: Final = frozenset({".hex", ".mem", ".sv", ".svh", ".v", ".vh", ".vhd", ".vhdl"})
BUILD_METADATA: Final = frozenset({
    "filelist.f", "host-contract.json", "resolved-profile.json", "source-manifest.json",
    "tool-lock.json", "upstream-lock.json", "result.json", "host-audit.json", "final.json",
    "memory-probe.json", "dependency-lock.json",
})
EXCLUDED_PARTS: Final = frozenset({
    ".bloop", ".bsp", ".cache", ".git", ".metals", ".omo", ".omx", "Testing",
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
                  root / "frontend/include/im2p_gemmini_frontend.hpp",
                  root / "frontend/src/im2p_gemmini_frontend.cpp",
                  root / "sim/include/im2p_sim.h"]
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
        if path.suffix.lower() not in RTL_SUFFIXES and path.name not in BUILD_METADATA and not is_log and not is_host_param and not (
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


def dependency_snapshot(source_root: Path, output: Path) -> dict[str, JsonValue]:
    try:
        upstream = json.loads((source_root / "src/gemmini/UPSTREAM.lock.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"schema_version": 1, "status": "NOT_RUN", "reason": "UPSTREAM_LOCK_MISSING"}
    if not isinstance(upstream, dict) or not isinstance(upstream.get("chipyard"), dict) or \
       not isinstance(upstream.get("gemmini"), dict):
        return {"schema_version": 1, "status": "NOT_RUN", "reason": "UPSTREAM_LOCK_INCOMPLETE"}
    workspace = Path(os.environ.get("IM2P_WORKSPACE_ROOT", source_root.parent)).resolve()
    work_root = Path(os.environ.get(
        "IM2P_GEMMINI_WORK_ROOT", Path.home() / "aisa-lab/build/im2p-gemmini",
    )).resolve()
    chipyard = work_root / "deps/chipyard-1.13.0"
    llama = workspace / "llama.cpp-gemmini"
    include = workspace / "RISC-V-DynDNN-gemmini-include"
    repositories = {
        "chipyard": (chipyard, "69eba860a352343e4ac6b6df0f3638a79a86ec78"),
        "gemmini": (chipyard / "generators/gemmini", "25809f78323a729ef76fb68f3cedd8a24da2942b"),
        "llama_cpp_gemmini": (llama, "257753bf77500081844598a28352b56ab505bd9a"),
        "gemmini_include": (include, "cd2caed8d93e49d3f8873bd6c4e340fcad54b9e0"),
    }
    repository_rows: dict[str, dict[str, JsonValue]] = {}
    patch_root = output / "dependency"
    patch_root.mkdir(parents=True, exist_ok=True)
    for name, (repository, expected) in repositories.items():
        if not repository.is_dir():
            raise ExportError(f"dependency checkout missing: {repository}")
        head = git(repository, "rev-parse", "HEAD").strip()
        if head != expected:
            raise ExportError(f"dependency HEAD mismatch: {name}: {head}")
        diff = git(repository, "diff", "--binary")
        patch = patch_root / f"{name}.patch"
        patch.write_text(diff, encoding="utf-8")
        overlays: list[JsonValue] = []
        for relative_name in git(repository, "ls-files", "--others", "--exclude-standard").splitlines():
            relative = Path(relative_name)
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
        status: list[JsonValue] = list(git(repository, "status", "--short").splitlines())
        repository_rows[name] = {
            "head": head,
            "remote": git(repository, "remote", "get-url", "origin").strip(),
            "tracked_patch": patch.relative_to(output).as_posix(),
            "tracked_patch_sha256": digest(patch),
            "tracked_patch_bytes": patch.stat().st_size,
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
        "Provide a validated board manifest and XDC only for synth, route or bitstream stages.",
    ))
    return "\n".join(lines) + "\n"


def inventory(root: Path) -> dict[str, tuple[str, int]]:
    return {path.relative_to(root).as_posix(): (digest(path), path.stat().st_size)
            for path in sorted(root.rglob("*")) if path.is_file() and path.name != "SHA256SUMS"}


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
    for name in regular_file(root / "filelist.f").read_text(encoding="utf-8").splitlines():
        relative = safe_relative(name)
        regular_file(root / relative)
        if relative.suffix.lower() not in RTL_SUFFIXES:
            raise ExportError(f"invalid RTL filelist entry: {name}")
    result = json.loads(regular_file(root / "result.json").read_text(encoding="utf-8"))
    if not isinstance(result, dict) or result.get("export") != "PASS":
        raise ExportError("export result does not report PASS")
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
    profiles: list[JsonValue] = []
    if build_root is not None:
        for manifest in sorted(build_root.rglob("resolved-profile.json")):
            if EXCLUDED_PARTS.intersection(manifest.relative_to(build_root).parts):
                continue
            document = json.loads(regular_file(manifest).read_text(encoding="utf-8"))
            if not isinstance(document, dict) or not isinstance(document.get("profile"), str):
                raise ExportError(f"invalid resolved profile: {manifest}")
            rtl_count = sum(
                path.is_file() and not path.is_symlink() and path.suffix.lower() in RTL_SUFFIXES
                for path in manifest.parent.rglob("*")
            )
            relative_root = Path("generated") / manifest.parent.relative_to(build_root)
            profiles.append({
                "profile": document["profile"], "status": "PASS" if rtl_count else "FAIL",
                "generated_rtl": rtl_count, "root": relative_root.as_posix(),
            })
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
    (output / "filelist.f").write_text("".join(f"{name}\n" for name in rtl), encoding="utf-8")
    (output / "source-manifest.json").write_text(json.dumps({"schema_version": 1, "files": rows}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    reason = "NOT_RUN_BY_EXPORT" if board is not None else "BOARD_REQUIRED"
    write_json(output / "result.json", {"schema_version": 1, "export": "PASS", "route": "NOT_RUN",
        "bitstream": "NOT_RUN", "physical": "NOT_RUN", "reason": reason,
        "generated_rtl": len(rtl), "board_included": board is not None, "profiles": profiles})
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
