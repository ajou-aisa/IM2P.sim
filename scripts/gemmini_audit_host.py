#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
#
# ─── How to run ───
# 1. Install uv (if not installed):
#      curl -LsSf https://astral.sh/uv/install.sh | sh
# 2. Run directly (no venv, no pip install needed):
#      uv run scripts/gemmini_audit_host.py --artifact HOST --role PHYSICAL_HOST --out audit.json
# 3. Or make executable and run:
#      chmod +x scripts/gemmini_audit_host.py && ./scripts/gemmini_audit_host.py --help
# ─────────────────

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Final

SIMULATOR_MARKERS: Final = ("Verilated", "VStandaloneTop", "VGemmini", "libim2p_sim", "im2p_sim_")
BYPASS_FLAGS: Final = (
    "--allow-shlib-undefined", "--unresolved-symbols=ignore", "-undefined dynamic_lookup",
    "-Wl,-undefined,dynamic_lookup",
)


class HostRole(str, Enum):
    HOST_COMMON = "HOST_COMMON"
    PHYSICAL_HOST = "PHYSICAL_HOST"
    RTL_TEST_ADAPTER = "RTL_TEST_ADAPTER"


@dataclass(frozen=True, slots=True)
class AuditRequest:
    artifact: Path
    role: HostRole
    command_log: Path | None


@dataclass(frozen=True, slots=True)
class ToolResult:
    name: str
    exit_code: int
    output_sha256: str
    output: str


@dataclass(frozen=True, slots=True)
class AuditResult:
    status: str
    reason: str | None
    role: str
    artifact: str
    artifact_sha256: str | None
    format: str | None
    architecture: str | None
    tools: tuple[str, ...]
    missing_tools: tuple[str, ...]
    failed_tools: tuple[str, ...]
    simulator_markers: tuple[str, ...]
    physical_operations: int


@dataclass(frozen=True, slots=True)
class ArtifactEvidence:
    sha256: str | None
    description: str | None


@dataclass(frozen=True, slots=True)
class ToolEvidence:
    used: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    failed: tuple[str, ...] = ()
    markers: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AuditEvidence:
    request: AuditRequest
    artifact: ArtifactEvidence
    tools: ToolEvidence


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def outcome(evidence: AuditEvidence, status: str, reason: str | None) -> AuditResult:
    description = evidence.artifact.description
    binary_format = "MACH_O" if description is not None and "Mach-O" in description else (
        "ELF" if description is not None and "ELF" in description else None)
    return AuditResult(status, reason, evidence.request.role.value, str(evidence.request.artifact),
                       evidence.artifact.sha256, binary_format, description, evidence.tools.used,
                       evidence.tools.missing, evidence.tools.failed, evidence.tools.markers, 0)


def run_tool(name: str, arguments: tuple[str, ...]) -> ToolResult:
    executable = shutil.which(name)
    if executable is None:
        raise FileNotFoundError(name)
    process = subprocess.run([executable, *arguments], text=True, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, check=False)
    output = process.stdout
    return ToolResult(name, process.returncode, hashlib.sha256(output.encode()).hexdigest(), output)


def inspect_tools(file_format: str) -> tuple[tuple[str, tuple[str, ...]], ...]:
    if "Mach-O" in file_format:
        return (("otool", ("-L",)), ("nm", ("-a",)))
    if "ELF" in file_format:
        return (("readelf", ("-h", "-d")), ("ldd", ()), ("nm", ("-a",)))
    return ()


def audit_host(request: AuditRequest) -> AuditResult:
    artifact = request.artifact.resolve()
    normalized = AuditRequest(artifact, request.role, request.command_log)
    evidence = AuditEvidence(normalized, ArtifactEvidence(None, None), ToolEvidence())
    if not artifact.is_file() or artifact.is_symlink():
        return outcome(evidence, "NOT_RUN", "ARTIFACT_MISSING")
    artifact_hash = digest(artifact)
    evidence = AuditEvidence(normalized, ArtifactEvidence(artifact_hash, None), ToolEvidence())
    if request.command_log is not None:
        if not request.command_log.is_file() or request.command_log.is_symlink():
            return outcome(evidence, "NOT_RUN", "COMMAND_LOG_MISSING")
        command_text = request.command_log.read_text(encoding="utf-8")
        if any(flag in command_text for flag in BYPASS_FLAGS):
            return outcome(evidence, "FAIL", "UNRESOLVED_SYMBOL_BYPASS")
    missing = tuple(name for name in ("file",) if shutil.which(name) is None)
    if missing:
        evidence = AuditEvidence(normalized, evidence.artifact, ToolEvidence(missing=missing))
        return outcome(evidence, "NOT_RUN", "DEPENDENCY")
    file_result = run_tool("file", ("-b", str(artifact)))
    evidence = AuditEvidence(normalized, evidence.artifact, ToolEvidence(("file",)))
    if file_result.exit_code != 0:
        return outcome(evidence, "FAIL", "TOOL_FAILURE")
    file_format = file_result.output.strip()
    evidence = AuditEvidence(normalized, ArtifactEvidence(artifact_hash, file_format), evidence.tools)
    commands = inspect_tools(file_format)
    if not commands:
        return outcome(evidence, "NOT_RUN", "UNSUPPORTED_FORMAT")
    missing = tuple(name for name, _ in commands if shutil.which(name) is None)
    tool_names = ("file", *(name for name, _ in commands))
    evidence = AuditEvidence(normalized, evidence.artifact, ToolEvidence(tool_names, missing))
    if missing:
        return outcome(evidence, "NOT_RUN", "DEPENDENCY")
    inspected: list[ToolResult] = [file_result]
    for name, options in commands:
        inspected.append(run_tool(name, (*options, str(artifact))))
    failed = tuple(item.name for item in inspected[1:] if item.exit_code != 0 and
                   not (item.name == "ldd" and "not a dynamic executable" in item.output.lower()))
    if failed:
        evidence = AuditEvidence(normalized, evidence.artifact, ToolEvidence(tool_names, failed=failed))
        return outcome(evidence, "FAIL", "TOOL_FAILURE")
    combined = "\n".join(item.output for item in inspected)
    markers = tuple(marker for marker in SIMULATOR_MARKERS if marker in combined)
    evidence = AuditEvidence(normalized, evidence.artifact, ToolEvidence(tool_names, markers=markers))
    if request.role is not HostRole.RTL_TEST_ADAPTER and markers:
        return outcome(evidence, "FAIL", "SIMULATOR_LINKAGE")
    return outcome(evidence, "PASS", None)


def write_result(path: Path, value: AuditResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as output:
        json.dump(asdict(value), output, indent=2, sort_keys=True)
        output.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit Gemmini host artifact format and simulator isolation")
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--role", choices=[role.value for role in HostRole], required=True)
    parser.add_argument("--command-log", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        audit = audit_host(AuditRequest(arguments.artifact, HostRole(arguments.role), arguments.command_log))
        write_result(arguments.out, audit)
    except (OSError, UnicodeError) as error:
        print(f"GEMMINI_HOST_AUDIT_FAILED: {error}", file=sys.stderr)
        return 1
    print(json.dumps(asdict(audit), indent=2, sort_keys=True))
    return {"PASS": 0, "FAIL": 1, "NOT_RUN": 2}[audit.status]


if __name__ == "__main__":
    raise SystemExit(main())
