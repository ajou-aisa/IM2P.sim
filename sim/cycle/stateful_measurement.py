"""Prepare validated stateful evidence without opening the reconstruction gate."""
from __future__ import annotations

from scripts.gemmini_replay_contract import hardware_contract
from sim.cycle.execution_sequence_admission import AdmissionInputs
from sim.cycle.stateful_domain import InitialState, WorkClass
from sim.cycle.stateful_domain_admission import admit
from sim.cycle.stateful_measurement_contract import (
    ClockRequirement,
    HostRequirement,
    ValidatedStatefulMeasurement,
)


def prepare_measurement(inputs: AdmissionInputs, profile: str,
                        work_class: WorkClass) -> ValidatedStatefulMeasurement:
    """Require real model admission and expose the still-missing hardware evidence."""
    admitted = admit(profile, work_class, InitialState(), inputs)
    contract = hardware_contract(profile)
    return ValidatedStatefulMeasurement(
        admitted.provider_certificate, admitted.domain.revision,
        admitted.trace_identity, profile,
        ClockRequirement(profile, str(contract["sha256"])), HostRequirement(),
    )
