from __future__ import annotations

from pathlib import Path
from fractions import Fraction

from scripts.evaluation_clock_contract import (
    ClockError, ClockSelection, Record, bound_file, file_ref, integer, read_record, record,
    rational, require, sha256, text,
)
from scripts.evaluation_ooc_policy import PART, POLICY_PATH, policy
from scripts.evaluation_ooc_rtl import constraints, core_top

RESOURCE_ROWS = {"CLB LUTs": ("LUT", 1), "CLB Registers": ("FF", 1),
                 "Block RAM Tile": ("BRAM18_EQUIVALENTS", 2), "DSPs": ("DSP", 1), "URAM": ("URAM", 1)}


def resources(report: str) -> tuple[Record, Record]:
    usage: Record = {}
    limits: Record = {}
    used_column, available_column = -1, -1
    for line in report.splitlines():
        columns = [column.strip().rstrip("*").strip() for column in line.split("|")[1:-1]]
        if "Used" in columns and "Available" in columns:
            used_column, available_column = columns.index("Used"), columns.index("Available")
            continue
        if not columns or columns[0] not in RESOURCE_ROWS:
            continue
        require(0 <= used_column < len(columns) and 0 <= available_column < len(columns), "utilization header missing")
        key, scale = RESOURCE_ROWS[columns[0]]
        require(key not in usage, "ambiguous utilization row: " + key)
        try:
            used = Fraction(columns[used_column].replace(",", "")) * scale
            available = Fraction(columns[available_column].replace(",", "")) * scale
        except (ValueError, ZeroDivisionError) as error:
            raise ClockError("invalid utilization numeric field") from error
        require(used.denominator == available.denominator == 1 and used >= 0 and available > 0,
                "invalid utilization units/capacity")
        usage[key], limits[key] = used.numerator, available.numerator
    require(len(usage) == len(RESOURCE_ROWS), "incomplete post-route resource report")
    return usage, limits


def normalized_values(raw: Record, utilization: str, source: Record) -> Record:
    profile = text(source, "profile")
    require(raw.get("schema") == "im2p-ooc-tool-result" and type(raw.get("version")) is int and raw.get("version") == 2
            and raw.get("status") == "ROUTE_COMPLETED", "completed OOC route report required")
    require(raw.get("part") == PART and raw.get("clock_port") == "CLK"
            and raw.get("timing_stage") == "POST_ROUTE" and raw.get("core_top") == core_top(profile)
            and raw.get("top") == "PoTalEvaluationOoc", "OOC part/top/clock/stage differs from policy")
    require(rational(raw, "clock_switching_worst_slack_ns") >= 0
            and rational(raw, "clock_switching_total_slack_ns") >= 0
            and integer(raw, "clock_switching_failing_endpoints") == 0
            and integer(raw, "clock_switching_total_endpoints") > 0,
            "clock-switching pulse-width/min-period limits failed or unmeasured")
    usage, limits = resources(utilization)
    fit = all(integer(usage, key) <= integer(limits, key) for key in usage)
    return {
        "top": core_top(profile), "tool_exit_code": 0,
        "setup_slack_ns": text(raw, "setup_slack_ns"), "hold_slack_ns": text(raw, "hold_slack_ns"),
        "timed_paths": integer(raw, "timed_paths"), "unconstrained_paths": integer(raw, "unconstrained_paths"),
        "fit": fit and integer(raw, "blocking_drc") == 0, "resource_usage": usage, "resource_limits": limits,
    }


def write_sample(directory: Path, source_path: Path) -> Path:
    import json
    source = read_record(source_path)
    require(source.get("execution_kind") in ("FRESH_OFFICIAL_RTL_BUILD", "SYNTHETIC"), "OOC build execution kind")
    raw_path = directory / "implementation/ooc-result.json"
    utilization = directory / "implementation/post-route-utilization.rpt"
    raw = read_record(raw_path)
    values = normalized_values(raw, utilization.read_text(), source)
    frequency = integer(read_record(directory / "request.json"), "frequency_hz", 1)
    values["frequency_hz"] = frequency
    normalized = directory / "normalized-tool-report.json"
    normalized.write_text(json.dumps({"schema": "im2p-clock-tool-report", "version": 1, **values}, sort_keys=True) + "\n")
    data: Record = {
        "schema": "im2p-clock-observation", "version": 1,
        "execution_kind": "TOOL_EXECUTION" if source["execution_kind"] == "FRESH_OFFICIAL_RTL_BUILD" else "SYNTHETIC",
        "profile": source["profile"], "top_scope": "INTEGRATED", "source": file_ref(source_path),
        "netlist": file_ref(directory / "implementation/route.dcp"),
        "constraints": [file_ref(directory / "clock.xdc")], "tool_report": file_ref(normalized),
        "hardware_contract_sha256": text(record(source["hardware_contract"]), "sha256"),
        "tool": {"name": "Vivado", "version": text(raw, "tool_version")},
        "technology": "FPGA " + PART, "memory_implementation": "production integrated RTL memories; Vivado inferred",
        "memory_interface": "integrated production core backing port; external service unmodeled by OOC",
        "timing_stage": "POST_ROUTE", "array_count": 1, "independent_macs_per_pe_per_cycle": 1,
        "peak_basis": file_ref(POLICY_PATH), **values,
    }
    observation = directory / "observation.json"
    observation.write_text(json.dumps(data, sort_keys=True) + "\n")
    sample: Record = {"schema": "im2p-evaluation-ooc-sample", "version": 1,
                      "observation": file_ref(observation), "raw_report": file_ref(raw_path),
                      "utilization": file_ref(utilization), "request": file_ref(directory / "request.json"),
                      "timing_summary": file_ref(directory / "implementation/post-route-timing.rpt"),
                      "pulse_width": file_ref(directory / "implementation/post-route-pulse-width.rpt"),
                      "source": file_ref(source_path)}
    output = directory / "sample.json"
    output.write_text(json.dumps(sample, sort_keys=True) + "\n")
    return output


def verify_sample(path: Path) -> Path:
    sample = read_record(path)
    require(set(sample) == {"schema", "version", "observation", "raw_report", "utilization", "request", "source", "timing_summary", "pulse_width"}
            and sample["schema"] == "im2p-evaluation-ooc-sample" and sample["version"] == 1, "OOC sample schema")
    paths = {key: Path(text(bound_file(sample[key], path.parent), "path"))
             for key in ("observation", "raw_report", "utilization", "request", "source", "timing_summary", "pulse_width")}
    observation, source = read_record(paths["observation"]), read_record(paths["source"])
    raw = read_record(paths["raw_report"])
    request = read_record(paths["request"])
    require(request.get("part") == PART
            and bound_file(request["policy"], paths["request"].parent)["sha256"] == sha256(POLICY_PATH),
            "request OOC policy mismatch")
    values = normalized_values(raw, paths["utilization"].read_text(), source)
    frequency = integer(request, "frequency_hz", 1)
    values["frequency_hz"] = frequency
    references = observation["constraints"]
    if not isinstance(references, list) or len(references) != 1:
        raise ClockError("one generated OOC constraint file required")
    xdc = Path(text(bound_file(references[0], path.parent), "path"))
    expected = constraints(frequency, text(source, "reset_policy"))
    require(xdc.read_text() == expected, "OOC constraint differs from requested frequency/reset proof")
    require(Fraction(text(raw, "period_ns")) == Fraction(expected.split()[4]),
            "reported clock period differs from generated constraint; no frequency relabeling")
    require(all(observation[key] == value for key, value in values.items()), "normalized report differs from routed tool evidence")
    require(observation["source"] == file_ref(paths["source"]), "OOC source binding mismatch")
    for value in record(source["artifacts"]).values():
        bound_file(value, paths["source"].parent)
    require(observation["timing_stage"] == "POST_ROUTE" and observation["array_count"] == 1
            and observation["independent_macs_per_pe_per_cycle"] == 1
            and observation["technology"] == "FPGA " + PART, "OOC observation policy mismatch")
    return paths["observation"]


def select_ooc(samples: list[Path], sweep: Path) -> Record:
    from scripts.evaluation_clock import select_clock
    current = policy()
    summary = read_record(sweep)
    grid, attempts = summary.get("attempted_frequency_hz"), summary.get("attempts")
    require(isinstance(grid, list) and isinstance(attempts, list) and len(grid) == len(attempts), "incomplete finite sweep")
    if not isinstance(grid, list) or not isinstance(attempts, list):
        raise ClockError("finite sweep arrays required")
    require(grid == [integer(record(item), "frequency_hz", 1) for item in attempts]
            and len(grid) == len(set(str(item) for item in grid)), "sweep frequency coverage mismatch")
    observations = [verify_sample(path) for path in samples]
    passed_commands = sorted(integer(record(item), "frequency_hz", 1) for item in attempts
                             if record(item).get("exit_code") == 0)
    require(sorted(integer(read_record(path), "frequency_hz", 1) for path in observations) == passed_commands,
            "completed route samples omitted or duplicated")
    output = select_clock(observations, "33", text(current, "target_basis"))
    if output["status"] == "DIAGNOSTIC_ONLY":
        output["status"] = "PASS"
    output.update({"version": 2, "evaluation_policy": file_ref(POLICY_PATH),
                   "ooc_samples": [file_ref(path) for path in samples], "sweep": file_ref(sweep)})
    return output


def load_operating_clock(path: Path, profile: str) -> ClockSelection:
    document = read_record(path)
    require(document.get("schema") == "im2p-operating-clock" and document.get("version") == 2
            and document.get("status") == "PASS" and document.get("profile") == profile,
            "current fixed-policy post-route operating clock required; generic/legacy clock rejected")
    require(bound_file(document["evaluation_policy"], path.parent)["sha256"] == sha256(POLICY_PATH), "OOC policy mismatch")
    refs = document["ooc_samples"]
    require(isinstance(refs, list) and bool(refs), "OOC sample evidence required")
    if not isinstance(refs, list):
        raise ClockError("OOC sample evidence required")
    samples = [Path(text(bound_file(value, path.parent), "path")) for value in refs]
    sweep = Path(text(bound_file(document["sweep"], path.parent), "path"))
    require(document == select_ooc(samples, sweep), "OOC selection/evidence mismatch")
    selected = record(document["selected"])
    return ClockSelection(integer(document, "selected_frequency_hz", 1), profile, "POST_ROUTE", sha256(path),
                          text(selected, "hardware_contract_sha256"))
