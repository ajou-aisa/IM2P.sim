#!/usr/bin/env python3
"""Freeze explicit sources, build RTL, simulate or route. Never programs hardware."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import time

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
VIVADO = Path('/tools/Xilinx/2025.2/Vivado')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def command(argv, cwd, prefix):
    prefix.with_suffix('.command.json').write_text(json.dumps({'argv':list(map(str,argv)), 'cwd':str(cwd)},indent=2)+'\n')
    start=time.monotonic()
    with prefix.with_suffix('.log').open('x') as log:
        result=subprocess.run(list(map(str,argv)),cwd=cwd,stdout=log,stderr=subprocess.STDOUT)
    prefix.with_suffix('.status.json').write_text(json.dumps({'exit':result.returncode,'seconds':time.monotonic()-start})+'\n')
    if result.returncode:
        raise RuntimeError(f'command failed: {prefix}.log')


def freeze(out):
    baseline=json.loads((HERE/'baseline.json').read_text())
    out.mkdir(parents=True,exist_ok=False)
    source=out/'source'; source.mkdir()
    archive=subprocess.check_output(['git','-C',str(ROOT),'archive',baseline['head']])
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(source)
    command(['patch','--batch','-p1','-i',HERE/'fixed-core.patch'],source,out/'fixed-patch')
    for row in baseline['files']:
        if digest(source/row['path']) != row['frozen_sha256']:
            raise ValueError('fixed source mismatch '+row['path'])
    selected=['synth/FullReplay.bsv','frontend/include/im2p_gemmini_frontend.hpp','frontend/src/im2p_gemmini_frontend.cpp',
              'frontend/tests/test_frontend.cpp']
    selected += [str(p.relative_to(ROOT)) for p in HERE.rglob('*') if p.is_file() and '__pycache__' not in p.parts]
    for name in selected:
        destination=source/name; destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(ROOT/name,destination)
    manifest={str(p.relative_to(source)):digest(p) for p in source.rglob('*') if p.is_file()}
    (out/'source-sha256.json').write_text(json.dumps(manifest,indent=2)+'\n')
    (out/'selection.json').write_text(json.dumps({'base':baseline['head'],'fixed_patch':digest(HERE/'fixed-core.patch'),
        'explicit_replacements':selected,'hardware_programming':False},indent=2)+'\n')


def verify(out):
    for name,value in json.loads((out/'source-sha256.json').read_text()).items():
        if digest(out/'source'/name)!=value:
            raise ValueError('frozen source changed '+name)


def bsc(out, asserted):
    verify(out); b=out/('asserted' if asserted else 'production'); b.mkdir()
    for name in ('bsc','info','rtl'): (b/name).mkdir()
    cmd=['bsc','-u','-verilog']+(['-check-assert'] if asserted else [])
    cmd += ['-p','+:src/common:src/io:src/array:src/vector:src/accumulator:src/control:src/core:synth',
      '-steps','4000000','-steps-warn-interval','1000000','-steps-max-intervals','20','+RTS','-K256M','-RTS',
      '-bdir',b/'bsc','-info-dir',b/'info','-vdir',b/'rtl','-g','mkFullReplay','synth/FullReplay.bsv']
    command(cmd,out/'source',b/'build')
    primitives=Path(shutil.which('bsc')).resolve().parents[1]/'lib/Verilog'
    (b/'primitives').mkdir()
    for name in ('FIFO2.v','BRAM1.v','RegFile.v'): shutil.copy2(primitives/name,b/'primitives'/name)
    (b/'generated-sha256.json').write_text(json.dumps({str(p.relative_to(b)):digest(p)
      for folder in ('rtl','primitives') for p in (b/folder).glob('*.v')},indent=2)+'\n')


def simulation(out, asserted):
    verify(out); b=out/('asserted' if asserted else 'production'); f=out/'source/fpga/full_replay'
    command(['verilator','--cc','--exe','--build','-j','2','--timing','--assert','-Wno-fatal',
      '--top-module','full_uart_shell','--Mdir',b/'obj_dir','--output-split','20000','--output-split-cfuncs','500',
      '-CFLAGS','-O1 -g0 -std=c++20', f/'uart.sv',f/'full_uart.sv',b/'rtl/mkFullReplay.v',
      *sorted((b/'primitives').glob('*.v')),f/'rtl_driver.cpp'],out,b/'verilator')


def board_simulation(out, itinerary):
    verify(out); b=out/'board-top'; b.mkdir()
    shutil.copy2(itinerary,b/'itinerary.txt')
    f=out/'source/fpga/full_replay'; production=out/'production'
    files=[f/'arty_full_top.sv',f/'full_uart.sv',f/'uart.sv',production/'rtl/mkFullReplay.v',
           *sorted((production/'primitives').glob('*.v')),VIVADO/'data/verilog/src/glbl.v',f/'board_top_tb.sv']
    command([VIVADO/'bin/xvlog','--sv',*files],b,b/'compile')
    command([VIVADO/'bin/xelab','--timescale','1ns/1ps','-L','unisims_ver','work.board_top_tb','work.glbl',
             '-s','board_top','--debug','off'],b,b/'elaborate')
    command([VIVADO/'bin/xsim','board_top','--runall'],b,b/'simulate')
    log=(b/'simulate.log').read_text()
    if 'BOARD_TOP_COMPLETE' not in log or 'Fatal:' in log or 'Dynamic assertion failed' in log:
        raise RuntimeError('board-top did not complete; exit zero alone is insufficient')


if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('stage',choices=['freeze','bsc','sim','board-sim','route']);
    p.add_argument('out',type=Path); p.add_argument('--asserted',action='store_true');
    p.add_argument('--itinerary',type=Path); a=p.parse_args(); out=a.out.resolve()
    if a.stage=='freeze': freeze(out)
    elif a.stage=='bsc': bsc(out,a.asserted)
    elif a.stage=='sim': simulation(out,a.asserted)
    elif a.stage=='board-sim': board_simulation(out,a.itinerary)
    else:
        verify(out)
        command([VIVADO/'bin/vivado','-mode','batch','-nojournal','-source',out/'source/fpga/full_replay/route.tcl',
          '-tclargs',out],out,out/'vivado')
