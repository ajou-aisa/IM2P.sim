"""Coherent selected-generation materialization for real-library caches."""

from __future__ import annotations

import os
import shutil
import stat
import uuid
from pathlib import Path

from scripts.real_lib_manifest import (
    SCHEMA,
    ArtifactRow,
    BuildConfig,
    CacheError,
    IdentityData,
    ToolIdentity,
    artifact_rows,
    atomic_json,
    confined_regular_file,
    sha256,
    verify_manifest,
)

SELECTED_ARTIFACTS = (
    "libim2p_gemmini_frontend.a",
    "libim2p_sim.a",
)


def selected_manifest_path(build_dir: Path, identity: str) -> Path:
    return build_dir / "selected" / identity / "current" / "real-lib.json"


def secure_directory(root: Path, relative: Path) -> Path:
    current = root
    for part in relative.parts:
        current /= part
        try:
            current_stat = current.lstat()
        except FileNotFoundError:
            current.mkdir()
            continue
        if not stat.S_ISDIR(current_stat.st_mode):
            raise CacheError(f"private cache path is not a directory: {current}")
    return current


def _copy_verified(
    source_root: Path,
    relative: Path,
    destination: Path,
    expected: ArtifactRow,
) -> None:
    source = confined_regular_file(source_root, relative.as_posix())
    with source.open("rb") as input_stream, destination.open("xb") as output_stream:
        shutil.copyfileobj(input_stream, output_stream)
        output_stream.flush()
        os.fsync(output_stream.fileno())
    if (
        destination.stat().st_size != expected["size"]
        or sha256(destination) != expected["sha256"]
    ):
        raise CacheError(f"cache artifact changed during restore: {relative}")


def _failpoint(name: str) -> None:
    if os.environ.get("IM2P_CACHE_FAILPOINT") == name:
        raise CacheError(f"injected failure: {name}")


def materialize(
    entry: Path,
    build_dir: Path,
    identity_data: IdentityData,
    tools: dict[str, ToolIdentity],
    build_config: BuildConfig,
    fingerprint: str,
    cache_artifacts: tuple[Path, ...],
    cache_rows: list[ArtifactRow],
) -> Path:
    identity = identity_data["id"]
    selected = secure_directory(build_dir, Path("selected") / identity)
    generations = secure_directory(
        build_dir, Path("selected") / identity / "generations"
    )
    current = selected / "current"
    if os.path.lexists(current):
        if not current.is_symlink():
            raise CacheError(f"selected generation pointer is not a symlink: {current}")
        resolved = current.resolve(strict=True)
        try:
            resolved.relative_to(generations)
        except ValueError as error:
            raise CacheError("selected generation pointer escapes its root") from error
        manifest = resolved / "real-lib.json"
        valid, _ = verify_manifest(
            manifest,
            expected_fingerprint=fingerprint,
            expected_identity_data=identity_data,
            expected_toolchains=tools,
            expected_build_config=build_config,
            expected_artifacts=SELECTED_ARTIFACTS,
        )
        if valid:
            return current / "real-lib.json"

    token = f"{fingerprint}.{uuid.uuid4().hex}"
    temporary = generations / f".{token}.tmp"
    generation = generations / token
    pointer = selected / f".current.{uuid.uuid4().hex}.tmp"
    switched = False
    try:
        temporary.mkdir()
        expected_rows = {str(row["path"]): row for row in cache_rows}
        for index, (relative, name) in enumerate(
            zip(cache_artifacts, SELECTED_ARTIFACTS, strict=True)
        ):
            cache_path = (Path("artifacts") / relative).as_posix()
            expected = expected_rows.get(cache_path)
            if expected is None:
                raise CacheError(f"verified cache row is missing: {cache_path}")
            _copy_verified(
                entry / "artifacts", relative, temporary / name, expected
            )
            if index == 0:
                _failpoint("materialize-after-first-artifact")
        manifest_data = {
            "schema": SCHEMA,
            "fingerprint": fingerprint,
            "identity": identity_data,
            "toolchains": tools,
            "build_config": build_config,
            "artifact_root": ".",
            "artifacts": artifact_rows(
                temporary, tuple(Path(name) for name in SELECTED_ARTIFACTS)
            ),
            "cache_manifest": str((entry / "real-lib.json").resolve()),
        }
        atomic_json(temporary / "real-lib.json", manifest_data)
        valid, detail = verify_manifest(
            temporary / "real-lib.json",
            expected_fingerprint=fingerprint,
            expected_identity_data=identity_data,
            expected_toolchains=tools,
            expected_build_config=build_config,
            expected_artifacts=SELECTED_ARTIFACTS,
        )
        if not valid:
            raise CacheError(f"selected generation verification failed: {detail}")
        os.replace(temporary, generation)
        for name in (*SELECTED_ARTIFACTS, "real-lib.json"):
            os.chmod(generation / name, 0o444)
        os.chmod(generation, 0o555)
        pointer.symlink_to(
            Path("generations") / generation.name, target_is_directory=True
        )
        _failpoint("materialize-before-switch")
        os.replace(pointer, current)
        switched = True
        return current / "real-lib.json"
    finally:
        if os.path.lexists(pointer):
            pointer.unlink()
        if temporary.exists():
            shutil.rmtree(temporary)
        if generation.exists() and not switched:
            os.chmod(generation, 0o755)
            for child in generation.iterdir():
                os.chmod(child, 0o644)
            shutil.rmtree(generation)
