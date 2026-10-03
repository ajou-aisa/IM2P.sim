"""Reviewed immutable evidence identities for certified CPU/NPU-interleaved issue sequences."""
from dataclasses import dataclass, field
from typing import Final

EVIDENCE_FILES: Final = {
    "stimulus": "stimulus.json", "stimulus_text": "stimulus.txt", "capture": "rtl-capture/capture.json",
    "comparison": "comparison.json", "replay_first": "replay-first/report.json",
    "replay_fresh": "replay-fresh/report.json",
}


@dataclass(frozen=True, slots=True)
class InterleavedPin:
    profile: str
    revision: str
    memory_sha256: str
    trace_sha256: str
    work_count: int
    rtl_work_count: int
    tag_peak: int
    row_peak: int
    final_cursor: int
    probe_sha256: str
    issue_sha256: str
    npu_frequency_hz: int
    evidence_sha256: dict[str, str] = field(default_factory=dict[str, str])


INTERLEAVED_PINS: Final[dict[str, InterleavedPin]] = {
    "actual-gpt2-a8w8-d32-interleaved-256p1": InterleavedPin(
        profile="a8w8-d32-hp1", revision="GUARDED_A8D32_INTERLEAVED_ISSUE_TAG5_ROW_LT5_V1",
        memory_sha256="e3dec90f975ea565e86b97cb086415d9babe17cc87b1e9b24b774d7b49d2d952",
        trace_sha256="6a05544b55cd3fd450c98a5153cbca742b9106900d3005f9b4a5964f60ff7181",
        work_count=374, rtl_work_count=372, tag_peak=5, row_peak=4, final_cursor=747997970,
        probe_sha256="e232d830787add2d2b80024c04cd69f566a523beb1911d2aceb2c42cb1ac1871",
        issue_sha256="b56aa6d940e87c3431ef3709cdcb51979e0a58040eb22fc7ad73f8be060d4c95",
        npu_frequency_hz=1000000000,
        evidence_sha256={
            "stimulus": "1d75199c006f0692fd75750bf4e02ac219680e607b598bd94184fd67187921cb",
            "stimulus_text": "9481dc3db905116a2819e06ed14fc2e2159708f5d3c2d6c1a3d53726a397a85f",
            "capture": "0bb06b9e2440ac18d6c3f31308da457feb1ec7c3a0f67dc36b8e019df647cff1",
            "comparison": "2c98b5bfd754d10cc8ba4809725017ed90a2f66336e182eeab73c3938d31c1f2",
            "replay_first": "98c6a59c23a4a5c0ff625783e36295a6a6d67e7ea749ce40d404be7b3a7e1968",
            "replay_fresh": "22566dee84c26b3ddd59a80e67b456b7fc9ae29be6addffffb2c89ca587bf024",
        }),
}
