from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from test_gemmini_export import refresh_sums, source_fixture

from scripts.gemmini_export import (
    ExportError,
    ExportRequest,
    create_export,
    extract_export,
    source_files,
    verify_export,
)

ROOT = Path(__file__).resolve().parents[1]
SEQUENCE_SOURCES = (
    "sim/include/im2p_cycle_sequence.h", "sim/cycle/sequence_c_api.cpp",
    "sim/cycle/sequence_binding.py", "sim/cycle/sequence_binding_abi.py",
    "sim/cycle/sequence_domain.py", "sim/cycle/stateful_sequence_evidence_tag5.py",
    "sim/cycle/stateful_sequence_evidence_tag6.py", "sim/cycle/stateful_sequence_tag6_pins.py",
    "sim/cycle/stateful_sequence_replay_certificate.py", "sim/cycle/stateful_sequence_replay_state.py",
    "sim/cycle/stateful_sequence_evidence_equivalence.py",
    "sim/cycle/execution_sequence_provider.py", "sim/cycle/execution_sequence_admission.py",
    "sim/cycle/stateful_sequence_certificate.py", "sim/cycle/stateful_sequence_evidence.py",
    "sim/cycle/stateful_sequence_evidence_v2.py", "sim/cycle/sequence_trace_cli.py",
    "sim/tests/cycle/test_sequence_session.cpp", "sim/tests/cycle/test_sequence_c_api.c",
    "sim/tests/cycle/test_sequence_milestone.cpp",
    "sim/tests/cycle/test_sequence_binding.py", "sim/tests/cycle/test_sequence_milestone_binding.py",
    "sim/tests/cycle/test_sequence_c_api.cpp", "sim/tests/cycle/tag_pressure_observer.hpp",
    "sim/tests/cycle/compositional_sequence_probe.cpp",
    "sim/tests/cycle/compositional_sequence_bounded.py",
    "sim/tests/cycle/compositional_sequence_mesh.py", "sim/tests/cycle/test_compositional_sequence_mesh.py",
    "sim/tests/cycle/test_stateful_sequence_tag6.py", "sim/tests/cycle/test_tag6_provider_gate.py",
    "sim/tests/cycle/test_stateful_replay_state.py", "sim/tests/cycle/test_stateful_replay_certificate.py",
    "sim/tests/cycle/test_compositional_sequence_bounded.py",
    "sim/tests/cycle/compositional_sequence_work.py",
    "sim/tests/cycle/compositional_sequence_repeat.py",
    "sim/tests/cycle/compositional_sequence_v1_runtime.py",
    "sim/tests/cycle/compositional_sequence_v2_base.py",
    "sim/tests/cycle/compositional_sequence_v2_compile.py",
    "sim/tests/cycle/compositional_sequence_v2_corpus.py",
    "sim/tests/cycle/compositional_sequence_v2_payload.py",
    "sim/tests/cycle/compositional_sequence_v2_runtime.py",
    "sim/tests/cycle/compositional_sequence_v2_stimulus.py",
    "sim/tests/cycle/compositional_sequence_v2_stream.py",
    "sim/tests/cycle/compositional_sequence_v2_text.py",
    "sim/tests/cycle/compositional_sequence_v2_values.py",
    "fpga/gemmini_hp1/host/run_aware_numeric_fixture.hpp",
    "sim/cycle/corpus_package.py", "sim/cycle/corpus-authority-v5.json",
    "sim/tests/cycle/current_corpus.py", "docs/CORPUS_AUTHORITY_V5.md",
    "sim/tests/cycle/test_corpus_authority_v5.py",
    "sim/tests/cycle/test_corpus_package_binding.py",
    "sim/cycle/current_run_capture.py", "sim/cycle/current_run_corpus.py",
    "sim/tests/cycle/test_current_run_corpus.py", "sim/tests/cycle/CURRENT_RUN_CORPUS.md",
    "sim/tests/cycle/test_current_run_capture_binding.py",
    "sim/tests/cycle/test_production_sequence_certificate.py",
    "sim/cycle/production_sequence_corpus_current.json",
    "sim/tests/cycle/test_execution_sequence_production_provider.py",
    "sim/tests/cycle/test_execution_sequence_provider.py",
    "sim/tests/cycle/test_stateful_execution_cli.py",
    "sim/tests/cycle/test_stateful_schedule.py", "sim/tests/cycle/test_execution_schedule_verifier.py",
)


@pytest.mark.parametrize("name", SEQUENCE_SOURCES)
def test_sequence_closure_when_removed_source_is_rehashed(tmp_path: Path, name: str) -> None:
    # Given: current sequence inputs, independently of the exporter's allowlist.
    source, package = tmp_path / "source", tmp_path / "package"
    source_fixture(source)
    for source_name in SEQUENCE_SOURCES:
        destination = source / source_name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / source_name, destination)
    create_export(ExportRequest(source, None, package, None))
    relative = f"source/{name}"
    assert (package / relative).is_file(), f"sequence closure missing: {name}"
    mutated = tmp_path / "missing"
    shutil.copytree(package, mutated)
    # When: both the source manifest and checksum inventory hide the omission.
    (mutated / relative).unlink()
    manifest = json.loads((mutated / "source-manifest.json").read_text())
    del manifest["files"][relative]
    (mutated / "source-manifest.json").write_text(json.dumps(manifest))
    refresh_sums(mutated)
    # Then: closure validation still rejects the missing required input.
    with pytest.raises(ExportError, match="required source closure missing"):
        verify_export(mutated)


def test_sequence_runtime_when_only_relocated_sources_exist(tmp_path: Path) -> None:
    # Given: real selected sources and fixture dependency metadata, without checkout imports.
    source, package = tmp_path / "source", tmp_path / "package"
    source_fixture(source)
    for original, relative in source_files(ROOT):
        destination = source / relative.relative_to("source")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original, destination)
    (source / "src/gemmini/UPSTREAM.lock.json").write_text("{}\n")
    shutil.copytree(ROOT / "config/gemmini_host_memory_contracts",
                    source / "config/gemmini_host_memory_contracts", dirs_exist_ok=True)
    result = create_export(ExportRequest(source, None, package, None))
    extract_export(result.archive, tmp_path / "relocated")
    relocated = tmp_path / "relocated/gemmini-hp1-export/source"
    build = tmp_path / "cycle-build"
    environment = {key: value for key, value in os.environ.items()
                   if key not in {"CPATH", "C_INCLUDE_PATH", "CPLUS_INCLUDE_PATH", "PYTHONPATH"}}
    environment.update(PYTHONPATH=str(relocated), PYTHONDONTWRITEBYTECODE="1")
    commands = (
        ("cmake", "-S", str(relocated / "sim/cycle"), "-B", str(build)),
        ("cmake", "--build", str(build), "--parallel", "2"),
        ("ctest", "--test-dir", str(build), "--output-on-failure", "--no-tests=error"),
    )
    # When: C/C++ builds, links and runs the packaged session tests.
    for command in commands:
        completed = subprocess.run(command, cwd=tmp_path, env=environment, text=True,
                                   capture_output=True, check=False, timeout=120)
        assert completed.returncode == 0, completed.stdout + completed.stderr
    library = next(path for path in build.glob("libim2p_cycle_model.*") if path.suffix != ".a")
    smoke = """
import sys
from pathlib import Path
from sim.cycle import execution_sequence_provider, sequence_binding as binding
from sim.cycle import stateful_sequence_evidence_v2 as evidence
from sim.cycle import stateful_sequence_evidence_tag6
from sim.tests.cycle.compositional_sequence_v2_base import stimulus_source_hashes
root = Path(sys.argv[2]).resolve()
assert all(Path(module.__file__).resolve().is_relative_to(root)
           for name, module in tuple(sys.modules.items())
           if name.startswith(('sim.', 'scripts.')) and getattr(module, '__file__', None))
assert evidence._python_closure()
assert stateful_sequence_evidence_tag6.SCHEMA == 'stateful-tag6-domain-v1'
assert stimulus_source_hashes()
with binding.SequenceSession(Path(sys.argv[1]), 'a8w8-d16-hp1') as session:
    previous = 0
    for work_id in (41, 42):
        offered = session.status().cursor
        assert session.offer(binding.Work(work_id, 1, 1, 1), offered) == binding.Code.OK
        if work_id == 41:
            assert session.advance_until(offered + 1000) == binding.Code.OK
        else:
            assert session.advance_to_boundary(offered + 1000, 1000) == binding.Code.OK
            assert session.status().stop_reason == binding.StopReason.REPORT_AVAILABLE
        report = session.pop_report()
        assert isinstance(report, binding.Report) and report.logical_work_id == work_id
        assert report.offered_cycle == offered and report.resource_ready_cycle > previous
        if work_id == 42:
            assert session.status().cursor == report.resource_ready_cycle
        previous = report.resource_ready_cycle
    assert session.counters().logical_work_count == 2
    session.verify_identity()
print('RELOCATED_SEQUENCE_TWO_WORK_PASS')
"""
    completed = subprocess.run((sys.executable, "-B", "-c", smoke, str(library), str(relocated)),
                               cwd=tmp_path, env=environment, text=True,
                               capture_output=True, check=False, timeout=30)
    # Then: Python owns two completed works using only packaged source identities.
    assert completed.returncode == 0, completed.stdout + completed.stderr
    for module in ("sim.cycle.execution_cli", "sim.cycle.sequence_trace_cli",
                   "sim.cycle.stateful_sequence_certificate", "sim.tests.cycle.compositional_sequence_work"):
        completed = subprocess.run((sys.executable, "-B", "-m", module, "--help"),
                                   cwd=tmp_path, env=environment, text=True,
                                   capture_output=True, check=False, timeout=30)
        assert completed.returncode == 0, completed.stdout + completed.stderr
