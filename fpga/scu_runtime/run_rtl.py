#!/usr/bin/env python3
"""Run a host executable against real RTL through a newly allocated PTY only."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import time


def run(rtl, host, arguments, out, timeout=3600):
    if timeout <= 0:
        raise ValueError('supervisor timeout must be positive')
    window = rtl.name == 'Vscu_window_uart_shell'
    for key in (() if window else ("IM2P_FPGA_FULL_REFERENCE", "IM2P_FPGA_HARDWARE_SHA256", "IM2P_FPGA_PRODUCTION_RTL_SHA256")):
        if not os.environ.get(key):
            raise ValueError(f"Missing pinned RTL test environment: {key}")
    if os.environ.get("IM2P_FPGA_ALLOW_UNPINNED_RTL_TEST"):
        raise ValueError("This standard-library test requires pinned provenance")
    out.mkdir(parents=True, exist_ok=False)
    files = [rtl, host, *[Path(x) for x in arguments if Path(x).is_absolute() and Path(x).is_file()]]
    hashes = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    (out / 'runner.py').write_bytes(Path(__file__).read_bytes())
    (out / 'identity.json').write_text(json.dumps(hashes, indent=2) + '\n')
    master, slave = os.openpty()
    device = os.ttyname(slave)
    environment = dict(os.environ, IM2P_FPGA_DEVICE=('uart4:' if window else '') + device, IM2P_FPGA_TIMEOUT_SECONDS='1800')
    arguments = [device if x == '@PTY' else x for x in arguments]
    commands = {'rtl': [str(rtl), str(master)], 'host': [str(host), *arguments],
                'device': device, 'physical_board': False, 'supervisor_timeout_seconds': timeout,
                'environment': {k: v for k, v in environment.items() if k.startswith(('IM2P_FPGA_', 'GEMMINI_')) or k in ('LD_PRELOAD', 'IM2P_TEST_NONZERO_RMD', 'IM2P_TEST_SIM_FORBIDDEN')}}
    if rtl.name == 'Vscu_uart_shell':
        commands['rtl'] = [str(rtl), '--pty', str(master), '--trace', str(out / 'trace')]
    elif window:
        commands['rtl'] = [str(rtl), '--pty', str(master)]
    (out / 'commands.json').write_text(json.dumps(commands, indent=2) + '\n')
    start = time.monotonic()
    failure = None
    with (out / 'rtl.log').open('x') as rtl_log, (out / 'host.log').open('x') as host_log:
        server_environment = dict(os.environ)
        server_environment.pop('LD_PRELOAD', None)  # Host audit must not interpose on the RTL server.
        server = subprocess.Popen(commands['rtl'], env=server_environment, pass_fds=[master],
                                  stdout=rtl_log, stderr=subprocess.STDOUT)
        client = None
        try:
            client = subprocess.Popen(commands['host'], env=environment, stdout=host_log,
                                      stderr=subprocess.STDOUT)
            # The parent must not keep the PTY alive after the RTL exits.
            # Supervision never sends a protocol command or restarts either process.
            os.close(master)
            os.close(slave)
            while client.poll() is None:
                if server.poll() is not None:
                    failure = 'RTL exited before host completion'
                    break
                if time.monotonic() - start >= timeout:
                    failure = 'host process timeout'
                    break
                try:
                    client.wait(timeout=0.1)
                except subprocess.TimeoutExpired:
                    pass
        finally:
            if client is not None and client.poll() is None:
                client.terminate()
                client.wait(timeout=30)
            if server.poll() is None:
                if window:
                    try:
                        server.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        server.send_signal(signal.SIGTERM)
                else:
                    server.send_signal(signal.SIGTERM)
            server.wait(timeout=30)
    unchanged = all(hashlib.sha256(p.read_bytes()).hexdigest() == hashes[str(p)] for p in files)
    log = (out / 'rtl.log').read_text()
    status = {'host_exit': None if client is None else client.returncode, 'rtl_exit': server.returncode,
              'supervisor_failure': failure,
              'rtl_completed': 'RTL_PTY_COMPLETE' in log,
              'runtime_failure': 'RTL_PTY_FAIL' in log or 'Dynamic assertion failed' in log or '$finish' in log,
              'preserved': unchanged, 'wall_seconds': time.monotonic() - start,
              'supervisor_timeout_seconds': timeout}
    status['pass'] = client is not None and not client.returncode and not server.returncode and not failure and unchanged and status['rtl_completed'] and not status['runtime_failure']
    (out / 'status.json').write_text(json.dumps(status, indent=2) + '\n')
    print(json.dumps(status), flush=True)
    if not status['pass']:
        raise RuntimeError('host/RTL PTY validation failed; original logs retained')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--rtl', type=Path, required=True)
    parser.add_argument('--host', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--timeout', type=int, default=3600, help='positive supervisor timeout in seconds')
    parser.add_argument('arguments', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    arguments = args.arguments[1:] if args.arguments[:1] == ['--'] else args.arguments
    run(args.rtl.resolve(), args.host.resolve(), arguments, args.out.resolve(), args.timeout)
