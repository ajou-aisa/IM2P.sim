#!/usr/bin/env python3
"""Build a tiny test-only GGUF with the selected standard ggml-base quantizer.

No downloads, NumPy, model conversion, backend loading, or device access.
The helper uses the repository's native GGUF writer and quantize_q8_h1 path.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import time


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--host-source', required=True, type=Path)
    ap.add_argument('--ggml-base', required=True, type=Path,
                    help='Actual standard build libggml-base.so (not a dedicated parent target)')
    ap.add_argument('--out', required=True, type=Path, help='New directory; must not exist')
    ap.add_argument('--cxx', default=os.environ.get('CXX', 'c++'))
    args = ap.parse_args()
    host, library, out = args.host_source.resolve(), args.ggml_base.resolve(), args.out.resolve()
    source = Path(__file__).with_suffix('.cpp').resolve()
    required = [source, library, host/'ggml/include/ggml.h', host/'ggml/include/gguf.h',
                host/'ggml/src/ggml-quants.h', host/'ggml/src/ggml-quants.c',
                host/'ggml/src/ggml-common.h']
    for path in required:
        if not path.is_file():
            raise SystemExit('missing input: ' + str(path))
    if library.read_bytes()[:4] != b'\x7fELF':
        raise SystemExit('ggml-base must be the native shared ELF library')
    out.mkdir(parents=True, exist_ok=False)
    commands = []
    def run(label, argv):
        record = {'label': label, 'argv': argv, 'cwd': str(out), 'start_ns': time.time_ns()}
        result = subprocess.run(argv, cwd=out, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        (out/(label+'.log')).write_text(result.stdout)
        record.update(end_ns=time.time_ns(), returncode=result.returncode)
        commands.append(record)
        (out/'commands.json').write_text(json.dumps(commands, indent=2)+'\n')
        if result.returncode:
            raise SystemExit(f'{label} failed; original evidence: {out/(label+".log")}')
        print(result.stdout, end='')
    helper = out/'tiny-model-writer'
    run('compile', shlex.split(args.cxx) + ['-std=c++20', '-O2', str(source),
        '-I'+str(host/'ggml/include'), '-I'+str(host/'ggml/src'), str(library),
        '-Wl,-rpath,'+str(library.parent), '-o', str(helper)])
    run('generate-h1', [str(helper), str(out/'tiny-h1.gguf'), 'H1'])
    run('generate-f32', [str(helper), str(out/'tiny-f32.gguf'), 'F32'])
    (out/'prompt.txt').write_text('a a a a')
    (out/'ppl.txt').write_text(' '.join(['a'] * 128))
    common = ['--ctx-size', '32', '--batch-size', '16', '--ubatch-size', '16',
              '--threads', '1', '--no-warmup']
    examples = {
        'environment': {'GEMMINI_MATMUL_MODE': 'STRIPE_PIPELINE',
                        'IM2P_FPGA_REQUIRE_COMPLETION': '1',
                        'IM2P_FPGA_DEVICE': '<explicit PTY for software test or separately approved device>',
                        'IM2P_FPGA_FULL_REFERENCE': '<pinned package02 reference>',
                        'IM2P_FPGA_HARDWARE_SHA256': '<pinned bitstream SHA256>',
                        'IM2P_FPGA_PRODUCTION_RTL_SHA256': '<pinned RTL SHA256>'},
        'cli': ['llama-cli', '--model', 'tiny-h1.gguf', '--device', 'GEMMINI', '-ngl', '99',
                '--prompt', 'a a a a', '--n-predict', '2', '--temp', '0', '--ignore-eos'] + common,
        'perplexity': ['llama-perplexity', '--model', 'tiny-h1.gguf', '--device', 'GEMMINI', '-ngl', '99',
                       '--file', 'ppl.txt', '--chunks', '1'] + common,
        'cpu_zero_completion_negative': ['llama-cli', '--model', 'tiny-f32.gguf',
                '--prompt', 'a a a a', '--n-predict', '1', '--temp', '0'] + common,
        'status': 'Commands prepared only. These must be executed by an explicit non-device PTY test runner.',
        'placement': 'Explicit GEMMINI and -ngl 99 select candidate layer placement; supports_op still leaves unsupported tensors on CPU. Numerical fixture generation is unchanged.',
        'semantics': 'Output projection only is H1 K64/N16. Other tensors are F32/CPU; untrained synthetic graph, no model quality claim.',
    }
    (out/'commands-to-test.json').write_text(json.dumps(examples, indent=2)+'\n')
    manifest = {'kind': 'test-only synthetic model, not a converted user model',
                'writer': 'native gguf C API', 'quantizer': 'ggml_quantize_chunk(GGML_TYPE_Q8_H1)',
                'inputs': {str(path): sha(path) for path in required},
                'files': {path.name: sha(path) for path in sorted(out.iterdir()) if path.is_file()},
                'CLI_runtime': 'NOT RUN by generator', 'device_access': 0}
    (out/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    print('Prepared synthetic fixtures:', out)


if __name__ == '__main__':
    main()
