#!/usr/bin/env python3
"""Focused regressions for sibling llama static-check contracts."""

from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import static_check

ORCHESTRATION = (
    ROOT.parent / "llama.cpp-gemmini/ggml/src/ggml-gemmini/ggml-gemmini.cpp"
)
ORCHESTRATION_LABEL = "../llama.cpp-gemmini/ggml/src/ggml-gemmini/ggml-gemmini.cpp"


def test_current_exsia_lifecycle_contract() -> None:
    # Given: the checked llama orchestration source uses its zero-argument wrapper.
    # When: the ExSIA integration contracts run against current production sources.
    result = static_check.check_exsia_integration_contracts()
    # Then: the current production lifecycle satisfies every contract.
    assert result is None


def test_contract_diagnostics_render_root_and_sibling_paths() -> None:
    # Given: both an in-root file and the sibling llama orchestration source.
    in_root = ROOT / "Makefile"
    paths = ((in_root, "Makefile"), (ORCHESTRATION, ORCHESTRATION_LABEL))

    for path, label in paths:
        # When: a required contract is missing.
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            try:
                static_check.require_regex(path, r"\bmissing static contract\b", "test")
            except SystemExit as failure:
                # Then: the checker fails nonzero with a deterministic path.
                assert failure.code == 1
            else:
                raise AssertionError("missing contract was accepted")
        assert f"{label} missing required contract: test" in stderr.getvalue()


def main() -> int:
    test_current_exsia_lifecycle_contract()
    test_contract_diagnostics_render_root_and_sibling_paths()
    print("STATIC CHECK REGRESSIONS PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
