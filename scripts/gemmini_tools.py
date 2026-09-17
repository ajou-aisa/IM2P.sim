#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
# Run: uv run scripts/gemmini_tools.py --out NEW_TOOL_LOCK.json

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Final, TypeAlias

JsonValue: TypeAlias = str | int | bool | None | Sequence["JsonValue"] | Mapping[str, "JsonValue"]
ROOT: Final = Path(__file__).resolve().parents[1]

if __package__ is None:
    sys.path.insert(0, str(ROOT))

from scripts.im2p_paths import resolve_gemmini_work_root


def build_environment(work_root: Path) -> Mapping[str, str]:
    environment = dict(os.environ)
    environment.setdefault("IM2P_GEMMINI_WORK_ROOT", str(work_root.resolve()))
    if platform.system() == "Darwin":
        configured_firtool = environment.get("CHISEL_FIRTOOL_PATH")
        pinned_firtool = (
            work_root / "deps/firtool-1.62.0-macos-x64"
            / "org.chipsalliance/llvm-firtool/macos-x64/bin"
        )
        if not configured_firtool or not (Path(configured_firtool) / "firtool").is_file():
            if (pinned_firtool / "firtool").is_file():
                environment["CHISEL_FIRTOOL_PATH"] = str(pinned_firtool)
            elif located_firtool := shutil.which("firtool", path=environment.get("PATH")):
                environment["CHISEL_FIRTOOL_PATH"] = str(Path(located_firtool).resolve().parent)
        if "JAVA_HOME" not in environment:
            probe = subprocess.run(
                ("/usr/libexec/java_home",), text=True, capture_output=True, check=False,
            )
            java_home = Path(probe.stdout.strip())
            if probe.returncode == 0 and (java_home / "bin/java").is_file():
                environment["JAVA_HOME"] = str(java_home)
            elif located_java := shutil.which("java", path=environment.get("PATH")):
                java_home = Path(located_java).resolve().parent.parent
                if (java_home / "bin/java").is_file():
                    environment["JAVA_HOME"] = str(java_home)
    return environment


def sha256(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def probe_tool(
    executable: str, arguments: Sequence[str], environment: Mapping[str, str],
) -> Mapping[str, JsonValue]:
    located = shutil.which(executable, path=environment.get("PATH"))
    if located is None:
        return {"status": "NOT_RUN", "reason": "DEPENDENCY", "path": executable}
    binary = Path(located).resolve()
    result: dict[str, JsonValue] = {"path": str(binary), "sha256": sha256(binary)}
    try:
        completed = subprocess.run(
            (str(binary), *arguments), text=True, capture_output=True,
            check=False, env=environment, timeout=30,
        )
        result.update({
            "status": "PASS" if completed.returncode == 0 else "FAIL",
            "returncode": completed.returncode,
            "version_output": (completed.stdout + completed.stderr).strip(),
        })
    except (OSError, subprocess.TimeoutExpired) as error:
        result.update({"status": "NOT_RUN", "reason": "DEPENDENCY", "detail": str(error)})
    if shutil.which("file") is not None:
        kind = subprocess.run(("file", "-b", str(binary)), text=True, capture_output=True, check=False)
        result["file_format"] = kind.stdout.strip() if kind.returncode == 0 else None
    return result


def collect_tool_lock(work_root: Path, environment: Mapping[str, str]) -> Mapping[str, JsonValue]:
    java_home = environment.get("JAVA_HOME")
    firtool_dir = environment.get("CHISEL_FIRTOOL_PATH")
    tools = {
        "python": probe_tool(sys.executable, ("--version",), environment),
        "java": probe_tool(str(Path(java_home) / "bin/java") if java_home else "java", ("-version",), environment),
        "sbt": probe_tool("sbt", ("--script-version",), environment),
        "firtool": probe_tool(str(Path(firtool_dir) / "firtool") if firtool_dir else "firtool", ("--version",), environment),
        "verilator": probe_tool("verilator", ("--version",), environment),
        "cmake": probe_tool("cmake", ("--version",), environment),
        "cxx": probe_tool(environment.get("CXX", "c++"), ("--version",), environment),
    }
    inputs = ("src/gemmini/build.sbt", "src/gemmini/project/build.properties", "src/gemmini/UPSTREAM.lock.json")
    return {
        "schema_version": 1,
        "host": {"system": platform.system(), "machine": platform.machine(), "release": platform.release()},
        "tools": tools,
        "build_configuration": {
            name: {"sha256": sha256(ROOT / name), "text": (ROOT / name).read_text(encoding="utf-8")}
            for name in inputs if (ROOT / name).is_file()
        },
        "selected_environment": {key: environment.get(key) for key in ("JAVA_HOME", "CHISEL_FIRTOOL_PATH")},
        "compatibility_evidence": "stage command exit codes and Scala/elaboration logs; probes alone are not build verification",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Record selected Gemmini build tools without changing the system")
    parser.add_argument("--work-root", type=Path, default=resolve_gemmini_work_root(ROOT))
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("x", encoding="utf-8") as output:
        json.dump(collect_tool_lock(args.work_root, build_environment(args.work_root)), output, indent=2, sort_keys=True)
        output.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
