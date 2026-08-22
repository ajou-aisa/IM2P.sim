"""Focused path-equivalence regressions for the build contract."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

import test_build_contract as contract


def main() -> int:
    nonexistent = "im2p-task11-path-contract/leaf/artifact.a"
    if Path("/private/tmp").is_dir() and os.path.samefile("/tmp", "/private/tmp"):
        assert contract.paths_equivalent(
            Path("/tmp") / nonexistent,
            Path("/private/tmp") / nonexistent,
        )

    relative = Path("build/path-contract/artifact.a")
    assert contract.paths_equivalent(relative, ROOT / relative)

    with tempfile.TemporaryDirectory(prefix="im2p path contract ") as temp:
        lexical = Path(temp) / "missing child" / "artifact.a"
        canonical = Path(os.path.realpath(temp)) / "missing child" / "artifact.a"
        assert contract.paths_equivalent(lexical, canonical)
        quoted_command = f'tool --output "{lexical}"'
        assert contract.output_has_path(quoted_command, canonical)

    assert not contract.paths_equivalent(
        "/tmp/im2p-task11-path-contract/one/artifact.a",
        "/tmp/im2p-task11-path-contract/two/artifact.a",
    )
    assert not contract.output_has_path(
        'tool --output "/tmp/im2p-task11-path-contract/wrong/artifact.a"',
        "/tmp/im2p-task11-path-contract/right/artifact.a",
    )

    print("BUILD CONTRACT PATHS PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
