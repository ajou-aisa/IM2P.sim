from __future__ import annotations

from pathlib import Path
import copy
import json
import subprocess
import tempfile
import unittest

from sim.cycle.collection_build import project_artifacts
from sim.cycle.collection_native import prepare_native_build, validate_receipt
from sim.cycle.reconstruct_cpu import CollectionFiles, verify_provenance
from scripts.real_lib_manifest import sha256
from sim.cycle.npu_trace_schema import object_value
from scripts.gemmini_replay_contract import contract_digest


class NativeCollectionBuildTests(unittest.TestCase):
    def test_bind_selected_native_runner_without_unrelated_cli(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "bin").mkdir()
            (root / "bin/llama-eval-workload").write_bytes(b"selected-native-artifact")
            actual = project_artifacts(root, executable="llama-eval-workload")
            self.assertEqual(set(actual), {"llama-eval-workload"})

    def test_real_compile_receipt_rejects_changed_source_and_stats_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source, build, evidence = root / "source", root / "build", root / "evidence"
            (source / "ggml-cpu").mkdir(parents=True)
            (source / "ggml-gemmini-utils/src").mkdir(parents=True)
            evidence.mkdir()
            kernel = source / "ggml-cpu/ggml-cpu.c"
            kernel.write_text("int kernel(void) { return 7; }\n")
            (source / "ggml-gemmini-utils/src/semantic.cpp").write_text("int semantic_fixture() { return 0; }\n")
            (source / "main.c").write_text('#include <stdio.h>\nint main(void) { puts("{\\"activation_metrics\\":0,\\"residual_metrics\\":0,\\"cycle_sim\\":0}"); return 0; }\n')
            (source / "CMakeLists.txt").write_text(
                'cmake_minimum_required(VERSION 3.20)\nproject(native_receipt C CXX)\n'
                'set(CMAKE_RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin")\n'
                'add_executable(llama-eval-workload main.c ggml-cpu/ggml-cpu.c ggml-gemmini-utils/src/semantic.cpp)\n'
                'target_compile_definitions(llama-eval-workload PRIVATE CYCLE_SIM=0 LOG_CYCLE=1 CYCLE_LOG=1 GEMMINI_SEMANTIC_CPU_ONLY_BUILD=1)\n')
            subprocess.run(["cmake","-S",str(source),"-B",str(build),"-G","Ninja",
                            "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON","-DCYCLE_SIM=0","-DLOG_CYCLE=1",
                            "-DGGML_CPU_CYCLE_LOG=ON","-DGGML_GEMMINI_OPTION=CPU",
                            "-DGGML_GEMMINI_EXECUTION_BACKEND=HARDWARE"],check=True,capture_output=True)
            cmake = source / "CMakeLists.txt"
            cmake.write_text(cmake.read_text() + "target_compile_definitions(llama-eval-workload PRIVATE CANDIDATE_REVISION=2)\n")
            bound = prepare_native_build(build,evidence)
            bound.verify()
            self.assertEqual(bound.role,"FULL_CPU")
            files = CollectionFiles(evidence/"cpu.jsonl",evidence/"graph.jsonl",evidence/"proof.json")
            files.log.write_text("synthetic consumer binding fixture\n")
            files.graph.write_text("synthetic consumer binding fixture\n")
            proof = dict(schema="im2p-collection-provenance",version=2,source_role="FULL_CPU",
                process_exit_code=0,collection_success=True,build_inputs_unchanged=True,model_sha256="4"*64,
                executable_sha256=object_value(bound.receipt["project_artifacts"])["llama-eval-workload"],
                compile_commands_sha256=bound.receipt["compile_commands_sha256"],
                cpu_kernel_contract=bound.kernel,command_arguments=[],input_files={},project_libraries=[],
                native_build=bound.receipt,runtime_dependencies=bound.receipt["runtime_dependencies"],
                process_receipt_sha256="7"*64,scope="SYNTHETIC_CONSUMER_BINDING_FIXTURE",
                artifacts=dict(cycle_log=dict(sha256=sha256(files.log)),semantic_graph=dict(sha256=sha256(files.graph))))
            files.provenance.write_text(json.dumps(proof))
            self.assertEqual(verify_provenance(files,"FULL_CPU")["version"],2)
            proof["version"] = 1
            files.provenance.write_text(json.dumps(proof))
            with self.assertRaises(ValueError):
                verify_provenance(files,"FULL_CPU")
            changed = copy.deepcopy(bound.receipt)
            changed["project_artifacts"] = {"llama-eval-workload":"0"*64}
            with self.assertRaises(ValueError):
                validate_receipt(changed)
            stats = copy.deepcopy(bound.receipt)
            stats["build_info"] = {"activation_metrics":1,"residual_metrics":0,"cycle_sim":0}
            stats["sha256"] = contract_digest(stats)
            with self.assertRaises(ValueError):
                validate_receipt(stats)
            self.assertEqual(json.loads((evidence/"native-build-receipt.json").read_text())["sha256"],bound.receipt["sha256"])
            kernel.write_text("int kernel(void) { return 8; }\n")
            with self.assertRaises(ValueError):
                bound.verify()


if __name__ == "__main__":
    unittest.main()
