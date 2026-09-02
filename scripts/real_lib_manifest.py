"""Manifest primitives for content-addressed IM2P real-library caches."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import uuid
from pathlib import Path, PurePosixPath
from typing import TypedDict

SCHEMA = "im2p-real-lib-cache-v3"
MAX_MANIFEST_BYTES = 64 * 1024
MAX_ARTIFACT_BYTES = 2 * 1024 * 1024 * 1024
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
IDENTITY_RE = re.compile(r"^a(?P<activation>4|8|16)-w(?P<weight>4|8|16)-d(?P<dim>16|32|64)$")


class CacheError(Exception):
    """Raised when a cache contract cannot be satisfied."""


class ArtifactRow(TypedDict):
    path: str
    sha256: str
    size: int


class ToolIdentity(TypedDict):
    command: str
    executable: str
    executable_sha256: str
    version: str
    inputs: list[ArtifactRow]


class IdentityData(TypedDict):
    id: str
    activation_bits: int
    weight_bits: int
    dim: int
    block_size: int
    platform: str
    platform_release: str
    arch: str


BuildConfig = dict[str, str]
ManifestValue = str | int | bool | list[ArtifactRow] | IdentityData | BuildConfig | dict[str, ToolIdentity]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(
    path: Path,
    value: dict[str, ManifestValue],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    )
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, sort_keys=True, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def artifact_rows(
    root: Path,
    relatives: tuple[Path, ...],
    prefix: Path = Path(),
) -> list[ArtifactRow]:
    rows: list[ArtifactRow] = []
    for relative in relatives:
        source = root / relative
        try:
            source_stat = source.lstat()
        except FileNotFoundError as error:
            raise CacheError(f"artifact is missing: {source}") from error
        if not stat.S_ISREG(source_stat.st_mode):
            raise CacheError(f"artifact is not a regular file: {source}")
        rows.append(
            {
                "path": (prefix / relative).as_posix(),
                "sha256": sha256(source),
                "size": source_stat.st_size,
            }
        )
    return rows


def _load_manifest(path: Path):
    path_stat = path.lstat()
    if not stat.S_ISREG(path_stat.st_mode):
        raise CacheError("manifest is not a regular file")
    if path_stat.st_size > MAX_MANIFEST_BYTES:
        raise CacheError("manifest exceeds size limit")
    return json.loads(path.read_text(encoding="utf-8"))


def _safe_relative(value: str) -> bool:
    candidate = PurePosixPath(value)
    return (
        value == candidate.as_posix()
        and not candidate.is_absolute()
        and len(value) <= 256
        and all(part not in ("", ".", "..") for part in candidate.parts)
    )


def confined_regular_file(root: Path, relative: str) -> Path:
    candidate = PurePosixPath(relative)
    current = root
    root_stat = current.lstat()
    if not stat.S_ISDIR(root_stat.st_mode):
        raise CacheError("artifact root is not a private directory")
    for part in candidate.parts[:-1]:
        current /= part
        current_stat = current.lstat()
        if not stat.S_ISDIR(current_stat.st_mode):
            raise CacheError(f"artifact ancestor is not a directory: {relative}")
    artifact = current / candidate.name
    artifact_stat = artifact.lstat()
    if not stat.S_ISREG(artifact_stat.st_mode):
        raise CacheError(f"artifact is not a regular file: {relative}")
    return artifact


def verify_manifest(
    path: Path,
    *,
    expected_fingerprint: str | None = None,
    expected_identity_data: IdentityData | None = None,
    expected_toolchains: dict[str, ToolIdentity] | None = None,
    expected_build_config: BuildConfig | None = None,
    expected_artifacts: tuple[str, ...] | None = None,
    expected_identity: str | None = None,
    expected_block_size: int | None = None,
    expected_platform: str | None = None,
    expected_platform_release: str | None = None,
    expected_arch: str | None = None,
    verified_rows: list[ArtifactRow] | None = None,
) -> tuple[bool, str]:
    try:
        manifest = _load_manifest(path)
        if not isinstance(manifest, dict) or manifest.get("schema") != SCHEMA:
            return False, "schema"
        fingerprint = manifest.get("fingerprint")
        if not isinstance(fingerprint, str) or not SHA256_RE.fullmatch(fingerprint):
            return False, "fingerprint"
        if expected_fingerprint is not None and fingerprint != expected_fingerprint:
            return False, "fingerprint"
        identity = manifest.get("identity")
        if not isinstance(identity, dict):
            return False, "identity"
        identity_id = identity.get("id")
        match = IDENTITY_RE.fullmatch(identity_id) if isinstance(identity_id, str) else None
        if match is None:
            return False, "identity.id"
        derived = {
            "activation_bits": int(match.group("activation")),
            "weight_bits": int(match.group("weight")),
            "dim": int(match.group("dim")),
        }
        if any(identity.get(field) != value for field, value in derived.items()):
            return False, "identity fields"
        if expected_identity_data is not None and identity != expected_identity_data:
            return False, "identity"
        expected = (
            ("id", expected_identity),
            ("block_size", expected_block_size),
            ("platform", expected_platform),
            ("platform_release", expected_platform_release),
            ("arch", expected_arch),
        )
        for field, value in expected:
            if value is not None and identity.get(field) != value:
                return False, field
        toolchains = manifest.get("toolchains")
        if not isinstance(toolchains, dict):
            return False, "toolchains"
        if expected_toolchains is not None and toolchains != expected_toolchains:
            return False, "toolchains"
        build_config = manifest.get("build_config")
        if not isinstance(build_config, dict):
            return False, "build_config"
        if expected_build_config is not None and build_config != expected_build_config:
            return False, "build_config"
        if manifest.get("artifact_root") != ".":
            return False, "artifact_root"
        artifact_root = path.parent
        rows = manifest.get("artifacts")
        if not isinstance(rows, list) or not rows or len(rows) > 16:
            return False, "artifacts"
        observed: list[str] = []
        validated_rows: list[ArtifactRow] = []
        total_size = 0
        for row in rows:
            if not isinstance(row, dict):
                return False, "artifact row"
            relative = row.get("path")
            size = row.get("size")
            digest = row.get("sha256")
            if (
                not isinstance(relative, str)
                or not isinstance(size, int)
                or not isinstance(digest, str)
                or not _safe_relative(relative)
                or size < 0
                or size > MAX_ARTIFACT_BYTES
                or not SHA256_RE.fullmatch(digest)
            ):
                return False, "artifact fields"
            if relative in observed:
                return False, "duplicate artifact"
            observed.append(relative)
            total_size += size
            if total_size > 2 * MAX_ARTIFACT_BYTES:
                return False, "artifact total size"
            artifact = confined_regular_file(artifact_root, relative)
            artifact_stat = artifact.lstat()
            if artifact_stat.st_size != size:
                return False, relative
            if sha256(artifact) != digest:
                return False, relative
            validated_rows.append(
                {"path": relative, "sha256": digest, "size": size}
            )
        if expected_artifacts is not None and set(observed) != set(expected_artifacts):
            return False, "artifact set"
    except (OSError, ValueError, TypeError, CacheError, json.JSONDecodeError) as error:
        return False, str(error)
    if verified_rows is not None:
        verified_rows.extend(validated_rows)
    return True, "ok"


def read_manifest_summary(path: Path) -> tuple[str, list[ArtifactRow]]:
    manifest = _load_manifest(path)
    if not isinstance(manifest, dict):
        raise CacheError("manifest root must be an object")
    fingerprint = manifest.get("fingerprint")
    rows = manifest.get("artifacts")
    if not isinstance(fingerprint, str) or not isinstance(rows, list):
        raise CacheError("manifest summary fields are malformed")
    artifacts: list[ArtifactRow] = []
    for row in rows:
        if not isinstance(row, dict):
            raise CacheError("manifest artifact row is malformed")
        relative = row.get("path")
        digest = row.get("sha256")
        size = row.get("size")
        if (
            not isinstance(relative, str)
            or not isinstance(digest, str)
            or not isinstance(size, int)
        ):
            raise CacheError("manifest artifact fields are malformed")
        artifacts.append(
            {"path": relative, "sha256": digest, "size": size}
        )
    return fingerprint, artifacts
