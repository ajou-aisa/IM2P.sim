from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from math import prod
from typing import TextIO

from scripts.gemmini_replay_contract import canonical_json, compatible, hardware_contract
from scripts.gemmini_resolve_profile import JsonValue
from sim.cycle.certificate_contract import read_document, validate_certificate
from sim.cycle.cli import RESULT_FIELDS
from sim.cycle.npu_trace import ReplayArtifacts
from sim.cycle.npu_trace_schema import Record, SEMANTIC_FIELDS, SemanticKey, integer, object_value, require, semantic_key
from sim.cycle.npu_trace_integrity import read_records, start_trace
from sim.cycle.reconstruct_cpu import CpuIndex, duration_sample, encoded_key
from sim.cycle.reconstruct_graph import Manifest, array, emit, fields, json_records, service_identity, sha256


@dataclass(frozen=True, slots=True)
class NpuFiles:
    trace: Path
    results: Path
    artifacts: ReplayArtifacts


class NpuJoin:
    def __init__(self, files: NpuFiles, graph: Manifest, cpu: CpuIndex) -> None:
        self.files, self.graph, self.cpu = files, graph, cpu
        self.counts: Counter[str] = Counter()
        self.targets: set[SemanticKey] = set()
        self.call_steps: dict[int, str] = {}
        self.call_works: dict[int, str] = {}

    def write(self, stream: TextIO) -> Record:
        cert = read_document(self.files.artifacts.certificate)
        validate_certificate(cert, self.files.artifacts.library)
        certificate_hash, library_hash = sha256(self.files.artifacts.certificate), sha256(self.files.artifacts.library)
        records = read_records(self.files.trace)
        state = start_trace(records)
        require(state.run.run_config_id == self.graph.run['run_config_id'], 'NPU/graph configuration mismatch')
        contracts = object_value(cert['hardware_contracts'])
        compatible(state.run.contract, object_value(contracts.get(state.run.profile)))
        compatible(state.run.contract, hardware_contract(state.run.profile))
        results = json_records(self.files.results)
        phase_index = 0
        for record in records:
            state.consume(record)
            kind = record['kind']
            if kind == 'PHASE':
                require(phase_index < len(self.graph.phases), 'extra NPU phase')
                phase = self.graph.phases[phase_index]
                require(all(record[key] == phase[key] for key in ('phase_kind', 'decode_index', 'input_tokens')), 'NPU/semantic phase mismatch')
                phase_index += 1
                continue
            if kind == 'RUN_END':
                continue
            key = semantic_key(record)
            require(key in self.graph.nodes, 'NPU record lacks original graph node')
            execution = self.graph.executions[key]['execution_class']
            operation_node = 'operation:' + encoded_key(key)
            predecessors = ['operation:' + encoded_key(source) for source in self.graph.edges[key]]
            if kind == 'TARGET_OPERATION':
                require(record['selected_target'] == execution, 'target operation/graph execution class mismatch')
                payload = object_value(self.graph.nodes[key]['payload'])
                require((record['operation'], record['layer']) == (payload['op'], payload['name']), 'target logical operation/name mismatch')
                operands = self.graph.inputs(key)
                require(len(operands) >= 2 and operands[0] is not None and operands[1] is not None, 'matmul operands missing')
                weight, activation = object_value(operands[0]), object_value(operands[1])
                ws, acts = array(weight['shape']), array(activation['shape'])
                rows = prod(integer({'dimension': value}, 'dimension') for value in acts[1:])
                require((record['m'], record['n'], record['k'], record['weight_type'], record['activation_type']) ==
                        (rows, ws[1], ws[0], weight['type'], activation['type']), 'target descriptor/original graph mismatch')
                require(record['actual_backend'] == self.graph.executions[key]['actual_backend'], 'target backend provenance mismatch')
                if execution == 'TARGET_NPU':
                    require(key not in self.targets and integer(record, 'npu_work_count') > 0, 'missing/duplicate target NPU operation')
                    self.targets.add(key)
            elif kind == 'NPU_WORK':
                require(execution == 'TARGET_NPU', 'NPU work belongs to non-NPU node')
                result = next(results, None)
                require(result is not None, 'missing NPU model result')
                if result is None: raise ValueError('missing result')
                extra = {'profile', 'cycle_library_sha256', 'certificate_sha256', 'certificate_schema', 'certificate_version',
                         'producer_execution_kind', 'target_work_validation', 'cycle_model_validation',
                         'actual_rtl_acceptance_in_collection', 'accounting_kind', 'cycle_unit', 'trace_sequence', 'modeled'}
                fields(result, set(record) | extra)
                require(result['schema'] == 'im2p-npu-cycle-result' and integer(result, 'version') == 1 and result['kind'] == 'NPU_WORK_RESULT', 'invalid NPU result schema')
                require(integer(result, 'sequence') == record['work_id'] and integer(result, 'trace_sequence') == record['sequence'], 'NPU result identity/order mismatch')
                expected = {name: value for name, value in record.items() if name not in ('schema', 'kind', 'sequence')}
                require(canonical_json({name: result[name] for name in expected}) == canonical_json(expected), 'NPU work/result field mismatch')
                require((result['profile'], result['certificate_sha256'], result['cycle_library_sha256']) ==
                        (state.run.profile, certificate_hash, library_hash), 'NPU result profile/certificate/library mismatch')
                require(result['certificate_schema'] == cert['schema'] and result['certificate_version'] == cert['version'] and
                        result['cycle_model_validation'] == 'CURRENT_CERTIFIED' and result['target_work_validation'] == 'PASS' and
                        result['actual_rtl_acceptance_in_collection'] == 'NOT_APPLICABLE' and result['producer_execution_kind'] == 'CPU_FUNCTIONAL' and
                        result['accounting_kind'] == 'isolated-work-accounting' and result['cycle_unit'] == 'cycles', 'invalid NPU result scope')
                modeled = object_value(result['modeled']); fields(modeled, set(RESULT_FIELDS))
                for name in RESULT_FIELDS: integer(modeled, name)
                require(modeled['logical_work_count'] == 1 and integer(modeled, 'total_cycles') > 0 and
                        integer(modeled, 'done_cycle') - integer(modeled, 'start_cycle') == modeled['total_cycles'], 'invalid model endpoints/counters')
                emit(stream, {'kind': 'SERVICE', 'node_class': 'TARGET_NPU', 'duration_source': 'NPU_MODEL', 'resource_kind': 'NPU',
                              'duration': {'source': 'NPU_MODEL', 'unit': 'cycles', 'cycles': modeled['total_cycles']},
                              'source_record': {'input': 'npu_results', 'work_id': record['work_id'],
                                                'sequence': result['sequence'], 'trace_sequence': record['sequence']},
                              'aggregation': 'NOT_MODELED', **{name: record[name] for name in SEMANTIC_FIELDS},
                              **service_identity(key, self.graph.nodes[key]),
                              'producer_operation_id': record['operation_id'], 'work_id': record['work_id'],
                              'node_id': 'npu:' + str(record['work_id']), 'is_execution_node': True,
                              'operation_node_id': operation_node,
                              'dependencies': list[JsonValue](['call:' + str(record['call_id']) + ':INVOKE'] +
                                              ['host:' + str(stage) for stage in array(record['required_host_stage_ids'])])})
                self.call_works[integer(record, 'call_id')] = 'npu:' + str(record['work_id'])
                self.counts['npu_work_count'] += 1
            elif kind == 'HOST_STAGE' and record['event'] == 'BEGIN':
                measurement = self.cpu.host(record)
                emit(stream, {'kind': 'SERVICE', 'node_class': record['execution_class'],
                              'duration_source': 'POTAL_COLLECTION' if measurement is not None else 'NONE',
                              'resource_kind': 'CPU', 'duration': duration_sample(measurement) if measurement is not None else None,
                              'cost_included': measurement is not None, 'host_stage_id': record['host_stage_id'],
                              'trace_sequence': record['sequence'], 'stage': record['stage_name'], 'aggregation': 'NOT_MODELED',
                              **service_identity(key, self.graph.nodes[key]), 'producer_operation_id': record['operation_id'],
                              'node_id': 'host:' + str(record['host_stage_id']), 'is_execution_node': True,
                              'operation_node_id': operation_node,
                              'dependencies': list[JsonValue](sorted(set(predecessors + ['npu:' + str(work) for work in array(record['required_work_ids'])] +
                                                         ['host:' + str(stage) for stage in array(record['required_host_stage_ids'])])))})
                self.counts[str(record['execution_class']).lower() + '_count'] += 1
            elif kind == 'NPU_CALL':
                identity = integer(record, 'call_id')
                node_id = 'call:' + str(identity) + ':' + str(record['stage'])
                dependencies = [self.call_steps[identity]] if identity in self.call_steps else predecessors
                dependencies += ['npu:' + str(work) for work in array(record['required_work_ids'])]
                if record['stage'] == 'PUBLISH': dependencies.append(self.call_works[identity])
                emit(stream, {'kind': 'CALL_BOUNDARY', 'node_class': 'TARGET_NPU', 'duration_source': 'NONE',
                              'resource_kind': 'STRUCTURAL', 'duration': None, 'call_id': record['call_id'],
                              'call_kind': record['call_kind'], 'stage': record['stage'], 'trace_sequence': record['sequence'],
                              **service_identity(key, self.graph.nodes[key]), 'producer_operation_id': record['operation_id'],
                              'node_id': node_id, 'is_execution_node': False, 'operation_node_id': operation_node,
                              'dependencies': list[JsonValue](sorted(set(dependencies)))})
                if record['stage'] == 'CONTINUATION':
                    self.call_steps.pop(identity, None); self.call_works.pop(identity, None)
                else:
                    self.call_steps[identity] = node_id
        validation = state.summary()
        require(next(results, None) is None, 'extra NPU model result')
        require(phase_index == len(self.graph.phases), 'missing NPU phase')
        require(self.targets == {key for key, row in self.graph.executions.items() if row['execution_class'] == 'TARGET_NPU'},
                'missing/extra NPU target operation coverage')
        require(self.counts['npu_work_count'] == validation['npu_work_count'], 'NPU result coverage mismatch')
        self.cpu.finish()
        return {'npu_work_count': self.counts['npu_work_count'], 'target_npu_operation_count': len(self.targets),
                'potal_host_count': self.counts['potal_host_count'], 'functional_emulation_count': self.counts['functional_emulation_count'],
                'certificate_sha256': certificate_hash, 'cycle_library_sha256': library_hash,
                'npu_work_result_bijection': 'PASS', 'host_stage_measurement_coverage': 'PASS', 'structural_dependency_dag': 'PASS'}
