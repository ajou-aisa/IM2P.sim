#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
# ─── How to run ───
# 1. Install uv: curl -LsSf https://astral.sh/uv/install.sh | sh
# 2. Run: uv run -m pytest sim/tests/cycle/test_production_sequence_work.py
# ──────────────────
from __future__ import annotations

from pathlib import Path
from typing import Final

from scripts.gemmini_resolve_profile import JsonValue
from sim.cycle.npu_trace_schema import Record, integer
from sim.cycle.reconstruct_graph import sha256

ROOT: Final = Path(__file__).resolve().parents[3]
SOURCE_PATHS: Final = tuple(
    'deps/chipyard-1.13.0/generators/gemmini/src/main/scala/gemmini/' + name + '.scala'
    for name in ('MeshWithDelays', 'ExecuteController', 'TagQueue', 'LocalAddr')
)
PAIR_FIELDS: Final = (
    ('result_ready', 'model_result_ready'),
    ('final_scale_release', 'model_final_scale_release'),
    ('resource_ready', 'model_resource_ready'),
    ('next_scratchpad_half', 'model_next_scratchpad_half'),
    ('next_accumulator_half', 'model_next_accumulator_half'),
    ('submissions', 'model_submissions'),
    ('scale_read_requests', 'model_scale_read_requests'),
    ('scale_read_responses', 'model_scale_read_responses'),
    ('scale_release_count', 'model_scale_release_count'),
    ('load_requests', 'model_load_requests'),
    ('load_responses', 'model_load_responses'),
    ('store_requests', 'model_store_requests'),
    ('store_responses', 'model_store_responses'),
)
OBSERVED_FIELDS: Final = (
    'ordinal', 'work_id', 'accepted', 'first_output_cycle',
    'mesh_tag_queue_len', 'mesh_tag_head_id', 'mesh_tag_head_rob_valid',
    'mesh_tag_head_rob_bits', 'mesh_tag_head_address', 'mesh_tag_head_garbage',
    'mesh_tag_head_is_acc', 'mesh_tag_head_accumulate', 'mesh_tag_head_full_row',
    'mesh_matmul_id', 'mesh_expected_carry_id', 'mesh_tag_read_pointer',
    'mesh_tag_write_pointer', 'mesh_tag_enqueues', 'mesh_tag_dequeues',
    'mesh_tag_max_occupancy', 'mesh_tag_never_full', 'mesh_tag_full_backpressure_cycles',
    'carry_in_tag_len', 'carry_in_tag_head_id', 'first_tag_dequeue_seen',
    'first_tag_dequeue_cycle', 'first_tag_dequeue_out_id',
    'first_tag_dequeue_resp_valid', 'first_tag_dequeue_resp_last',
    'first_tag_dequeue_match_ready', 'first_output_tag_head_id',
    'first_output_tag_head_rob_valid', 'first_output_matmul_id',
    'passive_mesh_tag_queue_len', 'passive_mesh_tag_head_id',
    'passive_mesh_tag_read_pointer', 'passive_mesh_tag_write_pointer',
)


def proof_document(rows: list[Record], rtl_log_sha256: str) -> Record:
    observations: list[JsonValue] = []
    checks: list[Record] = []
    for ordinal, row in enumerate(rows):
        observed = {key: row.get(key) for key in OBSERVED_FIELDS}
        observations.append(observed)
        boundary = (row.get('ordinal') == row.get('work_id') == ordinal and
                    row.get('mesh_tag_queue_len') == row.get('passive_mesh_tag_queue_len') == 1 and
                    row.get('mesh_tag_head_id') == row.get('mesh_expected_carry_id') ==
                    row.get('passive_mesh_tag_head_id') and
                    row.get('mesh_tag_head_rob_valid') == 0 and
                    all(row.get(key) == 1 for key in ('mesh_tag_head_garbage',
                        'mesh_tag_head_is_acc', 'mesh_tag_head_accumulate', 'mesh_tag_head_full_row')))
        occupancy = row.get('mesh_tag_max_occupancy')
        capacity = (type(occupancy) is int and occupancy < 6 and
                    row.get('mesh_tag_never_full') is True and
                    row.get('mesh_tag_full_backpressure_cycles') == 0)
        enqueues = row.get('mesh_tag_enqueues')
        dequeues = row.get('mesh_tag_dequeues')
        carry_len = row.get('carry_in_tag_len')
        end_len = row.get('mesh_tag_queue_len')
        conserved = (type(enqueues) is int and type(dequeues) is int and
                     type(carry_len) is int and type(end_len) is int and
                     enqueues - dequeues == end_len - carry_len)
        output_id = row.get('first_output_tag_head_id')
        output_valid = (type(output_id) is int and
                        output_id == row.get('first_output_matmul_id') and
                        row.get('first_output_tag_head_rob_valid') == 1)
        parity = row.get('numeric_pass') is True and all(type(row.get(rtl)) is int and
            row.get(rtl) == row.get(model) for rtl, model in PAIR_FIELDS)
        previous = rows[ordinal - 1] if ordinal else None
        carry = (row.get('carry_in_tag_len') == 0 if previous is None else
                 row.get('carry_in_tag_len') == 1 and
                 row.get('carry_in_tag_head_id') == previous.get('mesh_tag_head_id') and
                 row.get('first_tag_dequeue_seen') is True and
                 row.get('first_tag_dequeue_out_id') == row.get('carry_in_tag_head_id') and
                 all(row.get(key) == 1 for key in ('first_tag_dequeue_resp_valid',
                     'first_tag_dequeue_resp_last', 'first_tag_dequeue_match_ready')) and
                 type(row.get('first_tag_dequeue_cycle')) is int and
                 integer(row, 'accepted') <= integer(row, 'first_tag_dequeue_cycle') <
                 integer(row, 'first_output_cycle'))
        checks.append({'ordinal': ordinal, 'boundary_garbage': boundary,
                       'next_first_dequeue_matches_carry': carry,
                       'queue_never_full': capacity, 'tag_count_conserved': conserved,
                       'first_output_tag_valid': output_valid,
                       'public_service_and_numeric_exact': parity})
    wrapped = len(rows) == 4 and all(type(enqueues := row.get('mesh_tag_enqueues')) is int and enqueues >= 5
                                     for row in rows)
    complete = (len(rows) == 4 and wrapped and all(all(value is True for key, value in check.items()
                                                       if key != 'ordinal') for check in checks))
    return {'schema': 'im2p-inert-ws-tag-carry', 'version': 1,
            'status': 'PASS' if complete else 'NOT_READY',
            'scope': 'finite-four-work-same-dut-producer-sequence',
            'rtl_log_sha256': rtl_log_sha256, 'queue_capacity': 6,
            'id_wrap_exercised': wrapped,
            'id_wrap_basis': 'each work has >=5 observed enqueues; RTL matmul ID advances modulo 5',
            'source_sha256': {name: sha256(ROOT / name) for name in SOURCE_PATHS},
            'observations': observations, 'checks': list[JsonValue](checks)}
