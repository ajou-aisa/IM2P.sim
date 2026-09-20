"""Trace v2 parent coverage; no buffer lifetime or cross-work timing inference."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, TypeAlias

JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
COMMON: Final = {"kind", "sequence", "run_id"}
DESCRIPTOR_FIELDS: Final = (
    "layer", "operation", "provenance", "scope", "activation_bits", "weight_bits", "dim",
    "n", "k", "tile_i_count", "tile_j_count", "tile_k_count", "activation_stride_bytes",
    "weight_stride_bytes", "output_stride_bytes", "scale_stride_elements", "block_size",
    "vector_op", "output_domain", "production_geometry_version",
)
BEGIN_FIELDS: Final = COMMON | {"phase_id", "parent_invocation_id", "m"} | set(DESCRIPTOR_FIELDS)
END_FIELDS: Final = COMMON | {"phase_id", "parent_invocation_id", "status"}
TILE_FIELDS: Final = ("tile_i_count", "tile_j_count", "tile_k_count")


class ParentError(ValueError):
    def __init__(self, message: str) -> None:
        super().__init__(f"parent integrity: {message}")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ParentError(message)


def _number(record: Mapping[str, JsonValue], name: str) -> int:
    value = record.get(name)
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value < 2**64:
        raise ParentError(f"{name} must be uint64")
    return value


def _tiles(record: Mapping[str, JsonValue]) -> None:
    dim = _number(record, "dim")
    _require(dim in (16, 32, 64), "invalid DIM")
    _require(all(_number(record, key) > 0 for key in TILE_FIELDS), "zero final tile factor")
    _require(_number(record, "tile_i_count") <= 65535 // dim and
             _number(record, "tile_j_count") <= 65535 // dim and
             _number(record, "tile_k_count") <= (2**32 - 1) // dim, "final tile factor exceeds hardware domain")


@dataclass(slots=True)
class _Parent:
    """Mutable coverage cursor for one declared production invocation."""
    descriptor: tuple[JsonValue, ...]
    rows: int
    phase: int
    scope: str
    next_row: int = 0
    work_count: int = 0


class ParentValidator:
    def __init__(self) -> None:
        self._parents: dict[int, _Parent] = {}
        self._next_parent = 0
        self._phase: int | None = None
        self._profile: tuple[int, int, int] | None = None

    def consume(self, record: Mapping[str, JsonValue]) -> None:
        handlers = {
            "run": self._run, "phase": self._phase_begin,
            "parent_begin": self._begin, "npu_work": self._work,
            "parent_end": self._end, "run_end": self._run_end,
        }
        kind = record.get("kind")
        if not isinstance(kind, str) or kind not in handlers:
            raise ParentError("unsupported record kind")
        handlers[kind](record)

    def finish(self) -> None:
        _require(not self._parents, "incomplete parent at phase/run end")

    def _run(self, record: Mapping[str, JsonValue]) -> None:
        _require(self._profile is None, "duplicate run")
        self._profile = (_number(record, "activation_bits"), _number(record, "weight_bits"), _number(record, "dim"))

    def _phase_begin(self, record: Mapping[str, JsonValue]) -> None:
        self.finish()
        self._phase = _number(record, "phase_id")

    def _begin(self, record: Mapping[str, JsonValue]) -> None:
        _require(set(record) == BEGIN_FIELDS, "missing or unknown parent declaration fields")
        phase = _number(record, "phase_id")
        _require(self._phase is not None and phase == self._phase, "missing/stale parent phase")
        identity = _number(record, "parent_invocation_id")
        _require(identity == self._next_parent, "duplicate or nonmonotonic parent identity")
        scope = record.get("scope")
        _require(scope in ("full", "stripe", "residual_compact"), "invalid parent scope")
        if not isinstance(scope, str):
            raise ParentError("parent scope must be string")
        for key in ("layer", "operation", "provenance"):
            _require(isinstance(record[key], str) and bool(record[key]), f"invalid parent {key}")
        provenance = record["provenance"]
        _require((scope == "residual_compact" and provenance == "residual") or
                 (scope in ("full", "stripe") and provenance == "dense_main"), "parent scope/provenance mismatch")
        for key in DESCRIPTOR_FIELDS[4:]:
            _require(_number(record, key) > 0, f"parent {key} must be positive")
        profile = tuple(_number(record, key) for key in ("activation_bits", "weight_bits", "dim"))
        _require(profile == self._profile, "parent/run profile mismatch")
        rows = _number(record, "m")
        _require(rows > 0, "parent m must be positive")
        _tiles(record)
        _require(tuple(record[key] for key in ("block_size", "vector_op", "output_domain", "production_geometry_version"))
                 == (32, 5, 2, 1), "invalid parent HP1 contract")
        n, k = _number(record, "n"), _number(record, "k")
        _require(_number(record, "activation_stride_bytes") >= k and
                 _number(record, "weight_stride_bytes") >= n and
                 _number(record, "output_stride_bytes") >= 4*n and
                 _number(record, "output_stride_bytes") % 4 == 0 and
                 _number(record, "scale_stride_elements") >= n, "invalid parent strides")
        _require(scope != "residual_compact" or k <= 32, "residual parent is not compact")
        self._parents[identity] = _Parent(tuple(record[key] for key in DESCRIPTOR_FIELDS), rows, phase, scope)
        self._next_parent += 1

    def _parent(self, record: Mapping[str, JsonValue]) -> _Parent:
        identity = _number(record, "parent_invocation_id")
        _require(identity in self._parents, "undeclared or completed parent")
        parent = self._parents[identity]
        _require(_number(record, "phase_id") == parent.phase == self._phase, "stale parent phase")
        return parent

    def _work(self, record: Mapping[str, JsonValue]) -> None:
        parent = self._parent(record)
        _require(all(record.get(key) == expected
                     for key, expected in zip(DESCRIPTOR_FIELDS, parent.descriptor)
                     if parent.scope != "stripe" or key not in TILE_FIELDS),
                 "work differs from parent descriptor/geometry")
        _tiles(record)
        _require(_number(record, "geometry_m") == parent.rows, "work parent shape mismatch")
        begin, count = _number(record, "row_begin"), _number(record, "row_count")
        _require(count > 0 and count == _number(record, "m") and begin + count <= parent.rows,
                 "work exceeds parent row range")
        _require(begin == parent.next_row, "overlapping, gapped, or duplicate parent row range")
        if record.get("host_slot") is not None:
            _require(_number(record, "host_slot") in (0, 1), "host_slot outside [0,1]")
        if parent.scope == "stripe":
            _require(_number(record, "stripe_id") == parent.work_count, "duplicate or unordered parent stripe ID")
            _require(_number(record, "host_slot") in (0, 1), "host_slot outside [0,1]")
        else:
            _require(parent.work_count == 0 and count == parent.rows, "FULL/compact parent needs one complete work")
        parent.next_row += count
        parent.work_count += 1

    def _end(self, record: Mapping[str, JsonValue]) -> None:
        _require(set(record) == END_FIELDS, "missing or unknown parent completion fields")
        parent = self._parent(record)
        _require(record.get("status") == "success", "parent did not complete successfully")
        _require(parent.work_count > 0 and parent.next_row == parent.rows, "incomplete parent row coverage")
        del self._parents[_number(record, "parent_invocation_id")]

    def _run_end(self, record: Mapping[str, JsonValue]) -> None:
        self.finish()
