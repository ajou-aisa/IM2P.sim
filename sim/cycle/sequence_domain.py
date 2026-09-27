"""Immutable limits; selecting a revision does not validate a certificate."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from sim.cycle.certificate_contract import PROFILES

LEGACY_REVISION: Final = "GUARDED_TAG4_ROW_LT6_REVIEWED_V2"
TAG5_REVISION: Final = "GUARDED_A8D16_TAG5_ROW_LT6_REVIEWED_V3"
TAG6_REVISION: Final = "GUARDED_A8D16_TAG6_ROW_LT6_REVIEWED_V1"
TAG5_PROFILE: Final = "a8w8-d16-hp1"


@dataclass(frozen=True, slots=True)
class DomainLimits:
    max_tag_occupancy: int
    max_row_occupancy_exclusive: int = 6
    ready_violation_mask: int = 0
    tag_capacity: int = 6


class DomainRevisionError(ValueError):
    def __init__(self, profile: str, revision: str) -> None:
        super().__init__(f"unsupported sequence domain: {profile}/{revision}")


_TAG4: Final = DomainLimits(4)
_TAG5: Final = DomainLimits(5)
_TAG6: Final = DomainLimits(6)


def profile_domain(profile: str, revision: str = LEGACY_REVISION) -> DomainLimits:
    """Return limits for a revision obtained from validated ScopedEvidence."""
    if revision == TAG6_REVISION:
        if profile != TAG5_PROFILE:
            raise DomainRevisionError(profile, revision)
        return _TAG6
    if profile not in PROFILES or revision not in (LEGACY_REVISION, TAG5_REVISION):
        raise DomainRevisionError(profile, revision)
    return _TAG5 if profile == TAG5_PROFILE and revision == TAG5_REVISION else _TAG4
