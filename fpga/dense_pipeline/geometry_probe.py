#!/usr/bin/env python3
"""Run the existing frozen tiler and live ExSIA producer. No device access."""
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import subprocess


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--full', type=Path, required=True,
                        help='frozen host-full-replay experiment directory')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    frozen = args.full.resolve()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    build = frozen / 'host-build-03'
    flags_file = build / 'CMakeFiles/full_replay.dir/flags.make'
    flags = dict(line.split(' = ', 1) for line in flags_file.read_text().splitlines()
                 if ' = ' in line)
    source = Path(__file__).with_suffix('.cpp').resolve()
    archived_source = out / source.name
    archived_source.write_bytes(source.read_bytes())
    libraries = [build / 'host-build/ggml/src/ggml-gemmini/libggml-gemmini.a',
                 build / 'host-build/ggml/src/libggml-base.a',
                 build / 'host-build/ggml/src/ggml-gemmini-utils/libggml-gemmini-utils.a']
    executable = out / 'geometry_probe'
    command = ['/usr/bin/c++']
    for key in ('CXX_DEFINES', 'CXX_INCLUDES', 'CXX_FLAGS'):
        command += shlex.split(flags[key])
    command += [str(archived_source), '-o', str(executable),
                *map(str, libraries), '-pthread', '-ldl', '-lm']
    (out / 'compile-argv.json').write_text(json.dumps(command, indent=2) + '\n')
    with (out / 'compile.log').open('w') as log:
        subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True)
    for mode in ('scan', 'live'):
        with (out / f'{mode}.log').open('w') as log, (out / f'{mode}-debug.log').open('w') as debug:
            subprocess.run([str(executable), mode], stdout=log, stderr=debug, check=True)
    marker = 'LIVE_PRODUCER_GEOMETRY PASS jobs=4 events=12 slot_sequence=0,1,0 immutable_A=1 prefix_metadata=1 rejected_jobs=4'
    if marker not in (out / 'live.log').read_text():
        raise RuntimeError('producer completion marker missing')
    keys = ('I', 'J', 'K', 'tile_I', 'tile_J', 'tile_K', 'stripe_rows',
            'stripe_count', 'final_rows', 'ws_inner_calls')
    samples = [dict(zip(keys, map(int, row.split(','))))
               for row in (out / 'scan.log').read_text().splitlines()]
    summary = {'host_commit': '7a74ac29c9c8fa8fbc5d47cf073b8b9266e3e7a9',
               'include_commit': 'cd2caed8d93e49d3f8873bd6c4e340fcad54b9e0',
               'automatic_samples': samples,
               'first_three_stripes': [s for s in samples if s['stripe_count'] == 3],
               'live_jobs': 4, 'live_events': 12, 'rejected_jobs': 4,
               'physical_device_access': False, 'marker': marker}
    (out / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    inputs = [source, Path(__file__).resolve(), flags_file, *libraries,
              frozen / 'params/gemmini.h', frozen / 'params/gemmini_params.h']
    inputs += sorted((frozen / 'host/ggml/src/ggml-gemmini/quants/act').rglob('*.[ch]pp'))
    inputs += [frozen / 'host/ggml/src/ggml-gemmini/ggml-gemmini-args.h',
               frozen / 'host/ggml/src/ggml-gemmini/ggml-gemmini-geometry.hpp']
    manifest = {str(p): sha(p) for p in inputs}
    manifest.update({str(p): sha(p) for p in out.iterdir() if p.is_file()})
    (out / 'sha256.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(marker)


if __name__ == '__main__':
    main()
