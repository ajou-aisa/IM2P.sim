#!/usr/bin/env python3
"""Deterministic contracts for the production real-library cache."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "scripts/real_lib_cache.py"


def run(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *args], cwd=ROOT, env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
    )


def cache_args(temp: Path, fixture: Path, builder: Path) -> list[str]:
    fake_tool = temp / "fake-tool"
    verilator_include = temp / "verilator-root" / "include"
    verilator_include.mkdir(parents=True, exist_ok=True)
    for name in (
        "verilated.cpp", "verilated_threads.cpp",
        "verilated.h", "verilated_threads.h",
    ):
        (verilator_include / name).write_text(f"// {name}\n", encoding="utf-8")
    fake_tool.write_text(
        "#!/bin/sh\n"
        f"test \"${{1:-}}\" = -V && echo 'VERILATOR_ROOT = {temp / 'verilator-root'}' "
        "|| echo fake-tool-v1\n",
        encoding="utf-8",
    )
    fake_tool.chmod(0o755)
    bsc_verilog = temp / "bsc-verilog"
    bsc_verilog.mkdir(exist_ok=True)
    for name in ("RegFile.v", "FIFO2.v", "BRAM1.v"):
        (bsc_verilog / name).write_text(f"// {name}\n", encoding="utf-8")
    return [
        str(CACHE), "ensure", "--bits", "4", "--weight-bits", "4",
        "--dim", "16", "--block-size", "32", "--build-dir", str(temp / "build"),
        "--gemmini-root", str(ROOT.parent / "llama.cpp-gemmini"),
        "--params-root", str(ROOT.parent / "RISC-V-DynDNN-gemmini-include/include"),
        "--cxx", str(fake_tool), "--ar", str(fake_tool), "--bsc", str(fake_tool),
        "--bsc-verilog", str(bsc_verilog), "--bsc-extra-flags", "-steps 123",
        "--verilator", str(fake_tool), "--rustc", str(fake_tool),
        "--cargo", str(fake_tool), "--extra-input", str(fixture),
        "--builder", str(builder),
    ]


def main() -> int:
    assert CACHE.is_file(), "production cache driver is missing"
    with tempfile.TemporaryDirectory(prefix="im2p-real-lib-cache-") as raw:
        temp = Path(raw)
        fixture = temp / "source-input"
        fixture.write_text("source-v1\n", encoding="utf-8")
        count = temp / "build-count"
        builder = temp / "builder.py"
        builder.write_text(
            """#!/usr/bin/env python3
import os
from pathlib import Path
root = Path(os.environ['IM2P_CACHE_STAGE_BUILD_DIR'])
identity = os.environ['IM2P_CACHE_ARTIFACT_ID']
count = Path(os.environ['IM2P_TEST_BUILD_COUNT'])
count.write_text(count.read_text() + 'x' if count.exists() else 'x')
(root / 'lib' / identity).mkdir(parents=True)
(root / 'lib' / identity / 'libim2p_gemmini_frontend.a').write_bytes(b'frontend')
(root / 'cargo' / identity / 'release').mkdir(parents=True)
(root / 'cargo' / identity / 'release' / 'libim2p_sim.a').write_bytes(b'simulator')
obj = root / 'verilator' / identity / 'obj_dir'
obj.mkdir(parents=True)
(obj / 'Vmodel.h').write_bytes(b'header')
(obj / 'Vmodel.a').write_bytes(b'model')
""",
            encoding="utf-8",
        )
        builder.chmod(0o755)
        env = os.environ.copy()
        env["IM2P_TEST_BUILD_COUNT"] = str(count)
        args = cache_args(temp, fixture, builder)

        cold = run(*args, env=env)
        assert cold.returncode == 0, cold.stdout
        assert "state=rebuild" in cold.stdout and count.read_text() == "x"
        current = temp / "build/selected/a4-w4-d16/current"
        assert current.is_symlink(), "selected pair must use one atomic generation pointer"
        manifest_path = current / "real-lib.json"
        manifest = json.loads(manifest_path.read_text())
        assert manifest["identity"]["block_size"] == 32
        assert manifest["identity"]["platform"] and manifest["identity"]["arch"]
        assert set(manifest["toolchains"]) == {
            "cxx", "ar", "bsc", "verilator", "rustc", "cargo", "make"
        }
        assert manifest["build_config"]["bsc_extra_flags"] == "-steps 123"
        assert manifest["build_config"]["bsc_verilog"]
        assert manifest["artifacts"] and all("sha256" in row for row in manifest["artifacts"])
        verified = run(str(CACHE), "verify", "--manifest", str(manifest_path))
        assert verified.returncode == 0, verified.stdout
        expected = run(
            str(CACHE), "verify", "--manifest", str(manifest_path),
            "--expected-identity", "a4-w4-d16",
            "--expected-block-size", "32",
            "--expected-platform", manifest["identity"]["platform"],
            "--expected-arch", manifest["identity"]["arch"],
        )
        assert expected.returncode == 0, expected.stdout
        wrong_block = run(
            str(CACHE), "verify", "--manifest", str(manifest_path),
            "--expected-block-size", "64",
        )
        assert wrong_block.returncode != 0, wrong_block.stdout
        for malicious_path in ("/etc/hosts", "../outside"):
            malicious = json.loads(manifest_path.read_text())
            malicious["artifacts"][0]["path"] = malicious_path
            malicious_path_file = temp / "malicious.json"
            malicious_path_file.write_text(json.dumps(malicious))
            assert run(
                str(CACHE), "verify", "--manifest", str(malicious_path_file)
            ).returncode != 0
        duplicate = json.loads(manifest_path.read_text())
        duplicate["artifacts"] = [duplicate["artifacts"][0]] * 2
        duplicate_file = temp / "duplicate.json"
        duplicate_file.write_text(json.dumps(duplicate))
        assert run(
            str(CACHE), "verify", "--manifest", str(duplicate_file)
        ).returncode != 0
        assert {
            row["path"] for row in manifest["artifacts"]
        } == {
            "libim2p_gemmini_frontend.a",
            "libim2p_sim.a",
        }

        hit = run(*args, env=env)
        assert hit.returncode == 0, hit.stdout
        assert "state=hit" in hit.stdout and count.read_text() == "x"

        selected = current / "libim2p_gemmini_frontend.a"
        selected.chmod(0o644)
        selected.write_bytes(b"tampered-selected")
        assert run(str(CACHE), "verify", "--manifest", str(manifest_path)).returncode != 0
        repaired = run(*args, env=env)
        assert repaired.returncode == 0 and selected.read_bytes() == b"frontend"
        assert count.read_text() == "x", repaired.stdout

        cache_manifest = Path(manifest["cache_manifest"])
        cache_artifact = cache_manifest.parent / "artifacts/lib/a4-w4-d16/libim2p_gemmini_frontend.a"
        cache_artifact.write_bytes(b"tampered-cache")
        tamper = run(*args, env=env)
        assert tamper.returncode == 0 and "reason=tamper" in tamper.stdout
        assert count.read_text() == "xx", tamper.stdout

        fixture.write_text("source-concurrent\n", encoding="utf-8")
        first = subprocess.Popen([sys.executable, *args], cwd=ROOT, env=env, text=True,
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        second = subprocess.Popen([sys.executable, *args], cwd=ROOT, env=env, text=True,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        out1 = first.communicate()[0]
        out2 = second.communicate()[0]
        assert first.returncode == second.returncode == 0, out1 + out2
        assert count.read_text() == "xxx", out1 + out2
        assert sum("state=hit" in output for output in (out1, out2)) == 1
        assert sum("state=rebuild" in output for output in (out1, out2)) == 1

        for index, failpoint in enumerate(
            ("materialize-after-first-artifact", "materialize-before-switch")
        ):
            stable_generation = current.resolve()
            fixture.write_text(f"source-materialize-{index}\n", encoding="utf-8")
            failed_env = env.copy()
            failed_env["IM2P_CACHE_FAILPOINT"] = failpoint
            materialize_failed = run(*args, env=failed_env)
            assert materialize_failed.returncode != 0, materialize_failed.stdout
            assert current.resolve() == stable_generation
            assert run(
                str(CACHE), "verify", "--manifest", str(manifest_path)
            ).returncode == 0
            assert not [
                path for path in current.parent.rglob("*")
                if path.name.startswith(".")
            ], "materialization interruption leaked a partial generation"
            recovered = run(*args, env=env)
            assert recovered.returncode == 0 and "state=hit" in recovered.stdout
            assert current.resolve() != stable_generation

        failing = temp / "failing.py"
        failing.write_text("#!/usr/bin/env python3\nraise SystemExit(17)\n", encoding="utf-8")
        failing.chmod(0o755)
        fixture.write_text("source-interrupted\n", encoding="utf-8")
        interrupted_args = cache_args(temp, fixture, failing)
        interrupted = run(*interrupted_args, env=env)
        assert interrupted.returncode == 17
        staging = temp / "build/cache/real-lib/staging"
        assert not list(staging.iterdir()), "interrupted staging was published or leaked"

        fixture.write_text("source-v2\n", encoding="utf-8")
        changed = run(*args, env=env)
        assert changed.returncode == 0 and "state=rebuild" in changed.stdout
        assert count.read_text() == "xxxxxx"
        changed_manifest = json.loads(manifest_path.read_text())
        assert changed_manifest["fingerprint"] != manifest["fingerprint"]

        block_args = args.copy()
        block_args[block_args.index("--block-size") + 1] = "64"
        block_changed = run(*block_args, env=env)
        assert block_changed.returncode == 0 and "state=rebuild" in block_changed.stdout
        assert count.read_text() == "xxxxxxx"
        assert json.loads(manifest_path.read_text())["fingerprint"] != changed_manifest["fingerprint"]

        flags_args = args.copy()
        flags_args[flags_args.index("--bsc-extra-flags") + 1] = "-steps 456"
        flags_changed = run(*flags_args, env=env)
        assert flags_changed.returncode == 0 and "state=rebuild" in flags_changed.stdout
        assert count.read_text() == "xxxxxxxx"

        cargo_env = env.copy()
        cargo_env["CARGO_BUILD_RUSTFLAGS"] = "-Ctarget-cpu=generic"
        cargo_changed = run(*args, env=cargo_env)
        assert cargo_changed.returncode == 0
        assert "state=rebuild" in cargo_changed.stdout
        assert count.read_text() == "xxxxxxxxx"

        mixed = args.copy()
        mixed[mixed.index("--weight-bits") + 1] = "8"
        rejected = run(*mixed, env=env)
        assert rejected.returncode != 0 and "matched" in rejected.stdout

        current_manifest = json.loads(manifest_path.read_text())
        cache_manifest = Path(current_manifest["cache_manifest"])
        stale = json.loads(cache_manifest.read_text())
        del stale["identity"]["accumulator_bits"]
        cache_manifest.write_text(json.dumps(stale))
        prior_builds = count.read_text()
        stale_rebuilt = run(*args, env=cargo_env)
        assert stale_rebuilt.returncode == 0 and "reason=tamper" in stale_rebuilt.stdout
        assert count.read_text() == prior_builds + "x"

        primitive = temp / "bsc-verilog/BRAM1.v"
        primitive.write_text("// changed BRAM primitive\n", encoding="utf-8")
        primitive_rebuilt = run(*args, env=cargo_env)
        assert primitive_rebuilt.returncode == 0 and "state=rebuild" in primitive_rebuilt.stdout
        assert count.read_text() == prior_builds + "xx"
        assert json.loads(manifest_path.read_text())["fingerprint"] != current_manifest["fingerprint"]

        poison = temp / "poison"
        escape = temp / "escape"
        (poison / "build").mkdir(parents=True)
        escape.mkdir()
        (poison / "build/cache").symlink_to(escape, target_is_directory=True)
        poisoned = run(*cache_args(poison, fixture, builder), env=env)
        assert poisoned.returncode != 0
        assert not list(escape.iterdir()), "cache restore followed a symlinked parent"

    print("REAL LIB CACHE CONTRACT PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
