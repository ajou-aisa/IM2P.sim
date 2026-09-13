#!/usr/bin/env python3
"""Check semantic consumer traces against captured dimensions and each other.

Cycles, requests, prefetch counts and process-local job IDs are not compared.
WORK/FRAGMENT order is exact. Successful output callbacks form a separate exact
completion stream because transport and callback timing need not match.
"""
from pathlib import Path
import argparse
import hashlib
import itertools
import json
import re

FIELDS = {
    'WORK': ('stripe', 'i', 'rows', 'j', 'columns', 'k', 'op'),
    'FRAGMENT': ('stripe', 'i', 'j', 'k', 'count', 'block', 'accumulate', 'end_block'),
    'OUTPUT_COMPLETE': ('block', 'row', 'j', 'columns'),
}


def actual(path, completion=False):
    job = None
    with Path(path).open() as stream:
        for line in stream:
            match = re.match(r'IM2P_(WORK|FRAGMENT|OUTPUT_COMPLETE) (.*)$', line.strip())
            if not match:
                continue
            kind, values = match.groups()
            if (kind == 'OUTPUT_COMPLETE') != completion:
                continue
            pairs = values.split()
            values = dict(pair.split('=', 1) for pair in pairs)
            if len(values) != len(pairs) or set(values) != set(FIELDS[kind]) | (set() if completion else {'job'}):
                raise ValueError('unexpected trace field set')
            if not completion:
                current_job = int(values['job'])
                if job is not None and current_job != job:
                    raise ValueError('logical trace changed job identity')
                job = current_job
            yield (kind, *(int(values[field]) for field in FIELDS[kind]))


def expected(meta, completion=False):
    m, n, k = (meta[name] for name in ('M', 'N', 'K'))
    if not (0 < m <= 4096 and 0 < n <= 65536 and 0 < k <= 65536 and k % 32 == 0):
        raise ValueError('unsupported bounded trace fixture')
    if meta['mode'] == 'FULL':
        stripes = [(0, 0, m)]
    elif meta['mode'] == 'STRIPE_PIPELINE':
        stripes = [(e['stripe_id'], e['row_begin'], e['row_end']) for e in meta['events']]
        if [s[0] for s in stripes] != list(range(len(stripes))) or stripes[0][1] != 0 or stripes[-1][2] != m:
            raise ValueError('invalid captured stripe identities/extents')
        for index, (_, begin, end) in enumerate(stripes):
            if begin >= end or end - begin > meta['stripe_rows'] or (index and begin != stripes[index - 1][2]):
                raise ValueError('invalid captured stripe continuity')
    else:
        raise ValueError('invalid captured mode')
    for stripe, begin, end in stripes:
        for i in range(begin, end, 16):
            rows = min(16, end - i)
            for j in range(0, n, 16):
                columns = min(16, n - j)
                if completion:
                    for block in range(k // 32):
                        for row in range(i, i + rows):
                            yield ('OUTPUT_COMPLETE', block, row, j, columns)
                else:
                    yield ('WORK', stripe, i, rows, j, columns, k, 3)  # original External domain
                    for global_k in range(0, k, 16):
                        yield ('FRAGMENT', stripe, i, j, global_k, 16, global_k // 32,
                               int(global_k % 32 != 0), int(global_k % 32 == 16))


def check(observed, oracle):
    digest = hashlib.sha256()
    counts = {}
    sentinel = object()
    for at, (got, want) in enumerate(itertools.zip_longest(observed, oracle, fillvalue=sentinel)):
        if got != want:
            raise ValueError(f'event {at}: observed={got!r}, expected={want!r}')
        counts[got[0]] = counts.get(got[0], 0) + 1
        digest.update((json.dumps(got, separators=(',', ':')) + '\n').encode())
    if not counts:
        raise ValueError('empty semantic trace')
    return dict(counts=counts, normalized_sha256=digest.hexdigest())


def mutations(path, meta):
    events = list(actual(path))
    first = next(i for i, event in enumerate(events) if event[0] == 'FRAGMENT')
    changed_reset = list(events[first]); changed_reset[-2] ^= 1
    cases = {
        'missing_fragment': events[:first] + events[first + 1:],
        'duplicate_fragment': events[:first] + [events[first]] + events[first:],
        'wrong_accumulation_reset': events[:first] + [tuple(changed_reset)] + events[first + 1:],
        'fragment_order': events[:first] + [events[first + 1], events[first]] + events[first + 2:],
    }
    result = {}
    for name, values in cases.items():
        try:
            check(iter(values), expected(meta))
        except ValueError as error:
            result[name] = str(error)
        else:
            raise ValueError('mutation was accepted: ' + name)
    outputs = list(actual(path, True))
    for name, values in [('missing_output_completion', outputs[1:]),
                         ('duplicate_output_completion', outputs[:1] + outputs)]:
        try:
            check(iter(values), expected(meta, True))
        except ValueError as error:
            result[name] = str(error)
        else:
            raise ValueError('mutation was accepted: ' + name)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('replay_directory', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    result_path = args.replay_directory / 'result.json'
    run = json.loads(result_path.read_text())
    if run['status'] != 'PASS':
        raise ValueError('numerical replay did not complete')
    summary = dict(status='PASS', scope='semantic WORK/FRAGMENT order and successful original-provider output completion; cycles/prefetch excluded', cases={})
    for name, capture in run['captures'].items():
        for path, digest in capture['sha256'].items():
            if hashlib.sha256(Path(path).read_bytes()).hexdigest() != digest:
                raise ValueError('captured input changed: ' + path)
        meta = json.loads(Path(capture['prefix']).with_suffix('.json').read_text())
        case = dict(capture=capture, references={})
        for kind in ('R0', 'R1', 'R2'):
            path = args.replay_directory / f'{name}-{kind}.stdout'
            case['references'][kind] = dict(work_fragments=check(actual(path), expected(meta)),
                output_completions=check(actual(path, True), expected(meta, True)),
                trace_sha256=hashlib.sha256(path.read_bytes()).hexdigest(), path=str(path))
        for stream in ('work_fragments', 'output_completions'):
            if len({item[stream]['normalized_sha256'] for item in case['references'].values()}) != 1:
                raise ValueError('reference semantic traces differ: ' + name)
        if name == 'small':
            case['mutation_rejections'] = mutations(args.replay_directory / 'small-R0.stdout', meta)
        summary['cases'][name] = case
    if args.out.exists():
        raise ValueError('comparison output already exists')
    args.out.write_text(json.dumps(summary, indent=2) + '\n')
    for name, case in summary['cases'].items():
        print('SEMANTIC_TRACE_PASS', name, case['references']['R0']['work_fragments']['counts'],
              case['references']['R0']['output_completions']['counts'])


if __name__ == '__main__':
    main()
