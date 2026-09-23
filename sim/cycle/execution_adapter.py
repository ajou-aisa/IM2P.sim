from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import json
from pathlib import Path
import re

from scripts.gemmini_resolve_profile import JsonValue
from sim.cycle.certificate_contract import read_document
from sim.cycle.execution_application import ApplicationSource, add_application
from sim.cycle.execution_ir import Dependency, ExecutionIR, Kind, Milestone, Node, NodeId, ResourceId, ServiceId, ensure
from sim.cycle.execution_services import CpuService, NpuWork, Services, bind_cpu_resource, cpu_service
from sim.cycle.npu_trace_schema import Record, integer, object_value, text
from sim.cycle.reconstruct_graph import array, fields, json_records, sha256


@dataclass(frozen=True, slots=True)
class AdapterFiles:
    dataset: Path
    lifecycle: Path
    npu_results: Path
    application: Path | None = None


def adapt(files: AdapterFiles) -> tuple[ExecutionIR, Services]:
    lifecycle = read_document(files.lifecycle)
    ensure(lifecycle.get('dataset_sha256') == sha256(files.dataset), 'dataset/lifecycle binding mismatch')
    ensure(lifecycle.get('npu_results_sha256') == sha256(files.npu_results), 'NPU result/lifecycle binding mismatch')
    ir, services = adapt_records(json_records(files.dataset), lifecycle, json_records(files.npu_results))
    if files.application is not None:
        application = object_value(lifecycle['application'])
        ensure(application.get('sha256') == sha256(files.application), 'application/lifecycle binding mismatch')
        return add_application(ir, services, ApplicationSource(tuple(json_records(files.application)), application))
    ensure('application' not in lifecycle, 'declared application input missing')
    ensure(ir.scope == 'SYNTHETIC', 'PoTal application lifecycle is required for bound E2E execution')
    return ir, services


def validate_lifecycle_contract(contract: Record) -> None:
    fields(contract, {'schema', 'version', 'source_kind', 'dataset_sha256', 'npu_results_sha256',
                      'operation_exit_policy', 'publish_policy', 'slot_release_policy', 'phase_graph_policy',
                      'entry_dependencies', 'barriers', 'call_slots', 'submission_order', 'cpu_policy', 'worker_resources'} |
           ({'application'} if 'application' in contract else set()) |
           ({'producer_binding'} if 'producer_binding' in contract else set()))
    ensure(contract['schema'] == 'im2p-execution-lifecycle' and integer(contract, 'version') == 1,
           'missing current execution lifecycle; legacy structural dataset is not executable')
    ensure((contract['operation_exit_policy'], contract['publish_policy'], contract['slot_release_policy'],
            contract['phase_graph_policy']) == ('ALL_MEMBER_COMPLETIONS', 'NONBLOCKING_SUBMISSION',
                                               'NPU_RESOURCE_READY', 'EXPLICIT_EDGES'), 'unsupported execution policy')
    ensure(contract['source_kind'] in ('SYNTHETIC', 'PRODUCER_DECLARED'), 'unproven lifecycle source')
    if contract['source_kind'] == 'PRODUCER_DECLARED':
        binding = object_value(contract.get('producer_binding'))
        fields(binding, {'sidecar_sha256', 'semantic_graph_sha256', 'provenance_sha256',
                         'executable_sha256', 'native_build_sha256', 'join_summary_sha256'})
        ensure(all(isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None
                   for value in binding.values()), 'producer lifecycle artifact binding missing')


def adapt_records(rows: Iterable[Record], contract: Record, results: Iterable[Record]) -> tuple[ExecutionIR, Services]:
    validate_lifecycle_contract(contract)
    indexed: dict[str, Record] = {}
    for row in rows:
        identity = text(row, 'node_id')
        ensure(identity not in indexed, 'duplicate structural node')
        ensure(len(indexed) < 100_000, 'finite execution adapter limit exceeded: partition with explicit barriers')
        indexed[identity] = row
    operations = {key: row for key, row in indexed.items() if row['kind'] == 'OPERATION_CONTAINER'}
    ensure(bool(operations), 'missing operation containers')
    entry = object_value(contract['entry_dependencies'])
    ensure(set(entry) == set(operations), 'explicit phase/graph entry coverage mismatch')
    slots, resources = object_value(contract['call_slots']), object_value(contract['worker_resources'])
    submission = [text({'id': value}, 'id') for value in array(contract['submission_order'])]
    ensure(len(set(submission)) == len(submission), 'duplicate target submission order')
    result_map: dict[ServiceId, NpuWork] = {}
    for result in results:
        identity = ServiceId('npu:' + str(integer(result, 'work_id')))
        ensure(identity not in result_map, 'duplicate NPU model result')
        ensure(result.get('schema') == 'im2p-npu-cycle-result' and result.get('cycle_model_validation') == 'CURRENT_CERTIFIED',
               'NPU model result is not certified')
        result_map[identity] = NpuWork(ServiceId(identity), text(result, 'run_view_sha256'), text(result, 'profile'))
    npu_rows = {key for key, row in indexed.items() if row.get('node_class') == 'TARGET_NPU' and row['kind'] == 'SERVICE'}
    ensure(npu_rows == set(result_map) == set(submission), 'NPU service/submission/result bijection mismatch')
    members: dict[str, list[NodeId]] = {key: [] for key in operations}
    nodes: list[Node] = []
    cpu: dict[ServiceId, CpuService] = {}
    call_work: dict[str, str] = {}
    for identity in npu_rows:
        row = indexed[identity]
        own_call = [text({'id': value}, 'id') for value in array(row['dependencies'])
                    if isinstance(value, str) and value.startswith('call:') and value.endswith(':INVOKE')]
        ensure(len(own_call) == 1, 'NPU work lacks unique invocation')
        call_identity = own_call[0].split(':')[1]
        ensure(call_identity not in call_work, 'multiple works claim one invocation')
        call_work[call_identity] = identity
    ensure(set(slots) == set(call_work), 'explicit call/slot coverage mismatch')

    def edge(identity: str) -> Dependency:
        return Dependency(NodeId(identity + ':exit' if identity in operations else identity))

    for operation, row in operations.items():
        phase = json.dumps(row['phase'], sort_keys=True, separators=(',', ':'))
        dependencies = [edge(text({'id': value}, 'id')) for value in array(row['dependencies'])]
        dependencies.extend(edge(text({'id': value}, 'id')) for value in array(entry[operation]))
        nodes.append(Node(NodeId(operation + ':enter'), Kind.OP_ENTER, operation, phase, 0,
                          tuple(dict.fromkeys(dependencies))))

    for identity, row in indexed.items():
        if identity in operations:
            continue
        operation = text(row, 'operation_node_id')
        ensure(operation in operations, 'service lacks operation owner')
        phase = json.dumps(operations[operation]['phase'], sort_keys=True, separators=(',', ':'))
        dependencies = [edge(text({'id': value}, 'id')) for value in array(row['dependencies'])]
        dependencies.append(Dependency(NodeId(operation + ':enter')))
        kind, service, claims, order = Kind.BARRIER, None, (), 0
        match row['kind']:
            case 'CALL_BOUNDARY':
                call = str(integer(row, 'call_id'))
                if row['stage'] == 'PUBLISH':
                    ensure(call in call_work, 'publication lacks target work')
                    dependencies = [item for item in dependencies if item.node != call_work[call]]
                    kind = Kind.PUBLISH
            case 'SERVICE':
                match row['node_class']:
                    case 'ORDINARY_CPU' | 'POTAL_HOST' | 'APPLICATION_CPU':
                        kind = Kind.APPLICATION_CPU if row['node_class'] == 'APPLICATION_CPU' else Kind.CPU
                        source = text(row, 'duration_source')
                        expected_source = {'ORDINARY_CPU': 'FULL_CPU', 'POTAL_HOST': 'POTAL_COLLECTION',
                                           'APPLICATION_CPU': 'APPLICATION_CPU'}[str(row['node_class'])]
                        ensure(source == expected_source, 'duration source ownership mismatch')
                        duration = object_value(row['duration'])
                        vector = array(duration['worker_intervals']) if row['node_class'] == 'ORDINARY_CPU' else [duration]
                        worker_rows: list[JsonValue] = []
                        for raw in vector:
                            sample = object_value(raw)
                            worker_rows.append(bind_cpu_resource(sample, source, resources))
                        service = ServiceId(identity)
                        cpu[service] = cpu_service({'source': source, 'policy': contract['cpu_policy'], 'workers': worker_rows})
                        claims = tuple(worker.resource for worker in cpu[service].workers)
                    case 'TARGET_NPU':
                        kind, service = Kind.NPU, ServiceId(identity)
                        order = submission.index(identity)
                        call = next(call for call, work in call_work.items() if work == identity)
                        claims = (ResourceId('npu:0'),)
                        slot = slots[call]
                        if slot is not None:
                            ensure(type(slot) is int and slot in (0, 1), 'invalid target slot')
                            claims += (ResourceId('slot:' + str(slot)),)
                        publication = 'call:' + call + ':PUBLISH'
                        if publication in indexed:
                            invoke = 'call:' + call + ':INVOKE'
                            dependencies = [item for item in dependencies if item.node != invoke]
                            dependencies.append(Dependency(NodeId(publication)))
                        if order:
                            dependencies.append(Dependency(NodeId(submission[order - 1]), Milestone.ACCEPTED))
                    case 'FUNCTIONAL_EMULATION' | 'WAIT' | 'EXCLUDED':
                        kind = Kind(str(row['node_class']))
                    case _:
                        ensure(False, 'unknown execution source/class')
            case _:
                ensure(False, 'unsupported structural record kind')
        nodes.append(Node(NodeId(identity), kind, operation, phase, order,
                          tuple(dict.fromkeys(dependencies)), service, claims))
        members[operation].append(NodeId(identity))
    for operation, children in members.items():
        ensure(bool(children), 'operation has no completion-producing members')
        phase = json.dumps(operations[operation]['phase'], sort_keys=True, separators=(',', ':'))
        nodes.append(Node(NodeId(operation + ':exit'), Kind.OP_EXIT, operation, phase, 0,
                          tuple(Dependency(child) for child in children)))
    for raw in array(contract['barriers']):
        barrier = object_value(raw)
        fields(barrier, {'node_id', 'phase', 'dependencies'})
        dependencies = tuple(edge(text({'id': value}, 'id')) for value in array(barrier['dependencies']))
        identity = NodeId(text(barrier, 'node_id'))
        nodes.append(Node(identity, Kind.BARRIER, identity, text(barrier, 'phase'), 0, dependencies))
    scope = 'SYNTHETIC' if contract['source_kind'] == 'SYNTHETIC' else 'BOUND_DATASET'
    return ExecutionIR(tuple(nodes), scope, text(contract, 'dataset_sha256')), Services(cpu, result_map)
