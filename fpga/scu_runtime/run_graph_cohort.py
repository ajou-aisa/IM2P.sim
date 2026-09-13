#!/usr/bin/env python3
"""Five numerical invocations of one ordinary library, through fresh RTL PTYs."""
import argparse
import hashlib
import json
from pathlib import Path
from run_rtl import run


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser()
    for name in ('rtl', 'graph', 'library', 'out'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    for name in ('rtl', 'graph', 'library'):
        setattr(args, name, getattr(args, name).resolve(strict=True))
    args.out.mkdir(parents=True, exist_ok=False)
    identity = {str(path): sha(path) for path in (args.rtl, args.graph, args.library)}
    (args.out / 'inputs.json').write_text(json.dumps(identity, indent=2) + '\n')
    cases = [('small', 16, 16, 64, 1, 1, 'FULL'),
             ('long', 321, 48, 96, 3, 1, 'FULL'),
             ('live', 321, 48, 64, 2, 1, 'STRIPE_PIPELINE'),
             ('tail-two', 17, 19, 64, 4, 2, 'FULL')]
    completed = []
    for name, *values in cases:
        # run() raises at the first unexpected result; no later case or retry.
        run(args.rtl, args.graph, [str(args.library), *map(str, values)], args.out / name)
        completed.append(name)
        (args.out / 'completed.json').write_text(json.dumps(completed) + '\n')
    assert all(sha(Path(path)) == expected for path, expected in identity.items())
    print('standard-library RTL cohort PASS logical_invocations=5 physical_access=0')


if __name__ == '__main__':
    main()
