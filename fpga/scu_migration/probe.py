#!/usr/bin/env python3
"""Local environment evidence; no UART/JTAG open, CAP, SSH or installation.

Import and --help do not collect evidence. Default collection does not write
files. The optional compiler check writes only to an explicitly new directory.
"""
import argparse
import datetime
import grp
import json
import os
from pathlib import Path
import pwd
import shutil
import stat
import subprocess
import sys
import time


def unavailable(reason):
    return {'status': 'unavailable', 'reason': str(reason)}


def text_file(path):
    try:
        return {'status': 'ok', 'path': str(path),
                'raw': Path(path).read_bytes().decode('utf-8', errors='replace')}
    except OSError as error:
        return dict(unavailable(error), path=str(path))


def command(argv, timeout=10, cwd='/', env=None):
    """One command, no shell or retry. Keep failed/timeout output as evidence."""
    began = time.monotonic()
    row = {'argv': list(map(str, argv)), 'cwd': str(cwd),
           'start_utc': datetime.datetime.now(datetime.timezone.utc).isoformat()}
    environment = os.environ.copy()
    environment.update({'RUSTUP_AUTO_INSTALL': '0', 'RUSTUP_NO_UPDATE_CHECK': '1',
                        'CARGO_NET_OFFLINE': 'true'})
    if env:
        environment.update(env)
    try:
        result = subprocess.run(row['argv'], cwd=cwd, env=environment, timeout=timeout,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                universal_newlines=True, check=False)
        row.update(status='ok' if result.returncode == 0 else 'command_failed',
                   exit_code=result.returncode, stdout=result.stdout, stderr=result.stderr,
                   output_complete=True)
    except subprocess.TimeoutExpired as error:
        def decoded(value):
            return value.decode('utf-8', errors='replace') if isinstance(value, bytes) else (value or '')
        row.update(status='timeout', reason=str(error), exit_code=None,
                   stdout=decoded(error.stdout), stderr=decoded(error.stderr), output_complete=False)
    except OSError as error:
        row.update(status='unavailable', reason=str(error), exit_code=None,
                   stdout=None, stderr=None, output_complete=False)
    row.update(elapsed_seconds=time.monotonic() - began,
               end_utc=datetime.datetime.now(datetime.timezone.utc).isoformat())
    return row


def version(executable):
    selected = shutil.which(str(executable))
    if selected is None:
        return dict(unavailable('executable not found'), requested=str(executable))
    return dict(command([selected, '--version']), requested=str(executable),
                executable=selected, resolved_executable=str(Path(selected).resolve()))


def access_info(path):
    """stat/access only; permission bits are not a device functionality test."""
    try:
        data = Path(path).stat()
        return {'status': 'ok', 'path': str(path), 'resolved_path': str(Path(path).resolve()),
                'uid': data.st_uid, 'gid': data.st_gid, 'mode': oct(stat.S_IMODE(data.st_mode)),
                'character_device': stat.S_ISCHR(data.st_mode),
                'read_access': os.access(str(path), os.R_OK),
                'write_access': os.access(str(path), os.W_OK),
                'scope': 'account stat/access only; device not opened'}
    except OSError as error:
        return dict(unavailable(error), path=str(path))


def usb_inventory(root=Path('/sys/bus/usb/devices'), by_id=Path('/dev/serial/by-id')):
    result = {'scope': 'sysfs text and pathname metadata only; no USB or serial device open'}
    try:
        entries = []
        for path in sorted(root.iterdir()):
            if not (path / 'idVendor').exists():
                continue  # USB interface entries are not additional physical devices.
            item = {'sysfs_path': str(path), 'resolved_path': str(path.resolve())}
            for name in ('idVendor', 'idProduct', 'manufacturer', 'product', 'serial', 'busnum', 'devnum'):
                item[name] = text_file(path / name)
            try:
                bus, device = (int(item[name]['raw'].strip()) for name in ('busnum', 'devnum'))
                item['device_node'] = access_info('/dev/bus/usb/{:03d}/{:03d}'.format(bus, device))
            except (KeyError, ValueError):
                item['device_node'] = unavailable('busnum/devnum unavailable or invalid')
            entries.append(item)
        result['usb'] = {'status': 'ok', 'entries': entries}
    except OSError as error:
        result['usb'] = unavailable(error)
    try:
        result['serial_by_id'] = {'status': 'ok', 'entries': [access_info(path) for path in sorted(by_id.iterdir())]}
    except OSError as error:
        result['serial_by_id'] = unavailable(error)
    return result


def filesystem(path):
    try:
        data = os.statvfs(str(path))
        return {'status': 'ok', 'path': str(path), 'resolved_path': str(Path(path).resolve()),
                'total_bytes': data.f_blocks * data.f_frsize,
                'free_bytes': data.f_bfree * data.f_frsize,
                'available_bytes': data.f_bavail * data.f_frsize,
                'free_inodes': data.f_ffree, 'available_inodes': data.f_favail,
                'readonly_flag': bool(data.f_flag & os.ST_RDONLY)}
    except OSError as error:
        return dict(unavailable(error), path=str(path))


def collect(paths, cxx=None):
    began = time.monotonic()
    result = {'schema': 'im2p.local_environment.v1',
              'start_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'target_classification': 'not_assumed; inspect uname/device-tree evidence',
              'device_access': 'no UART/JTAG/USB device open; no CAP or programming'}
    uname = os.uname()
    result['uname'] = dict(zip(('sysname', 'nodename', 'release', 'version', 'machine'), uname))
    result['device_tree_model'] = text_file('/proc/device-tree/model')
    result['os_release'] = text_file('/etc/os-release')
    result['nv_tegra_release'] = text_file('/etc/nv_tegra_release')
    try:
        libc = os.confstr('CS_GNU_LIBC_VERSION')
        result['glibc'] = {'status': 'ok', 'raw': libc} if libc else unavailable('confstr returned no value')
    except (ValueError, OSError) as error:
        result['glibc'] = unavailable(error)
    result['tools'] = {name: version(executable) for name, executable in (
        ('cc', 'cc'), ('cxx', cxx or 'c++'), ('cmake', 'cmake'),
        ('python', sys.executable), ('rustc', 'rustc'), ('cargo', 'cargo'))}
    result['ram_and_swap'] = text_file('/proc/meminfo')
    result['swap_files'] = text_file('/proc/swaps')
    result['cpu_features'] = text_file('/proc/cpuinfo')
    result['mountinfo'] = text_file('/proc/self/mountinfo')
    result['filesystems'] = [filesystem(path) for path in paths]
    groups = []
    for gid in sorted(set(os.getgroups() + [os.getegid()])):
        try:
            groups.append({'gid': gid, 'name': grp.getgrgid(gid).gr_name})
        except KeyError:
            groups.append({'gid': gid, 'name': None, 'reason': 'group name unavailable'})
    try:
        name = pwd.getpwuid(os.geteuid()).pw_name
    except KeyError:
        name = None
    result['account'] = {'uid': os.getuid(), 'euid': os.geteuid(), 'gid': os.getgid(),
                         'egid': os.getegid(), 'name': name, 'groups': groups}
    result['usb_inventory'] = usb_inventory()
    result.update(elapsed_seconds=time.monotonic() - began,
                  end_utc=datetime.datetime.now(datetime.timezone.utc).isoformat())
    return result


CXX20_SOURCE = r'''#include <bit>
#include <charconv>
#include <cstdint>
#include <iostream>
#include <string_view>
int main() {
    static_assert(__cplusplus >= 202002L);
    constexpr std::string_view text = "65790";
    uint32_t value = 0;
    const auto parsed = std::from_chars(text.data(), text.data() + text.size(), value);
    if (parsed.ec != std::errc{} || parsed.ptr != text.data() + text.size() || value != 65790 ||
        !text.starts_with("65") || std::bit_cast<uint32_t>(1.0f) != 0x3f800000U) return 1;
    const char *endian = std::endian::native == std::endian::little ? "little" :
        std::endian::native == std::endian::big ? "big" : "mixed";
    std::cout << "CXX20_PROBE_PASS endian=" << endian << " cplusplus=" << __cplusplus << '\n';
}
'''


def compile_probe(cxx, out, run_native=False):
    compiler = Path(cxx)
    if not compiler.is_absolute() or not compiler.is_file() or not os.access(str(compiler), os.X_OK):
        raise ValueError('--cxx must name an existing absolute executable path')
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    temporary = out / 'tmp'
    temporary.mkdir()
    source, executable = out / 'cxx20.cpp', out / 'cxx20-probe'
    source.write_text(CXX20_SOURCE)
    env = {name: str(temporary) for name in ('TMPDIR', 'TMP', 'TEMP')}
    result = {'scope': 'C++20 standard-library/toolchain check; no FPGA or SCU numerical execution',
              'compiler': str(compiler), 'resolved_compiler': str(compiler.resolve()),
              'compile': command([str(compiler), '-std=c++20', '-Wall', '-Wextra', '-Werror',
                                  str(source), '-o', str(executable)], timeout=120, cwd=out, env=env)}
    if result['compile']['status'] == 'ok' and run_native:
        result['native_run'] = command([str(executable)], timeout=10, cwd=out, env=env)
        result['native_marker_pass'] = result['native_run']['status'] == 'ok' and \
            result['native_run']['stdout'].count('CXX20_PROBE_PASS ') == 1
    else:
        result['native_run'] = {'status': 'not_run', 'reason': 'explicit --run-native absent' if not run_native else 'compile failed'}
    (out / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--path', type=Path, action='append', help='Filesystem to inspect; repeatable, default / and current directory')
    parser.add_argument('--cxx', help='Compiler executable; absolute path required for optional compile check')
    parser.add_argument('--compile-probe', action='store_true', help='Explicitly compile a small C++20 toolchain check')
    parser.add_argument('--out', type=Path, help='New output directory, required only with --compile-probe')
    parser.add_argument('--run-native', action='store_true', help='Explicitly execute the compiled local check; requires --compile-probe')
    args = parser.parse_args(argv)
    if args.compile_probe and (not args.cxx or not args.out):
        parser.error('--compile-probe requires --cxx and --out')
    if not args.compile_probe and (args.out or args.run_native):
        parser.error('--out/--run-native require --compile-probe')
    if args.compile_probe and (not Path(args.cxx).is_absolute() or args.out.exists()):
        parser.error('--cxx must be absolute and --out must not exist')
    result = collect(args.path or [Path('/'), Path.cwd()], args.cxx)
    if args.compile_probe:
        result['cxx20_probe'] = compile_probe(args.cxx, args.out, args.run_native)
    print(json.dumps(result, indent=2))
    if args.compile_probe:
        check = result['cxx20_probe']
        if check['compile']['status'] != 'ok' or (args.run_native and not check.get('native_marker_pass')):
            return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
