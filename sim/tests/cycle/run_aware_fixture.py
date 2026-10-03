from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path

from scripts.gemmini_replay_contract import ContractError, compatible, hardware_contract
from scripts.gemmini_resolve_profile import BuildFailure, JsonValue
from sim.tests.cycle.rtl_hardening import PROFILES


CASE_NAMES = (
    "unequal_12_10", "gap_0_3", "boundary_31_1", "zero_sentinel",
    "high_exponent", "positive_sat32", "negative_sat32", "sat32_order",
)
CASE_KEY_SHA256 = "268fa642294cc3a50156b8a2f60a744b31caea812059154ca38669c857f619e5"
MANIFEST_SHA256 = "5c05fe5a4bd17c9be8824b33297a943ec4fb4635ece29f545c45a111a834ef78"
SUMMARY_FIELDS = (
    "attempted", "admitted", "exact", "expected", "actual", "logical_done",
    "start", "done", "cycles", "loops", "loads", "executes", "stores",
    "commits", "scale_reads", "scale_responses", "completions",
)
LOOP_FIELDS = ("loop", "original_block", "fragment_base", "generation", "carrier")


@dataclass(frozen=True, slots=True)
class CompactRun:
    original_block_id: int
    original_k_mask: int
    compact_k_begin: int
    compact_k_count: int


@dataclass(frozen=True, slots=True)
class Case:
    name: str
    k: int
    original_k: int
    expected_value: int
    carriers: tuple[int, int]
    runs: tuple[CompactRun, ...]


@dataclass(frozen=True, slots=True)
class Corpus:
    profiles: tuple[str, ...]
    cases: tuple[Case, ...]
    case_count: int
    case_key_sha256: str
    timing: dict[str, int]


@dataclass(frozen=True, slots=True)
class Observation:
    profile: str
    case: str
    values: dict[str, int]
    loops: tuple[dict[str, int], ...]


class FixtureError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class RunFixture:
    profile: str
    hardware_contract_sha256: str
    m: int
    n: int
    k: int
    original_k: int
    tile_i_count: int
    tile_j_count: int
    tile_k_count: int
    runs: tuple[CompactRun, ...]


def _uint(value: JsonValue, label: str, minimum: int = 0) -> int:
    if type(value) is not int or not minimum <= value < 2**32:
        raise FixtureError(f"{label}: uint32 required")
    return value


def parse_fixture(document: JsonValue) -> RunFixture:
    keys = {"schema", "version", "profile", "hardware_contract", "m", "n", "k",
            "original_k", "tile_i_count", "tile_j_count", "tile_k_count", "runs"}
    if (not isinstance(document, dict) or set(document) != keys or
            document["schema"] != "im2p-run-aware-fixture" or
            type(document["version"]) is not int or document["version"] != 1):
        raise FixtureError("fixture schema/version or fields differ")
    profile = document["profile"]
    if not isinstance(profile, str) or profile not in PROFILES:
        raise FixtureError("unsupported fixture profile")
    contract = document["hardware_contract"]
    if not isinstance(contract, dict):
        raise FixtureError("hardware contract must be an object")
    try:
        compatible(contract, hardware_contract(profile))
    except (ContractError, BuildFailure) as error:
        raise FixtureError(f"hardware contract mismatch: {error}") from error
    m, n, k, original_k = (_uint(document[name], name, 1) for name in ("m", "n", "k", "original_k"))
    ti, tj, tk = (_uint(document[name], name, 1) for name in
                  ("tile_i_count", "tile_j_count", "tile_k_count"))
    dim = int(profile.split("-d", 1)[1].split("-", 1)[0])
    if ti * dim > 65535 or tj * dim > 65535 or tk * dim >= 2**32:
        raise FixtureError("tile factor exceeds hardware field")
    raw_runs = document["runs"]
    if not isinstance(raw_runs, list) or not raw_runs:
        raise FixtureError("nonempty run list required")
    runs: list[CompactRun] = []
    end = 0
    previous_block = -1
    for raw in raw_runs:
        if not isinstance(raw, dict) or set(raw) != {
                "original_block_id", "original_k_mask", "compact_k_begin", "compact_k_count"}:
            raise FixtureError("run fields differ")
        block, mask, begin, count = (_uint(raw[name], name) for name in
                                     ("original_block_id", "original_k_mask", "compact_k_begin", "compact_k_count"))
        if (block <= previous_block or begin != end or not 1 <= count <= 32 or
                mask.bit_count() != count or 32 * block + mask.bit_length() > original_k or
                block * (32 // min(dim, 32)) > 65535 or begin + count > k):
            raise FixtureError("malformed compact run view")
        runs.append(CompactRun(block, mask, begin, count))
        previous_block, end = block, begin + count
    if end != k:
        raise FixtureError("compact runs do not cover final K")
    return RunFixture(profile, str(contract["sha256"]), m, n, k, original_k,
                      ti, tj, tk, tuple(runs))


def read_fixture(path: Path) -> RunFixture:
    def unique(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
        result: dict[str, JsonValue] = {}
        for key, value in pairs:
            if key in result:
                raise FixtureError(f"duplicate JSON key: {key}")
            result[key] = value
        return result
    try:
        return parse_fixture(json.loads(path.read_text(), object_pairs_hook=unique))
    except json.JSONDecodeError as error:
        raise FixtureError(f"invalid fixture JSON: {error}") from error


def load_manifest(path: Path) -> Corpus:
    check_hash(path, MANIFEST_SHA256)
    data = json.loads(path.read_text())
    profiles = tuple(data["profiles"])
    cases = tuple(
        Case(row["name"], row["k"], row["original_k"], row["expected_value"],
             tuple(row["carriers"]), tuple(CompactRun(*run) for run in row["runs"]))
        for row in data["cases"]
    )
    keys = "\n".join(f"{profile}/{case.name}" for profile in profiles for case in cases)
    digest = sha256(keys.encode()).hexdigest()
    if (data["schema_version"] != 1 or data["artifact_role"] != "FIXTURE_ONLY" or
            profiles != PROFILES or tuple(case.name for case in cases) != CASE_NAMES or
            len(cases) != 8 or data["case_count"] != 48 or digest != CASE_KEY_SHA256 or
            data["case_key_sha256"] != CASE_KEY_SHA256 or
            data["geometry"] != {"m": 1, "n": 1, "tile_i": 1, "tile_j": 1, "tile_k": 2}):
        raise ValueError("run-aware corpus manifest changed")
    for case in cases:
        if (len(case.runs) != 2 or len(case.carriers) != 2 or
                sum(run.compact_k_count for run in case.runs) != case.k):
            raise ValueError(f"invalid compact geometry: {case.name}")
    return Corpus(profiles, cases, data["case_count"], digest, data["timing"])


def expected_loops(case: Case, dim: int) -> tuple[dict[str, int], ...]:
    rows: list[dict[str, int]] = []
    fragment_size = min(dim, 32)
    for run_index, run in enumerate(case.runs):
        rows.append({
            "loop": len(rows), "original_block": run.original_block_id,
            "compact_k_begin": run.compact_k_begin,
            "compact_k_count": run.compact_k_count,
            "fragment_base": run.original_block_id * (32 // fragment_size),
            "generation": len(rows) + 1, "carrier": case.carriers[run_index],
        })
    return tuple(rows)


def _tokens(line: str) -> dict[str, str]:
    fields = [piece.split("=", 1) for piece in line.split()[1:]]
    if any(len(field) != 2 for field in fields):
        raise ValueError(f"malformed RTL line: {line}")
    tokens = dict(fields)
    if len(tokens) != len(fields):
        raise ValueError(f"duplicate RTL field: {line}")
    return tokens


def parse_rtl_log(text: str, profile: str, corpus: Corpus) -> tuple[Observation, ...]:
    if profile not in corpus.profiles:
        raise ValueError(f"unexpected profile: {profile}")
    dim = int(profile.split("-d", 1)[1].split("-", 1)[0])
    observations: list[Observation] = []
    pending: list[dict[str, int]] = []
    for line in text.splitlines():
        if not line.startswith("FIXTURE_ONLY "):
            raise ValueError(f"unexpected RTL output: {line}")
        fields = _tokens(line)
        if "loop" in fields:
            if set(fields) != set(LOOP_FIELDS) | {"compact_k"}:
                raise ValueError(f"unexpected loop fields: {line}")
            begin, count = fields["compact_k"].split("+", 1)
            pending.append({**{key: int(fields[key]) for key in LOOP_FIELDS},
                            "compact_k_begin": int(begin), "compact_k_count": int(count)})
            continue
        if "case" not in fields or len(observations) >= len(corpus.cases):
            raise ValueError(f"unexpected case row: {line}")
        case = corpus.cases[len(observations)]
        if (fields["case"] != case.name or fields.get("profile") != profile or
                set(fields) != set(SUMMARY_FIELDS) | {"case", "profile"}):
            raise ValueError(f"missing, duplicate, or wrong profile/case: {line}")
        values = {key: int(fields[key]) for key in SUMMARY_FIELDS}
        if (values["attempted"], values["admitted"], values["exact"],
                values["logical_done"]) != (1, 1, 1, 1):
            raise ValueError(f"RTL fixture rejected {profile}/{case.name}")
        if (values["expected"] != case.expected_value or
                values["actual"] != case.expected_value or
                values["done"] - values["start"] != values["cycles"] or
                values["start"] < 1 or values["loops"] != len(pending) or
                tuple(pending) != expected_loops(case, dim)):
            raise ValueError(f"RTL geometry, numerical, or timing mismatch: {profile}/{case.name}")
        observations.append(Observation(profile, case.name, values, tuple(pending)))
        pending = []
    if pending or len(observations) != len(corpus.cases):
        raise ValueError(f"truncated RTL corpus: {profile}")
    return tuple(observations)


def check_hash(path: Path, expected: str) -> str:
    actual = sha256(path.read_bytes()).hexdigest()
    if actual != expected:
        raise ValueError(f"SHA256 mismatch: {path} expected={expected} actual={actual}")
    return actual
