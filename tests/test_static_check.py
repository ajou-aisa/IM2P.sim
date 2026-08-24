#!/usr/bin/env python3
"""Focused regressions for sibling llama static-check contracts."""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import static_check

ORCHESTRATION = (
    ROOT.parent / "llama.cpp-gemmini/ggml/src/ggml-gemmini/ggml-gemmini.cpp"
)
ORCHESTRATION_LABEL = "../llama.cpp-gemmini/ggml/src/ggml-gemmini/ggml-gemmini.cpp"
LIFECYCLE = """\
if (full_requested) {
    full.execution->install_sink();
    quantize_activation();
    full.execution->finish(quantize_ok);
}
if (!pipeline_requested) {
}
start_exsia_stripe_pipeline(args);
started.pipeline->install_sink();
quantize_activation();
started.pipeline->finish(quantize_ok);
"""


def assert_exsia_lifecycle_rejected(text: str) -> None:
    with tempfile.TemporaryDirectory(prefix="im2p-static-check-") as temp_dir:
        path = Path(temp_dir) / "ggml-gemmini.cpp"
        path.write_text(text, encoding="utf-8")
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            try:
                static_check.require_exsia_lifecycle_contract(path)
            except SystemExit as failure:
                assert failure.code == 1
            else:
                raise AssertionError("invalid ExSIA lifecycle was accepted")
        assert "missing required contract" in stderr.getvalue()


def test_current_exsia_lifecycle_contract() -> None:
    # Given: the checked llama orchestration source uses its zero-argument wrapper.
    # When: the ExSIA integration contracts run against current production sources.
    result = static_check.check_exsia_integration_contracts()
    # Then: the current production lifecycle satisfies every contract.
    assert result is None


def test_exsia_lifecycle_rejects_member_quantize_wrapper() -> None:
    # Given: lifecycle calls are qualified through another object.
    text = LIFECYCLE.replace("quantize_activation()", "other.quantize_activation()")
    # When: the lifecycle contract is checked.
    assert_exsia_lifecycle_rejected(text)
    # Then: member calls cannot satisfy the unqualified wrapper contract.


def test_exsia_lifecycle_rejects_commented_lifecycle() -> None:
    # Given: the complete lifecycle exists only in a block comment.
    text = f"/*\n{LIFECYCLE}*/\n"
    # When: the lifecycle contract is checked.
    assert_exsia_lifecycle_rejected(text)
    # Then: comments cannot satisfy a production lifecycle contract.


def test_exsia_lifecycle_rejects_string_lifecycle() -> None:
    # Given: the complete lifecycle appears only in a C++ string literal.
    text = f'const char * lifecycle = "{LIFECYCLE.replace(chr(10), " ")}";\n'
    # When: the lifecycle contract is checked.
    assert_exsia_lifecycle_rejected(text)
    # Then: string contents cannot satisfy a production lifecycle contract.


def test_exsia_lifecycle_rejects_obsolete_wrapper_arguments() -> None:
    # Given: lifecycle calls use the obsolete argument-bearing form.
    text = LIFECYCLE.replace("quantize_activation()", "quantize_activation(src1, args)")
    # When: the lifecycle contract is checked.
    assert_exsia_lifecycle_rejected(text)
    # Then: only the current zero-argument wrapper is accepted.


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
    test_exsia_lifecycle_rejects_member_quantize_wrapper()
    test_exsia_lifecycle_rejects_commented_lifecycle()
    test_exsia_lifecycle_rejects_string_lifecycle()
    test_exsia_lifecycle_rejects_obsolete_wrapper_arguments()
    test_contract_diagnostics_render_root_and_sibling_paths()
    print("STATIC CHECK REGRESSIONS PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
