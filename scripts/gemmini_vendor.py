#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///

# ─── How to run ───
# 1. Install uv (if not installed):
#      curl -LsSf https://astral.sh/uv/install.sh | sh
# 2. Run directly (no venv, no pip install needed):
#      uv run scripts/gemmini_vendor.py [--source CHIPYARD] [--destination DIR] [--verify]
# 3. Or make executable and run:
#      chmod +x scripts/gemmini_vendor.py && ./scripts/gemmini_vendor.py --verify
# ─────────────────

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final, NewType, TypeAlias, TypedDict, final

if __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.im2p_paths import resolve_gemmini_work_root

CommitSha = NewType("CommitSha", str)
Artifacts: TypeAlias = dict[str, bytes]

ROOT: Final = Path(__file__).resolve().parents[1]
CHIPYARD_PIN: Final = CommitSha("69eba860a352343e4ac6b6df0f3638a79a86ec78")
GEMMINI_PIN: Final = CommitSha("25809f78323a729ef76fb68f3cedd8a24da2942b")
CHIPYARD_REPOSITORY: Final = "https://github.com/ucb-bar/chipyard.git"
GEMMINI_REPOSITORY: Final = "https://github.com/ucb-bar/gemmini.git"
GEMMINI_GITLINK: Final = "generators/gemmini"
CHIPYARD_BUILD_SUBMODULES: Final = (
    GEMMINI_GITLINK,
    "generators/rocket-chip",
    "generators/hardfloat",
    "generators/diplomacy",
    "tools/cde",
    "sims/firesim",
)
USAGE: Final = "usage: gemmini_vendor.py [--source CHIPYARD] [--destination DIR] [--verify] [--overlay NEW_DIR]"
PATCH_PATH: Final = "patches/0001-packed-input-controller-bytes.patch"
RESET_PATCH_PATH: Final = "patches/0002-loop-head-reset.patch"
PATCHES: Final = (
    (PATCH_PATH, "packed input bit-to-byte accounting"),
    (RESET_PATCH_PATH, "deterministic LoopMatmul head reset"),
)
OVERLAY_NAMES: Final = ("GemminiConfigs.scala", "LoadController.scala", "LoopMatmul.scala", "StoreController.scala")


@dataclass(frozen=True, slots=True)
class FileSpec:
    upstream_path: str
    snapshot_path: str
    compile_include: bool
    compile_reason: str
    direct_dependencies: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class VendorRequest:
    source: Path
    destination: Path
    verify: bool
    overlay: Path | None = None


@final
class VendorError(RuntimeError):
    __slots__ = ("message",)

    message: str

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class RepositoryLock(TypedDict):
    repository: str
    commit: str


class GemminiLock(RepositoryLock):
    chipyard_gitlink_path: str
    chipyard_gitlink: str


class UpstreamLock(TypedDict):
    schema_version: int
    chipyard: RepositoryLock
    gemmini: GemminiLock
    extraction: str


class VendorEntry(TypedDict):
    upstream_repository: str
    upstream_commit: str
    upstream_path: str
    git_blob_sha: str
    snapshot_path: str
    snapshot_sha256: str
    extraction: str
    selected_symbols: list[str]
    patches: list[str]
    patch_reason: str
    compile_include: bool
    compile_overlay: bool
    compile_reason: str
    direct_dependencies: list[str]


class PatchRecord(TypedDict):
    path: str
    sha256: str
    reason: str


class VendorManifest(TypedDict):
    schema_version: int
    patch_sha256: str
    patches: list[PatchRecord]
    entries: list[VendorEntry]


SOURCE_PREFIX: Final = "src/main/scala/gemmini/"
FILE_SPECS: Final = (
    FileSpec("LICENSE", "LICENSE.upstream", False, "license provenance only"),
    FileSpec(f"{SOURCE_PREFIX}Activation.scala", f"upstream/{SOURCE_PREFIX}Activation.scala", True, "AccumulatorMem dependency"),
    FileSpec(f"{SOURCE_PREFIX}AccumulatorMem.scala", f"upstream/{SOURCE_PREFIX}AccumulatorMem.scala", True, "standalone accumulator SRAM/RMW", ("Activation.scala", "Arithmetic.scala", "SharedExtMem.scala", "SyncMem.scala", "Util.scala")),
    FileSpec(f"{SOURCE_PREFIX}Arithmetic.scala", f"upstream/{SOURCE_PREFIX}Arithmetic.scala", True, "PE and accumulator arithmetic typeclass"),
    FileSpec(f"{SOURCE_PREFIX}Dataflow.scala", f"upstream/{SOURCE_PREFIX}Dataflow.scala", True, "PE control dependency"),
    *(FileSpec(f"{SOURCE_PREFIX}{name}", f"upstream/{SOURCE_PREFIX}{name}", False, "compile patched copy through upstreamGemmini source overlay") for name in OVERLAY_NAMES[:3]),
    FileSpec(f"{SOURCE_PREFIX}Mesh.scala", f"upstream/{SOURCE_PREFIX}Mesh.scala", True, "standalone systolic mesh", ("Arithmetic.scala", "PE.scala", "Tile.scala")),
    FileSpec(f"{SOURCE_PREFIX}MeshWithDelays.scala", f"upstream/{SOURCE_PREFIX}MeshWithDelays.scala", True, "WS array timing and tags", ("Arithmetic.scala", "Dataflow.scala", "Mesh.scala", "PE.scala", "Shifter.scala", "TagQueue.scala", "Transposer.scala", "Util.scala")),
    FileSpec(f"{SOURCE_PREFIX}PE.scala", f"upstream/{SOURCE_PREFIX}PE.scala", True, "standalone processing element", ("Arithmetic.scala", "Dataflow.scala")),
    FileSpec(f"{SOURCE_PREFIX}Scratchpad.scala", f"upstream/{SOURCE_PREFIX}Scratchpad.scala", False, "full file couples ScratchpadBank to Rocket/TL DMA; extract selected symbols before compile", ("SharedExtMem.scala",)),
    FileSpec(f"{SOURCE_PREFIX}SharedExtMem.scala", f"upstream/{SOURCE_PREFIX}SharedExtMem.scala", True, "ExtMemIO used by original memory banks", ("Util.scala",)),
    FileSpec(f"{SOURCE_PREFIX}Shifter.scala", f"upstream/{SOURCE_PREFIX}Shifter.scala", True, "MeshWithDelays skew dependency", ("SyncMem.scala", "Util.scala")),
    FileSpec(f"{SOURCE_PREFIX}StoreController.scala", f"upstream/{SOURCE_PREFIX}StoreController.scala", False, "compile patched copy through upstreamGemmini source overlay"),
    FileSpec(f"{SOURCE_PREFIX}SyncMem.scala", f"upstream/{SOURCE_PREFIX}SyncMem.scala", True, "AccumulatorMem SRAM dependency"),
    FileSpec(f"{SOURCE_PREFIX}TagQueue.scala", f"upstream/{SOURCE_PREFIX}TagQueue.scala", True, "MeshWithDelays tag lifetime", ("Util.scala",)),
    FileSpec(f"{SOURCE_PREFIX}Tile.scala", f"upstream/{SOURCE_PREFIX}Tile.scala", True, "standalone PE tile", ("Arithmetic.scala", "PE.scala", "Util.scala")),
    FileSpec(f"{SOURCE_PREFIX}Transposer.scala", f"upstream/{SOURCE_PREFIX}Transposer.scala", True, "MeshWithDelays transpose dependency", ("Util.scala",)),
    FileSpec(f"{SOURCE_PREFIX}Util.scala", f"upstream/{SOURCE_PREFIX}Util.scala", True, "shared queue, wrap, and reduction helpers"),
)


def run_git(repository: Path, arguments: list[str]) -> bytes:
    try:
        result = subprocess.run(
            ["git", "-C", str(repository), *arguments],
            check=False,
            capture_output=True,
        )
    except FileNotFoundError as error:
        raise VendorError("git executable unavailable") from error
    if result.returncode != 0:
        detail = result.stderr.decode(errors="replace").strip()
        raise VendorError(f"git failed in {repository}: {detail}")
    return result.stdout


def git_text(repository: Path, arguments: list[str]) -> str:
    return run_git(repository, arguments).decode().strip()


def bootstrap_source(source: Path) -> None:
    """Materialize the pinned Chipyard checkout only when it is absent."""
    if source.exists() or source.is_symlink():
        return
    source.parent.mkdir(parents=True, exist_ok=True)
    print(f"Gemmini dependencies: bootstrapping pinned Chipyard at {source}", file=sys.stderr)
    temporary = source.parent / f".{source.name}.bootstrap-{os.getpid()}"
    if temporary.exists() or temporary.is_symlink():
        raise VendorError(f"bootstrap staging path already exists: {temporary}")
    try:
        temporary.mkdir()
        run_git(temporary, ["init", "--quiet"])
        run_git(temporary, ["remote", "add", "origin", CHIPYARD_REPOSITORY])
        run_git(temporary, ["fetch", "--depth", "1", "origin", CHIPYARD_PIN])
        run_git(temporary, ["checkout", "--quiet", "--detach", "FETCH_HEAD"])
        run_git(temporary, [
            "submodule", "update", "--init", "--depth", "1", *CHIPYARD_BUILD_SUBMODULES,
        ])
        if git_text(temporary, ["rev-parse", "HEAD"]) != CHIPYARD_PIN:
            raise VendorError("bootstrapped Chipyard checkout has unexpected HEAD")
        gemmini = temporary / GEMMINI_GITLINK
        if git_text(gemmini, ["rev-parse", "HEAD"]) != GEMMINI_PIN:
            raise VendorError("bootstrapped Gemmini checkout has unexpected HEAD")
        if source.exists():
            shutil.rmtree(temporary)
        else:
            try:
                temporary.rename(source)
            except OSError:
                if source.is_dir():
                    shutil.rmtree(temporary)
                else:
                    raise
    except Exception:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise


def verify_source(source: Path) -> Path:
    gemmini = source / GEMMINI_GITLINK
    chipyard_head = git_text(source, ["rev-parse", "HEAD"])
    if chipyard_head != CHIPYARD_PIN:
        raise VendorError(f"Chipyard HEAD mismatch: expected {CHIPYARD_PIN}, got {chipyard_head}")
    gitlink = git_text(source, ["rev-parse", f"{CHIPYARD_PIN}:{GEMMINI_GITLINK}"])
    if gitlink != GEMMINI_PIN:
        raise VendorError(f"Gemmini gitlink mismatch: expected {GEMMINI_PIN}, got {gitlink}")
    gemmini_head = git_text(gemmini, ["rev-parse", "HEAD"])
    if gemmini_head != GEMMINI_PIN:
        raise VendorError(f"Gemmini HEAD mismatch: expected {GEMMINI_PIN}, got {gemmini_head}")
    if git_text(gemmini, ["status", "--porcelain=v1", "--untracked-files=all"]):
        raise VendorError("Gemmini checkout is dirty")
    if git_text(source, ["status", "--porcelain=v1", "--untracked-files=all"]):
        raise VendorError("Chipyard checkout is dirty")
    return gemmini


def render_artifacts(gemmini: Path) -> Artifacts:
    entries: list[VendorEntry] = []
    artifacts: Artifacts = {path: (ROOT / "src/gemmini" / path).read_bytes() for path, _ in PATCHES}
    for spec in FILE_SPECS:
        content = run_git(gemmini, ["show", f"{GEMMINI_PIN}:{spec.upstream_path}"])
        blob = git_text(gemmini, ["rev-parse", f"{GEMMINI_PIN}:{spec.upstream_path}"])
        artifacts[spec.snapshot_path] = content
        entries.append(
            VendorEntry(
                upstream_repository=GEMMINI_REPOSITORY,
                upstream_commit=GEMMINI_PIN,
                upstream_path=spec.upstream_path,
                git_blob_sha=blob,
                snapshot_path=spec.snapshot_path,
                snapshot_sha256=hashlib.sha256(content).hexdigest(),
                extraction="full_file",
                selected_symbols=["ScratchpadBank"] if spec.upstream_path.endswith("/Scratchpad.scala") else [],
                patches=([PATCH_PATH, RESET_PATCH_PATH] if spec.upstream_path.endswith("/LoopMatmul.scala")
                         else [PATCH_PATH] if Path(spec.upstream_path).name in OVERLAY_NAMES else []),
                patch_reason=("packed input accounting and deterministic head reset; immutable snapshot"
                              if spec.upstream_path.endswith("/LoopMatmul.scala") else
                              "packed input bit-to-byte accounting; snapshot remains immutable"
                              if Path(spec.upstream_path).name in OVERLAY_NAMES else "none; immutable upstream snapshot"),
                compile_include=spec.compile_include,
                compile_overlay=Path(spec.upstream_path).name in OVERLAY_NAMES,
                compile_reason=spec.compile_reason,
                direct_dependencies=list(spec.direct_dependencies),
            )
        )
    lock = UpstreamLock(
        schema_version=1,
        chipyard=RepositoryLock(repository=CHIPYARD_REPOSITORY, commit=CHIPYARD_PIN),
        gemmini=GemminiLock(
            repository=GEMMINI_REPOSITORY,
            commit=GEMMINI_PIN,
            chipyard_gitlink_path=GEMMINI_GITLINK,
            chipyard_gitlink=GEMMINI_PIN,
        ),
        extraction="git show <commit>:<path>; no dependency checkout writes",
    )
    manifest = VendorManifest(
        schema_version=1, entries=entries,
        patch_sha256=hashlib.sha256(artifacts[PATCH_PATH]).hexdigest(),
        patches=[PatchRecord(path=path, sha256=hashlib.sha256(artifacts[path]).hexdigest(), reason=reason)
                 for path, reason in PATCHES],
    )
    artifacts["UPSTREAM.lock.json"] = encode_json(lock)
    artifacts["vendor-manifest.json"] = encode_json(manifest)
    artifacts["README.md"] = f"""# Pinned Gemmini sources

Immutable provenance snapshots from Gemmini `{GEMMINI_PIN}`, selected by Chipyard `{CHIPYARD_PIN}`.
Generate with `uv run scripts/gemmini_vendor.py`; verify with `uv run scripts/gemmini_vendor.py --verify`.

`vendor-manifest.json` records each upstream path, Git blob, snapshot SHA256, dependencies, patches, and compile inclusion. Snapshots are immutable. Run `uv run scripts/gemmini_vendor.py --overlay NEW_DIR` to apply the ordered packed-input and loop-reset patches to separate copies. `scripts/gemmini_build.py` supplies `-Dim2p.gemmini.overlay=NEW_DIR` to the single `build.sbt`; it replaces exactly four imported Gemmini sources. The build declares `scuCore` (no upstream or host dependency), `gemminiIntegration`, integrated standalone `root`, and test-only lower-level `diagnostics` source sets (no alternative standalone top). `control/build.sbt` is no longer a separate build. Full `Scratchpad.scala` is provenance-only because its SoC wrapper pulls Rocket/TL DMA; standalone integration must extract the listed `ScratchpadBank` boundary without compiling both copies.
""".encode()
    return artifacts


def materialize_overlay(gemmini: Path, destination: Path) -> None:
    if destination.exists() or destination.is_symlink():
        raise VendorError(f"overlay already exists: {destination}")
    if destination.resolve().is_relative_to(gemmini.parent.parent.resolve()):
        raise VendorError("overlay must be outside the Chipyard dependency")
    destination.mkdir(parents=True)
    for name in OVERLAY_NAMES:
        relative = f"{SOURCE_PREFIX}{name}"
        path = destination / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        _ = path.write_bytes(run_git(gemmini, ["show", f"{GEMMINI_PIN}:{relative}"]))
    for relative, _reason in PATCHES:
        patch = str(ROOT / "src/gemmini" / relative)
        _ = run_git(destination, ["apply", "--check", patch])
        _ = run_git(destination, ["apply", patch])


def encode_json(value: UpstreamLock | VendorManifest) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def materialize(request: VendorRequest, artifacts: Artifacts) -> None:
    mismatches = [
        relative
        for relative, expected in artifacts.items()
        if (request.destination / relative).exists()
        and (not (request.destination / relative).is_file() or (request.destination / relative).read_bytes() != expected)
    ]
    missing = [relative for relative in artifacts if not (request.destination / relative).is_file()]
    if mismatches:
        raise VendorError(f"vendor snapshot mismatch: {', '.join(mismatches)}")
    if request.verify and missing:
        raise VendorError(f"vendor snapshot missing: {', '.join(missing)}")
    if request.verify:
        return
    for relative in missing:
        path = request.destination / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        _ = path.write_bytes(artifacts[relative])


def parse_arguments(arguments: list[str]) -> VendorRequest | None:
    work_root = resolve_gemmini_work_root(ROOT)
    source = work_root / "deps/chipyard-1.13.0"
    destination = ROOT / "src/gemmini"
    verify = False
    overlay: Path | None = None
    index = 0
    while index < len(arguments):
        token = arguments[index]
        if token in ("--help", "-h"):
            return None
        if token == "--verify":
            verify = True
            index += 1
            continue
        if token not in ("--source", "--destination", "--overlay"):
            raise VendorError(f"unknown argument: {token}")
        if index + 1 >= len(arguments):
            raise VendorError(f"missing value for {token}")
        value = Path(arguments[index + 1]).expanduser()
        if token == "--overlay":
            overlay = value
        else:
            source, destination = (value, destination) if token == "--source" else (source, value)
        index += 2
    if verify and overlay is not None:
        raise VendorError("--verify and --overlay cannot be combined")
    return VendorRequest(source=source, destination=destination, verify=verify, overlay=overlay)


def main() -> int:
    try:
        outcome = parse_arguments(sys.argv[1:])
        if outcome is None:
            print(USAGE)
            return 0
        bootstrap_source(outcome.source)
        gemmini = verify_source(outcome.source)
        if outcome.overlay is not None:
            materialize_overlay(gemmini, outcome.overlay)
            print(f"Gemmini overlay: {outcome.overlay}")
            return 0
        materialize(outcome, render_artifacts(gemmini))
        action = "verified" if outcome.verify else "vendored"
        print(f"Gemmini {action}: {outcome.destination}")
        return 0
    except VendorError as error:
        print(error, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
