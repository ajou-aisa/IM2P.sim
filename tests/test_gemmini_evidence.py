#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.gemmini_evidence import (
    host_artifact_evidence,
    integration_ready,
    layout_differential,
    orchestration_symbols,
    rmd_bound_runtime_evidence,
    rmd_runtime_evidence,
    runtime_evidence,
    sha256,
)


def runtime_log(bits: int = 8, dim: int = 16, overlap: int = 17) -> str:
    rows = (
        (
            "WS RTL FULL rows=2 row_begin=0 N=3 K=64 cycles=408 tile=1/1/4 "
            "load/ex/store=11/9/2 contexts=4 committed_rows=8 scale_reads=2 reordered_reads=27"
        ),
        (
            "WS RTL FULL rows=2 row_begin=0 N=3 K=64 cycles=606 tile=1/1/4 "
            "load/ex/store=11/9/2 contexts=4 committed_rows=8 scale_reads=2 reordered_reads=20"
        ),
        (
            "WS RTL FULL rows=129 row_begin=0 N=129 K=96 cycles=30665 tile=5/6/6 "
            "load/ex/store=228/976/85 contexts=486 committed_rows=6966 scale_reads=54 reordered_reads=1102"
        ),
        (
            "WS RTL PIPELINE rows=64 row_begin=0 N=129 K=96 cycles=15272 tile=5/6/6 "
            "load/ex/store=108/434/38 contexts=216 committed_rows=3456 scale_reads=27 reordered_reads=546"
        ),
        (
            "WS RTL PIPELINE rows=64 row_begin=64 N=129 K=96 cycles=15272 tile=5/6/6 "
            "load/ex/store=108/434/38 contexts=216 committed_rows=3456 scale_reads=27 reordered_reads=546"
        ),
        (
            "WS RTL PIPELINE rows=1 row_begin=128 N=129 K=96 cycles=3878 tile=5/6/6 "
            "load/ex/store=72/110/11 contexts=54 committed_rows=54 scale_reads=27 reordered_reads=300"
        ),
        (
            "WS RTL FULL rows=1 row_begin=0 N=1 K=8256 cycles=41328 tile=1/1/256 "
            "load/ex/store=1041/1035/4 contexts=516 committed_rows=516 scale_reads=258 reordered_reads=3099"
        ),
        (
            f"integrated upstream WS HP1 RTL passed A{bits}W{bits}D{dim} loops=15 "
            f"load_execute_overlap={overlap}"
        ),
        (
            "WS_DUAL_LOOP slots=0,1 overlap_loop_issue=1 load_execute_overlap=50 "
            "slot0_reuse_blocked=1 delayed_done_cycles=194 local_halves=0,1 "
            "outputs=6,-16 accepted_load_execute_store=7,5,3 "
            f"accepted_load_rows_store_rows_contexts={2 * (dim + 1)},2,2"
        ),
    )
    return "\n".join(rows) + "\n"


def test_runtime_requires_real_large_and_overlap_cases() -> None:
    evidence = runtime_evidence(runtime_log(), "a8w8-d16-hp1")
    assert evidence["large_full"]["cycles"] == 30665
    assert len(evidence["pipeline_stripes"]) == 3
    assert evidence["outer_k"]["scale_reads"] == 258
    assert evidence["load_execute_overlap"] == 17
    assert evidence["dual_loop"]["accepted_load_rows"] == 34
    assert evidence["dual_loop"]["outputs"] == [6, -16]
    assert (
        runtime_evidence(runtime_log(dim=64), "a8w8-d64-hp1")["dual_loop"][
            "accepted_load_rows"
        ]
        == 130
    )

    for broken in (
        runtime_log().replace(" scale_reads=54", " scale_reads=0"),
        runtime_log().replace("rows=1 row_begin=128", "rows=1 row_begin=127"),
        runtime_log(overlap=0),
        runtime_log().replace("WS_DUAL_LOOP ", "REMOVED "),
        runtime_log().replace("slot0_reuse_blocked=1", "slot0_reuse_blocked=0"),
        runtime_log().replace(
            "accepted_load_execute_store=7,5,3", "accepted_load_execute_store=6,5,3"
        ),
        runtime_log().replace(
            "accepted_load_rows_store_rows_contexts=34,2,2",
            "accepted_load_rows_store_rows_contexts=33,2,2",
        ),
    ):
        try:
            _ = runtime_evidence(broken, "a8w8-d16-hp1")
        except ValueError:
            pass
        else:
            raise AssertionError("incomplete runtime evidence accepted")


def test_rmd_runtime_requires_scaled_scu_compose_and_negative_checks() -> None:
    marker = (
        "WS_RMD_RUNS_DIAGNOSTIC A8W8D16 run_callbacks=12 compact_exact=12 lanes=5 high_carry=1 "
        "compose_exact=6 merge_exact=6 negative_tests=7 missing_reject=1 "
        "duplicate_reject=1 missing_runs_reject=1 high_exponent_run=1 sparse_k=1 odd_k=1 stripes=3 slots=0,1,0\n"
    )
    result = rmd_runtime_evidence(marker, "a8w8-d16-hp1")
    assert result["run_callbacks"] == 12
    assert result["lanes"] == 5
    assert rmd_runtime_evidence(
        marker.replace("compact_exact=12", "compact_exact=24"), "a8w8-d16-hp1",
    )["compact_exact"] == 24
    assert rmd_runtime_evidence(
        marker.replace("A8W8D16", "A4W4D64").replace("lanes=5", "lanes=9"),
        "a4w4-d64-hp1",
    )["lanes"] == 9
    for broken in (
        "", marker + marker,
        marker.replace("run_callbacks=12", "run_callbacks=0"),
        marker.replace("compact_exact=12", "compact_exact=11"),
        marker.replace("lanes=5", "lanes=4"),
        marker.replace("high_carry=1", "high_carry=0"),
        marker.replace("compose_exact=6", "compose_exact=0"),
        marker.replace("merge_exact=6", "merge_exact=0"),
        marker.replace("missing_reject=1", "missing_reject=0"),
        marker.replace("duplicate_reject=1", "duplicate_reject=0"),
        marker.replace("missing_runs_reject=1", "missing_runs_reject=0"),
        marker.replace("high_exponent_run=1", "high_exponent_run=0"),
    ):
        try:
            _ = rmd_runtime_evidence(broken, "a8w8-d16-hp1")
        except ValueError:
            pass
        else:
            raise AssertionError("incomplete RMD runtime evidence accepted")


def test_rmd_public_entry_requires_full_pipeline_and_transactional_failures() -> None:
    marker = (
        "WS_RMD_BOUND_RUNS_DIAGNOSTIC bits=8 DIM=16 full_exact=27 pipeline_exact=27 dense_calls=6 "
        "runs_calls=18 stripes=3 slots=0,1,0 rollback=2 public_entry=1\n"
    )
    result = rmd_bound_runtime_evidence(marker, "a8w8-d16-hp1")
    assert result["full_exact"] == result["pipeline_exact"] == 27
    for broken in (
        "", marker + marker,
        marker.replace("bits=8", "bits=4"),
        marker.replace("full_exact=27", "full_exact=0"),
        marker.replace("runs_calls=18", "runs_calls=0"),
        marker.replace("pipeline_exact=27", "pipeline_exact=26"),
        marker.replace("rollback=2", "rollback=1"),
        marker.replace("public_entry=1", "public_entry=0"),
    ):
        try:
            _ = rmd_bound_runtime_evidence(broken, "a8w8-d16-hp1")
        except ValueError:
            pass
        else:
            raise AssertionError("incomplete RMD public dispatch evidence accepted")


def test_layout_classifies_shared_failure_only_with_matching_probes_and_logs() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        probe = {
            "sizeof": 1080,
            "native_weight_bytes": 872,
            "col_stride_f_out": 976,
            "stride_f_out": 984,
            "tile_I": 1008,
        }
        current = root / "current.json"
        baseline = root / "baseline.json"
        _ = current.write_text(json.dumps(probe), encoding="utf-8")
        _ = baseline.write_text(json.dumps(probe), encoding="utf-8")
        failure = "FAIL: native_weight_bytes offset remains unchanged\n"
        current_log = root / "current.log"
        baseline_log = root / "baseline.log"
        _ = current_log.write_text(failure, encoding="utf-8")
        _ = baseline_log.write_text(failure, encoding="utf-8")

        result = layout_differential(current, baseline, current_log, baseline_log)
        assert result["status"] == "PASS"
        assert result["classification"] == "BASELINE_EXISTING_FAIL"
        assert result["layouts_equal"] is True

        _ = baseline_log.write_text("PASS\n", encoding="utf-8")
        try:
            _ = layout_differential(current, baseline, current_log, baseline_log)
        except ValueError:
            pass
        else:
            raise AssertionError("current-only regression misclassified")


def test_marker_requires_every_mac_gate() -> None:
    gates = {
        name: {"status": "PASS"}
        for name in (
            "DATAPATH_NUMERICAL",
            "UPSTREAM_CONTROLLER_CLOSURE",
            "UPSTREAM_WS_HP1_RUNTIME_INTEGRATION",
            "BACKING_LOAD_STORE_INTEGRATION",
            "LARGE_HOST_TILE_EXECUTION",
            "WS_DOUBLE_BUFFER_OWNERSHIP",
            "WS_OVERLAP_TEST",
            "LOGICAL_MATMUL_CYCLE_BOUNDARY",
            "NO_SIM_HOST_ARTIFACT",
            "EXPORT_SELECTED_TOP",
            "LAYOUT_BASELINE_DIFFERENTIAL",
        )
    }
    assert integration_ready(gates)
    gates["EXPORT_SELECTED_TOP"] = {"status": "FAIL"}
    assert not integration_ready(gates)


def test_no_sim_gate_requires_frontend_and_orchestration_symbols() -> None:
    symbols = (
        "im2p::gemmini::execute(ggml_gemmini_args_t const*, im2p::gemmini::Mode)\n"
        + "ggml_gemmini_fpga_execute(ggml_gemmini_args_t&, bool)\n"
        + "im2p::gemmini_hp1::encode_work_plan_v1(im2p::gemmini_hp1::WorkPlanV1 const&)\n"
        + "im2p::gemmini_hp1::decode_work_plan_v1(std::vector<unsigned char> const&)"
    )
    assert len(orchestration_symbols(symbols)) == 4
    try:
        _ = orchestration_symbols(symbols.replace("im2p::gemmini::execute", "missing"))
    except ValueError:
        pass
    else:
        raise AssertionError("codec-only host artifact accepted")

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        artifact = root / "a8w8-d16-hp1/host-build/gemmini_hp1_host_orchestration"
        artifact.parent.mkdir(parents=True)
        _ = artifact.write_bytes(b"Mach-O fixture")
        audit: dict[str, object] = {
            "status": "PASS",
            "role": "PHYSICAL_HOST",
            "artifact": str(artifact),
            "artifact_sha256": sha256(artifact),
            "simulator_markers": [],
            "physical_operations": 0,
        }
        completed = subprocess.CompletedProcess(["nm"], 0, stdout=symbols, stderr="")
        with (
            patch("scripts.gemmini_evidence.shutil.which", return_value="nm"),
            patch("scripts.gemmini_evidence.subprocess.run", return_value=completed),
        ):
            result = host_artifact_evidence(root, "a8w8-d16-hp1", audit)
            assert result["role"] == "PHYSICAL_HOST"
            audit["role"] = "HOST_COMMON"
            try:
                _ = host_artifact_evidence(root, "a8w8-d16-hp1", audit)
            except RuntimeError:
                pass
            else:
                raise AssertionError(
                    "HOST_COMMON codec audit accepted as physical host"
                )


if __name__ == "__main__":
    test_runtime_requires_real_large_and_overlap_cases()
    test_rmd_runtime_requires_scaled_scu_compose_and_negative_checks()
    test_rmd_public_entry_requires_full_pipeline_and_transactional_failures()
    test_layout_classifies_shared_failure_only_with_matching_probes_and_logs()
    test_marker_requires_every_mac_gate()
    test_no_sim_gate_requires_frontend_and_orchestration_symbols()
