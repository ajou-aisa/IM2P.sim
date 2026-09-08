#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
# Run: uv run tests/test_host_reconstruction.py
from __future__ import annotations

import math
import struct
import sys
from dataclasses import dataclass, field
from fractions import Fraction


@dataclass(frozen=True, slots=True)
class Reconstruction:
    raw_blocks: tuple[int, ...]
    weight_factors: tuple[float, ...]
    activation_scale: float
    initial_output: float = 0.0
    residuals: tuple[float, ...] = ()


@dataclass(slots=True)
class FloatingBoundary:
    max_abs: Fraction = Fraction(0)
    overflow_count: int = 0
    first_index: int | None = None
    first_exact_value: Fraction | None = None

    def observe(self, exact: Fraction, actual: float, position: tuple[int, int]) -> None:
        index, width = position
        limit = sys.float_info.max if width == 64 else float.fromhex("0x1.fffffep127")
        self.max_abs = max(self.max_abs, abs(exact))
        if abs(exact) > Fraction.from_float(limit) or not math.isfinite(actual):
            self.overflow_count += 1
            if self.first_index is None:
                self.first_index = index
                self.first_exact_value = exact


@dataclass(frozen=True, slots=True)
class ReconstructionResult:
    value: float
    mathematical_value: Fraction
    boundaries: dict[str, FloatingBoundary] = field(default_factory=dict)

    @property
    def overflow_free(self) -> bool:
        return all(boundary.overflow_count == 0 for boundary in self.boundaries.values())


def float32(value: float) -> float:
    try:
        return struct.unpack("=f", struct.pack("=f", value))[0]
    except OverflowError:
        return math.copysign(math.inf, value)


def reconstruct(spec: Reconstruction) -> ReconstructionResult:
    """Mirror frontend write_output and float residual merges without fused operations.

    Factors are the actual double values returned by factor(), and activation
    scale is the stored row scale. Fraction tracks mathematical pre-rounding
    values separately; finite-input validation does not hide later overflow.
    """
    if not spec.raw_blocks or len(spec.raw_blocks) != len(spec.weight_factors):
        raise ValueError("one weight factor is required per provider block")
    if any(type(x) is not int or not -(1 << 63) <= x < (1 << 63) for x in spec.raw_blocks):
        raise ValueError("provider values must be signed INT64 integers")
    real_inputs = (*spec.weight_factors, spec.activation_scale, spec.initial_output, *spec.residuals)
    if any(type(x) not in (int, float) for x in real_inputs):
        raise ValueError("host reconstruction factors/scales/output must be finite numbers")
    try:
        finite_inputs = all(math.isfinite(float(x)) for x in real_inputs)
    except OverflowError as error:
        raise ValueError("host input exceeds binary64 range") from error
    if not finite_inputs:
        raise ValueError("host reconstruction factors/scales/output must be finite numbers")
    factors = tuple(float(x) for x in spec.weight_factors)
    activation = float32(float(spec.activation_scale))
    boundaries = {name: FloatingBoundary() for name in (
        "double_product", "double_scaled_product", "double_sum", "initial_output_cast",
        "activation_scale_cast", "float_output_cast", "float_output_merge", "residual_cast", "residual_merge",
    )}
    boundaries["activation_scale_cast"].observe(Fraction(spec.activation_scale), activation, (0, 32))
    exact_activation = Fraction.from_float(activation) if math.isfinite(activation) else Fraction(spec.activation_scale)
    total, exact_total = 0.0, Fraction(0)
    for block, (raw, factor) in enumerate(zip(spec.raw_blocks, factors)):
        exact_product = Fraction(raw) * Fraction.from_float(factor)
        product = float(raw) * factor
        boundaries["double_product"].observe(exact_product, product, (block, 64))
        exact_scaled = exact_product * exact_activation
        scaled = product * activation
        boundaries["double_scaled_product"].observe(exact_scaled, scaled, (block, 64))
        total += scaled
        exact_total += exact_scaled
        boundaries["double_sum"].observe(exact_total, total, (block, 64))
    initial = float32(float(spec.initial_output))
    initial_input = Fraction(spec.initial_output)
    boundaries["initial_output_cast"].observe(initial_input, initial, (0, 32))
    initial_exact = Fraction.from_float(initial) if math.isfinite(initial) else initial_input
    cast = float32(total)
    cast_exact = Fraction.from_float(total) if math.isfinite(total) else exact_total
    boundaries["float_output_cast"].observe(cast_exact, cast, (len(spec.raw_blocks) - 1, 32))
    value = float32(initial + cast)
    merge_exact = Fraction.from_float(initial) + Fraction.from_float(cast) if math.isfinite(initial) and math.isfinite(cast) else initial_exact + exact_total
    boundaries["float_output_merge"].observe(merge_exact, value, (0, 32))
    mathematical = initial_exact + exact_total
    for index, residual in enumerate(spec.residuals):
        narrowed = float32(float(residual))
        boundaries["residual_cast"].observe(Fraction(residual), narrowed, (index, 32))
        exact_residual = Fraction.from_float(narrowed) if math.isfinite(narrowed) else Fraction(residual)
        merge_exact = Fraction.from_float(value) + exact_residual if math.isfinite(value) else mathematical + exact_residual
        value = float32(value + narrowed)
        mathematical += exact_residual
        boundaries["residual_merge"].observe(merge_exact, value, (index, 32))
    return ReconstructionResult(value, mathematical, boundaries)
