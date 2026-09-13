#!/usr/bin/env python3
"""Source/provenance checks for the M9 harness; these are not numerical DUTs."""
from collections import Counter
import hashlib
import os
from pathlib import Path
import re
import shutil

PRIMITIVES = ('RegFile.v', 'FIFO2.v', 'BRAM1.v', 'BRAM2.v')
REQUIRED_SOURCE_FILES = (
    'src/core/IM2PCore.bsv', 'src/control/WorkTypes.bsv',
    'synth/SynthA8W8D16.bsv', 'sim/ffi/im2p_config.h',
    'sim/include/im2p_sim.h', 'config/im2p_profiles.json',
)


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def source_inputs(source: Path) -> dict[str, str]:
    """Hash actual BSV inputs, not old generated RTL or another frozen layer."""
    source = source.resolve(strict=True)
    paths = {source / name for name in REQUIRED_SOURCE_FILES}
    for directory in ('src', 'synth'):
        paths.update((source / directory).rglob('*.bsv'))
    result = {}
    for path in sorted(paths):
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(source):
            raise ValueError(f'source input escapes selected source: {path}')
        if not resolved.is_file():
            raise ValueError(f'not a regular source file: {path}')
        result[str(path)] = digest(path)
    return result


def changed_inputs(hashes: dict[str, str]) -> list[str]:
    changed = []
    for name, expected in hashes.items():
        try:
            if digest(Path(name)) != expected:
                changed.append(name)
        except OSError:
            changed.append(name)
    return sorted(changed)


def changed_source_inputs(source: Path, original: dict[str, str]) -> list[str]:
    try:
        current = source_inputs(source)
    except (OSError, ValueError):
        return sorted(set(changed_inputs(original)) | {str(source)})
    return sorted(name for name in set(original) | set(current)
                  if original.get(name) != current.get(name))


def primitive_directory(bsc: Path, override: str | None = None) -> Path:
    prefix = bsc.resolve(strict=True).parents[1]
    candidates = [Path(override)] if override else [
        prefix / 'libexec/lib/Verilog', prefix / 'lib/Verilog']
    for directory in candidates:
        if all((directory / name).is_file() and os.access(directory / name, os.R_OK)
               for name in PRIMITIVES):
            return directory.resolve()
    raise ValueError('BSC primitive directory unavailable/incomplete: ' +
                     ', '.join(map(str, candidates)))


def discover_toolchain(environ: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ if environ is None else environ
    tools = {}
    for name, variable in (('bsc', 'BSC'), ('verilator', 'VERILATOR')):
        selected = env.get(variable, name)
        executable = shutil.which(selected, path=env.get('PATH', os.defpath))
        if executable is None:
            raise ValueError(f'{name} is not visible/executable in this command environment: {selected}; '
                             'check PATH and the CatDesk toolchain mounts, not a reinstall')
        tools[name] = str(Path(executable).resolve(strict=True))
    tools['bsc_verilog'] = str(primitive_directory(Path(tools['bsc']), env.get('BSC_VERILOG')))
    return tools


def warning_inventory(log: str) -> dict:
    """Retain diagnostics, including G0117, without suppressing any warnings."""
    blocks = re.split(r'(?=^(?:Warning|Error):)', log, flags=re.M)
    diagnostics = [block.rstrip() for block in blocks if block.startswith(('Warning:', 'Error:'))]
    counts = Counter()
    for block in diagnostics:
        match = re.search(r'\(([A-Z][0-9]{4})\)', block)
        if match:
            counts[match.group(1)] += 1
    return {'counts': dict(sorted(counts.items())), 'g0117_count': counts.get('G0117', 0),
            'diagnostics': diagnostics}
