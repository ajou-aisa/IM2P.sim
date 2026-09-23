from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from scripts.gemmini_resolve_profile import JsonValue
from sim.cycle.execution_adapter import AdapterFiles, adapt_records
from sim.cycle.execution_ir import Dependency, Kind, Milestone, NodeId
from sim.cycle.execution_stream import adapt_stream
from sim.cycle.npu_trace_schema import Record, object_value
from sim.cycle.reconstruct_graph import array, sha256
from sim.tests.cycle.test_execution_adapter import contract


TRANSITIONS = (
    ("EXSIA_WORKSPACE", "ACQUIRE"),
    ("ACTIVATION_ROWS", "COMMIT"),
    ("RESIDUAL_PAYLOAD", "SEAL"),
    ("FRONTEND_OUTSTANDING", "ACQUIRE"),
    ("FRONTEND_QUEUE", "ENQUEUE"),
    ("EXSIA_WORKSPACE", "RELEASE"),
    ("FRONTEND_QUEUE", "DEQUEUE"),
    ("CPU_FUNCTIONAL_STREAM", "ACCEPTED"),
    ("CPU_FUNCTIONAL_STREAM", "COMPLETED"),
    ("RESIDUAL_MERGE_CALL", "COMPLETE"),
    ("RESIDUAL_CALLBACK", "COMPLETE"),
    ("FRONTEND_OUTSTANDING", "RELEASE"),
)


def pipeline_case() -> tuple[list[Record], Record, list[Record]]:
    rows: list[Record] = [
        {"kind": "OPERATION_CONTAINER", "node_id": "operation:a", "dependencies": [],
         "phase": {"kind": "prefill", "decode_index": None}},
    ]
    results: list[Record] = []
    owners: list[JsonValue] = []
    sequence = 0
    for stripe in range(3):
        call = stripe
        work = stripe
        merge_call = stripe + 4
        for stage in ("PREPARE", "INVOKE", "PUBLISH", "COMPLETE_REQUIRED", "CONTINUATION"):
            predecessor: dict[str, list[JsonValue]] = {
                "PREPARE": [], "INVOKE": [f"call:{call}:PREPARE"],
                "PUBLISH": [f"call:{call}:INVOKE", f"npu:{work}"],
                "COMPLETE_REQUIRED": [f"call:{call}:PUBLISH", f"npu:{work}"],
                "CONTINUATION": [f"call:{call}:COMPLETE_REQUIRED"]}
            rows.append({"kind": "CALL_BOUNDARY", "node_id": f"call:{call}:{stage}",
                         "call_id": call, "call_kind": "STRIPE", "stage": stage,
                         "operation_node_id": "operation:a", "node_class": "TARGET_NPU",
                         "dependencies": predecessor[stage]})
        rows.append({"kind": "SERVICE", "node_id": f"npu:{work}", "node_class": "TARGET_NPU",
                     "operation_node_id": "operation:a", "dependencies": [f"call:{call}:INVOKE"]})
        if stripe == 0:
            merge_stages: tuple[tuple[str, list[JsonValue]], ...] = (
                ("PREPARE", []), ("INVOKE", [f"call:{merge_call}:PREPARE"]),
                ("COMPLETE_REQUIRED", [f"call:{merge_call}:INVOKE", f"npu:{work}"]),
                ("CONTINUATION", [f"call:{merge_call}:COMPLETE_REQUIRED"]))
            for stage, dependencies in merge_stages:
                rows.append({"kind": "CALL_BOUNDARY", "node_id": f"call:{merge_call}:{stage}",
                             "call_id": merge_call, "call_kind": "RESIDUAL_MERGE", "stage": stage,
                             "operation_node_id": "operation:a", "node_class": "TARGET_NPU",
                             "dependencies": dependencies})
        results.append({"schema": "im2p-npu-cycle-result", "cycle_model_validation": "CURRENT_CERTIFIED",
                        "scope": "stripe", "host_slot": stripe % 2, "work_id": work, "call_id": call,
                        "phase_id": 0, "operation_id": 5, "parent_id": 7, "stripe_id": stripe,
                        "row_begin": stripe, "row_count": 1, "m": 1, "parent_m": 3,
                        "n": 16, "k": 16, "dim": 16, "activation_bits": 8, "weight_bits": 8,
                        "tile_i_count": 1, "tile_j_count": 1, "tile_k_count": 1,
                        "production_geometry_version": 1, "profile": "a8w8-d16-hp1",
                        "run_view_sha256": f"work-{work}"})
        for resource, transition in TRANSITIONS:
            if resource == "RESIDUAL_MERGE_CALL" and stripe != 0:
                continue
            complete = (resource, transition) in (("CPU_FUNCTIONAL_STREAM", "COMPLETED"),
                                                   ("RESIDUAL_CALLBACK", "COMPLETE"),
                                                   ("FRONTEND_OUTSTANDING", "RELEASE"))
            merge = resource == "RESIDUAL_MERGE_CALL"
            owners.append({"producer_sequence": sequence, "phase_id": 0, "operation_id": 5,
                           "parent_id": 7, "work_id": work, "producer_run_id": 0,
                           "stripe_id": stripe, "workspace_slot": stripe % 2,
                           "target_npu_slot": None, "row_begin": stripe, "row_end": stripe + 1,
                           "resource": resource, "transition": transition,
                           "required_work_ids": [work] if complete else [],
                           "required_call_ids": [merge_call] if stripe == 0 and
                           (merge or resource in ("RESIDUAL_CALLBACK", "FRONTEND_OUTSTANDING") and
                            transition in ("COMPLETE", "RELEASE")) else [],
                           "observed_call_id": merge_call if merge else None,
                           "rmd_packet": stripe == 0 and (resource, transition) != ("EXSIA_WORKSPACE", "ACQUIRE"),
                           "direct_residual": False,
                           "source_owner": "IM2P.sim", "source_location": "frontend/src:fixture"})
            sequence += 1
    fence_stages: tuple[tuple[str, list[JsonValue]], ...] = (
        ("PREPARE", []), ("INVOKE", ["call:3:PREPARE"]),
        ("COMPLETE_REQUIRED", ["call:3:INVOKE", "npu:0", "npu:1", "npu:2"]),
        ("FENCE", ["call:3:COMPLETE_REQUIRED"]),
        ("CONTINUATION", ["call:3:FENCE"]))
    for stage, dependencies in fence_stages:
        rows.append({"kind": "CALL_BOUNDARY", "node_id": f"call:3:{stage}", "call_id": 3,
                     "call_kind": "FENCE", "stage": stage, "operation_node_id": "operation:a",
                     "node_class": "TARGET_NPU", "dependencies": dependencies})
    declaration = contract()
    declaration.update(version=2, entry_dependencies={"operation:a": []},
                       call_slots={str(index): None for index in range(3)},
                       submission_order=[f"npu:{index}" for index in range(3)],
                       pipeline_parents=[{"operation_id": 5, "parent_id": 7,
                                          "phase_id": 0, "required_work_ids": [0, 1, 2],
                                          "fence_call_id": 3, "fence_required_work_ids": [0, 1, 2],
                                          "production_geometry_version": 1,
                                          "scope": "STREAM", "activation_bits": 8,
                                          "weight_bits": 8, "dim": 16, "parent_m": 3,
                                          "n": 16, "k": 16, "tile_i_count": 1,
                                          "tile_j_count": 1, "tile_k_count": 1,
                                          "residual_bindings": []}],
                       pipeline_owners=owners)
    return rows, declaration, results


class PipelineProjectionTests(unittest.TestCase):
    def test_three_stripes_preserve_two_credits_and_separate_workspace_reuse(self) -> None:
        rows, declaration, results = pipeline_case()
        ir, _ = adapt_records(rows, declaration, results)
        nodes = {node.identity: node for node in ir.nodes}
        credit = NodeId("owner:7:0:FRONTEND_OUTSTANDING:RELEASE")
        acquire = NodeId("owner:7:2:FRONTEND_OUTSTANDING:ACQUIRE")
        self.assertIn(Dependency(credit), nodes[acquire].dependencies)
        workspace = NodeId("owner:7:0:EXSIA_WORKSPACE:RELEASE")
        self.assertIn(Dependency(workspace), nodes[NodeId("owner:7:2:EXSIA_WORKSPACE:ACQUIRE")].dependencies)
        self.assertNotIn(Dependency(NodeId("npu:0")),
                         nodes[NodeId("call:0:CONTINUATION")].dependencies)
        self.assertEqual(nodes[NodeId("call:0:PUBLISH")].kind, Kind.PUBLISH)
        self.assertIn(Dependency(NodeId("owner:7:0:CPU_FUNCTIONAL_STREAM:ACCEPTED")),
                      nodes[NodeId("npu:0")].dependencies)
        self.assertIn(Dependency(NodeId("npu:0"), Milestone.RESULT_READY),
                      nodes[NodeId("owner:7:0:FRONTEND_OUTSTANDING:RELEASE")].dependencies)
        self.assertNotIn(Dependency(NodeId("npu:0"), Milestone.RESOURCE_READY),
                         nodes[NodeId("owner:7:0:FRONTEND_OUTSTANDING:RELEASE")].dependencies)
        self.assertIn(Dependency(NodeId("owner:7:0:FRONTEND_QUEUE:ENQUEUE")),
                      nodes[NodeId("owner:7:0:EXSIA_WORKSPACE:RELEASE")].dependencies)
        self.assertIn(Dependency(NodeId("owner:7:2:FRONTEND_OUTSTANDING:RELEASE")),
                      nodes[NodeId("operation:a:exit")].dependencies)
        self.assertIn(Dependency(NodeId("npu:2"), Milestone.RESULT_READY),
                      nodes[NodeId("call:3:COMPLETE_REQUIRED")].dependencies)

    def test_sqlite_uses_same_pipeline_edges(self) -> None:
        rows, declaration, results = pipeline_case()
        ir, _ = adapt_records(rows, declaration, results)
        expected_edges = {(str(node.identity), str(edge.node), edge.milestone.value)
                          for node in ir.nodes for edge in node.dependencies}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "dataset.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
            (root / "npu.jsonl").write_text("".join(json.dumps(row) + "\n" for row in results))
            declaration["dataset_sha256"] = sha256(root / "dataset.jsonl")
            declaration["npu_results_sha256"] = sha256(root / "npu.jsonl")
            (root / "lifecycle.json").write_text(json.dumps(declaration))
            result = adapt_stream(AdapterFiles(root / "dataset.jsonl", root / "lifecycle.json",
                                               root / "npu.jsonl"), root / "execution.sqlite")
            self.assertEqual(object_value(result["class_counts"])["TARGET_NPU"], 3)
            with closing(sqlite3.connect(root / "execution.sqlite")) as database:
                self.assertEqual(set(database.execute("SELECT node,parent,milestone FROM edges")),
                                 expected_edges)
                credit = database.execute("SELECT parent,milestone FROM edges WHERE node=?",
                    ("owner:7:2:FRONTEND_OUTSTANDING:ACQUIRE",)).fetchall()
                self.assertIn(("owner:7:0:FRONTEND_OUTSTANDING:RELEASE", "RESULT_READY"), credit)
                modeled = database.execute("SELECT parent,milestone FROM edges WHERE node=?",
                    ("owner:7:0:FRONTEND_OUTSTANDING:RELEASE",)).fetchall()
                self.assertIn(("npu:0", "RESULT_READY"), modeled)
                self.assertNotIn(("npu:0", "RESOURCE_READY"), modeled)

    def test_missing_owner_transition_is_rejected(self) -> None:
        rows, declaration, results = pipeline_case()
        owners = array(declaration["pipeline_owners"])
        owners.pop(0)
        with self.assertRaises(ValueError):
            adapt_records(rows, declaration, results)

    def test_overlapping_parent_rows_are_rejected(self) -> None:
        rows, declaration, results = pipeline_case()
        results[1]["row_begin"] = 0
        for owner in array(declaration["pipeline_owners"]):
            record = object_value(owner)
            if record["stripe_id"] == 1:
                record["row_begin"], record["row_end"] = 0, 1
        with self.assertRaises(ValueError):
            adapt_records(rows, declaration, results)

    def test_duplicate_owner_transition_is_rejected(self) -> None:
        rows, declaration, results = pipeline_case()
        owners = array(declaration["pipeline_owners"])
        duplicate = dict(object_value(owners[0]))
        duplicate["producer_sequence"] = len(owners)
        owners.append(duplicate)
        with self.assertRaises(ValueError):
            adapt_records(rows, declaration, results)

    def test_workspace_slot_is_not_target_slot(self) -> None:
        rows, declaration, results = pipeline_case()
        object_value(array(declaration["pipeline_owners"])[0])["target_npu_slot"] = 0
        with self.assertRaises(ValueError):
            adapt_records(rows, declaration, results)

    def test_capacity_release_requires_selected_work(self) -> None:
        rows, declaration, results = pipeline_case()
        owners = array(declaration["pipeline_owners"])
        release = next(object_value(owner) for owner in owners if
                       object_value(owner)["resource"] == "FRONTEND_OUTSTANDING" and
                       object_value(owner)["transition"] == "RELEASE")
        release["required_work_ids"] = []
        with self.assertRaises(ValueError):
            adapt_records(rows, declaration, results)

    def test_residual_child_work_is_bound_to_dense_stripe_and_fence(self) -> None:
        rows, declaration, results = pipeline_case()
        residual_call = 7
        stages: tuple[tuple[str, list[JsonValue]], ...] = (
            ("PREPARE", ["npu:0"]), ("INVOKE", ["call:7:PREPARE"]),
            ("COMPLETE_REQUIRED", ["call:7:INVOKE", "npu:3"]),
            ("CONTINUATION", ["call:7:COMPLETE_REQUIRED"]))
        for stage, dependencies in stages:
            rows.append({"kind": "CALL_BOUNDARY", "node_id": f"call:{residual_call}:{stage}",
                         "call_id": residual_call, "call_kind": "RESIDUAL_COMPACT", "stage": stage,
                         "operation_node_id": "operation:a", "node_class": "TARGET_NPU",
                         "dependencies": dependencies})
        rows.append({"kind": "SERVICE", "node_id": "npu:3", "node_class": "TARGET_NPU",
                     "operation_node_id": "operation:a", "dependencies": ["call:7:INVOKE"]})
        child: Record = {**results[0], "scope": "residual_compact", "work_id": 3,
                         "call_id": residual_call, "parent_id": 8, "stripe_id": None,
                         "host_slot": None, "parent_m": 1, "source_row_begin": 0,
                         "source_row_count": 1, "run_view_sha256": "work-3"}
        results.append(child)
        parent = object_value(array(declaration["pipeline_parents"])[0])
        parent["required_work_ids"] = [0, 3, 1, 2]
        parent["fence_required_work_ids"] = [0, 3, 1, 2]
        next(row for row in rows if row["node_id"] == "call:3:COMPLETE_REQUIRED")["dependencies"] = [
            "call:3:INVOKE", "npu:0", "npu:1", "npu:2", "npu:3"]
        parent["residual_bindings"] = [{"work_id": 3, "call_id": 7,
                                        "child_parent_id": 8, "dense_work_id": 0,
                                        "dense_parent_id": 7, "stripe_id": 0,
                                        "row_begin": 0, "row_end": 1,
                                        "source_row_begin": 0, "source_row_count": 1}]
        for raw in array(declaration["pipeline_owners"]):
            owner = object_value(raw)
            if (owner["stripe_id"] == 0 and
                owner["resource"] in ("RESIDUAL_CALLBACK", "FRONTEND_OUTSTANDING") and
                owner["transition"] in ("COMPLETE", "RELEASE")):
                owner["required_work_ids"] = [0, 3]
        object_value(declaration["call_slots"])["7"] = None
        array(declaration["submission_order"]).insert(1, "npu:3")
        ir, _ = adapt_records(rows, declaration, results)
        nodes = {node.identity: node for node in ir.nodes}
        self.assertIn(Dependency(NodeId("npu:3"), Milestone.RESULT_READY),
                      nodes[NodeId("owner:7:0:FRONTEND_OUTSTANDING:RELEASE")].dependencies)
        self.assertIn(Dependency(NodeId("npu:3"), Milestone.RESULT_READY),
                      nodes[NodeId("call:3:COMPLETE_REQUIRED")].dependencies)


if __name__ == "__main__":
    unittest.main()
