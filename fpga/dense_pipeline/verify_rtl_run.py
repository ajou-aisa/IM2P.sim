#!/usr/bin/env python3
"""Check host numerical markers against actual RTL launch/publication counters."""
import argparse
import json
from pathlib import Path
import re


def values(line):
    return {key: value for key, value in re.findall(r'(\w+)=([^\s]+)', line)}


def verify(path):
    status = json.loads((path / 'status.json').read_text())
    if not status['pass'] or status['runtime_failure'] or not status['preserved']:
        raise ValueError('runtime failure or artifact changed')
    host = (path / 'host.log').read_text()
    rtl = (path / 'rtl.log').read_text()
    markers = [line for line in rtl.splitlines() if line.startswith('RTL_PTY_COMPLETE ')]
    if len(markers) != 1:
        raise ValueError('missing unique RTL completion marker')
    counts = values(markers[0])
    numerical = [line for line in host.splitlines() if
                 line.startswith('FPGA_UART_GGML_NUMERICAL PASS ') or
                 line.startswith('PERSISTENT_FULL_PASS ') or line.startswith('PERSISTENT_PIPELINE_PASS ')]
    if len(numerical) != 1:
        raise ValueError('missing unique numerical/backend PASS marker')
    completed = values(numerical[0])
    jobs = int(completed.get('jobs', completed.get('logical', '0')))
    if jobs <= 0 or int(counts['launches']) != jobs or int(counts['pending_output_bytes']) != 0:
        raise ValueError('logical execution count or undrained UART output')
    if any(int(completed.get(key, '0')) for key in
           ('simulator_creates', 'simulator_executes', 'simulator_stream_begins')):
        raise ValueError('FPGA simulator fallback')
    if numerical[0].startswith('PERSISTENT_'):
        samples = [values(line) for line in host.splitlines() if line.startswith('SAMPLE ')]
        if len(samples) != jobs or any(row['exact'] != '1' or row['commit'] != '1' for row in samples):
            raise ValueError('incomplete per-invocation numerical checks')
        stripes = sum(int(row['stripes']) for row in samples)
        logical = int(completed['logical'])
        padding = int(completed['padding'])
    else:
        stripes = sum(line.startswith('LIVE_STRIPE ') for line in host.splitlines())
        logical = int(completed['f_out'])
        padding = 0  # Actual ggml test tensors are contiguous; fixture tests cover padding.
    if int(counts['publications']) != stripes or int(counts['stripe_acks']) != stripes:
        raise ValueError('stripe publication/completion count mismatch')
    if int(completed['raw']) <= 0 or logical <= 0:
        raise ValueError('vacuous numerical completion')
    return {'status': 'PASS', 'logical_jobs': jobs, 'stripes': stripes,
            'raw_comparisons': int(completed['raw']), 'f_out_comparisons': logical,
            'padding_comparisons': padding, 'rtl': counts, 'board_measured': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('run', type=Path)
    args = parser.parse_args()
    result = verify(args.run)
    print(json.dumps(result))
    with (args.run / 'numerical-validation.json').open('x') as output:
        output.write(json.dumps(result, indent=2) + '\n')
