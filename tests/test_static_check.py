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
if constexpr (im2p_exsia) {
    if (full_requested) {
        full.execution->install_sink();
        quantize_activation();
        full.execution->finish(quantize_ok);
    }
    if (!pipeline_requested) {
    }
    ggml::gemmini::im2p_adapter::start_exsia_stripe_pipeline(args);
    started.pipeline->install_sink();
    quantize_activation();
    started.pipeline->finish(quantize_ok);
}
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


def test_exsia_lifecycle_rejects_namespace_quantize_wrapper() -> None:
    # Given: lifecycle calls are qualified through a namespace.
    text = LIFECYCLE.replace(
        "quantize_activation()", "namespace_name::quantize_activation()"
    )
    # When: the lifecycle contract is checked.
    assert_exsia_lifecycle_rejected(text)
    # Then: namespace calls cannot satisfy the unqualified wrapper contract.


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


def test_exsia_lifecycle_rejects_duplicate_full_quantize() -> None:
    # Given: FULL quantization is invoked twice in production code.
    text = LIFECYCLE.replace(
        "        quantize_activation();",
        "        quantize_activation();\n        quantize_activation();",
        1,
    )
    # When: the lifecycle contract is checked.
    assert_exsia_lifecycle_rejected(text)
    # Then: FULL has exactly one unqualified quantization operation.


def test_exsia_lifecycle_rejects_additional_pipeline_start() -> None:
    # Given: PIPELINE startup is invoked twice in production code.
    text = LIFECYCLE.replace(
        "start_exsia_stripe_pipeline(args);",
        "start_exsia_stripe_pipeline(args);\nstart_exsia_stripe_pipeline(args);",
    )
    # When: the lifecycle contract is checked.
    assert_exsia_lifecycle_rejected(text)
    # Then: PIPELINE has exactly one unqualified startup operation.


def test_exsia_lifecycle_rejects_duplicate_pipeline_finish() -> None:
    # Given: PIPELINE completion is invoked twice in production code.
    text = LIFECYCLE.replace(
        "started.pipeline->finish(quantize_ok);",
        "started.pipeline->finish(quantize_ok);\n"
        "started.pipeline->finish(quantize_ok);",
    )
    # When: the lifecycle contract is checked.
    assert_exsia_lifecycle_rejected(text)
    # Then: PIPELINE has exactly one matching completion operation.


def test_exsia_lifecycle_rejects_third_production_mode() -> None:
    # Given: a third mode performs another production quantization operation.
    text = LIFECYCLE.replace(
        "    started.pipeline->finish(quantize_ok);",
        """\
    started.pipeline->finish(quantize_ok);
    if (third_requested) {
        quantize_activation();
    }
""",
    )
    # When: the lifecycle contract is checked.
    assert_exsia_lifecycle_rejected(text)
    # Then: production lifecycle operations belong only to FULL or PIPELINE.


def test_exsia_lifecycle_ignores_nonproduction_duplicate_calls() -> None:
    # Given: call-shaped near-misses occur in qualified code, comments, and strings.
    text = LIFECYCLE.replace(
        "    started.pipeline->finish(quantize_ok);",
        """\
    started.pipeline->finish(quantize_ok);
    other.quantize_activation();
    namespace_name::quantize_activation();
    // quantize_activation();
    const char * call = "quantize_activation();";
""",
    )
    # When: the lifecycle contract is checked.
    with tempfile.TemporaryDirectory(prefix="im2p-static-check-") as temp_dir:
        path = Path(temp_dir) / "ggml-gemmini.cpp"
        path.write_text(text, encoding="utf-8")
        result = static_check.require_exsia_lifecycle_contract(path)
    # Then: only unqualified production calls contribute to cardinality.
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
    test_exsia_lifecycle_rejects_member_quantize_wrapper()
    test_exsia_lifecycle_rejects_namespace_quantize_wrapper()
    test_exsia_lifecycle_rejects_commented_lifecycle()
    test_exsia_lifecycle_rejects_string_lifecycle()
    test_exsia_lifecycle_rejects_obsolete_wrapper_arguments()
    test_exsia_lifecycle_rejects_duplicate_full_quantize()
    test_exsia_lifecycle_rejects_additional_pipeline_start()
    test_exsia_lifecycle_rejects_duplicate_pipeline_finish()
    test_exsia_lifecycle_rejects_third_production_mode()
    test_exsia_lifecycle_ignores_nonproduction_duplicate_calls()
    test_contract_diagnostics_render_root_and_sibling_paths()
    print("STATIC CHECK REGRESSIONS PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
