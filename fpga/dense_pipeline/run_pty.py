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


def run(rtl, host, arguments, out):
    out.mkdir(parents=True, exist_ok=False)
    files = [rtl, host]
    hashes = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    (out / 'identity.json').write_text(json.dumps(hashes, indent=2) + '\n')
    master, slave = os.openpty()
    device = os.ttyname(slave)
    environment = dict(os.environ, IM2P_FPGA_DEVICE=device, IM2P_FPGA_TIMEOUT_SECONDS='1800')
    arguments = [device if x == '@PTY' else x for x in arguments]
    commands = {'rtl': [str(rtl), str(master)], 'host': [str(host), *arguments],
                'device': device, 'physical_board': False}
    if rtl.name == 'Vdense_uart_shell':
        commands['rtl'] = [str(rtl), '--pty', str(master), '--trace', str(out / 'trace')]
    (out / 'commands.json').write_text(json.dumps(commands, indent=2) + '\n')
    start = time.monotonic()
    with (out / 'rtl.log').open('x') as rtl_log, (out / 'host.log').open('x') as host_log:
        server = subprocess.Popen(commands['rtl'], pass_fds=[master], stdout=rtl_log, stderr=subprocess.STDOUT)
        try:
            client = subprocess.run(commands['host'], env=environment, stdout=host_log,
                                    stderr=subprocess.STDOUT, timeout=3600)
        finally:
            server.send_signal(signal.SIGTERM)
            server.wait(timeout=30)
            os.close(master)
            os.close(slave)
    unchanged = all(hashlib.sha256(p.read_bytes()).hexdigest() == hashes[str(p)] for p in files)
    log = (out / 'rtl.log').read_text()
    status = {'host_exit': client.returncode, 'rtl_exit': server.returncode,
              'rtl_completed': 'RTL_PTY_COMPLETE' in log,
              'runtime_failure': 'RTL_PTY_FAIL' in log or 'Dynamic assertion failed' in log or '$finish' in log,
              'preserved': unchanged, 'wall_seconds': time.monotonic() - start}
    status['pass'] = not client.returncode and not server.returncode and unchanged and status['rtl_completed'] and not status['runtime_failure']
    (out / 'status.json').write_text(json.dumps(status, indent=2) + '\n')
    print(json.dumps(status), flush=True)
    if not status['pass']:
        raise RuntimeError('host/RTL PTY validation failed; original logs retained')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--rtl', type=Path, required=True)
    parser.add_argument('--host', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('arguments', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    run(args.rtl.resolve(), args.host.resolve(), args.arguments, args.out.resolve())
