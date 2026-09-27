from __future__ import annotations

import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

from scripts.gemmini_rtl_build_binding import verify_build
from sim.cycle.npu_trace_schema import Record, object_value
from sim.tests.cycle.compositional_sequence_v2_base import (
    BASE,
    ROOT,
    SOURCE,
    AbsoluteOfferError,
)


def compile_probe(build: Path, library: Path, out: Path, projection: Record) -> Path:
    if verify_build(build, str(projection['profile']))['hardware_contract'] != projection['hardware_contract']:
        raise AbsoluteOfferError('RTL build hardware contract differs from producer')
    source = BASE.read_text()
    marker = '#undef IM2P_SERVICE_PROBE_NO_MAIN\n'
    event = '''void event(const char *kind, std::uint64_t cycle) {
  if (current_ordinal != UINT32_MAX)
    std::cout << "RTL_EVENT " << current_ordinal << ' ' << current_work_id << ' '
              << cycle << ' ' << kind << '\\n';
}'''
    atomic_event = '''void event(const char *kind, std::uint64_t cycle) {
  if (current_ordinal != UINT32_MAX)
    std::fprintf(stdout, "RTL_EVENT %u %u %llu %s\\n", current_ordinal, current_work_id, static_cast<unsigned long long>(cycle), kind);
}'''
    if source.count(marker) != 1 or source.count(event) != 1:
        raise AbsoluteOfferError('base probe inclusion seam changed')
    generated = out / 'compositional-base.inc'
    generated.write_text('#include <cstdio>\n' + source.replace(
        marker, marker + '#define main legacy_sequence_main\n').replace(event, atomic_event))
    resolved = json.loads((build / 'resolved-profile.json').read_text())
    makefile = (build / 'rtl-test-obj/VIM2PGemminiWSHP1RtlTest.mk').read_text()
    flags_match = re.search(r'VM_USER_CFLAGS = \\\n(.*?)\n\n', makefile, re.DOTALL)
    root_match = re.search(r'^VERILATOR_ROOT = (.+)$', makefile, re.MULTILINE)
    if flags_match is None or root_match is None:
        raise AbsoluteOfferError('official Verilator compile inputs missing')
    flags = shlex.split(flags_match[1].replace('\\\n', ' ').rstrip().removesuffix('\\'))
    top = resolved.get('selected_top')
    if not isinstance(top, str) or not re.fullmatch(r'IM2PGemminiWSHP1A[48]W[48]D(?:16|32|64)', top):
        raise AbsoluteOfferError('official generated top missing')
    flags.append('-DIM2P_RTL_SELECTED_TOP=' + top)
    if resolved.get('llama_source') is not None:
        recorded = object_value(resolved['llama_source'])['root']
        flags = [flag.replace(str(recorded), str(ROOT.parent / 'llama.cpp-gemmini')) for flag in flags]
    objects = build / 'rtl-test-obj'
    include = Path(root_match[1]) / 'include'
    flags += ['-ffunction-sections', '-fdata-sections', f'-I{objects}', f'-I{include}',
              f'-I{include / "vltstd"}', '-O1', f'-I{SOURCE.parent}',
              '-DIM2P_COMPOSITIONAL_BASE_SOURCE="' + str(generated) + '"']
    binary = out / 'compositional-probe'
    link = (['-Wl,-dead_strip', '-Wl,-U,__Z15vl_time_stamp64v,-U,__Z13sc_time_stampv']
            if sys.platform == 'darwin' else ['-Wl,--gc-sections'])
    argv = ['c++', *flags, str(SOURCE), str(ROOT / 'sim/common/gemmini_schedule.cpp'),
            str(objects / 'VIM2PGemminiWSHP1RtlTest__ALL.a'), str(objects / 'verilated.o'),
            str(objects / 'verilated_threads.o'), str(library), f'-Wl,-rpath,{library.parent}',
            *link, '-pthread', '-o', str(binary)]
    (out / 'compile-command.json').write_text(json.dumps(argv, indent=2) + '\n')
    with (out / 'compile.log').open('x') as log:
        completed = subprocess.run(argv, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                   check=False, timeout=300)
    if completed.returncode:
        raise AbsoluteOfferError(f'probe compile failed: {out / "compile.log"}')
    return binary
