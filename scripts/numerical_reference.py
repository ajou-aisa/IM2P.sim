#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
# Run: uv run --no-project scripts/numerical_reference.py --self-test
"""Bit-accurate integer dot reference with independent pre-truncation checks.

The oracle checks each actual operation, including overflows later cancelled.
It does not certify model quality or infer safety from a final saturated output.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.im2p_config import profile_config


class Operation(str, Enum):
    BYPASS = "bypass"
    MULTIPLY = "multiply"
    SHIFT = "shift"
    EXTERNAL = "external"


@dataclass(frozen=True, slots=True)
class Location:
    route: str = "independent-dot"
    layer: str = "unspecified"
    stripe: int = 0
    row: int = 0
    column: int = 0


@dataclass(slots=True)
class Boundary:
    """Mutable running extrema and first failure, bounded independently of K."""

    max_abs: int = 0
    overflow_count: int = 0
    first_k: int | None = None
    first_block: int | None = None
    first_value: int | None = None

    def observe(self, value: int, width: int, position: tuple[int, int]) -> None:
        self.max_abs = max(self.max_abs, abs(value))
        if not -(1 << (width - 1)) <= value < (1 << (width - 1)):
            self.overflow_count += 1
            if self.first_k is None:
                self.first_k, self.first_block = position
                self.first_value = value


@dataclass(frozen=True, slots=True)
class Dot:
    activations: tuple[int, ...]
    weights: tuple[int, ...]
    scales: tuple[int, ...]
    operand_bits: int = 8
    diagnostic_width: int | None = None
    dim: int = 16
    block_size: int = 32
    k_origin: int = 0
    operation: Operation = Operation.MULTIPLY
    location: Location = field(default_factory=Location)


@dataclass(frozen=True, slots=True)
class Result:
    location: Location
    value: int
    mathematical_value: int
    block_outputs: tuple[int, ...]
    boundaries: dict[str, Boundary]
    datapath_width: int
    diagnostic_profile: bool
    raw_int32_value: int | None
    reconstruction_policy: str

    @property
    def overflow_free(self) -> bool:
        return all(x.overflow_count == 0 for x in self.boundaries.values())


def wrap(value: int, width: int) -> int:
    return (value + (1 << (width - 1))) % (1 << width) - (1 << (width - 1))


def transform(partial: int, scale: int, op: Operation) -> int:
    """Return untruncated arithmetic value, including INT8_MIN exponents."""
    match op:
        case Operation.BYPASS | Operation.EXTERNAL:
            return partial
        case Operation.MULTIPLY:
            return partial * scale
        case Operation.SHIFT:
            return partial >> -scale if scale < 0 else partial << scale
    raise ValueError(f"invalid vector operation: {op}")


def evaluate(dot: Dot) -> Result:
    if (any(type(x) is not int for x in (dot.operand_bits, dot.dim, dot.block_size, dot.k_origin))
            or (dot.diagnostic_width is not None and
                (type(dot.diagnostic_width) is not int or dot.diagnostic_width not in (32, 64)))
            or not 0 <= dot.block_size <= 0xFFFFFFFF
            or not 0 <= dot.k_origin <= 0xFFFFFFFF or not dot.activations
            or len(dot.activations) > 0xFFFFFFFF - dot.k_origin
            or len(dot.activations) != len(dot.weights)):
        raise ValueError("invalid dot dimensions/profile")
    profile = profile_config(dot.operand_bits, dot.operand_bits, dot.dim)
    width = profile["accumulator_bits"] if dot.diagnostic_width is None else dot.diagnostic_width
    limit = 1 << (dot.operand_bits - 1)
    if any(type(x) is not int or not -limit <= x < limit
           for values in (dot.activations, dot.weights) for x in values):
        raise ValueError("operand is not a signed integer in the selected profile")
    uses_scale = dot.operation != Operation.BYPASS
    if uses_scale:
        if dot.block_size == 0:
            raise ValueError("scaled work block size must be positive")
        last_block = (dot.k_origin + len(dot.activations) - 1) // dot.block_size
        if (len(dot.scales) <= last_block
                or any(type(x) is not int or not -128 <= x <= 127 for x in dot.scales)):
            raise ValueError("missing or invalid signed INT8 scale")
    boundaries = {name: Boundary() for name in (
        "pe_partial", "vector_contribution", "accumulator_update",
        "provider_output", "diagnostic_integer_reconstruction",
    )}
    offset, accumulator, mathematical = 0, 0, 0
    outputs: list[int] = []
    reconstructed = 0
    position = (dot.k_origin, 0)
    while offset < len(dot.activations):
        global_k = dot.k_origin + offset
        block, within = divmod(global_k, dot.block_size) if uses_scale else (0, 0)
        count = min(dot.dim, len(dot.activations) - offset)
        if dot.operation != Operation.BYPASS:
            count = min(count, dot.block_size - within)
        scale = 0 if dot.operation == Operation.BYPASS else dot.scales[block]
        partial, exact_partial = 0, 0
        for index in range(offset, offset + count):
            product = dot.activations[index] * dot.weights[index]
            exact_partial += product
            wide_partial = partial + product
            boundaries["pe_partial"].observe(
                wide_partial, width, (dot.k_origin + index, block))
            partial = wrap(wide_partial, width)
        wide_contribution = transform(partial, scale, dot.operation)
        position = (global_k, block)
        boundaries["vector_contribution"].observe(wide_contribution, width, position)
        contribution = wrap(wide_contribution, width)
        wide_update = accumulator + contribution
        boundaries["accumulator_update"].observe(wide_update, width, position)
        accumulator = wrap(wide_update, width)
        if dot.operation == Operation.EXTERNAL:
            mathematical += exact_partial * scale
        else:
            mathematical += transform(exact_partial, scale, dot.operation)
        offset += count
        block_end = within + count == dot.block_size or offset == len(dot.activations)
        if dot.operation == Operation.EXTERNAL and block_end:
            boundaries["provider_output"].observe(accumulator, 64, position)
            outputs.append(accumulator)
            reconstructed += accumulator * scale
            boundaries["diagnostic_integer_reconstruction"].observe(reconstructed, 64, position)
            accumulator = 0
    value = reconstructed if dot.operation == Operation.EXTERNAL else accumulator
    if dot.operation != Operation.EXTERNAL:
        boundaries["provider_output"].observe(value, 64, position)
    raw_output = None if dot.operation == Operation.EXTERNAL else max(-(1 << 31), min(value, (1 << 31) - 1))
    policy = "diagnostic-integer-only; use host_reconstruction for frontend" if dot.operation == Operation.EXTERNAL else "none"
    return Result(dot.location, value, mathematical, tuple(outputs), boundaries,
                  width, dot.diagnostic_width is not None, raw_output, policy)


def self_test() -> None:
    from dataclasses import replace

    overflow = Dot((-128,) * 8192, (-128,) * 8192, (16,) * 256)
    result = evaluate(overflow)
    assert result.mathematical_value == 1 << 31 and result.value == -(1 << 31)
    assert result.boundaries["accumulator_update"].first_value == 1 << 31
    assert not result.overflow_free
    external = evaluate(replace(overflow, operation=Operation.EXTERNAL))
    assert external.value == 1 << 31 and external.overflow_free
    assert external.block_outputs == (524288,) * 256
    wide = Dot((-32768,) * 3, (-32768,) * 3, (1,), operand_bits=16)
    assert evaluate(wide).value == 3221225472 and evaluate(wide).overflow_free
    assert not evaluate(replace(wide, diagnostic_width=32)).overflow_free
    for width in (32, 64):
        for exponent in (-128, -65, -64, -33, -32, -1, 0, 1, 31, 32, 63, 64, 127):
            for value in (-7, 0, 7):
                transformed = wrap(transform(value, exponent, Operation.SHIFT), width)
                if exponent >= width:
                    assert transformed == 0
                if exponent <= -width:
                    assert transformed == (-1 if value < 0 else 0)
    for dim in (16, 32, 64):
        for count in (1, dim - 1, dim, dim + 1, 1025):
            for block in (1, 7, 32, 97):
                values = tuple((i % 15) - 7 for i in range(count))
                for operation in Operation:
                    dot = Dot(values, values, (-1,) * (count + 3), dim=dim,
                              block_size=block, k_origin=3, operation=operation)
                    narrow, wide = evaluate(dot), evaluate(replace(dot, diagnostic_width=64))
                    assert narrow.overflow_free and narrow.value == wide.value
    cancellation = Dot(tuple(-32768 if i % 16 == 0 else 0 for i in range(64)),
                       tuple((-32768 if i < 32 else 32767) if i % 16 == 0 else 0 for i in range(64)),
                       (1, 1), operand_bits=16, diagnostic_width=32)
    cancelled = evaluate(cancellation)
    assert cancelled.value == cancelled.mathematical_value == 65536
    assert not cancelled.overflow_free


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--input", type=Path, help="JSON matching Dot fields; operand_bits selects production profile")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        print("NUMERICAL REFERENCE: PASS (intentional overflow detected; no model-quality claim)")
        return 0
    if args.input is None:
        parser.error("provide --self-test or --input")
    raw = json.loads(args.input.read_text(encoding="utf-8"))
    if "width" in raw:
        parser.error("use operand_bits for production or diagnostic_width for an explicit counterfactual")
    dot = Dot(tuple(raw["activations"]), tuple(raw["weights"]), tuple(raw["scales"]),
              operand_bits=raw.get("operand_bits", 8), diagnostic_width=raw.get("diagnostic_width"),
              dim=raw.get("dim", 16), block_size=raw.get("block_size", 32),
              k_origin=raw.get("k_origin", 0), operation=Operation(raw["operation"]),
              location=Location(**raw.get("location", {})))
    result = evaluate(dot)
    print(json.dumps({**asdict(result), "overflow_free": result.overflow_free}, indent=2))
    return 0 if result.overflow_free else 1


if __name__ == "__main__":
    raise SystemExit(main())
