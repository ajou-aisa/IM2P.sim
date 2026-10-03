from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Final, assert_never

from scripts.gemmini_resolve_profile import JsonValue
from sim.cycle.npu_trace_schema import Record, integer, object_value, unique_pairs
from sim.cycle.reconstruct_graph import array, sha256

ROOT: Final = Path(__file__).resolve().parents[3]
CORPUS_SOURCES: Final = (Path(__file__).resolve(),
                         *(ROOT / f'sim/cycle/{name}.py' for name in (
                             'execution_lifecycle', 'execution_pipeline_contract',
                             'execution_ir', 'npu_trace', 'npu_trace_integrity',
                             'npu_trace_calls', 'npu_trace_hosts', 'npu_trace_schema',
                             'reconstruct_graph')),
                         ROOT / 'scripts/gemmini_replay_contract.py')


class CorpusAuthorityError(ValueError):
    detail: str

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


def _bound_file(binding: Record, name: str) -> Path:
    digest = binding.get('sha256')
    raw_path = binding.get('path')
    if (set(binding) != {'path', 'sha256'} or not isinstance(raw_path, str) or
            not isinstance(digest, str) or re.fullmatch(r'[0-9a-f]{64}', digest) is None):
        raise CorpusAuthorityError(f'producer corpus {name} binding malformed')
    path = Path(raw_path)
    if not path.is_absolute() or not path.is_file() or sha256(path) != digest:
        raise CorpusAuthorityError(f'producer corpus {name} digest mismatch')
    return path


def _cycles(value: JsonValue) -> list[int]:
    return [integer({'cycle': row}, 'cycle') for row in array(value)]


def _grid_epochs(grid: Record, stimulus: Record, plan: Record) -> list[list[int]]:
    if grid.get('status') != 'PREDECLARED_BEFORE_TARGET_RTL':
        raise CorpusAuthorityError('predeclared offer grid status differs')
    schema = grid.get('schema')
    if schema not in ('im2p-two-parent-absolute-offer-grid-v1',
                      'im2p-todo14-directed-offer-grid-v1'):
        raise CorpusAuthorityError('predeclared offer grid schema unsupported')
    match schema:
        case 'im2p-two-parent-absolute-offer-grid-v1':
            if grid.get('version') != 1:
                raise CorpusAuthorityError('predeclared offer grid version differs')
            cases = [object_value(row) for row in array(grid['cases'])
                     if object_value(row).get('profile') == stimulus['profile']]
            if len(cases) != 1:
                raise CorpusAuthorityError('predeclared offer grid case/profile differs')
            case = cases[0]
            if (case.get('producer_artifacts') != stimulus['producer_artifacts'] or
                    case.get('required_work_ids') != plan['required_work_ids'] or
                    case.get('parent_ids') != plan['parent_ids'] or
                    case.get('parent_fences') != plan['fence_work_ids']):
                raise CorpusAuthorityError('predeclared offer grid source differs')
            available = _cycles(case['request_available_cycles'])
            ports = _cycles(case['electrical_port_offer_cycles'])
        case 'im2p-todo14-directed-offer-grid-v1':
            if grid.get('profile') != stimulus['profile']:
                raise CorpusAuthorityError('predeclared offer grid profile differs')
            cases = [object_value(row) for row in array(grid['cases'])
                     if object_value(row).get('case_id') == stimulus['case_id']]
            if len(cases) != 1:
                raise CorpusAuthorityError('predeclared offer grid case/profile differs')
            prior_report = object_value(grid['todo13_report'])
            report_path = _bound_file(prior_report, 'predeclared offer grid todo13 report')
            prior = object_value(json.loads(report_path.read_text(), object_pairs_hook=unique_pairs))
            prior_q0 = integer(object_value(array(prior['works'])[0]), 'resource_ready')
            if (grid.get('prior_q0') != prior_q0 or plan.get('prior_q0') != prior_q0 or
                    plan.get('prior_q_report_sha256') != prior_report['sha256'] or
                    prior.get('profile') != stimulus['profile']):
                raise CorpusAuthorityError('predeclared offer grid prior_q0 differs from source report')
            old_plan_path = _bound_file(object_value(grid['todo13_plan']),
                                        'predeclared offer grid todo13 plan')
            old_plan = object_value(json.loads(old_plan_path.read_text(), object_pairs_hook=unique_pairs))
            ports = _cycles(grid['base_ports'])
            base = [_cycles(row)[1] for row in array(old_plan['offer_epochs'])]
            if (ports != base or old_plan.get('profile') != stimulus['profile'] or
                    old_plan.get('producer_artifacts') != stimulus['producer_artifacts'] or
                    old_plan.get('required_work_ids') != plan['required_work_ids']):
                raise CorpusAuthorityError('predeclared offer grid source base differs')
            receipt = object_value(grid['producer_receipt'])
            _bound_file(receipt, 'predeclared offer grid producer receipt')
            if receipt['sha256'] != plan['producer_receipt_sha256']:
                raise CorpusAuthorityError('predeclared offer grid producer source differs')
            _bound_file(object_value(grid['native_library']), 'predeclared offer grid native library')
            build = object_value(grid['rtl_build'])
            if sha256(Path(str(build['path'])) / 'rtl-build-binding.json') != build['binding_sha256']:
                raise CorpusAuthorityError('predeclared offer grid RTL build digest mismatch')
            case = cases[0]
            if len(ports) < 2:
                raise CorpusAuthorityError('predeclared offer grid work count differs')
            ports[1] = prior_q0 + integer(case, 'idle_cycles')
            available = ports.copy()
            available[1] = integer(case, 'work1_available')
            if available[1] > ports[1]:
                raise CorpusAuthorityError('predeclared offer grid work1 availability after port')
            for key, value in object_value(case['extra_ports']).items():
                if not key.isdecimal() or str(int(key)) != key or not 2 <= int(key) < len(ports):
                    raise CorpusAuthorityError('predeclared offer grid extra port index differs')
                ports[int(key)] = integer({'port': value}, 'port')
                available[int(key)] = ports[int(key)]
        case unreachable:
            assert_never(unreachable)
    if len(available) != len(ports) or len(ports) != len(array(plan['offer_epochs'])):
        raise CorpusAuthorityError('predeclared offer grid work count differs')
    return [[a, p] for a, p in zip(available, ports, strict=True)]


def validate_corpus(stimulus: Record, projector: Callable[[Path, Path, Path,
                     tuple[int, ...], tuple[int, ...]], Record]) -> None:
    if 'producer_corpus' not in stimulus:
        return
    parents = [object_value(row) for row in array(stimulus['pipeline_parents'])]
    indices = [integer({'index': row}, 'index') for row in array(stimulus['selected_parent_indices'])]
    if len(parents) != 2 or len(indices) != 2 or indices[1] != indices[0] + 1:
        raise CorpusAuthorityError('two contiguous producer parents required')
    binding = object_value(stimulus['producer_corpus'])
    if set(binding) != {'schema', 'plan', 'receipt'} or binding['schema'] != 'TWO_CONTIGUOUS_PARENTS_V1':
        raise CorpusAuthorityError('producer corpus binding shape differs')
    receipt = object_value(binding['receipt'])
    _bound_file(receipt, 'receipt')
    plan_path = _bound_file(object_value(binding['plan']), 'plan')
    try:
        plan = object_value(json.loads(plan_path.read_text(), object_pairs_hook=unique_pairs))
    except (ValueError, TypeError) as error:
        raise CorpusAuthorityError('producer corpus plan malformed') from error
    if (plan.get('schema'), plan.get('version'), plan.get('status')) != \
            ('im2p-two-parent-corpus-plan-v1', 1, 'REVIEWED_SOURCE_BOUND'):
        raise CorpusAuthorityError('producer corpus plan schema differs')
    if (plan.get('case_id') != stimulus['case_id'] or plan.get('profile') != stimulus['profile'] or
            plan.get('selected_parent_indices') != indices or
            plan.get('producer_receipt_sha256') != receipt['sha256'] or
            plan.get('producer_artifacts') != stimulus['producer_artifacts']):
        raise CorpusAuthorityError('producer corpus plan identity differs')
    artifacts = object_value(stimulus['producer_artifacts'])
    paths = [Path(str(object_value(artifacts[name])['path']))
             for name in ('trace', 'lifecycle', 'semantic_graph')]
    projected = projector(paths[0], paths[1], paths[2],
                          (0,) * len(array(plan['required_work_ids'])), tuple(indices))
    source_parents = [object_value(row) for row in array(projected['pipeline_parents'])]
    source_works = [object_value(row) for row in array(projected['works'])]
    if (plan.get('parent_ids') != [row['parent_id'] for row in source_parents] or
            plan.get('required_work_ids') != [row['work_id'] for row in source_works] or
            plan.get('fence_work_ids') != [row['fence_required_work_ids'] for row in source_parents] or
            plan.get('work_bindings') != [row['work_binding'] for row in source_works] or
            plan.get('work_slots') != [row['slot'] for row in source_works]):
        raise CorpusAuthorityError('producer corpus plan content differs')
    if (projected['pipeline_parents'] != stimulus['pipeline_parents'] or
            projected['selected_parent_indices'] != stimulus['selected_parent_indices']):
        raise CorpusAuthorityError('producer corpus parent projection differs')
    if projected['pipeline_owners'] != stimulus['pipeline_owners']:
        raise CorpusAuthorityError('producer corpus owner projection differs')
    if (projected['npu_summary'] != stimulus['npu_summary'] or
            projected['hardware_contract'] != stimulus['hardware_contract'] or
            projected['producer_artifacts'] != stimulus['producer_artifacts']):
        raise CorpusAuthorityError('producer corpus summary/source differs')
    manifest_works = [{key: value for key, value in object_value(row).items()
                       if key not in ('request_available_cycle', 'port_offer_cycle')}
                      for row in array(stimulus['works'])]
    projected_works = [{key: value for key, value in row.items() if key != 'arrival_delay'}
                       for row in source_works]
    if manifest_works != projected_works:
        raise CorpusAuthorityError('producer corpus work projection differs')
    offers = [[row['request_available_cycle'], row['port_offer_cycle']]
              for row in map(object_value, array(stimulus['works']))]
    if plan.get('offer_epochs') != offers:
        raise CorpusAuthorityError('producer corpus offer plan differs')
    grid_sha = plan.get('predeclared_offer_grid_sha256')
    grid_binding = stimulus.get('predeclared_offer_grid')
    if grid_sha is None or grid_binding is None:
        raise CorpusAuthorityError('predeclared offer grid missing')
    binding = object_value(grid_binding)
    grid_path = _bound_file(binding, 'predeclared offer grid')
    if binding['sha256'] != grid_sha:
        raise CorpusAuthorityError('predeclared offer grid plan digest differs')
    try:
        grid = object_value(json.loads(grid_path.read_text(), object_pairs_hook=unique_pairs))
        expected_offers = _grid_epochs(grid, stimulus, plan)
    except CorpusAuthorityError:
        raise
    except (IndexError, KeyError, TypeError, ValueError, OSError) as error:
        raise CorpusAuthorityError('predeclared offer grid malformed') from error
    if offers != expected_offers:
        raise CorpusAuthorityError('predeclared offer grid epochs differ')
