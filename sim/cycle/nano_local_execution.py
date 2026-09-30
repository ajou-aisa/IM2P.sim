# How to run (evaluation only; never certified):
#   PYTHONPATH=IM2P.sim python3 -B -m sim.cycle.nano_local_execution join --local-validation RECEIPT ... (sim.cycle.reconstruct inputs)
#   PYTHONPATH=IM2P.sim python3 -B -m sim.cycle.nano_local_execution lifecycle --local-validation RECEIPT --library LIB ...
#   PYTHONPATH=IM2P.sim python3 -B -m sim.cycle.nano_local_execution adapt --local-validation RECEIPT --library LIB ...
"""Evaluation-only join, lifecycle and execution IR under a NANO_LOCAL_VALIDATED receipt.

The official stages split authority from transformation: sim.cycle.reconstruct takes an admission function and
the lifecycle/IR builders take a result admission. This module supplies the host-local authority to those same
cores. It never relabels results: the admitted validation stays NANO_LOCAL_VALIDATED, every result must carry the
receipt and library identity, and every output is accompanied by provenance with publication_certified=false.
Certified publication stages keep requiring CURRENT_CERTIFIED and therefore reject these outputs.
"""
from __future__ import annotations

import argparse
from collections.abc import Iterable, Iterator
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sim.cycle.certificate_contract import read_document
from sim.cycle.execution_adapter import AdapterFiles
from sim.cycle.execution_ir import ensure
from sim.cycle.local_validation import NANO_LOCAL_VALIDATED, admit_library, digest, sha256
from sim.cycle.npu_result_admission import ResultAdmission
from sim.cycle.npu_trace import ReplayArtifacts, ReplayOutputs
from sim.cycle.certificate_contract import object_value
from sim.cycle.npu_trace_schema import Record
from sim.cycle.reconstruct_npu import NpuAuthority

SCOPE = 'NANO_LOCAL_EVALUATION_ONLY'


def provenance(receipt_path: Path, library: Path) -> Record:
    """Identity every nano-local output carries; admission fails closed on any receipt/library/source mismatch."""
    receipt, validation = admit_library(receipt_path, library)
    ensure(validation == NANO_LOCAL_VALIDATED, 'a PASS local validation receipt is required')
    closure = object_value(receipt.get('source_closure'), 'receipt source closure')
    return {'evaluation_scope': SCOPE, 'validation_mode': NANO_LOCAL_VALIDATED,
            'validation_receipt_sha256': sha256(receipt_path), 'cycle_library_sha256': sha256(library),
            'source_closure_sha256': closure.get('closure_sha256'),
            'semantic_config_sha256': receipt.get('semantic_options_sha256'),
            'platform': receipt.get('platform'), 'publication_certified': False}


def local_authority(artifacts: ReplayArtifacts) -> NpuAuthority:
    """Join admission: `artifacts.certificate` is the local validation receipt; no certificate is read."""
    ensure(artifacts.run_certificate is None and artifacts.transition_certificate is None,
           'certificates cannot be combined with a local validation receipt')
    identity = provenance(artifacts.certificate, artifacts.library)
    receipt = read_document(artifacts.certificate)
    return NpuAuthority(NANO_LOCAL_VALIDATED, sha256(artifacts.certificate), receipt['schema'], receipt['version'],
                        sha256(artifacts.library), None, None, NANO_LOCAL_VALIDATED, True, None, identity)


def local_results(receipt_sha256: str, library_sha256: str) -> ResultAdmission:
    """Result admission: exactly the local replay rows of this receipt and library, never certified rows."""
    def admit(rows: Iterable[Record], detail: str) -> Iterator[Record]:
        for row in rows:
            ensure(row.get('cycle_model_validation') == NANO_LOCAL_VALIDATED and
                   row.get('certificate_sha256') == receipt_sha256 and
                   row.get('cycle_library_sha256') == library_sha256, detail + ' (NANO_LOCAL_VALIDATED receipt)')
            yield row
    return admit


def publish_provenance(output: Path, identity: Record, inputs: Record) -> None:
    path = output.with_name(output.name + '.nano-local.json')
    ensure(not path.exists(), 'nano-local provenance output must be new')
    document: Record = {'schema': 'im2p-nano-local-execution-provenance', 'version': 1, **identity,
                        'output': {'path': str(output), 'sha256': sha256(output)}, 'inputs': inputs}
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + '\n')


def join(args: argparse.Namespace) -> Record:
    from sim.cycle.reconstruct import Inputs, reconstruct
    from sim.cycle.reconstruct_cpu import CollectionFiles
    from sim.cycle.reconstruct_npu import NpuFiles
    inputs = Inputs(CollectionFiles(args.full_cpu_log, args.full_cpu_graph, args.full_cpu_provenance),
                    CollectionFiles(args.potal_log, args.potal_graph, args.potal_provenance),
                    NpuFiles(args.npu_trace, args.npu_results, ReplayArtifacts(args.library, args.local_validation)))
    return reconstruct(inputs, ReplayOutputs(args.output, args.summary), local_authority)


def lifecycle(args: argparse.Namespace) -> Record:
    from sim.cycle.execution_cli import publish
    from sim.cycle.execution_lifecycle_cli import CpuScenario, LifecycleFiles, build
    identity = provenance(args.local_validation, args.library)
    files = LifecycleFiles(args.sidecar, args.semantic_graph, args.provenance, args.application,
                           args.dataset, args.npu_results, args.join_summary)
    admit = local_results(str(identity['validation_receipt_sha256']), str(identity['cycle_library_sha256']))
    result = build(files, CpuScenario(read_document(args.worker_resources), args.cpu_policy, args.sampler_resource),
                   admit)
    publish(args.output, result)
    publish_provenance(args.output, identity, {'npu_results_sha256': sha256(args.npu_results),
                                               'join_summary_sha256': sha256(args.join_summary)})
    return {'status': 'PASS', 'source_kind': result['source_kind'], 'output': str(args.output), **identity}


def adapt(args: argparse.Namespace) -> Record:
    """The official `execution_cli adapt --streaming` checks, then the shared streaming IR core."""
    from sim.cycle.execution_stream import adapt_stream
    identity = provenance(args.local_validation, args.library)
    summary, contract = read_document(args.join_summary), read_document(args.lifecycle)
    ensure(summary.get('status') == 'PASS' and summary.get('scope') == 'structural-three-source-reconstruction',
           'successful current structural join required')
    ensure(summary.get('validation_mode') == NANO_LOCAL_VALIDATED and
           summary.get('validation_receipt_sha256') == identity['validation_receipt_sha256'],
           'join was not admitted by this local validation receipt')
    fingerprints = object_value(summary['decode_token_fingerprint_matches'], 'decode fingerprints')
    application = contract.get('application')
    decode_free = isinstance(application, dict) and application.get('expected_samples') == 1
    ensure(bool(fingerprints) != decode_free and all(value is True for value in fingerprints.values()),
           'decode trajectory mismatch or missing fingerprints')
    ensure(object_value(object_value(summary['source_artifacts'], 'sources')['npu_results'], 'npu')['sha256'] ==
           sha256(args.npu_results), 'join/NPU result binding mismatch')
    files = AdapterFiles(args.dataset, args.lifecycle, args.npu_results, args.application)
    admit = local_results(str(identity['validation_receipt_sha256']), str(identity['cycle_library_sha256']))
    result = adapt_stream(files, args.output, admit)
    publish_provenance(args.output, identity, {'join_summary_sha256': sha256(args.join_summary),
                                               'lifecycle_sha256': sha256(args.lifecycle),
                                               'result_sha256': digest(result)})
    return {**result, **identity}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    joiner = sub.add_parser('join')
    for source in ('full-cpu', 'potal'):
        for kind in ('log', 'graph', 'provenance'):
            joiner.add_argument('--' + source + '-' + kind, type=Path, required=True)
    for name in ('npu-trace', 'npu-results', 'library', 'local-validation', 'summary', 'output'):
        joiner.add_argument('--' + name, type=Path, required=True)
    life = sub.add_parser('lifecycle')
    for name in ('sidecar', 'semantic-graph', 'provenance', 'application', 'dataset', 'npu-results',
                 'join-summary', 'worker-resources', 'output', 'library', 'local-validation'):
        life.add_argument('--' + name, type=Path, required=True)
    life.add_argument('--cpu-policy', choices=('THREAD_CPU_NS_GANG', 'HOST_ELAPSED_NS_GANG'), required=True)
    life.add_argument('--sampler-resource', required=True)
    adapter = sub.add_parser('adapt')
    for name in ('dataset', 'lifecycle', 'npu-results', 'join-summary', 'output', 'library', 'local-validation'):
        adapter.add_argument('--' + name, type=Path, required=True)
    adapter.add_argument('--application', type=Path)
    args = parser.parse_args()
    try:
        action = {'join': join, 'lifecycle': lifecycle, 'adapt': adapt}[args.command]
        result = action(args)
        print(json.dumps({key: result[key] for key in sorted(result) if key in (
            'status', 'scope', 'npu_work_count', 'validation_mode', 'publication_certified', 'output')}, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError) as error:
        print(f'nano-local {args.command} failed: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
