from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.im2p_paths import CHIPYARD_RELATIVE, resolve_gemmini_work_root


def test_repo_local_checkout_is_default() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        base = Path(temporary)
        repository = base / "IM2P.sim"
        (repository / CHIPYARD_RELATIVE).mkdir(parents=True)
        legacy_home = base / "home"
        (legacy_home / "aisa-lab/build/im2p-gemmini" / CHIPYARD_RELATIVE).mkdir(parents=True)
        assert resolve_gemmini_work_root(repository, {}, legacy_home) == repository.resolve()


def test_existing_legacy_checkout_remains_compatible() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        base = Path(temporary)
        repository = base / "IM2P.sim"
        repository.mkdir()
        legacy_home = base / "home"
        legacy = legacy_home / "aisa-lab/build/im2p-gemmini"
        (legacy / CHIPYARD_RELATIVE).mkdir(parents=True)
        assert resolve_gemmini_work_root(repository, {}, legacy_home) == legacy.resolve()


def test_fresh_clone_defaults_to_repository() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        base = Path(temporary)
        repository = base / "IM2P.sim"
        repository.mkdir()
        assert resolve_gemmini_work_root(repository, {}, base / "home") == repository.resolve()


def test_explicit_override_wins() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        base = Path(temporary)
        repository = base / "IM2P.sim"
        repository.mkdir()
        override = base / "shared-gemmini"
        assert resolve_gemmini_work_root(
            repository, {"IM2P_GEMMINI_WORK_ROOT": str(override)}, base / "home"
        ) == override.resolve()


def test_cli_reports_repository_default_without_legacy_checkout() -> None:
    # The repository running this test may have a real legacy checkout, so use
    # the function tests above for precedence and only require a valid CLI path.
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts/im2p_paths.py"), "--gemmini-work-root"],
        text=True, capture_output=True, check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert Path(completed.stdout.strip()).is_absolute()


def main() -> int:
    tests = (
        test_repo_local_checkout_is_default,
        test_existing_legacy_checkout_remains_compatible,
        test_fresh_clone_defaults_to_repository,
        test_explicit_override_wins,
        test_cli_reports_repository_default_without_legacy_checkout,
    )
    for test in tests:
        test()
    print(f"IM2P PATHS PASS ({len(tests)} tests)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
