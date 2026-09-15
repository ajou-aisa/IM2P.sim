#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = Path(os.environ.get("IM2P_WORKSPACE_ROOT", ROOT.parent)).resolve()
PROFILES = tuple(f"a{bits}w{bits}-d{dim}-hp1" for bits in (4, 8) for dim in (16, 32, 64))
StatusEntry = dict[str, str | None]
ProfileStatus = dict[str, StatusEntry]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def git(repository: Path, *arguments: str) -> str:
    result = subprocess.run(
        ("git", "-C", str(repository), *arguments), text=True,
        capture_output=True, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(arguments)} failed: {repository}: {result.stderr.strip()}")
    return result.stdout


def repository_state(repository: Path) -> dict[str, object]:
    return {
        "path": str(repository.resolve()),
        "head": git(repository, "rev-parse", "HEAD").strip(),
        "branch": git(repository, "branch", "--show-current").strip(),
        "remote": git(repository, "remote", "get-url", "origin").strip(),
        "status": git(repository, "status", "--short").splitlines(),
        "staged": git(repository, "diff", "--cached", "--name-status").splitlines(),
        "tracked_diff": git(repository, "diff", "--name-status").splitlines(),
        "untracked": git(repository, "ls-files", "--others", "--exclude-standard").splitlines(),
    }


def task_sources() -> list[Path]:
    selections = (
        ROOT / "src/gemmini",
        ROOT / "fpga/gemmini_hp1",
        ROOT / "config/gemmini_hp1_profiles.json",
        ROOT / "config/gemmini_host_memory_contracts",
        ROOT / "frontend/include/im2p_gemmini_frontend.hpp",
        ROOT / "frontend/src/im2p_gemmini_frontend.cpp",
        ROOT / "sim/include/im2p_sim.h",
    )
    files = [path for selected in selections if selected.exists()
             for path in ([selected] if selected.is_file() else selected.rglob("*"))]
    files.extend((ROOT / "scripts").glob("gemmini_*"))
    files.extend((ROOT / "tests").glob("test_gemmini_*"))
    return sorted({path for path in files if path.is_file() and not path.is_symlink()
                   and not {"target", "__pycache__", ".bloop", ".bsp"}.intersection(path.parts)})


def artifact_manifest(build_root: Path) -> list[dict[str, object]]:
    result = []
    for profile in PROFILES:
        for path in sorted((build_root / profile / "rtl").rglob("*")):
            if path.is_file() and not path.is_symlink():
                result.append({
                    "profile": profile,
                    "path": path.relative_to(build_root).as_posix(),
                    "bytes": path.stat().st_size,
                    "sha256": sha256(path),
                })
    return result


def first_failure(build: Path) -> dict[str, object] | None:
    result_path = build / "result.json"
    if not result_path.is_file():
        return None
    result = json.loads(result_path.read_text(encoding="utf-8"))
    for profile in result.get("profiles", []):
        for command in profile.get("command_results", []):
            if command.get("returncode") != 0:
                log = Path(command["log"]) if command.get("log") else None
                return {
                    "build": str(build.resolve()),
                    "profile": profile.get("profile"),
                    "command": command,
                    "first_failure_log": log.read_text(encoding="utf-8") if log and log.is_file() else None,
                }
    return None


def rows(document: dict[str, object], key: str) -> list[dict[str, object]]:
    value = document.get(key)
    if not isinstance(value, list) or not all(isinstance(row, dict) for row in value):
        raise RuntimeError(f"{key} is not an object array")
    return [row for row in value if isinstance(row, dict)]


def named_rows(document: dict[str, object]) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for row in rows(document, "profiles"):
        name = row.get("profile")
        if not isinstance(name, str):
            raise RuntimeError("profile name missing")
        result[name] = row
    return result


def status_table(build: dict[str, object], probe: dict[str, object],
                 export: dict[str, object]) -> dict[str, ProfileStatus]:
    build_profiles = named_rows(build)
    probe_profiles = named_rows(probe)
    export_profiles = named_rows(export)
    build_root = build.get("build_root")
    if not isinstance(build_root, str):
        raise RuntimeError("build root missing")
    result: dict[str, ProfileStatus] = {}
    for name in PROFILES:
        row = build_profiles[name]
        command_value = row.get("command_results")
        if not isinstance(command_value, list) or not all(isinstance(command, dict) for command in command_value):
            raise RuntimeError(f"command results missing: {name}")
        commands = [command for command in command_value if isinstance(command, dict)]
        if len(commands) < 5:
            raise RuntimeError(f"command results incomplete: {name}")
        audit_path = Path(name) / "host-audit.json"
        audit = json.loads((Path(build_root) / audit_path).read_text(encoding="utf-8"))
        if not isinstance(audit, dict):
            raise RuntimeError(f"host audit invalid: {name}")
        passed = row["status"] == "PASS"
        result[name] = {
            "elaboration": {"status": "PASS" if commands[0]["returncode"] == 0 else "FAIL"},
            "rtl_numerical": {"status": "PASS" if passed else "FAIL"},
            "host_contract": {"status": "PASS" if commands[4]["returncode"] == 0 else "FAIL"},
            "host_rtl_integration": {"status": "PASS" if commands[-1]["returncode"] == 0 else "FAIL"},
            "no_sim_host_artifact": {"status": audit["status"], "reason": audit.get("reason")},
            "memory_contract": {"status": "PASS" if name in probe_profiles else "FAIL"},
            "export": {"status": str(export_profiles[name]["status"])},
            "route": {"status": "NOT_RUN", "reason": "PLATFORM"},
            "bitstream": {"status": "NOT_RUN", "reason": "PLATFORM_BOARD_REQUIRED"},
            "physical": {"status": "NOT_RUN", "reason": "OUT_OF_SCOPE"},
            "rmd": {"status": "NOT_RUN", "reason": "UNSUPPORTED_BEFORE_DISPATCH"},
        }
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--build-root", type=Path, required=True)
    parser.add_argument("--memory-probe", type=Path, required=True)
    parser.add_argument("--export-root", type=Path, required=True)
    parser.add_argument("--controller-rtl", type=Path)
    parser.add_argument("--controller-result", type=Path)
    parser.add_argument("--numerical-build", type=Path)
    parser.add_argument("--software-root", type=Path)
    parser.add_argument("--scala-log", type=Path)
    parser.add_argument("--failed-build", type=Path, action="append", default=[])
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if os.path.lexists(args.out):
        raise SystemExit(f"new evidence directory required: {args.out}")
    build_path = args.build_root / "stage-host-test.json"
    if not build_path.is_file():
        build_path = args.build_root / "result.json"
    build = json.loads(build_path.read_text(encoding="utf-8"))
    build["build_root"] = str(args.build_root.resolve())
    probe = json.loads(args.memory_probe.read_text(encoding="utf-8"))
    export = json.loads((args.export_root / "result.json").read_text(encoding="utf-8"))
    architecture: dict[str, object] = {
        "status": "NOT_RUN", "reason": "CONTROLLER_RTL_MISSING",
    }
    if args.controller_rtl is not None:
        required = (
            "ExecuteController.sv", "LoadController.sv", "LoopMatmul.sv",
            "ReservationStation.sv", "StoreController.sv", "UpstreamWsControl.sv",
        )
        missing = [name for name in required if not (args.controller_rtl / name).is_file()]
        architecture = {
            "status": "FAIL" if missing else "PARTIAL",
            "reason": "CONTROLLER_CLOSURE_INCOMPLETE" if missing else "HP1_SIX_PROFILE_DATAPATH_NOT_USING_UPSTREAM_CONTROL",
            "control_closure": "FAIL" if missing else "PASS",
            "hp1_control_integration": "NOT_RUN",
            "path": str(args.controller_rtl.resolve()),
            "missing": missing,
            "files": [{"path": name, "sha256": sha256(args.controller_rtl / name)}
                      for name in required if name not in missing],
        }
    if build.get("status") != "PASS" or probe.get("status") != "PASS" or export.get("export") != "PASS":
        raise SystemExit("green host matrix, memory probe and export required")
    if {row.get("profile") for row in build.get("profiles", [])} != set(PROFILES):
        raise SystemExit("build result does not contain six exact profiles")

    args.out.mkdir(parents=True)
    repositories = {
        "im2p_sim": repository_state(ROOT),
        "llama_cpp_gemmini": repository_state(WORKSPACE_ROOT / "llama.cpp-gemmini"),
        "gemmini_include": repository_state(WORKSPACE_ROOT / "RISC-V-DynDNN-gemmini-include"),
    }
    write_json(args.out / "baseline.json", repositories)
    agents = [path for path in (WORKSPACE_ROOT / "AGENTS.md",
                                WORKSPACE_ROOT / "RISC-V-DynDNN-gemmini-include/AGENTS.md")
              if path.is_file()]
    write_json(args.out / "instruction-hashes.json", {
        "agents": [{"path": str(path), "sha256": sha256(path)} for path in agents],
        "plan": {
            "source": "user_prompt_inline_mac_v2",
            "original_sha256_from_plan": "51f03ab1c0370a19a9c8b290c99a36203cbf4feb1d2426b70713527f7fd886f2",
            "inline_revision_sha256": None,
            "reason": "conversation text has no stable attachment bytes",
        },
    })
    source_rows = [{
        "path": path.relative_to(ROOT).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
    } for path in task_sources()]
    write_json(args.out / "task-source-manifest.json", {"files": source_rows})
    write_json(args.out / "rtl-artifact-manifest.json", {
        "files": artifact_manifest(args.build_root),
    })
    shutil.copy2(build_path, args.out / "stage-host-test.json")
    shutil.copy2(args.memory_probe, args.out / "memory-probe.json")
    shutil.copy2(args.build_root / PROFILES[0] / "tool-lock.json", args.out / "tool-lock.json")
    shutil.copy2(ROOT / "src/gemmini/UPSTREAM.lock.json", args.out / "upstream-lock.json")
    shutil.copy2(ROOT / "src/gemmini/vendor-manifest.json", args.out / "vendor-manifest.json")
    shutil.copy2(args.export_root / "result.json", args.out / "export-result.json")
    write_json(args.out / "architecture-result.json", architecture)
    if args.controller_result is not None:
        shutil.copy2(args.controller_result, args.out / "controller-result.json")
        controller_logs = args.controller_result.parent / "logs"
        if controller_logs.is_dir():
            shutil.copytree(controller_logs, args.out / "controller-logs")
    if args.numerical_build is not None:
        shutil.copy2(args.numerical_build / "result.json", args.out / "stage-test.json")
        shutil.copytree(args.numerical_build, args.out / "numerical-build",
                        ignore=shutil.ignore_patterns("target", "rtl-test-obj"))
    if args.software_root is not None:
        shutil.copytree(args.software_root, args.out / "software-tests")
    if args.scala_log is not None:
        shutil.copy2(args.scala_log, args.out / "scala-full-test.log")
    archive = args.export_root.with_name(args.export_root.name + ".tar.gz")
    write_json(args.out / "export-artifact.json", {
        "path": str(archive.resolve()), "bytes": archive.stat().st_size, "sha256": sha256(archive),
        "relocation_verified": True,
    })
    (args.out / "resolved-profiles").mkdir()
    (args.out / "host-audits").mkdir()
    for profile in PROFILES:
        shutil.copytree(args.build_root / profile / "logs", args.out / "logs" / profile)
        shutil.copy2(args.build_root / profile / "resolved-profile.json",
                     args.out / "resolved-profiles" / f"{profile}.json")
        shutil.copy2(args.build_root / profile / "host-audit.json",
                     args.out / "host-audits" / f"{profile}.json")
    failures = [failure for path in args.failed_build if (failure := first_failure(path))]
    write_json(args.out / "first-failures.json", failures)
    (args.out / "im2p-final.diff").write_text(git(ROOT, "diff"), encoding="utf-8")
    llama = WORKSPACE_ROOT / "llama.cpp-gemmini"
    (args.out / "llama-final.diff").write_text(git(llama, "diff"), encoding="utf-8")
    profiles = status_table(build, probe, export)
    final = {
        "schema_version": 1,
        "plan_revision": "mac-v2",
        "scope": "HP1_SHIFT_ONLY_A4W4_A8W8_D16_D32_D64",
        "profiles": profiles,
        "linux_vivado": {"status": "NOT_RUN", "reason": "PLATFORM"},
        "rmd_on_ready": False,
        "hardware_access": {"uart": 0, "jtag": 0, "flash": 0},
        "git_commit_push": "NOT_RUN",
        "mac_implementation_ready": architecture.get("status") == "PASS" and all(
            all(row[field]["status"] == "PASS" for field in (
                "elaboration", "rtl_numerical", "host_contract", "host_rtl_integration",
                "no_sim_host_artifact", "memory_contract", "export",
            )) for row in profiles.values()
        ),
    }
    write_json(args.out / "final.json", final)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
