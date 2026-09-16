"""Serialize the resolved profile's hardware facts for Scala and C++ consumers.

These are configuration assertions, not a second resolver or a timing model.
"""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import cast


def _object(value: object) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise ValueError("resolved hardware section must be an object")
    return cast(Mapping[str, object], value)


def _integer(document: Mapping[str, object], key: str) -> int:
    value = document.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"resolved hardware {key} must be a positive integer")
    return value


def hardware_facts(resolved: Mapping[str, object]) -> dict[str, int]:
    memory = _object(resolved.get("memory"))
    latency = _object(resolved.get("fixed_latencies"))
    facts = {key: _integer(resolved, key) for key in (
        "activation_bits", "weight_bits", "dim", "block_size",
        "array_partial_bits", "accumulator_bits",
    )}
    facts.update({key: _integer(memory, key) for key in (
        "bank_count", "bank_rows", "accumulator_rows",
        "scratchpad_row_bytes", "accumulator_row_bytes",
    )})
    facts.update({key: _integer(latency, key) for key in (
        "scratchpad_read_delay", "accumulator_latency",
    )})
    return facts


def write_hardware_contract(resolved: Mapping[str, object], output: Path) -> None:
    facts = hardware_facts(resolved)
    properties = "# Generated from resolved-profile.json; do not edit.\n" + "".join(
        f"{key}={value}\n" for key, value in sorted(facts.items())
    )
    header = "#pragma once\n// Generated from resolved-profile.json; do not edit.\n" + "".join(
        f"#define IM2P_GEMMINI_{key.upper()} {value}\n" for key, value in sorted(facts.items())
    )
    for name, content in (("resolved-hardware.properties", properties),
                          ("im2p_gemmini_hardware.h", header)):
        path = output / name
        if path.exists():
            if not path.is_file() or path.read_text(encoding="utf-8") != content:
                raise ValueError(f"existing resolved hardware differs: {path}")
        else:
            path.write_text(content, encoding="utf-8")
