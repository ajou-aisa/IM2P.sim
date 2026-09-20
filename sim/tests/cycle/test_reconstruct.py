from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest

from sim.cycle.npu_trace_schema import Record


def graph_records(role: str) -> list[Record]:
    cpu: Record = dict(threads=1, poll=50, priority=0, strict_cpu=False, mask_valid=False, mask='0')
    workload: Record = dict(model='fixture.gguf', prompt_tokens=256, generated_tokens=5, context_tokens=512,
        batch_tokens=256, microbatch_tokens=256, cpu=cpu, cpu_batch=cpu, flash_attention=False,
        kv_type_k='f16', kv_type_v='f16', seed=1, numa=0, build_target='test-cpu')
    first: Record = dict(node_ordinal=0, excluded=False, payload=dict(op='ADD', name='same', type='F32',
        shape=[3072, 1, 1, 1], parameters_hex='00', parameters_status='STATIC_BYTES', inputs=[dict(kind='leaf', ordinal=0)]))
    second: Record = dict(node_ordinal=1, excluded=False, payload=dict(op='MUL_MAT', name='same', type='F32',
        shape=[1, 1, 1, 1], parameters_hex='00', parameters_status='STATIC_BYTES',
        inputs=[dict(kind='leaf', ordinal=1), dict(kind='node', ordinal=0)]))
    rows: list[Record] = [
        dict(kind='RUN', source_role=role, run_config_id='workload-0', workload=workload,
             producer=dict(cycle_sim=int(role == 'POTAL_COLLECTION'), log_cycle=1, cpu_only_build=role == 'FULL_CPU',
                           git_commit='a'*40, warmup_excluded=True, reserve_measure_graphs_excluded=True),
             cpu_only_build=role == 'FULL_CPU'),
        dict(kind='PHASE', phase_kind='prefill', decode_index=None, input_tokens=256,
             token_fingerprint='fnv1a64-le-i32:'+'1'*16),
        dict(kind='GRAPH', phase_kind='prefill', decode_index=None, graph_occurrence=0,
             nodes=[first, second], leaves=[dict(name='leaf', type='F32', shape=[3072, 1, 1, 1]),
                                           dict(name='weight', type='Q8_HP1', shape=[3072, 1, 1, 1])]),
        dict(kind='NODE_EXECUTION', semantic_phase_kind='prefill', semantic_decode_index=None,
             semantic_graph_occurrence=0, semantic_node_ordinal=0, source_role=role, run_config_id='workload-0',
             actual_backend='CPU', execution_class='ORDINARY_CPU', success=True),
        dict(kind='NODE_EXECUTION', semantic_phase_kind='prefill', semantic_decode_index=None,
             semantic_graph_occurrence=0, semantic_node_ordinal=1, source_role=role, run_config_id='workload-0',
             actual_backend='Gemmini', execution_class='ORDINARY_CPU' if role == 'FULL_CPU' else 'TARGET_NPU', success=True),
        dict(kind='RUN_END', success=True, expected_node_count=2, executed_node_count=2,
             cpu_only_proven=role == 'FULL_CPU', graph_count=1),
    ]
    for sequence, row in enumerate(rows):
        row.update(schema='im2p-semantic-graph', version=1, sequence=sequence)
    return rows


def write_rows(path: Path, rows: list[Record]) -> None:
    path.write_text(''.join(json.dumps(row)+'\n' for row in rows))


class SemanticJoinTests(unittest.TestCase):
    def pair(self, full: list[Record], potal: list[Record]):
        from sim.cycle.reconstruct_graph import compare_manifests, read_manifest
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); write_rows(root/'full.jsonl', full); write_rows(root/'potal.jsonl', potal)
            left, right = read_manifest(root/'full.jsonl'), read_manifest(root/'potal.jsonl')
            compare_manifests(left, right)
            return right

    def test_repeated_names_join_by_original_semantic_ordinal(self):
        graph = self.pair(graph_records('FULL_CPU'), graph_records('POTAL_COLLECTION'))
        self.assertEqual(len(graph.nodes), 2)

    def test_missing_duplicate_and_unproven_execution_rejected(self):
        for index, field, value in ((5, 'executed_node_count', 1), (5, 'cpu_only_proven', False),
                                   (4, 'semantic_node_ordinal', 0), (4, 'execution_class', 'TARGET_NPU')):
            full = graph_records('FULL_CPU'); full[index][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.pair(full, graph_records('POTAL_COLLECTION'))

    def test_workload_phase_payload_and_edges_cannot_be_guessed(self):
        base = graph_records('POTAL_COLLECTION')
        changes = []
        variant = copy.deepcopy(base); variant[1]['token_fingerprint'] = 'fnv1a64-le-i32:'+'2'*16; changes.append(variant)
        variant = copy.deepcopy(base); variant[0]['workload'] = {}; changes.append(variant)
        variant = copy.deepcopy(base); variant[2]['leaves'] = []; changes.append(variant)
        variant = copy.deepcopy(base); variant[4]['run_config_id'] = 'other'; changes.append(variant)
        for variant in changes:
            with self.assertRaises(ValueError): self.pair(graph_records('FULL_CPU'), variant)

    def test_decode_values_can_differ_when_semantic_structure_matches(self):
        full, potal = graph_records('FULL_CPU'), graph_records('POTAL_COLLECTION')
        for rows in (full, potal):
            second = copy.deepcopy(rows[1:5])
            for row in second:
                if 'phase_kind' in row: row.update(phase_kind='decode', decode_index=0)
                if 'semantic_phase_kind' in row: row.update(semantic_phase_kind='decode', semantic_decode_index=0)
                if row['kind'] == 'PHASE': row['input_tokens'] = 1
            rows[-1:-1] = second
            rows[-1].update(expected_node_count=4, executed_node_count=4, graph_count=2)
            for sequence, row in enumerate(rows): row['sequence'] = sequence
        potal[5]['token_fingerprint'] = 'fnv1a64-le-i32:'+'9'*16
        self.assertEqual(len(self.pair(full, potal).nodes), 4)


if __name__ == '__main__':
    unittest.main()
