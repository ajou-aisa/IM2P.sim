#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
# noqa: SIZE_OK - one profile-schema trust boundary; splitting duplicates invariants

# ─── How to run ───
# 1. Install uv (if not installed):
#      curl -LsSf https://astral.sh/uv/install.sh | sh
# 2. Run directly (no venv, no pip install needed):
#      uv run scripts/gemmini_resolve_profile.py --help
# 3. Or make executable and run:
#      chmod +x scripts/gemmini_resolve_profile.py && ./scripts/gemmini_resolve_profile.py --help
# ─────────────────

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from enum import StrEnum, unique
from hashlib import sha256
from pathlib import Path
from typing import Final, Mapping, Sequence, TypeAlias

ROOT: Final = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG: Final = ROOT / "config" / "gemmini_hp1_profiles.json"
JsonValue: TypeAlias = (
    str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
)


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
        if self.activation_bits != self.weight_bits:
            raise BuildFailure(FailureReason.VALIDATION, "activation and weight widths differ")
        if self.dim not in (16, 32, 64):
            raise BuildFailure(FailureReason.VALIDATION, "DIM outside 16/32/64")

    @property
    def name(self) -> str:
        return f"a{self.activation_bits}w{self.weight_bits}-d{self.dim}-hp1"


@dataclass(frozen=True, slots=True)
class ProfileSpec:
    profile: str
    activation_bits: int
    weight_bits: int
    dim: int
    packing: str


@dataclass(frozen=True, slots=True)
class Catalog:
    implementation: str
    numerical_revision: str
    block_size: int
    accumulator_bits: int
    bank_count: int
    scratchpad_total_bytes: int
    accumulator_total_bytes: int
    memory_policy: str
    profiles: tuple[ProfileSpec, ...]


@dataclass(frozen=True, slots=True)
class MemoryContract:
    profile: str
    activation_bits: int
    weight_bits: int
    dim: int
    accumulator_bits: int
    bank_count: int
    bank_rows: int
    accumulator_rows: int
    scratchpad_row_bytes: int
    accumulator_row_bytes: int
    scratchpad_total_bytes: int
    accumulator_total_bytes: int
    ws_double_buffered: bool
    ws_scratchpad_rows_per_buffer: int
    ws_accumulator_rows_per_buffer: int
    provenance_kind: str
    provenance_policy: str
    provenance_reference: str
    tile_policy: str


@dataclass(frozen=True, slots=True)
class ResolvedProfile:
    selection: ProfileSelection
    spec: ProfileSpec
    catalog: Catalog
    memory: MemoryContract
    catalog_path: Path
    contract_path: Path

    def to_document(self) -> Mapping[str, JsonValue]:
        memory: dict[str, JsonValue] = {
            "bank_count": self.memory.bank_count,
            "bank_rows": self.memory.bank_rows,
            "accumulator_rows": self.memory.accumulator_rows,
            "scratchpad_row_bytes": self.memory.scratchpad_row_bytes,
            "accumulator_row_bytes": self.memory.accumulator_row_bytes,
            "scratchpad_total_bytes": self.memory.scratchpad_total_bytes,
            "accumulator_total_bytes": self.memory.accumulator_total_bytes,
            "ws_double_buffered": self.memory.ws_double_buffered,
            "ws_scratchpad_rows_per_buffer": self.memory.ws_scratchpad_rows_per_buffer,
            "ws_accumulator_rows_per_buffer": self.memory.ws_accumulator_rows_per_buffer,
            "capacity_policy": self.catalog.memory_policy,
            "provenance_kind": self.memory.provenance_kind,
            "provenance_reference": self.memory.provenance_reference,
            "tile_policy": self.memory.tile_policy,
        }
        return {
            "schema_version": 1,
            "profile": self.selection.name,
            "implementation": self.catalog.implementation,
            "activation_bits": self.selection.activation_bits,
            "weight_bits": self.selection.weight_bits,
            "dim": self.selection.dim,
            "accumulator_bits": self.catalog.accumulator_bits,
            "array_partial_bits": self.selection.activation_bits
            + self.selection.weight_bits
            + (self.selection.dim.bit_length() - 1),
            "block_size": self.catalog.block_size,
            "fragment_limit": min(self.selection.dim, self.catalog.block_size),
            "scu": self.selection.scu.value,
            "packing": self.spec.packing,
            "numerical_revision": self.catalog.numerical_revision,
            "memory": memory,
            "host_contract_source": _portable_path(self.contract_path),
            "host_contract_sha256": _file_sha256(self.contract_path),
            "profile_catalog_source": _portable_path(self.catalog_path),
            "profile_catalog_sha256": _file_sha256(self.catalog_path),
        }


def _portable_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(ROOT).as_posix()
    except ValueError:
        return str(resolved)


def _file_sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _read_mapping(path: Path) -> Mapping[str, JsonValue]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise BuildFailure(FailureReason.IO, f"cannot read JSON: {path}: {error}") from error
    match value:  # noqa: MATCH_OK - untrusted JSON needs typed validation failure
        case dict():
            return value
        case unreachable:
            raise BuildFailure(FailureReason.VALIDATION, f"JSON root is not an object: {path}")


def _mapping(document: Mapping[str, JsonValue], key: str) -> Mapping[str, JsonValue]:
    value = document.get(key)
    match value:  # noqa: MATCH_OK - untrusted JSON needs typed validation failure
        case dict():
            return value
        case unreachable:
            raise BuildFailure(FailureReason.VALIDATION, f"{key} is not an object")


def _integer(document: Mapping[str, JsonValue], key: str) -> int:
    value = document.get(key)
    match value:  # noqa: MATCH_OK - untrusted JSON needs typed validation failure
        case bool():
            raise BuildFailure(FailureReason.VALIDATION, f"{key} is not an integer")
        case int():
            return value
        case unreachable:
            raise BuildFailure(FailureReason.VALIDATION, f"{key} is not an integer")


def _string(document: Mapping[str, JsonValue], key: str) -> str:
    value = document.get(key)
    match value:  # noqa: MATCH_OK - untrusted JSON needs typed validation failure
        case str():
            return value
        case unreachable:
            raise BuildFailure(FailureReason.VALIDATION, f"{key} is not a string")


def _boolean(document: Mapping[str, JsonValue], key: str) -> bool:
    value = document.get(key)
    match value:  # noqa: MATCH_OK - untrusted JSON needs typed validation failure
        case bool():
            return value
        case unreachable:
            raise BuildFailure(FailureReason.VALIDATION, f"{key} is not a boolean")


def load_catalog(path: Path) -> Catalog:
    document = _read_mapping(path)
    policy = _mapping(document, "memory_policy")
    profile_values = document.get("profiles")
    match profile_values:  # noqa: MATCH_OK - untrusted JSON needs typed validation failure
        case list():
            profiles = tuple(
                ProfileSpec(
                    _string(_mapping({"entry": value}, "entry"), "profile"),
                    _integer(_mapping({"entry": value}, "entry"), "activation_bits"),
                    _integer(_mapping({"entry": value}, "entry"), "weight_bits"),
                    _integer(_mapping({"entry": value}, "entry"), "dim"),
                    _string(_mapping({"entry": value}, "entry"), "packing"),
                )
                for value in profile_values
            )
        case unreachable:
            raise BuildFailure(FailureReason.VALIDATION, "profiles is not an array")
    return Catalog(
        _string(document, "implementation"),
        _string(document, "numerical_revision"),
        _integer(document, "block_size"),
        _integer(document, "accumulator_bits"),
        _integer(policy, "bank_count"),
        _integer(policy, "scratchpad_total_bytes"),
        _integer(policy, "accumulator_total_bytes"),
        _string(policy, "name"),
        profiles,
    )


def load_memory_contract(path: Path) -> MemoryContract:
    document = _read_mapping(path)
    provenance = _mapping(document, "provenance")
    return MemoryContract(
        _string(document, "profile"), _integer(document, "activation_bits"),
        _integer(document, "weight_bits"), _integer(document, "dim"),
        _integer(document, "accumulator_bits"), _integer(document, "bank_count"),
        _integer(document, "bank_rows"), _integer(document, "accumulator_rows"),
        _integer(document, "scratchpad_row_bytes"), _integer(document, "accumulator_row_bytes"),
        _integer(document, "scratchpad_total_bytes"), _integer(document, "accumulator_total_bytes"),
        _boolean(document, "ws_double_buffered"),
        _integer(document, "ws_scratchpad_rows_per_buffer"),
        _integer(document, "ws_accumulator_rows_per_buffer"),
        _string(provenance, "kind"), _string(provenance, "policy"),
        _string(provenance, "reference"), _string(document, "tile_policy"),
    )


def resolve_profile(
    selection: ProfileSelection, catalog_path: Path, contract_path: Path,
) -> ResolvedProfile:
    catalog = load_catalog(catalog_path)
    matches = tuple(spec for spec in catalog.profiles if spec.profile == selection.name)
    if len(matches) != 1:
        raise BuildFailure(FailureReason.VALIDATION, f"profile not catalogued: {selection.name}")
    contract = load_memory_contract(contract_path)
    spec = matches[0]
    expected = (
        contract.profile == selection.name,
        (contract.activation_bits, contract.weight_bits, contract.dim)
        == (selection.activation_bits, selection.weight_bits, selection.dim),
        contract.accumulator_bits == catalog.accumulator_bits,
        contract.bank_count == catalog.bank_count,
        contract.scratchpad_row_bytes == selection.dim * selection.activation_bits // 8,
        contract.accumulator_row_bytes == selection.dim * 4,
        contract.scratchpad_total_bytes == catalog.scratchpad_total_bytes,
        contract.accumulator_total_bytes == catalog.accumulator_total_bytes,
        contract.ws_double_buffered,
        contract.bank_rows * contract.bank_count * contract.scratchpad_row_bytes
        == contract.scratchpad_total_bytes,
        contract.accumulator_rows * contract.accumulator_row_bytes
        == contract.accumulator_total_bytes,
        contract.ws_scratchpad_rows_per_buffer == contract.bank_rows * contract.bank_count // 2,
        contract.ws_accumulator_rows_per_buffer == contract.accumulator_rows // 2,
        contract.provenance_policy == catalog.memory_policy,
    )
    if not all(expected):
        raise BuildFailure(FailureReason.VALIDATION, f"memory contract mismatch: {contract_path}")
    return ResolvedProfile(selection, spec, catalog, contract, catalog_path, contract_path)


def write_resolved_profile(resolved: ResolvedProfile, output: Path) -> None:
    if os.path.lexists(output):
        raise BuildFailure(FailureReason.OUTPUT_EXISTS, f"output exists: {output}")
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(resolved.to_document(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except OSError as error:
        raise BuildFailure(FailureReason.IO, f"cannot write output: {output}: {error}") from error


def parse_arguments(arguments: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profiles", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--a-bits", type=int, choices=(4, 8), required=True)
    parser.add_argument("--w-bits", type=int, choices=(4, 8), required=True)
    parser.add_argument("--dim", type=int, choices=(16, 32, 64), required=True)
    parser.add_argument("--scu", choices=tuple(Scu), required=True)
    parser.add_argument("--memory-contract", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    return parser.parse_args(arguments)


def main(arguments: Sequence[str] | None = None) -> int:
    namespace = parse_arguments(sys.argv[1:] if arguments is None else arguments)
    try:
        selection = ProfileSelection(
            namespace.a_bits, namespace.w_bits, namespace.dim, Scu(namespace.scu),
        )
        resolved = resolve_profile(selection, namespace.profiles, namespace.memory_contract)
        write_resolved_profile(resolved, namespace.out)
    except BuildFailure as error:
        print(json.dumps(error.to_document(), sort_keys=True), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
