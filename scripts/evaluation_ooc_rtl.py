from __future__ import annotations

from decimal import Decimal, localcontext, ROUND_FLOOR
from fractions import Fraction
import re

from scripts.evaluation_clock_contract import ClockError, require
from scripts.evaluation_ooc_policy import dimensions


def core_top(profile: str) -> str:
    bits, dim = dimensions(profile)
    return f"IM2PGemminiWSHP1A{bits}W{bits}D{dim}"


def wrap_core(source: str, profile: str) -> tuple[str, str]:
    top = core_top(profile)
    cleaned = re.sub(r'/\*.*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"', "", source, flags=re.S)
    match = re.search(r"\bmodule\s+" + re.escape(top) + r"\s*\((.*?)\)\s*;(.*?)\bendmodule", cleaned, re.S)
    if match is None:
        raise ClockError("plain generated production top declaration required")
    ports: list[tuple[str, str, str]] = []
    direction, width = "", ""
    for raw in match[1].split(","):
        token = raw.strip()
        declared = re.fullmatch(r"(input|output|inout)\s+(?:(?:wire|logic|reg)\s+)?(\[\d+:\d+\]\s*)?(\w+)", token)
        if declared:
            direction, width, name = declared[1], declared[2] or "", declared[3]
        else:
            require(bool(direction) and re.fullmatch(r"[A-Za-z_]\w*", token) is not None,
                    "unsupported generated ANSI port declaration: " + token)
            name = token
        ports.append((direction, width.strip(), name))
    require(len({item[2] for item in ports}) == len(ports), "duplicate generated port")
    require(("input", "", "clock") in ports and all(item[2] != "CLK" for item in ports),
            "single scalar production clock input required")
    declarations = [" ".join(part for part in (direction, width, "CLK" if name == "clock" else name) if part)
                    for direction, width, name in ports]
    connections = [f".{name}({'CLK' if name == 'clock' else name})" for _, _, name in ports]
    wrapper = "module PoTalEvaluationOoc(\n  " + ",\n  ".join(declarations) + ");\n"
    wrapper += top + " core(\n  " + ",\n  ".join(connections) + ");\nendmodule\n"
    async_reset = (("input", "", "RST_N") in ports and
                   re.search(r"always(?:_ff)?\s*@\s*\([^)]*\bnegedge\s+RST_N\b", match[2]) is not None)
    return wrapper, "ASYNC_RST_N_SOURCE_EVENT_PROVEN" if async_reset else "NO_ASYNC_RST_N_EXCLUSION"


def constraints(frequency_hz: int, reset_policy: str) -> str:
    require(type(frequency_hz) is int and frequency_hz > 0, "positive frequency required")
    require(reset_policy in ("ASYNC_RST_N_SOURCE_EVENT_PROVEN", "NO_ASYNC_RST_N_EXCLUSION"), "reset proof required")
    period = Fraction(10**9, frequency_hz)
    with localcontext() as context:
        context.prec = 30
        context.rounding = ROUND_FLOOR
        decimal = Decimal(period.numerator) / Decimal(period.denominator)
    result = f"create_clock -name core_clk -period {decimal:f} [get_ports CLK]\n"
    if reset_policy == "ASYNC_RST_N_SOURCE_EVENT_PROVEN":
        result += "set_false_path -from [get_ports RST_N]\n"
    return result
