#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///

# ─── How to run ───
# 1. Install uv (if not installed):
#      curl -LsSf https://astral.sh/uv/install.sh | sh
# 2. Run directly (no venv, no pip install needed):
#      uv run tests/test_gemmini_vendor.py
# 3. Or make executable and run:
#      chmod +x tests/test_gemmini_vendor.py && ./tests/test_gemmini_vendor.py
# ─────────────────

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Final

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.im2p_paths import resolve_gemmini_work_root

ROOT: Final = Path(__file__).resolve().parents[1]
VENDOR: Final = ROOT / "scripts/gemmini_vendor.py"
CHIPYARD_PIN: Final = "69eba860a352343e4ac6b6df0f3638a79a86ec78"
GEMMINI_PIN: Final = "25809f78323a729ef76fb68f3cedd8a24da2942b"
EXPECTED_SOURCES: Final = (
    "LICENSE",
    "src/main/scala/gemmini/Activation.scala",
    "src/main/scala/gemmini/AccumulatorMem.scala",
    "src/main/scala/gemmini/Arithmetic.scala",
    "src/main/scala/gemmini/Dataflow.scala",
    "src/main/scala/gemmini/GemminiConfigs.scala",
    "src/main/scala/gemmini/LoadController.scala",
    "src/main/scala/gemmini/LoopMatmul.scala",
    "src/main/scala/gemmini/Mesh.scala",
    "src/main/scala/gemmini/MeshWithDelays.scala",
    "src/main/scala/gemmini/PE.scala",
    "src/main/scala/gemmini/Scratchpad.scala",
    "src/main/scala/gemmini/SharedExtMem.scala",
    "src/main/scala/gemmini/Shifter.scala",
    "src/main/scala/gemmini/StoreController.scala",
    "src/main/scala/gemmini/SyncMem.scala",
    "src/main/scala/gemmini/TagQueue.scala",
    "src/main/scala/gemmini/Tile.scala",
    "src/main/scala/gemmini/Transposer.scala",
    "src/main/scala/gemmini/Util.scala",
)


def git(repository: Path, arguments: list[str]) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def run_vendor(source: Path, destination: Path, *, verify: bool = False) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
        str(VENDOR),
        "--source",
        str(source),
        "--destination",
        str(destination),
    ]
    if verify:
        command.append("--verify")
    return subprocess.run(command, check=False, capture_output=True, text=True)


def source_checkout() -> Path:
    work_root = resolve_gemmini_work_root(ROOT)
    checkout = work_root / "deps/chipyard-1.13.0"
    assert checkout.is_dir(), f"pinned Chipyard checkout missing: {checkout}"
    return checkout


def tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def test_vendor_generation_when_source_is_pinned() -> None:
    # Given
    source = source_checkout()
    gemmini = source / "generators/gemmini"
    source_status = (git(source, ["status", "--porcelain=v1"]), git(gemmini, ["status", "--porcelain=v1"]))

    with tempfile.TemporaryDirectory(prefix="im2p-gemmini-vendor-") as directory:
        destination = Path(directory) / "gemmini"

        # When
        result = run_vendor(source, destination)

        # Then
        assert result.returncode == 0, result.stderr
        lock = (destination / "UPSTREAM.lock.json").read_text(encoding="utf-8")
        manifest = (destination / "vendor-manifest.json").read_text(encoding="utf-8")
        assert lock.count(f'"commit": "{CHIPYARD_PIN}"') == 1
        assert lock.count(f'"commit": "{GEMMINI_PIN}"') == 1
        assert f'"chipyard_gitlink": "{GEMMINI_PIN}"' in lock
        upstream_paths: list[str] = re.findall(r'^\s+"upstream_path": "([^"]+)"', manifest, flags=re.MULTILINE)
        snapshot_paths: list[str] = re.findall(r'^\s+"snapshot_path": "([^"]+)"', manifest, flags=re.MULTILINE)
        blob_shas: list[str] = re.findall(r'^\s+"git_blob_sha": "([0-9a-f]+)"', manifest, flags=re.MULTILINE)
        snapshot_shas: list[str] = re.findall(r'^\s+"snapshot_sha256": "([0-9a-f]+)"', manifest, flags=re.MULTILINE)
        assert tuple(upstream_paths) == EXPECTED_SOURCES
        assert len(snapshot_paths) == len(blob_shas) == len(snapshot_shas) == len(EXPECTED_SOURCES)
        assert manifest.count('"patches": []') == len(EXPECTED_SOURCES) - 4
        assert manifest.count('"compile_overlay": true') == 4
        patch = destination / "patches/0001-packed-input-controller-bytes.patch"
        assert f'"patch_sha256": "{hashlib.sha256(patch.read_bytes()).hexdigest()}"' in manifest
        assert manifest.count('"extraction": "full_file"') == len(EXPECTED_SOURCES)
        for upstream_path, snapshot_path, blob_sha, snapshot_sha in zip(
            upstream_paths, snapshot_paths, blob_shas, snapshot_shas, strict=True
        ):
            snapshot = destination / snapshot_path
            expected = subprocess.run(
                ["git", "-C", str(gemmini), "show", f"{GEMMINI_PIN}:{upstream_path}"],
                check=True,
                capture_output=True,
            ).stdout
            assert snapshot.read_bytes() == expected
            assert blob_sha == git(gemmini, ["rev-parse", f"{GEMMINI_PIN}:{upstream_path}"])
            assert snapshot_sha == hashlib.sha256(expected).hexdigest()
        assert source_status == (
            git(source, ["status", "--porcelain=v1"]),
            git(gemmini, ["status", "--porcelain=v1"]),
        )


def test_vendor_is_idempotent_when_destination_matches() -> None:
    # Given
    with tempfile.TemporaryDirectory(prefix="im2p-gemmini-idempotent-") as directory:
        destination = Path(directory) / "gemmini"
        first = run_vendor(source_checkout(), destination)
        assert first.returncode == 0, first.stderr
        before = tree_digest(destination)

        # When
        second = run_vendor(source_checkout(), destination)

        # Then
        assert second.returncode == 0, second.stderr
        assert tree_digest(destination) == before


def test_verify_rejects_tampered_snapshot() -> None:
    # Given
    with tempfile.TemporaryDirectory(prefix="im2p-gemmini-tamper-") as directory:
        destination = Path(directory) / "gemmini"
        generated = run_vendor(source_checkout(), destination)
        assert generated.returncode == 0, generated.stderr
        _ = (destination / "upstream/src/main/scala/gemmini/PE.scala").write_text("tampered\n", encoding="utf-8")

        # When
        result = run_vendor(source_checkout(), destination, verify=True)

        # Then
        assert result.returncode != 0
        assert "snapshot mismatch" in result.stderr


def test_vendor_rejects_wrong_pin_and_dirty_dependency() -> None:
    source = source_checkout()

    with tempfile.TemporaryDirectory(prefix="im2p-gemmini-reject-") as directory:
        temporary = Path(directory)

        # Given: repository with wrong Chipyard identity.
        wrong_source = source / "generators/gemmini"

        # When
        wrong = run_vendor(wrong_source, temporary / "wrong")

        # Then
        assert wrong.returncode != 0
        assert "Chipyard HEAD mismatch" in wrong.stderr

        # Given: exact local mirrors, dirtied without touching dependency checkout.
        mirror = temporary / "chipyard"
        _ = subprocess.run(
            ["git", "clone", "--quiet", "--shared", str(source), str(mirror)], check=True, capture_output=True
        )
        mirror_gemmini = mirror / "generators/gemmini"
        _ = subprocess.run(
            ["git", "clone", "--quiet", "--shared", str(source / "generators/gemmini"), str(mirror_gemmini)],
            check=True,
            capture_output=True,
        )
        _ = (mirror_gemmini / "dirty.marker").write_text("dirty\n", encoding="utf-8")

        # When
        dirty = run_vendor(mirror, temporary / "dirty")

        # Then
        assert dirty.returncode != 0
        assert "Gemmini checkout is dirty" in dirty.stderr
        for rejected_source in (wrong_source, mirror):
            overlay = temporary / "rejected-overlay"
            result = subprocess.run(
                [sys.executable, str(VENDOR), "--source", str(rejected_source), "--overlay", str(overlay)],
                check=False, capture_output=True, text=True,
            )
            assert result.returncode != 0 and not overlay.exists()


def test_overlay_is_reproducible_without_dependency_writes() -> None:
    # Given
    source = source_checkout()
    gemmini = source / "generators/gemmini"
    names = ("GemminiConfigs.scala", "LoadController.scala", "LoopMatmul.scala", "StoreController.scala")
    prefix = "src/main/scala/gemmini/"
    originals = {name: (gemmini / prefix / name).read_bytes() for name in names}
    with tempfile.TemporaryDirectory(prefix="im2p-gemmini-overlay-") as directory:
        overlay = Path(directory) / "overlay"
        command = [sys.executable, str(VENDOR), "--source", str(source), "--overlay", str(overlay)]
        # When
        result = subprocess.run(command, check=False, capture_output=True, text=True)
        # Then
        assert result.returncode == 0, result.stderr
        assert sorted(path.name for path in overlay.rglob("*.scala")) == sorted(names)
        config = (overlay / prefix / names[0]).read_text()
        load = (overlay / prefix / names[1]).read_text()
        loop = (overlay / prefix / names[2]).read_text()
        store = (overlay / prefix / names[3]).read_text()
        assert config.count("dma_maxbytes * 8 / inputType.getWidth") == 2
        assert "val row_bits = Mux" in load
        assert "((row_bits +& 7.U) >> 3) * actual_rows_read" in load
        assert "input_w/8" not in loop
        assert "dma_max_bytes * 8 / (block_size * input_w)" in loop
        assert "dma_maxbytes * 8 / (block_cols * inputType.getWidth)" in store
        assert originals == {name: (gemmini / prefix / name).read_bytes() for name in names}
        assert git(gemmini, ["status", "--porcelain=v1"]) == ""
        before = tree_digest(overlay)
        repeated = subprocess.run(command, check=False, capture_output=True, text=True)
        assert repeated.returncode != 0 and "overlay already exists" in repeated.stderr
        assert tree_digest(overlay) == before
        second = Path(directory) / "second"
        command[-1] = str(second)
        regenerated = subprocess.run(command, check=False, capture_output=True, text=True)
        assert regenerated.returncode == 0, regenerated.stderr
        assert tree_digest(second) == before
        empty = Path(directory) / "empty"
        empty.mkdir()
        command[-1] = str(empty)
        rejected = subprocess.run(command, check=False, capture_output=True, text=True)
        assert rejected.returncode != 0 and not list(empty.iterdir())


def test_focused_patches_have_independent_effects() -> None:
    """Prove each patch has one purpose against immutable pinned source bytes."""
    source = source_checkout() / "generators/gemmini"
    names = ("GemminiConfigs.scala", "LoadController.scala", "LoopMatmul.scala", "StoreController.scala")
    prefix = Path("src/main/scala/gemmini")
    old = "val head_loop_id = Reg(UInt(log2Up(concurrent_loops).W))"
    new = "val head_loop_id = RegInit(0.U(log2Up(concurrent_loops).W))"
    with tempfile.TemporaryDirectory(prefix="im2p-focused-patches-") as directory:
        root = Path(directory)
        for index in (1, 2):
            case = root / str(index)
            (case / prefix).mkdir(parents=True)
            originals = {name: (source / prefix / name).read_bytes() for name in names}
            for name, content in originals.items():
                (case / prefix / name).write_bytes(content)
            patch_name = ("0001-packed-input-controller-bytes.patch" if index == 1
                          else "0002-loop-head-reset.patch")
            patch = ROOT / "src/gemmini/patches" / patch_name
            for args in (["apply", "--check"], ["apply"]):
                subprocess.run(["git", "-C", str(case), *args, str(patch)], check=True)
            loop = (case / prefix / "LoopMatmul.scala").read_text()
            if index == 1:
                assert old in loop and new not in loop
                assert "input_w/8" not in loop
                assert all((case / prefix / name).read_bytes() != originals[name] for name in names)
            else:
                assert loop == originals["LoopMatmul.scala"].decode().replace(old, new)
                assert all((case / prefix / name).read_bytes() == originals[name]
                           for name in names if name != "LoopMatmul.scala")


def main() -> None:
    test_focused_patches_have_independent_effects()
    test_overlay_is_reproducible_without_dependency_writes()
    test_vendor_generation_when_source_is_pinned()
    test_vendor_is_idempotent_when_destination_matches()
    test_verify_rejects_tampered_snapshot()
    test_vendor_rejects_wrong_pin_and_dirty_dependency()
    print("GEMMINI VENDOR: PASS (pin, gitlink, blob/snapshot hash, idempotence, dirty rejection)")


if __name__ == "__main__":
    main()
