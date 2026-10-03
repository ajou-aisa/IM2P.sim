"""Reviewed immutable identities for decode-trace certificates."""
from dataclasses import dataclass, field
from typing import Final

# Relative to the decode evidence root; every file is re-hashed against its pin.
EVIDENCE_FILES: Final = {
    "identity": "identity/workload-identity.json", "anchors": "identity/rtl-anchors.json",
    "workload": "workload/native/workload.json", "application": "workload/native/application.jsonl",
    "trace": "workload/native/chunk-0/npu-cycle-trace.jsonl",
    "lifecycle": "workload/native/chunk-0/execution-lifecycle.jsonl",
    "boundary": "simulator/replay-boundary/report.json", "boundary_records": "simulator/replay-boundary/works.jsonl",
    "edge": "simulator/replay-edge/report.json", "edge_records": "simulator/replay-edge/works.jsonl",
}
# Frozen generation contract of the GPT-2 256+128 decode target (llama-eval-workload workload.json fields).
GENERATION: Final = {
    "workload": "E2E_GENERATION_256_128", "recipe_id": "wikitext2-test-256x128-greedy-seed1234-v1",
    "context_tokens": 256, "requested_generated_tokens": 128, "runtime_context": 384, "batch": 256, "ubatch": 256,
    "threads": 1, "threads_batch": 1, "seed": 1234, "temperature": 0.0, "sampler_policy": "user-confirmed-greedy-v3",
    "top_k": 0, "top_p": 1.0, "min_p": 0.0, "output_mask": "last_token", "kv_policy": "clear_per_chunk",
    "add_bos": False, "bos_policy": "replace_chunk_first", "eos_stopping": False, "execution_kind": "FREE_GENERATION",
}


@dataclass(frozen=True, slots=True)
class DecodePin:
    profile: str
    model_sha256: str
    dataset_sha256: str
    input_tokens_sha256: str
    generated_tokens_sha256: str
    producer_sha256: str
    trace_sha256: str
    work_binding_fingerprint: str
    prefill_works: int
    works_per_step: int
    decode_steps: int
    memory_sha256: str
    rtl_build_binding_sha256: str
    tool_lock_sha256: str
    actual_trace_certificate_sha256: str
    transition_certificate_sha256: str
    milestone_work_count: int
    final_cursor: int
    event_count: int
    evidence_sha256: dict[str, str] = field(default_factory=dict[str, str])


DECODE_PINS: Final[dict[str, DecodePin]] = {
    "decode-gpt2-a8w8-d32-256p128": DecodePin(
        profile="a8w8-d32-hp1",
        model_sha256="cf23a45e4db23736d7d1f67ab42018803a198cfcb01db36daca69ff56bdbc7d4",
        dataset_sha256="173c87a53759e0201f33e0ccf978e510c2042d7f2cb78229d9a50d79b9e7dd08",
        input_tokens_sha256="7508130fea45cec699d54c21c8a0087e5b456812b7211050d5f71ea17c3ef305",
        generated_tokens_sha256="fad635302974d78071f88852daa9b62c6fcdb910791959833fddc81efe38978b",
        producer_sha256="6e43b44d6e66f9c3b5177c25c393c119b00b721b60a70980e0db852bb3e997b3",
        trace_sha256="cf7f9a9a1d45879c671e37835f04afcdc2287b22b3ff79e9bbc6b84118417a74",
        work_binding_fingerprint="8091bdf9d07181434343b7f0a18975c0e8a012c6aa2cf6965a9c7486d335efb0",
        prefill_works=374, works_per_step=98, decode_steps=127,
        memory_sha256="e3dec90f975ea565e86b97cb086415d9babe17cc87b1e9b24b774d7b49d2d952",
        rtl_build_binding_sha256="33e0fc1088e4f5a66169b60f716eb2bf044a2db124417423c3a2ccb23bb00031",
        tool_lock_sha256="ea178b1a56c4e31926b269baddb1c7ac0b097f97d44f97c42e379cdf1102473f",
        actual_trace_certificate_sha256="52b2a7403e71d1a0750bf5388b74e9917c36182259322f8c16802ac3e90d9705",
        transition_certificate_sha256="245f08fc788bc47a3e2ed90e17db33dd4c6c89791947b0cbcd4701284c6a0546",
        milestone_work_count=472, final_cursor=6398850880, event_count=7628622308,
        evidence_sha256={
            "identity": "87fe82d6c831c09b2648733e25022029b4a82be3ce1fe6e033a296ab27860472",
            "anchors": "0a4efbdbeda5660a2245d41f5a1b5d14ce9e4224efb6e2756aff03934c1d7647",
            "workload": "b2a0ee57b676cacc87492a6287b843218fe32e4e233897b2ac9005e224086b54",
            "application": "1587559eedd8e21b74baae4516e3ea466b29775f1ccb9ac483c476aed69cad42",
            "trace": "cf7f9a9a1d45879c671e37835f04afcdc2287b22b3ff79e9bbc6b84118417a74",
            "lifecycle": "cf1f567eeed37efa0579dc3c7a6890a35aefe33ce8e16320ce00eb698fc83c64",
            "boundary": "358a7035fdb67070b03806dd3f927d6fd627f0803ef5842fbea2e389f43678f5",
            "boundary_records": "b5956c261a7074968ef3c5db4a6287019b3faf80fd34ef7ea52506edf54e85cb",
            "edge": "76d5e30ecf4db4a23d71eccea83fbf0196b7c64b354b2806aa8c7eb2b1ae4eb8",
            "edge_records": "6e9cd3a6f8109325f718ba2d32dff1c21979e2f664cb86afe258b6768ac934b8",
        }),
}
