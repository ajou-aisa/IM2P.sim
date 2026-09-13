#!/usr/bin/env python3
"""Record the stock host pin rejecting FPGA_UART, before any dependency build."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tarfile

PIN = '7a74ac29c9c8fa8fbc5d47cf073b8b9266e3e7a9'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host-repo', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    archive_command = ['git', '-C', str(args.host_repo), 'archive', '--format=tar', PIN, 'CMakeLists.txt', 'cmake']
    data = subprocess.check_output(archive_command)
    args.out.mkdir(parents=True, exist_ok=False)
    source = args.out / 'source'
    source.mkdir()
    hashes = {}
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        for member in tar:
            name = PurePosixPath(member.name)
            if name.is_absolute() or '..' in name.parts or not (member.isdir() or member.isfile()):
                raise ValueError('unexpected stock archive member')
            path = source / member.name
            if member.isdir():
                path.mkdir(parents=True, exist_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                payload = tar.extractfile(member).read()
                path.write_bytes(payload)
                hashes[member.name] = hashlib.sha256(payload).hexdigest()
    temporary = args.out / 'tmp'; temporary.mkdir()
    environment = dict(os.environ, TMPDIR=str(temporary), TMP=str(temporary), TEMP=str(temporary))
    command = ['cmake', '-S', str(source), '-B', str(args.out / 'build'),
               '-DGGML_GEMMINI=ON', '-DGGML_GEMMINI_OPTION=WS',
               '-DGGML_GEMMINI_EXECUTION_BACKEND=FPGA_UART', '-DLLAMA_CURL=OFF']
    process = subprocess.run(command, env=environment, text=True, capture_output=True)
    (args.out / 'stdout.log').write_text(process.stdout)
    (args.out / 'stderr.log').write_text(process.stderr)
    expected = "must be exactly HARDWARE or IM2P_SIM, got"
    if process.returncode == 0 or expected not in process.stderr or 'FPGA_UART' not in process.stderr:
        raise RuntimeError('unexpected stock configure outcome; preserved logs')
    result = {'status': 'EXPECTED_REJECTION_REPRODUCED', 'host_pin': PIN,
              'archive_argv': archive_command, 'configure_argv': command, 'exit': process.returncode,
              'source_scope': 'stock root CMake and cmake modules; stops before ggml/dependency configuration',
              'files': hashes, 'heavy_provisioning': 0, 'physical_operations': 0}
    (args.out / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'status': result['status'], 'exit': process.returncode, 'out': str(args.out)}))


if __name__ == '__main__':
    main()
