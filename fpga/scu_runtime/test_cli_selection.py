#!/usr/bin/env python3
"""Check final argument selection using standard llama-cli --help, without model execution."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cli', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True, help='New evidence directory')
    args = parser.parse_args()
    cli = args.cli.resolve(strict=True)
    args.out.mkdir(parents=True, exist_ok=False)
    env = {k: v for k, v in os.environ.items()
           if not k.startswith('IM2P_FPGA_') and not k.startswith('LLAMA_ARG_')}
    cases = [
        ('fpga-device-first', ['--device', 'GEMMINI', '-ngl', '99'], False),
        ('fpga-layers-first', ['-ngl', '99', '--device', 'GEMMINI'], False),
        ('cpu-explicit-layers', ['--device', 'none', '-ngl', '99'], True),
        ('cpu-default-layers', ['--device', 'none'], False),
    ]
    results = []
    warning = '--gpu-layers option will be ignored'
    for name, options, expected_warning in cases:
        command = [str(cli), *options, '--help']
        result = subprocess.run(command, env=env, cwd=args.out,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, timeout=60)
        (args.out / (name + '.log')).write_text(result.stdout)
        passed = result.returncode == 0 and (warning in result.stdout) == expected_warning
        results.append({'name': name, 'argv': command, 'exit': result.returncode,
                        'expected_warning': expected_warning, 'observed_warning': warning in result.stdout,
                        'status': 'PASS' if passed else 'FAIL'})
        (args.out / 'results.json').write_text(json.dumps({
            'cli': str(cli), 'cli_sha256': hashlib.sha256(cli.read_bytes()).hexdigest(),
            'cases': results, 'scope': 'Argument parsing and help only; no model/device execution',
        }, indent=2) + '\n')
        print(name, results[-1]['status'])
        if not passed:
            raise SystemExit('Unexpected CLI selection result; evidence: ' + str(args.out))


if __name__ == '__main__':
    main()
