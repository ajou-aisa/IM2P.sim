"""Reviewed immutable evidence identities for independently certified actual-inference traces."""
from dataclasses import dataclass, field
from typing import Final

EVIDENCE_FILES: Final = {
    "stimulus": "stimulus.json", "stimulus_text": "stimulus.txt", "capture": "rtl-capture/capture.json",
    "comparison": "comparison.json", "replay_first": "replay-first/report.json",
    "replay_fresh": "replay-fresh/report.json", "milestone": "milestone-parity.json",
}


@dataclass(frozen=True, slots=True)
class ActualTracePin:
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
    evidence_sha256: dict[str, str] = field(default_factory=dict[str, str])


ACTUAL_TRACE_PINS: Final[dict[str, ActualTracePin]] = {
    "actual-gpt2-a8w8-d32-evaluation": ActualTracePin(
        profile="a8w8-d32-hp1", revision="GUARDED_A8D32_ACTUAL_EVAL_TAG5_ROW_LT5_V1",
        memory_sha256="e3dec90f975ea565e86b97cb086415d9babe17cc87b1e9b24b774d7b49d2d952",
        trace_sha256="19993617104f373ece5619aaef35c6b58c930468cadb98991028b1092873f430",
        work_count=374, rtl_work_count=372, tag_peak=5, row_peak=4, final_cursor=338503920,
        probe_sha256="e232d830787add2d2b80024c04cd69f566a523beb1911d2aceb2c42cb1ac1871",
        evidence_sha256={
            "stimulus": "7595a62b5c9da0239f04ee46d927cb03bab6250883d437a0d5e8943e23559b87",
            "stimulus_text": "a69e6be3216d590f85ad958e416fffb5db089893c281b3d40b0a270634266317",
            "capture": "8c31d16ce5457c98dc6010b7fa556e2323e7c62f79e7a204e13b1974952b4c17",
            "comparison": "94077c2cd0aae31375eb03ff28dd1a73079ca167b613de1f5adce092fb1a127b",
            "replay_first": "07f51878be360f50279a9090f7b2795ec60040fcbe5249d8178440721998efac",
            "replay_fresh": "1d59cfe804b4e0acf01ac88626411d7f2ea4e600eb22f0684dfdbe9c4f646aad",
            "milestone": "980eb4b1b4d21d1298fc8d1d0956682b10eb003a846cd4cadf1aa54928e39b31",
        }),
}
