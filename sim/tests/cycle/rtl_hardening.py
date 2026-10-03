#!/usr/bin/env python3
"""Build and run external single-work RTL probes for cycle-model certificates.

The probe is generated outside the repository from the preserved numerical RTL
fixture.  It relinks unchanged RTL objects, drives descriptors produced directly
by sim/common/gemmini_schedule.cpp, and emits passive timing events.  It is not a
production runtime and never updates RTL goldens.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shlex
import subprocess
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
PROFILES = tuple(f'a{bits}w{bits}-d{dim}-hp1' for bits in (4, 8) for dim in (16, 32, 64))
SELECTED_EVENTS = {
    'work', 'load_issue', 'execute_issue', 'store_issue', 'context', 'load_dma',
    'read_request', 'read_response', 'scratchpad_read', 'array_input', 'raw_completed',
    'array_output', 'accumulator_write', 'accumulator_commit', 'store_dma',
    'write_request', 'write_completion', 'loop_done', 'logical_done',
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def profile_bits_dim(profile: str) -> tuple[int, int]:
    match = re.fullmatch(r'a([48])w\1-d(16|32|64)-hp1', profile)
    if match is None:
        raise ValueError(f'unsupported profile: {profile}')
    return int(match[1]), int(match[2])


def record_command(root: Path, argv: list[str], log: Path | None = None) -> None:
    row: dict[str, Any] = {'argv': argv, 'cwd': str(ROOT)}
    if log is not None:
        row['log'] = str(log)
    with (root / 'commands.jsonl').open('a') as stream:
        stream.write(json.dumps(row) + '\n')


def run_logged(root: Path, argv: list[str], log: Path, env: dict[str, str] | None = None) -> int:
    record_command(root, argv, log)
    with log.open('x') as stream:
        completed = subprocess.run(argv, cwd=ROOT, env=env, stdout=stream,
                                   stderr=subprocess.STDOUT, check=False)
    return completed.returncode


def _replace_once(text: str, old: str, new: str, label: str) -> str:
    if text.count(old) != 1:
        raise ValueError(f'{label}: expected one source anchor, found {text.count(old)}')
    return text.replace(old, new)


def _instrument_events(text: str) -> str:
    include_anchor = '#include <cstring>\n'
    text = _replace_once(
        text, include_anchor,
        include_anchor + '#include <cstdlib>\n#include <fstream>\n#include <VIM2PGemminiWSHP1RtlTest___024root.h>\n#include "gemmini_schedule.hpp"\n',
        'instrument includes')
    namespace_anchor = '\nnamespace {\n'
    globals_text = '''
static std::ofstream hardening_observer(std::getenv("IM2P_CYCLE_OBSERVER"));
static unsigned hardening_case = 0;
'''
    text = _replace_once(text, namespace_anchor, globals_text + namespace_anchor,
                         'observer globals')
    step_anchor = '    dut.eval();\n    if (dut.io_error) {'
    hook = '    if (event_observer)\n      event_observer(*this);\n'
    hooked_anchor = step_anchor.replace('    if (dut.io_error) {', hook + '    if (dut.io_error) {')
    anchors = text.count(step_anchor) + text.count(hooked_anchor)
    if anchors != 1:
        raise ValueError(f'event injection: expected one source anchor, found {anchors}')
    events = r'''    dut.eval();
    auto hardening_event = [&](const char *kind, std::uint64_t a = 0,
                               std::uint64_t b = 0, std::uint64_t c = 0) {
      hardening_observer << hardening_case << ',' << dut.io_coreCycle << ',' << kind
                         << ',' << a << ',' << b << ',' << c << '\n';
    };
    if (dut.io_work_valid && dut.io_work_ready) {
      hardening_event("work", dut.io_work_bits_maxI, dut.io_work_bits_maxJ,
                      dut.io_work_bits_maxK);
      hardening_observer << "LOOP," << hardening_case << ',' << dut.io_coreCycle << ','
                         << unsigned(dut.io_work_bits_firstLoop) << ','
                         << unsigned(dut.io_work_bits_finalLoop) << ','
                         << unsigned(dut.io_work_bits_accumulate) << ','
                         << dut.io_work_bits_fragmentBase << ','
                         << unsigned(dut.io_work_bits_hostSlot) << ','
                         << dut.io_work_bits_scaleBase << ','
                         << unsigned(dut.io_work_bits_rmdRaw) << '\n';
    }
    if (dut.io_events_loadIssued) hardening_event("load_issue");
    if (dut.io_events_executeIssued) hardening_event("execute_issue", ROOT_EX_FUNCT);
    if (dut.io_events_storeIssued) hardening_event("store_issue");
    if (dut.io_events_outputContextIssued) hardening_event("context");
    if (dut.io_events_loadDmaAccepted)
      hardening_event("load_dma", dut.io_events_loadLocalAddressRaw,
                      dut.io_events_loadColumnsElements, dut.io_events_loadVaddrBytes);
    if (dut.io_readRequest_valid && dut.io_readRequest_ready)
      hardening_event("read_request", dut.io_readRequest_bits_id,
                      dut.io_readRequest_bits_address);
    if (dut.io_readBeat_valid && dut.io_readBeat_ready)
      hardening_event("read_response", dut.io_readBeat_bits_id);
    if (dut.io_events_scratchpadReadAcceptedBankMask)
      hardening_event("scratchpad_read", dut.io_events_scratchpadReadAcceptedBankMask);
    if (ROOT_MESH_INPUT) hardening_event("array_input", ROOT_MESH_ID, ROOT_MESH_ROW);
    if (ROOT_RAW_COMPLETED) hardening_event("raw_completed", ROOT_RAW_ROB);
    if (dut.io_events_rawRow) hardening_event("array_output");
    if (dut.io_events_scaledRow) hardening_event("accumulator_write");
    if (dut.io_events_accumulatorCommitted) hardening_event("accumulator_commit");
    if (dut.io_events_storeDmaAccepted)
      hardening_event("store_dma", dut.io_events_storeLocalAddressRaw,
                      dut.io_events_storeLengthElements);
    if (dut.io_writeRequest_valid && dut.io_writeRequest_ready)
      hardening_event("write_request", dut.io_writeRequest_bits_id,
                      dut.io_writeRequest_bits_address);
    if (dut.io_writeCompletion_valid && dut.io_writeCompletion_ready)
      hardening_event("write_completion", dut.io_writeCompletion_bits_id);
    if (dut.io_loopDone_valid && dut.io_loopDone_ready) hardening_event("loop_done");
    if (dut.io_logicalDone_valid) hardening_event("logical_done");
    if (dut.io_error) {'''
    if text.count(hooked_anchor):
        step_anchor = hooked_anchor
        events = events.replace('    if (dut.io_error) {', hook + '    if (dut.io_error) {')
    text = _replace_once(text, step_anchor, events, 'event injection')
    request_anchor = ('  const auto old_contexts = state.contexts, old_raw = state.raw_rows, '
                      'old_commits = state.commits;')
    wrapped_request_anchor = request_anchor.replace(', old_commits', ',\n             old_commits')
    request_anchors = text.count(request_anchor) + text.count(wrapped_request_anchor)
    if request_anchors != 1:
        raise ValueError(f'case injection: expected one source anchor, found {request_anchors}')
    if text.count(wrapped_request_anchor):
        request_anchor = wrapped_request_anchor
    request = r'''  ++hardening_case;
  hardening_observer << "CASE," << hardening_case << ',' << work.rows << ','
                     << work.columns << ',' << work.k << ',' << work.row_begin << ','
                     << work.plan.tile_i << ',' << work.plan.tile_j << ','
                     << work.plan.tile_k << ',' << state.read_latency << ','
                     << state.reorder_latency << ',' << state.scale_latency << ','
                     << state.write_latency << ',' << state.read_ready_period << ','
                     << state.cycle - state.dut.io_coreCycle << ','
                     << unsigned(work.plan.kind == WorkKind::rmd_raw) << '\n';
'''
    return _replace_once(text, request_anchor, request + request_anchor, 'case injection')


def _planner_block_traversal(text: str) -> str:
    execute_at = text.index('int execute(void *opaque, const FrontendRtlWork &work,')
    start = text.index('  bool first = true;\n  std::uint32_t generation = 1;\n', execute_at)
    end = text.index('  check(state.logical_done - old_done == 1,', start)
    replacement = r'''  std::uint64_t planner_loop_count = 0, planner_fragment_count = 0;
  std::uint32_t generation = 1;
  im2p::gemmini::ScheduleConfig schedule{
      {work.rows, work.columns, work.k},
      {dim, IM2P_OPERAND_BITS},
      {work.plan.tile_i, work.plan.tile_j, work.plan.tile_k, work.rows},
      {work.k, work.columns, work.columns * sizeof(std::uint32_t),
       state.c_stride[state.active_slot], 0},
      true, true, false};
  check(im2p::gemmini::valid_config(schedule), "planner-block schedule rejected work");
  im2p::gemmini::LoopCursor cursor{};
  while (cursor.i < work.rows) {
    const auto planned = im2p::gemmini::plan_loop(schedule, work.rows, cursor);
    const auto outputs = (planned.ip / dim) * (planned.jp / dim);
    if (planned.k == 0) {
      state.output_ids.assign(outputs, false);
      expected_outputs += outputs;
    }
    expected_contexts += planned.fragment_count;
    expected_rows += planned.is * (planned.jp / dim) * (planned.kp / dim);
    expected_scale_reads += planned.scale_rows;
    state.loop(work, planned.i, planned.j, planned.is, planned.js, planned.k,
               planned.k + planned.ks, 0, planned.first, planned.last, generation);
    ++planner_loop_count;
    planner_fragment_count += planned.fragment_count;
    if (planned.final_contribution) {
      check(std::all_of(state.output_ids.begin(), state.output_ids.end(),
                        [](bool done) { return done; }),
            "missing RTL output tile completion");
    }
    im2p::gemmini::advance_loop(schedule, planned, cursor);
    generation = generation % 255 + 1;
  }
'''
    text = text[:start] + replacement + text[end:]
    output_anchor = ('            << " work_count=1 loop_count=" << state.loops - old_loops\n')
    output_replacement = (
        '            << " work_count=1 loop_count=" << state.loops - old_loops\n'
        '            << " planner_loop_count=" << planner_loop_count\n'
        '            << " fragment_count=" << planner_fragment_count\n')
    text = _replace_once(text, output_anchor, output_replacement, 'summary counts')
    # The original fixture makes these coverage assertions for every invocation.
    # Hardening probes cover them at corpus level instead, including very small K.
    text = text.replace(
        '  check(state.reordered_reads > old_reordered, "backing fixture did not exercise out-of-order IDs");\n',
        '')
    text = text.replace(
        '  check(state.read_backpressure_cycles > old_backpressure,\n'
        '        "backing fixture did not exercise read-request backpressure");\n',
        '')
    return text


def _single_case_main(text: str) -> str:
    main_at = text.index('int main(int argc, char **argv)')
    main = r'''int main(int argc, char **argv) {
  try {
    if (argc != 13)
      throw std::invalid_argument(
          "M N K tileI tileJ tileK read even scale write ready_period rmdRaw");
    const auto number = [](const char *value) -> std::uint64_t {
      std::size_t used = 0;
      const auto parsed = std::stoull(value, &used);
      if (value[used] != '\0') throw std::invalid_argument("invalid integer");
      return parsed;
    };
    const auto m = number(argv[1]), n = number(argv[2]), k = number(argv[3]);
    const auto tile_i = number(argv[4]), tile_j = number(argv[5]),
               tile_k = number(argv[6]);
    const bool raw = number(argv[12]) != 0;
    check(m && n && k && tile_i && tile_j && tile_k, "empty hardening request");
    if (raw) check(k <= 32, "RMD raw compact K exceeds 32");

    VerilatedContext context;
    context.commandArgs(argc, argv);
    Dut dut{&context};
    Adapter state{dut, context};
    dut.io_work_valid = 0;
    dut.io_loopDone_ready = 1;
    dut.io_scaleRelease_valid = 0;
    dut.io_readRequest_ready = 1;
    dut.io_readBeat_valid = 0;
    dut.io_writeRequest_ready = 1;
    dut.io_writeCompletion_valid = 0;
    dut.reset = 1;
    for (unsigned edge = 0; edge < 5; ++edge) state.clock();
    dut.reset = 0;
    state.clock();
    state.read_latency = number(argv[7]);
    state.reorder_latency = number(argv[8]);
    state.scale_latency = number(argv[9]);
    state.write_latency = number(argv[10]);
    state.read_ready_period = number(argv[11]);

    FrontendRtlWork work{};
    work.work_id = 1;
    work.host_slot = 0;
    work.row_begin = 0;
    work.rows = static_cast<std::uint32_t>(m);
    work.columns = static_cast<std::uint32_t>(n);
    work.k = static_cast<std::uint32_t>(k);
    work.plan = {m, n, k, tile_i, tile_j, tile_k, m, Mode::full,
                 raw ? WorkKind::rmd_raw : WorkKind::dense_hp1_final};
    work.activations.assign(m * k, 1);
    work.weights.assign(k * n, 1);
    work.carriers.assign(((k + 31) / 32) * n, 0);
    std::vector<std::int32_t> output;
    FrontendRtlTiming timing{};
    const auto status = execute(&state, work, output, timing);
    check(status == IM2P_OK, "hardening execute failed");
    std::cout << "HARDENING_PROBE raw=" << unsigned(raw)
              << " output_values=" << output.size() << '\n';
    dut.final();
    return 0;
  } catch (const std::exception &error) {
    std::cerr << "hardening RTL probe failed: " << error.what() << '\n';
    return 1;
  }
}
#endif
'''
    return text[:main_at] + main


def generate_probe_source(golden_root: Path) -> str:
    source = golden_root / 'cycle_probe_test_ws_rtl.cpp'
    text = source.read_text()
    text = _instrument_events(text)
    text = _planner_block_traversal(text)
    text = _single_case_main(text)
    return text


def build_probe(golden_root: Path, output_root: Path, profile: str) -> Path:
    bits, dim = profile_bits_dim(profile)
    del bits, dim
    profile_root = output_root / profile
    profile_root.mkdir(parents=True, exist_ok=False)
    build = golden_root / 'cycle-rtl/current' / profile
    obj = build / 'obj'
    command = shlex.split((build / 'build-command.txt').read_text())
    flags = shlex.split(command[command.index('-CFLAGS') + 1])
    ldflags = shlex.split(command[command.index('-LDFLAGS') + 1])
    top = command[command.index('--top-module') + 1]
    source_text = generate_probe_source(golden_root)
    root_prefix = 'dut.rootp->' + top + '__DOT__control__DOT__'
    names = {
        'ROOT_EX_FUNCT': '_reservation_io_issue_ex_cmd_cmd_inst_funct',
        'ROOT_MESH_INPUT': 'execute__DOT__mesh__DOT__input_next_row_into_spatial_array',
        'ROOT_MESH_ID': 'execute__DOT__mesh__DOT__matmul_id',
        'ROOT_MESH_ROW': 'execute__DOT__mesh__DOT__fire_counter',
        'ROOT_RAW_COMPLETED': '_execute_io_completed_valid',
        'ROOT_RAW_ROB': '_execute_io_completed_bits',
    }
    root_header = (obj / 'VIM2PGemminiWSHP1RtlTest___024root.h').read_text()
    for token, suffix in names.items():
        symbol = root_prefix + suffix
        if symbol.split('->')[1] not in root_header:
            raise ValueError(f'{profile}: missing passive observer symbol {symbol}')
        source_text = source_text.replace(token, symbol)
    source = profile_root / 'planner-block-probe.cpp'
    source.write_text(source_text)
    executable = profile_root / 'planner-block-probe'
    verilator = subprocess.check_output(['verilator', '-V'], text=True)
    match = re.search(r'VERILATOR_ROOT\s*=\s*(\S+)', verilator)
    if match is None:
        raise ValueError('Verilator include root not reported')
    vroot = Path(match.group(1))
    retained = [obj / name for name in (
        'frontend_rtl_fixture.o', 'rmd_rtl_fixture.o', 'bound_rmd_rtl_fixture.o',
        'VIM2PGemminiWSHP1RtlTest__ALL.a', 'verilated.o', 'verilated_threads.o')]
    weak_time = ['-Wl,-U,__Z13sc_time_stampv'] if platform.system() == 'Darwin' else []
    compile_command = [
        'c++', *flags, '-O2', '-I' + str(ROOT / 'sim/common'), '-isystem', str(obj),
        '-isystem', str(vroot / 'include'), '-isystem', str(vroot / 'include/vltstd'),
        str(source), str(ROOT / 'sim/common/gemmini_schedule.cpp'), *map(str, retained),
        *ldflags, *weak_time, '-pthread', '-o', str(executable),
    ]
    log = profile_root / 'build.log'
    if run_logged(output_root, compile_command, log) != 0:
        raise RuntimeError(f'{profile}: hardening RTL probe build failed')
    provenance = {
        'profile': profile,
        'preserved_probe': str(golden_root / 'cycle_probe_test_ws_rtl.cpp'),
        'preserved_probe_sha256': sha256(golden_root / 'cycle_probe_test_ws_rtl.cpp'),
        'generated_probe_sha256': sha256(source),
        'planner_source': 'sim/common/gemmini_schedule.cpp',
        'planner_source_sha256': sha256(ROOT / 'sim/common/gemmini_schedule.cpp'),
        'rtl_objects': {path.name: sha256(path) for path in retained},
        'production_rtl_changed': False,
    }
    (profile_root / 'provenance.json').write_text(json.dumps(provenance, indent=2) + '\n')
    return executable


def run_probe(output_root: Path, executable: Path, case_name: str, *, m: int, n: int, k: int,
              tile_i: int, tile_j: int, tile_k: int, timing: tuple[int, int, int, int, int],
              raw: bool) -> dict[str, Any]:
    case_root = executable.parent / case_name
    case_root.mkdir(exist_ok=False)
    events = case_root / 'events.csv'
    log = case_root / 'run.log'
    argv = [str(executable), *map(str, (m, n, k, tile_i, tile_j, tile_k, *timing, int(raw)))]
    env = dict(os.environ, IM2P_CYCLE_OBSERVER=str(events))
    returncode = run_logged(output_root, argv, log, env)
    lines = log.read_text().splitlines()
    summary_line = next((line for line in lines if line.startswith('WS RTL ')), None)
    values = ({key: int(value) for key, value in re.findall(r'\b(\w+)=(\d+)\b', summary_line)}
              if summary_line is not None else {})
    rows = list(csv.reader(events.open())) if events.is_file() else []
    selected = sorted((int(row[1]), row[2]) for row in rows
                      if row and row[0].isdigit() and row[2] in SELECTED_EVENTS)
    cases = [row for row in rows if row and row[0] == 'CASE']
    loops = [row for row in rows if row and row[0] == 'LOOP']
    work = [row for row in rows if row and row[0].isdigit() and row[2] == 'work']
    return {
        'returncode': returncode, 'summary': values, 'summary_line': summary_line,
        'selected_events': selected, 'cases': cases, 'loops': loops, 'work_events': work,
        'events_path': str(events), 'log': str(log),
    }


def normalized_model_events(events: list[dict[str, Any]]) -> list[tuple[int, str]]:
    aliases = {'scale_request': 'read_request', 'scale_response': 'read_response'}
    return sorted((int(event['cycle']), aliases.get(str(event['type']), str(event['type'])))
                  for event in events
                  if str(event['type']) in SELECTED_EVENTS | set(aliases))
