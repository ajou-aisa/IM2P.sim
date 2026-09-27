from __future__ import annotations

import ast
import csv
import json
import re
from dataclasses import dataclass
from hashlib import sha256 as digest_bytes
from pathlib import Path

from scripts.gemmini_rtl_build_binding import verify_build
from sim.cycle import cli
from sim.cycle.certificate_contract import (
    PROFILES,
    TIMING,
    array_value,
    object_value,
    read_document,
)
from sim.cycle.npu_trace_schema import Record, integer
from sim.cycle.reconstruct_graph import sha256
from sim.cycle.sequence_binding import source_identity
from sim.cycle.stateful_sequence_evidence import (
    EvidenceContext,
    StatefulCertificateError,
    raw_digests,
    reference,
    require,
)
from sim.tests.cycle.compositional_sequence_v2_runtime import (
    validate_absolute_log_stream,
)
from sim.tests.cycle.current_rtl_certificate import (
    event_comparison,
    exact_result,
    ownership,
)
from sim.tests.cycle.production_block_certificate import RESULT_MAP, TIMING_KEYS
from sim.tests.cycle.reaggregate_certificate import probe_proof
from sim.tests.cycle.rtl_hardening import (
    SELECTED_EVENTS,
    normalized_model_events,
    profile_bits_dim,
)

ROOT = Path(__file__).resolve().parents[2]
INPUT_SCHEMA = "im2p-stateful-current-evidence-input"
PYTHON_ROOTS = (
    "sim/cycle/stateful_sequence_certificate.py",
    "sim/cycle/stateful_sequence_evidence.py",
    "sim/cycle/stateful_sequence_evidence_v2.py",
    "sim/cycle/certificate_contract.py",
    "sim/cycle/run_aware_certificate.py",
    "sim/cycle/service_certificate.py",
)


@dataclass(frozen=True, slots=True)
class CurrentEvidence:
    input_reference: Record
    library: Record
    shared_library: Record
    source_sha256: Record
    reviewed_evidence: Record
    cases: tuple[Record, ...]
    completeness: Record
    state_refinement: Record
    parent_equivalence: Record | None = None


def _relative(root: Path, raw: str, boundary: str) -> Path:
    relative = Path(raw)
    require(not relative.is_absolute() and ".." not in relative.parts, boundary, "E-relative path required")
    try:
        path = (root / relative).resolve(strict=True)
    except OSError as error:
        raise StatefulCertificateError(boundary, str(error)) from error
    require(path.is_relative_to(root), boundary, "path escapes evidence root")
    return path


def _reference(root: Path, value: Record, boundary: str) -> tuple[Path, Record]:
    require(set(value) == {"path", "sha256"}, boundary, "path and digest required")
    raw, digest = value["path"], value["sha256"]
    require(isinstance(raw, str) and isinstance(digest, str), boundary, "path/digest malformed")
    path = _relative(root, str(raw), boundary)
    return path, reference(path, str(digest))


def _corrected_review(root: Path, addendum_path: Path, review_path: Path,
                      review_digest: str, addendum: Record) -> str:
    prior = object_value(addendum.get("prior_review"), "prior review")
    corrected = object_value(addendum.get("corrected_review"), "corrected review")
    raw_prior = prior.get("path")
    if not isinstance(raw_prior, str) or not raw_prior.startswith("E/"):
        raise StatefulCertificateError("review correction", "prior review route missing")
    old_path = _relative(root, raw_prior[2:], "review correction")
    old_digest = str(prior.get("sha256"))
    _ = reference(old_path, old_digest)
    require(addendum.get("schema") == "im2p.todo15.sixprofile-review-hash-correction.v1" and
            addendum.get("correction_kind") == "clerical SHA256 field only" and
            corrected.get("path") == review_path.relative_to(addendum_path.parent).as_posix() and
            corrected.get("sha256") == review_digest and
            addendum.get("corrected_field") ==
                "a4d16_reuse_resolution.prior_false_failure_receipt_sha256",
            "review correction", "independent correction identity differs")
    wrong, actual = addendum.get("prior_incorrect_value"), addendum.get("actual_value")
    if not isinstance(wrong, str) or not isinstance(actual, str):
        raise StatefulCertificateError("review correction", "receipt SHA correction malformed")
    require(len(wrong) == len(actual) == 64 and wrong != actual,
            "review correction", "receipt SHA correction malformed")
    old_bytes = old_path.read_bytes()
    require(old_bytes.count(wrong.encode()) == 1 and
            old_bytes.replace(wrong.encode(), actual.encode(), 1) == review_path.read_bytes(),
            "review correction", "review changes beyond one receipt SHA")
    receipt = object_value(addendum.get("receipt"), "corrected receipt")
    raw_receipt = receipt.get("path")
    if not isinstance(raw_receipt, str) or not raw_receipt.startswith("E/"):
        raise StatefulCertificateError("review correction", "receipt route malformed")
    receipt_path = _relative(root, raw_receipt[2:], "review correction")
    require(sha256(receipt_path) == actual == receipt.get("sha256_recomputed_from_immutable_bytes"),
            "review correction", "corrected receipt bytes differ")
    return old_digest


def _option(command: list[str], name: str) -> str:
    try:
        return command[command.index(name) + 1]
    except (ValueError, IndexError) as error:
        raise StatefulCertificateError("case command", f"{name} missing") from error


def _python_closure() -> set[str]:
    pending: list[str] = list(PYTHON_ROOTS)
    found: set[str] = set()
    while pending:
        name = pending.pop()
        if name in found:
            continue
        found.add(name)
        source = ROOT / name
        for node in ast.walk(ast.parse(source.read_text())):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules = [item.name for item in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                modules = [node.module, *(f"{node.module}.{item.name}" for item in node.names)]
            for module in modules:
                if module.startswith(("sim.", "scripts.")):
                    relative = Path(*module.split(".")).with_suffix(".py")
                    if (ROOT / relative).is_file():
                        pending.append(relative.as_posix())
    return found


def _case(root: Path, context: EvidenceContext, declared: Record, aggregate: Record,
          reviewed: Record, index: int, library_digest: str) -> Record:
    profile, identity = declared.get("profile"), declared.get("case_id")
    if not isinstance(profile, str) or not isinstance(identity, str):
        raise StatefulCertificateError("case set", "case/profile identity missing")
    require(profile == aggregate.get("profile") == reviewed.get("profile") and
            identity == aggregate.get("case_id") == reviewed.get("case_id"),
            "case set", "case/profile order differs")
    raw_dir = declared.get("case_dir")
    require(isinstance(raw_dir, str), "case set", "case directory missing")
    directory = _relative(root, str(raw_dir), "case set")
    require(directory.is_dir(), "case set", "case directory required")
    receipt_path = directory / ("case-receipt.json" if index == 0 else "case.json")
    receipt = read_document(receipt_path)
    receipt_ref = reference(receipt_path, sha256(receipt_path))
    require(receipt.get("profile") == profile and receipt.get("case_id") == identity,
            "case set", "receipt identity differs")
    if index == 0:
        require(receipt.get("status") == "FAIL" and receipt.get("exit_code") == 0 and
                receipt.get("first_failure") == "RuntimeError: numerical projection changed" and
                receipt_ref["sha256"] == reviewed.get("prior_receipt_sha256"),
                "independent review", "A4D16 prior failure not independently resolved")
    else:
        require(receipt.get("status") == "PASS" and receipt.get("official_exit_code") == 0 and
                receipt.get("first_failure") is None and receipt.get("first_mismatch") is None,
                "case scope", f"{profile} official case failed")
    command = receipt.get("command")
    require(isinstance(command, list) and all(isinstance(item, str) for item in command),
            "case command", "official argv missing")
    argv = [str(item) for item in command] if isinstance(command, list) else []
    report_path, raw_path = directory / "target/report.json", directory / "target/rtl.log"
    stimulus_path = Path(_option(argv, "--stimulus")).resolve(strict=True)
    require(stimulus_path.is_relative_to(root), "case command", "stimulus escapes E")
    require(_option(argv, "--stimulus") == str(stimulus_path) and
            _option(argv, "--out") == str(directory / "target") and
            _option(argv, "--case") == identity and
            _option(argv, "--library") == str(context.library) and
            _option(argv, "--expected-stimulus-sha256") == reviewed.get("stimulus_sha256") and
            "--require-queue-payload-v2" in argv,
            "case command", "official invocation differs")
    build_root = Path(_option(argv, "--rtl-build"))
    binding = verify_build(build_root, profile)
    build_artifacts = object_value(binding.get("artifact_sha256"), "RTL artifacts")
    rtl_object = build_root / "rtl-test-obj/VIM2PGemminiWSHP1RtlTest__ALL.a"
    artifacts: Record = {
        "receipt": receipt_ref,
        "report": reference(report_path, str(reviewed["report_sha256"])),
        "raw": reference(raw_path, str(reviewed["raw_sha256"])),
        "stimulus": reference(stimulus_path, str(reviewed["stimulus_sha256"])),
        "binary": reference(directory / "target/compositional-probe", str(reviewed["binary_sha256"])),
        "rtl_build_binding": reference(build_root / "rtl-build-binding.json",
                                       str(reviewed["rtl_build_binding_sha256"])),
        "rtl_object": reference(rtl_object, str(build_artifacts[rtl_object.relative_to(build_root).as_posix()])),
    }
    report, stimulus = read_document(report_path), read_document(stimulus_path)
    artifacts["diagnostics"] = reference(directory / "target/rtl-diagnostics.log",
                                       str(report.get("rtl_diagnostics_sha256")))
    require(report.get("schema") == "im2p-compositional-sequence-run" and
            report.get("version") == 2 and report.get("status") == "PASS_ABSOLUTE_MODEL_RTL" and
            report.get("fixture_kind") == stimulus.get("fixture_kind") == "GENUINE_COMPLETE_PARENT" and
            report.get("profile") == stimulus.get("profile") == profile and
            report.get("case_id") == stimulus.get("case_id") == identity and
            report.get("cycle_library_sha256") == stimulus.get("cycle_library_sha256") == library_digest and
            report.get("rtl_log_sha256") == reviewed["raw_sha256"] and
            report.get("binary_sha256") == reviewed["binary_sha256"] and
            report.get("rtl_build_binding_sha256") == reviewed["rtl_build_binding_sha256"],
            "case scope", f"{profile} report/stimulus identity differs")
    source_map = object_value(report.get("source_sha256"), "report source")
    require(all(sha256(ROOT / name) == digest for name, digest in source_map.items()),
            "source closure", f"{profile} probe source changed")
    producer = object_value(report.get("producer_artifacts"), "producer triplet")
    for name in ("trace", "lifecycle", "semantic_graph"):
        item = object_value(producer.get(name), name)
        artifacts[name] = reference(Path(str(item["path"])), str(item["sha256"]))
    works = [object_value(item, "stimulus work") for item in array_value(stimulus.get("works"), "works")]
    ids = tuple(integer(work, "work_id") for work in works)
    try:
        validated = validate_absolute_log_stream(raw_path, stimulus, ids,
            expected_stimulus_sha256=str(reviewed["stimulus_sha256"]), expected_repeats=None,
            require_boundary_v2=True, require_queue_payload_v2=True)
    except (OSError, ValueError) as error:
        raise StatefulCertificateError("raw RTL", f"{profile}: {error}") from error
    require(validated == report.get("works") and len(validated) == aggregate.get("work_count") ==
            reviewed.get("works") == report.get("work_count") and
            all(work.get("numeric_pass") is True for work in validated) and
            stimulus.get("selected_parent_indices") == [0, 1] and
            aggregate.get("row_peak") == reviewed.get("row_peak") == 3 and
            aggregate.get("tag_peak") == reviewed.get("tag_peak") == 4,
            "work/run/offer", f"{profile} validated work/domain differs")
    digests = raw_digests(raw_path)
    require(digests["raw"] == reviewed["raw_sha256"] and
            sha256(context.library) == library_digest,
            "raw RTL", f"{profile} raw or library changed during validation")
    input_rows = [{key: work.get(key) for key in ("work_id", "work_binding", "input", "runs",
                                                  "original_k", "request_available_cycle",
                                                  "port_offer_cycle")} for work in works]
    return {"case_id": identity, "profile": profile,
            "work_inputs_sha256": digest_bytes(json.dumps(input_rows, sort_keys=True,
                separators=(",", ":")).encode()).hexdigest(),
            "artifacts": artifacts,
            "raw_digests": digests,
            "comparison": {"work_count": len(validated), "parent_windows": 2,
                           "selected_event_pairs": reviewed["selected_event_pairs"],
                           "queue_v2_pairs": reviewed["queue_v2_pairs"],
                           "boundary_v2_pairs": reviewed["boundary_v2_pairs"],
                           "numeric_pass": reviewed["numeric_pass_works"],
                           "tag_peak": reviewed["tag_peak"], "row_peak": reviewed["row_peak"]}}


def reviewed_current(context: EvidenceContext, expected_ids: tuple[str, ...]) -> CurrentEvidence:
    input_path = context.current_evidence_input
    require(input_path is not None, "evidence input", "explicit current input required")
    if input_path is None:
        raise StatefulCertificateError("evidence input", "missing path")
    input_digest = sha256(input_path)
    manifest = read_document(input_path)
    if manifest.get("version") == 2:
        from sim.cycle.stateful_sequence_evidence_equivalence import (
            reviewed_equivalence,
        )

        return reviewed_equivalence(context, manifest, expected_ids)
    require(manifest.get("schema") == INPUT_SCHEMA and manifest.get("version") == 1 and
            set(manifest) - {"review_correction_addendum", "parent_reviews"} ==
                {"schema", "version", "aggregate", "aggregate_review",
                 "state_audit", "state_review", "cases"},
            "evidence input", "unsupported current reviewed input")
    root = context.evidence_root.resolve(strict=True)
    refs: Record = {}
    docs: dict[str, Record] = {}
    for name in ("aggregate", "aggregate_review", "state_audit", "state_review"):
        path, refs[name] = _reference(root, object_value(manifest[name], name), name)
        docs[name] = read_document(path)
    review_digest = str(object_value(refs["aggregate_review"], "aggregate review")["sha256"])
    old_review_digest = review_digest
    if "review_correction_addendum" in manifest:
        addendum_path, refs["review_correction_addendum"] = _reference(root,
                object_value(manifest["review_correction_addendum"], "review correction"),
                "review correction")
        old_review_digest = _corrected_review(root, addendum_path,
                Path(str(object_value(refs["aggregate_review"], "aggregate review")["path"])),
                review_digest, read_document(addendum_path))
    if "parent_reviews" in manifest:
        reviews = object_value(manifest["parent_reviews"], "parent reviews")
        require(set(reviews) <= {"base", "run_aware", "service"},
                "parent reviews", "unsupported parent review")
        refs["parent_reviews"] = {name: _reference(root, object_value(value, name), name)[1]
                                  for name, value in reviews.items()}
    aggregate, review = docs["aggregate"], docs["aggregate_review"]
    audit, state_review = docs["state_audit"], docs["state_review"]
    require(aggregate.get("schema") == "im2p.todo15.rowpeak.resume.aggregate.v1" and
            aggregate.get("verdict") == "PASS_FINITE_SIX_PROFILE_CURRENT_SOURCE" and
            review.get("verdict") == state_review.get("verdict") == "confirmed" and
            state_review.get("todo15_acceptance_criteria_met") is True and
            object_value(audit.get("scope_boundary"), "guarded domain")
                .get("todo15_guarded_diagnostic_relation") == "CLOSED" and
            object_value(state_review.get("refreshed_source_state_audit"), "state review")
                .get("sha256") == object_value(refs["state_audit"], "state audit")["sha256"] and
            object_value(state_review.get("source_and_evidence_pins"), "review pins")
                .get("current_sixprofile_independent_review_sha256") == old_review_digest,
            "independent review", "current guarded evidence review missing or changed")
    impact = object_value(audit.get("current_source_impact"), "audited source impact")
    guarded = object_value(audit.get("guarded_state_array_relation"), "guarded State.array")
    boundary = object_value(audit.get("scope_boundary"), "guarded domain")
    parity = object_value(audit.get("finite_parity"), "finite parity")
    provider = object_value(audit.get("source_bound_provider"), "audited provider")
    require(guarded.get("first_concrete_hidden_state_gap_within_guard") is None and
            boundary.get("full_six_inside_domain") is False and
            boundary.get("universal_RTL_proof") == "NOT_RUN" and
            boundary.get("production_certificate") == "STALE_NOT_READY" and
            parity.get("independent_review_sha256") == old_review_digest and
            parity.get("profiles") == 6 and parity.get("works") == 80 and
            parity.get("parent_windows") == 12 and parity.get("row_peak") == 3 and
            parity.get("tag_peak") == 4 and parity.get("first_mismatch") is None,
            "State.array domain", "guarded state or finite scope differs")
    raw_prior = impact.get("prior_guarded_verdict_relpath")
    require(isinstance(raw_prior, str), "source closure", "prior guarded audit missing")
    prior_path = _relative(root, str(raw_prior), "source closure")
    refs["prior_guarded_audit"] = reference(prior_path,
                                             str(impact.get("prior_guarded_verdict_sha256")))
    prior = read_document(prior_path)
    prior_pins = object_value(prior.get("pins"), "prior guarded pins")
    prior_sources = object_value(prior_pins.get("source_sha256"), "guarded source pins")
    library_digest = str(aggregate["archive_sha256"])
    library = reference(context.library, library_digest)
    shared = reference(context.shared_library, str(aggregate["shared_sha256"]))
    require(object_value(review.get("source_and_build"), "review build")
                .get("native_archive_sha256") == library_digest and
            object_value(state_review.get("source_and_evidence_pins"), "review pins")
                .get("native_shared_sha256") == shared["sha256"],
            "library/ABI2", "reviewed native identities differ")
    sources: Record = object_value(aggregate.get("source_sha256"), "source map").copy()
    for profile in PROFILES:
        for name, digest in source_identity(context.library, profile).source_sha256:
            require(name not in sources or sources[name] == digest,
                    "source closure", f"source disagreement: {name}")
            sources[name] = digest
    for name, digest in prior_sources.items():
        if name in ("sim/cycle/stateful_sequence_certificate.py",
                    "sim/cycle/stateful_sequence_evidence.py"):
            continue
        if name == "sim/cycle/execution_sequence_admission.py":
            digest = impact.get("current_admission_sha256")
        require(isinstance(digest, str) and sha256(ROOT / name) == digest and
                (name not in sources or sources[name] == digest),
                "source closure", f"guarded source changed: {name}")
        sources[name] = digest
    require(sources.get("sim/cycle/sequence_binding_abi.py") ==
                provider.get("binding_abi_sha256") and
            sources.get("sim/cycle/cli.py") == provider.get("cli_sha256") and
            prior_pins.get("native_archive_sha256") == library["sha256"] and
            prior_pins.get("native_shared_sha256") == shared["sha256"],
            "source closure", "provider/native audit pins differ")
    for name in _python_closure():
        digest = sha256(ROOT / name)
        require(name not in sources or sources[name] == digest,
                "source closure", f"reviewed source changed: {name}")
        sources[name] = digest
    require(all(sha256(ROOT / name) == digest for name, digest in sources.items()),
            "source closure", "current source map changed")
    declared = [object_value(item, "case") for item in array_value(manifest.get("cases"), "cases")]
    aggregate_cases = [object_value(item, "aggregate case") for item in
                       array_value(aggregate.get("cases"), "aggregate cases")]
    reviewed_cases = [object_value(item, "review case") for item in
                      array_value(review.get("cases"), "review cases")]
    require(len(declared) == len(aggregate_cases) == len(reviewed_cases) == len(expected_ids) == 6 and
            [item.get("case_id") for item in declared] == list(expected_ids) and
            [item.get("profile") for item in declared] == list(PROFILES),
            "case set", "six reviewed case IDs/order required")
    first_review = object_value(review.get("a4d16_reuse_resolution"), "A4D16 review")
    require(first_review.get("prior_case_receipt") ==
            "FAIL from its projection comparison despite official exit 0 and PASS_ABSOLUTE_MODEL_RTL report",
            "independent review", "A4D16 failed receipt not resolved")
    reviewed_cases[0]["prior_receipt_sha256"] = first_review["prior_false_failure_receipt_sha256"]
    cases = tuple(_case(root, context, declared[index], aggregate_cases[index],
                        reviewed_cases[index], index, library_digest) for index in range(6))
    totals: Record = {"profiles_expected": 6, "profiles_observed": len(cases),
              "works_expected": 80, "works_observed": sum(integer(object_value(item["comparison"], "comparison"),
                                                              "work_count") for item in cases),
              "parent_windows_expected": 12, "parent_windows_observed": 2 * len(cases),
              "selected_event_pairs": sum(integer(object_value(item["comparison"], "comparison"),
                                                  "selected_event_pairs") for item in cases),
              "queue_v2_pairs": sum(integer(object_value(item["comparison"], "comparison"),
                                            "queue_v2_pairs") for item in cases),
              "boundary_v2_pairs": sum(integer(object_value(item["comparison"], "comparison"),
                                               "boundary_v2_pairs") for item in cases),
              "numeric_pass": sum(integer(object_value(item["comparison"], "comparison"),
                                          "numeric_pass") for item in cases)}
    require(totals == {"profiles_expected": 6, "profiles_observed": 6,
                       "works_expected": 80, "works_observed": 80,
                       "parent_windows_expected": 12, "parent_windows_observed": 12,
                       "selected_event_pairs": 2538768, "queue_v2_pairs": 31418,
                       "boundary_v2_pairs": 18, "numeric_pass": 80} and
            aggregate.get("observed_work_count") == 80 and
            aggregate.get("observed_parent_window_count") == 12,
            "completeness", "current case denominator differs")
    require(sha256(input_path) == input_digest and
            all(sha256(ROOT / name) == digest for name, digest in sources.items()),
            "source closure", "input or source changed during review")
    state = {"guarded_state_array": "CLOSED_WITHIN_REJECTED_FULL6_DOMAIN",
             "max_tag_occupancy": 4, "max_row_occupancy_exclusive": 6,
             "ready_violation_mask": 0, "full6_supported": False,
             "formal_proof": "NOT_RUN", "production_admitted": False,
             "state_audit_sha256": object_value(refs["state_audit"], "state audit")["sha256"],
             "state_review_sha256": object_value(refs["state_review"], "state review")["sha256"]}
    return CurrentEvidence(reference(input_path, input_digest), library, shared, sources,
                           refs, cases, totals, state)


def _base_model(library: Path, row: Record) -> None:
    log, events = Path(str(row["rtl_log"])), Path(str(row["rtl_events"]))
    require(events == log.parent / "events.csv", "base current model", "case artifact route differs")
    with events.open() as stream:
        raw = list(csv.reader(stream))
    captured = [item for item in raw if item and item[0] == "CASE"]
    accepted = [item for item in raw if item and item[0].isdigit() and item[2] == "work"]
    require(len(captured) == 1 and bool(accepted), "base current model", "accepted RTL work missing")
    shape, tile, timing = (array_value(row.get(key), key) for key in ("shape", "tile", "timing"))
    require(list(map(int, captured[0][2:5])) == shape and
            list(map(int, captured[0][6:9])) == tile and
            list(map(int, captured[0][9:14])) == timing and
            bool(int(captured[0][15])) == row.get("raw") and
            int(captured[0][14]) == TIMING["backing_cycle_offset"] and int(accepted[0][1]) == 1,
            "base current model", "raw RTL work/reference differs")
    request = read_document(log.parent / "model-request.json")
    expected_request: Record = {"profile": row["profile"], "timing_profile": "rtl-regression",
        "request": dict(zip(("m", "n", "k", "tile_i", "tile_j", "tile_k"), [*shape, *tile])) |
            {"accepted_cycle": 1, "submission": row["framing"], "record_events": 1},
        "timing": dict(zip(TIMING_KEYS, [*timing, TIMING["backing_cycle_offset"]]))}
    require(request == expected_request, "base current model", "request differs from certified RTL work")
    model: Record = cli.estimate(library, request)
    retained = read_document(log.parent / "model-result.json")
    require(model == retained and model.get("result") == row.get("model_summary"),
            "base current model", "fresh model result/events differ from retained evidence")
    summary_line = next((line for line in log.read_text().splitlines() if line.startswith("WS RTL ")), "")
    observed = {key: int(value) for key, value in re.findall(r"\b(\w+)=(\d+)\b", summary_line)}
    summary = object_value(model.get("result"), "model summary")
    require(observed == row.get("rtl_summary") and
            set(observed) >= set(RESULT_MAP) - {"planner_loop_count", "fragment_count"},
            "base current model", "raw RTL summary differs")
    differences: Record = {key: {"rtl": observed[key], "model": summary[field]}
                           for key, field in RESULT_MAP.items()
                           if key in observed and observed[key] != summary[field]}
    model_events = normalized_model_events([object_value(item, "model event")
        for item in array_value(model.get("events"), "model events")])
    selected = sorted((int(item[1]), item[2]) for item in raw
                      if item and item[0].isdigit() and item[2] in SELECTED_EVENTS)
    scale = ownership(events, profile_bits_dim(str(row["profile"]))[1])
    require(exact_result(differences, model_events, selected) and scale["status"] == "PASS" and
            row.get("event_comparison") == event_comparison(model_events, selected) and
            row.get("scale_ownership") == {key: value for key, value in scale.items() if key != "details"},
            "base current model", "fresh model/RTL structured comparison failed")


def _base_probe(row: Record, bindings: Record) -> None:
    directory = Path(str(row["rtl_log"])).parent.parent
    command = object_value(json.loads((directory / "commands.jsonl").read_text().splitlines()[0]), "probe command")
    arguments = array_value(command.get("argv"), "probe argv")
    archives = [Path(arg) for arg in arguments
                if isinstance(arg, str) and arg.endswith("/rtl-test-obj/VIM2PGemminiWSHP1RtlTest__ALL.a")]
    require(len(archives) == 1, "base RTL", "retained RTL build route missing")
    binding = verify_build(archives[0].parent.parent, str(row["profile"]))
    require(binding == bindings.get(str(row["profile"])), "base RTL", "current build binding differs")
    hashes = {name: sha256(directory / name) for name in ("provenance.json", "commands.jsonl", "probe.cpp", "probe")}
    _ = probe_proof(directory, directory, hashes)
    provenance = read_document(directory / "provenance.json")
    require(provenance.get("profile") == row.get("profile") and
            provenance.get("framing") == row.get("framing") and
            provenance.get("single_reset_work") is True and
            provenance.get("passive_observation_only") is True and
            provenance.get("rtl_source_modified") is False and
            provenance.get("generated_probe_sha256") == hashes["probe.cpp"],
            "base RTL", "current probe provenance differs")


def reviewed_parent(context: EvidenceContext, name: str, certificate: Path,
                    document: Record, library_digest: str) -> Record:
    input_path = context.current_evidence_input
    require(input_path is not None, "parent certificate", "current input required")
    if input_path is None:
        raise StatefulCertificateError("parent certificate", "current input missing")
    manifest = read_document(input_path)
    reviews = object_value(manifest.get("parent_reviews"), "parent reviews")
    review_path, review_ref = _reference(context.evidence_root.resolve(strict=True),
        object_value(reviews.get(name), f"{name} review"), f"{name} review")
    review = read_document(review_path)
    sources: Record = {}
    for profile in PROFILES:
        for source, digest in source_identity(context.library, profile).source_sha256:
            require(source not in sources or sources[source] == digest,
                    "parent review", f"{name} native source disagreement")
            sources[source] = digest
    rows = [object_value(value, "parent case") for value in
            array_value(document.get("cases"), "parent cases")]
    denominator = {"base": 240, "run_aware": 42, "service": 30}[name]
    require(len(rows) == denominator, "parent review", f"{name} case denominator differs")
    case_evidence: list[Record] = []
    artifact_count = 0
    verified_probes: set[Path] = set()
    for row in rows:
        artifacts: Record = {}
        if name == "base":
            log = Path(str(row["rtl_log"]))
            if log.parent.parent not in verified_probes:
                _base_probe(row, object_value(document.get("rtl_build_bindings"), "RTL bindings"))
                verified_probes.add(log.parent.parent)
            _base_model(context.shared_library, row)
            for artifact_name, path in (("rtl_log", log),
                                        ("rtl_events", Path(str(row["rtl_events"]))),
                                        ("model_request", log.parent / "model-request.json"),
                                        ("model_result", log.parent / "model-result.json")):
                artifacts[artifact_name] = reference(path, sha256(path))["sha256"]
        elif name == "run_aware":
            for artifact_name, value in object_value(row.get("artifacts"), "run artifacts").items():
                item = object_value(value, artifact_name)
                path = Path(str(item["path"]))
                artifacts[artifact_name] = reference(path, str(item["sha256"]))["sha256"]
        else:
            item = object_value(row.get("report"), "service report")
            path = Path(str(item["path"]))
            artifacts["report"] = reference(path, str(item["sha256"]))["sha256"]
        artifact_count += len(artifacts)
        case_evidence.append({"row_sha256": digest_bytes(json.dumps(row, sort_keys=True,
            separators=(",", ":")).encode()).hexdigest(), "artifacts": artifacts})
    evidence_digest = digest_bytes(json.dumps(case_evidence, sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()
    require(review.get("schema") == "im2p-stateful-parent-recert-review-v1" and
            review.get("verdict") == "confirmed" and review.get("parent") == name and
            review.get("certificate_sha256") == sha256(certificate) and
            review.get("library_sha256") == library_digest and
            review.get("native_source_sha256") == sources and
            review.get("profiles") == list(PROFILES) and review.get("timing") == dict(TIMING) and
            review.get("expected_case_count") == denominator and
            review.get("fresh_model_comparison_count") == denominator and
            review.get("verified_rtl_artifact_count") == artifact_count and
            review.get("case_evidence_sha256") == evidence_digest,
            "parent review", f"{name} independent current-source case review differs")
    return review_ref
