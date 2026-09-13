#!/usr/bin/env python3
"""Materialize the sealed SCU sources; build only with explicit --build."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import shutil
import stat
import subprocess
import sys

SOURCE_SHA256 = "607827f129c61b60dffd9e9fb02fd0ceb4a7ab2abbeb25bd6b08d4d9b2782d69"
SOURCE_FILES = 1746
SEMANTICS = {"abi": 5, "numerical_revision": "signed-scu-sat-v2",
             "profile": "a8-w8-d16", "scale_storage_bytes": 4,
             "host_pin": "7a74ac29c9c8fa8fbc5d47cf073b8b9266e3e7a9",
             "params_pin": "cd2caed8d93e49d3f8873bd6c4e340fcad54b9e0"}


def digest(path):
    with path.open("rb") as source:
        result = hashlib.sha256()
        for block in iter(lambda: source.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def regular_file(root, relative):
    name = PurePosixPath(relative)
    if name.is_absolute() or ".." in name.parts or str(name) != relative:
        raise ValueError(f"invalid selected path: {relative}")
    path = root
    for part in name.parts:
        path = path / part
        if path.is_symlink():
            raise ValueError(f"symlink in selected path: {relative}")
    if not stat.S_ISREG(path.stat().st_mode):
        raise ValueError(f"not a regular selected file: {relative}")
    return path


def verify_frozen(frozen):
    manifest = regular_file(frozen, "integration-sha256.json")
    if digest(manifest) != SOURCE_SHA256:
        raise ValueError("sealed integration manifest SHA256 mismatch")
    entries = json.loads(manifest.read_text())
    if not isinstance(entries, dict) or len(entries) != SOURCE_FILES:
        raise ValueError("sealed integration file count mismatch")
    identity = json.loads(regular_file(frozen, "identity.json").read_text())
    if identity.get("source_sha256") != SOURCE_SHA256 or any(
            identity.get(key) != value for key, value in SEMANTICS.items()):
        raise ValueError("sealed source/ABI/numerical/profile/pin mismatch")
    for name, expected in entries.items():
        if PurePosixPath(name).parts[0] not in ("source", "host", "params"):
            raise ValueError(f"unexpected selected source role: {name}")
        if digest(regular_file(frozen, name)) != expected:
            raise ValueError(f"selected source SHA256 mismatch: {name}")
    return entries, identity


def write_json(path, value):
    with path.open("x") as output:
        json.dump(value, output, indent=2)
        output.write("\n")


def materialize(frozen, run, entries, original):
    run.mkdir(parents=True, exist_ok=False)
    for name in entries:
        target = run / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(regular_file(frozen, name), target)
    shutil.copy2(frozen / "integration-sha256.json", run / "integration-sha256.json")
    shutil.copy2(frozen / "identity.json", run / "identity-origin.json")
    derived = dict(original)
    for key in ("sim_archive", "sim_archive_sha256", "sim_archive_machine",
                "native_environment", "native_toolchain"):
        derived.pop(key, None)
    derived.update(machine=platform.machine(), sim_archive=None,
                   physical_board_access=False,
                   migration_origin={"integration_sha256": SOURCE_SHA256,
                                     "identity_sha256": digest(run / "identity-origin.json"),
                                     "selected_files": len(entries),
                                     "original_identity": "identity-origin.json"})
    write_json(run / "identity.json", derived)
    verify_frozen(run)


def commands(run, jobs):
    script = run / "source/fpga/scu_block_scale/build.py"
    return [[sys.executable, str(script), stage, str(run)] +
            (["--jobs", str(jobs)] if stage in ("host", "host-fpga") else [])
            for stage in ("native", "host", "host-fpga", "verify")]


def build_environment(run):
    environment = os.environ.copy()
    for name in ("CARGO_BUILD_TARGET", "CMAKE_TOOLCHAIN_FILE"):
        if environment.get(name):
            raise ValueError(f"native recipe does not support cross setting {name}")
    environment.update(TMPDIR=str(run / "tmp"), TMP=str(run / "tmp"),
                       TEMP=str(run / "tmp"), XDG_CACHE_HOME=str(run / "cache/xdg"),
                       PYTHONPYCACHEPREFIX=str(run / "cache/python"),
                       CARGO_NET_OFFLINE="true", CCACHE_DISABLE="1", SCCACHE_DISABLE="1")
    return environment


def cargo_sources(package):
    prefix = "host/toolchain/cargo-vendor/"
    vendor = package / prefix
    manifest = package / "manifest.json"
    inventory = json.loads(regular_file(package, "manifest.json").read_text()) if manifest.exists() else {}
    expected = {name: row for name, row in inventory.get("files", {}).items() if name.startswith(prefix)}
    if not vendor.exists() and not vendor.is_symlink():
        if expected:
            raise ValueError("manifest-listed Cargo vendor directory is missing")
        return {"kind": "existing_cargo_home", "offline": True,
                "reason": "Package has no vendored crates; caller must supply the locked registry cache",
                "cargo_home": os.environ.get("CARGO_HOME", str(Path.home() / ".cargo"))}
    if not expected or inventory.get("source_manifest_sha256") != SOURCE_SHA256:
        raise ValueError("Cargo vendor requires a sealed package file manifest")
    actual = set()
    for path in vendor.rglob("*"):
        if path.is_symlink():
            raise ValueError("symlink in Cargo vendor directory")
        if path.is_file():
            name = path.relative_to(package).as_posix()
            path = regular_file(package, name)
            row = expected.get(name, {})
            if digest(path) != row.get("sha256") or path.stat().st_size != row.get("bytes"):
                raise ValueError(f"Cargo vendor SHA256/size mismatch: {name}")
            actual.add(name)
    if actual != set(expected):
        raise ValueError("Cargo vendor file inventory mismatch")
    return {"kind": "vendored", "offline": True, "directory": str(vendor),
            "files": len(actual), "package_manifest_sha256": digest(manifest),
            "cargo_lock_sha256": digest(regular_file(package, "host/frozen/source/sim/Cargo.lock"))}


def build(run, jobs, cargo=None):
    environment = build_environment(run)
    if cargo and cargo["kind"] == "vendored":
        cargo_home = run / "cache/cargo"
        cargo_home.mkdir(parents=True, exist_ok=False)
        config = ('[source.crates-io]\nreplace-with = "vendored-sources"\n\n'
                  '[source.vendored-sources]\ndirectory = ' + json.dumps(cargo["directory"]) + '\n')
        (cargo_home / "config.toml").write_text(config)
        environment["CARGO_HOME"] = str(cargo_home)
        cargo = dict(cargo, config_sha256=digest(cargo_home / "config.toml"))
    (run / "tmp").mkdir()
    evidence = run / "migration-evidence"
    evidence.mkdir()
    for index, command in enumerate(commands(run, jobs)):
        stage = command[2]
        record = {"argv": command, "cwd": str(run),
                  "start_utc": datetime.now(timezone.utc).isoformat(), "cargo_sources": cargo,
                  "environment": {name: environment.get(name) for name in
                      ("TMPDIR", "TMP", "TEMP", "XDG_CACHE_HOME", "PYTHONPYCACHEPREFIX",
                       "CARGO_HOME", "CARGO_NET_OFFLINE", "CC", "CXX", "RUSTFLAGS")}}
        prefix = evidence / f"{index:02}-{stage}"
        with prefix.with_suffix(".log").open("x") as log:
            result = subprocess.run(command, cwd=run, env=environment,
                                    stdout=log, stderr=subprocess.STDOUT)
        record.update(exit=result.returncode, end_utc=datetime.now(timezone.utc).isoformat())
        write_json(prefix.with_suffix(".json"), record)
        if result.returncode:
            raise RuntimeError(f"native stage {stage} failed; preserved {prefix}.log; no retry")
    verify_frozen(run)
    artifacts = ["source/build/selected/a8-w8-d16/current/libim2p_sim.a",
                 "source/build/selected/a8-w8-d16/current/libim2p_gemmini_frontend.a",
                 "host-build/scu_frontend_final", "fpga-host-build/scu_host_dispatch"]
    expected_machine = {"x86_64": 62, "aarch64": 183}[platform.machine()]
    rows = {}
    for name in artifacts:
        path = run / name
        if path.suffix == ".a":
            members = subprocess.check_output(["ar", "t", str(path)], text=True).splitlines()
            member = next(item for item in members if item.endswith(".o"))
            header = subprocess.check_output(["ar", "p", str(path), member])[:20]
        else:
            with path.open("rb") as source:
                header = source.read(20)
        if header[:6] != b"\x7fELF\x02\x01" or int.from_bytes(header[18:20], "little") != expected_machine:
            raise ValueError(f"native ELF architecture mismatch: {name}")
        rows[name] = {"sha256": digest(path), "bytes": path.stat().st_size,
                      "ELF_machine": expected_machine,
                      "ELF_check": "first_object_member" if path.suffix == ".a" else "executable"}
    for name in ("source/build/selected/a8-w8-d16/current/real-lib.json",
                 "host-build-sha256.json", "fpga-host-build-sha256.json"):
        rows[name] = {"sha256": digest(run / name), "bytes": (run / name).stat().st_size}
    write_json(run / "native-build-manifest.json", {
        "status": "BUILT_NOT_RUNTIME_TESTED", "machine": platform.machine(),
        "source_sha256": SOURCE_SHA256, "identity_sha256": digest(run / "identity.json"),
        "artifacts": rows, "cargo_sources": cargo, "cross_build": False, "board_access": False,
        "ARM64_runtime": "NOT_RUN"})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--materialize", action="store_true")
    action.add_argument("--build", action="store_true")
    parser.add_argument("--jobs", type=int, default=2)
    args = parser.parse_args(argv)
    try:
        package, run = args.package.resolve(), args.run.resolve()
        if args.jobs < 1 or run.is_relative_to(package) or os.path.lexists(args.run):
            raise ValueError("positive jobs and a nonexistent RUN outside the package are required")
        frozen = package / "host/frozen"
        regular_file(package, "host/frozen/integration-sha256.json")
        entries, original = verify_frozen(frozen)
        cargo = cargo_sources(package)
        if args.build:
            build_environment(run)
            if platform.system() != "Linux" or platform.machine() not in ("x86_64", "aarch64"):
                raise ValueError("NOT_RUN: native Linux x86_64/aarch64 required; no cross backend")
            missing = [tool for tool in ("make", "g++", "ar", "bsc", "verilator", "rustc", "cargo", "cmake")
                       if shutil.which(tool) is None]
            if missing:
                raise ValueError("NOT_RUN: missing native tools: " + ", ".join(missing))
        if args.materialize or args.build:
            materialize(frozen, run, entries, original)
        if args.build:
            build(run, args.jobs, cargo)
        print(json.dumps({"status": "BUILT_NOT_RUNTIME_TESTED" if args.build else
                         "MATERIALIZED" if args.materialize else "CHECKED_DRY_RUN",
                          "source_sha256": SOURCE_SHA256, "selected_files": len(entries),
                          "machine": platform.machine(), "commands": commands(run, args.jobs),
                          "cargo_sources": cargo,
                          "ARM64_runtime": "NOT_RUN", "board_access": False}, indent=2))
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"NATIVE_BUILD_STOP: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
