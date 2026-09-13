#!/usr/bin/env python3
"""Device-free checks of the patched host gate, callback, and CMake probe."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]


def run(command, cwd, log, expected=0):
    result = subprocess.run([str(x) for x in command], cwd=cwd,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    log.write_text(result.stdout)
    if (result.returncode == 0) != (expected == 0):
        raise RuntimeError(f'{log.name}: exit {result.returncode}\n{result.stdout}')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', type=Path, required=True)
    parser.add_argument('--params', type=Path, required=True)
    parser.add_argument('--generated', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    host, params, generated, out = (getattr(args, x).resolve()
                                    for x in ('host', 'params', 'generated', 'out'))
    out.mkdir(parents=True, exist_ok=False)
    source = host / 'ggml/src/ggml-gemmini'
    includes = [generated, source, host/'ggml/src', host/'ggml/include',
                host/'ggml/src/ggml-gemmini-utils/include', params, params/'include',
                ROOT/'frontend/include', ROOT/'sim/include']
    flags = ['g++', '-std=c++20', '-O0', '-ffunction-sections', '-fdata-sections',
             '-DGGML_GEMMINI_EXECUTION_BACKEND_IM2P_SIM=1', '-DGGML_GEMMINI_ENABLE_RMD=0',
             '-DGGML_GEMMINI_ACTIVATION_BITS=8', '-DGGML_GEMMINI_WEIGHT_BITS=8',
             '-DGGML_GEMMINI_CONFIGURED_DIM=16', '-DIM2P_GEMMINI_FRONTEND_EXPECTED_DIM=16',
             '-DLOG_CYCLE=0', '-DLOG_DEBUG=0', '-DLOG_DUMP=0', '-DOPTION=WS']
    flags += ['-I'+str(x) for x in includes]
    commands = []

    def checked(command, name, expected=0):
        result = run(command, out, out/(name+'.log'), expected)
        commands.append({'argv': [str(x) for x in command], 'returncode': result.returncode,
                         'log': name+'.log'})
        return result

    adapter = source/'ggml-gemmini-im2p.cpp'
    checked(flags+['-c', adapter, '-o', out/'adapter.o'], 'adapter-compile')
    gate = out/'gate.cpp'
    gate.write_text(r'''#include "ggml-gemmini-im2p.hpp"
#include <cassert>
#include <cstdio>
using namespace ggml::gemmini::im2p_adapter;
int main() {
    unsigned checks = 0;
    for (auto bits : {4, 8, 16}) for (auto mode : {PublicMode::full, PublicMode::stripe_pipeline})
    for (auto family : {WeightFamily::h1, WeightFamily::hp1})
    for (auto backend : {ResidualBackend::cpu_direct, ResidualBackend::compact_ws})
    for (bool residual : {false, true}) {
        ExsiaRouteRequest r{true, (uint8_t)bits, (uint8_t)bits, (uint8_t)bits,
            (uint8_t)bits, residual, mode, family, backend, BuildIdentity::im2p_sim_ws};
        auto result = gate_route(r);
        assert(residual ? result.error == Error::unsupported_route : result.ok()); ++checks;
        r.artifact_activation_bits = bits == 4 ? 8 : 4;
        assert(gate_route(r).error == Error::invalid_contract); ++checks;
    }
    for (auto mode : {PublicMode::full, PublicMode::stripe_pipeline}) {
        ExsiaRouteRequest r{true, 8, 8, 8, 8, true, mode, WeightFamily::h0,
                           ResidualBackend::cpu_direct, BuildIdentity::im2p_sim_ws};
        assert(gate_route(r).ok()); ++checks;
        r.residual_backend = ResidualBackend::compact_ws;
        assert(gate_route(r).error == Error::unsupported_route); ++checks;
        r.residual_backend = ResidualBackend::cpu_direct; r.rmd_enabled = false;
        assert(gate_route(r).error == Error::unsupported_route); ++checks;
    }
    assert(gate_route(false, 8, false, false, 8).ok()); ++checks;
    assert(!gate_route(false, 8, true, false, 8).ok()); ++checks;
    std::printf("SCU HOST GATE PASS checks=%u H1_HP1_OFF=accept ON=reject H0_legacy=preserved\n", checks);
}
''')
    checked(flags+[gate, out/'adapter.o', '-Wl,--gc-sections', '-pthread', '-o', out/'gate'], 'gate-link')
    checked([out/'gate'], 'gate-run')
    callback = out/'callback.cpp'
    callback.write_text('''#include "residual/rmd/rmd-im2p-executor.cpp"
#include <cassert>
#include <cstdio>
int main() {
    using namespace ggml::gemmini::rmd::detail;
    Im2pCompactDot dot{}; dot.rows = 1; dot.columns = 2;
    ggml::gemmini::rmd::OutputValue output[2] = {77, 88};
    ProviderContext context{}; context.dot = &dot; context.output = output;
    context.output_row_stride = 2; context.seen.assign(2, 0);
    const int64_t values[2] = {-2147483648LL, 2147483647LL};
    for (uint32_t domain : {1U, 2U, 99U}) {
        assert(write_output(&context, 0, 0, 0, 2, values, domain) == -1);
        assert(output[0] == 77 && output[1] == 88 && context.seen_count == 0);
    }
    assert(write_output(&context, 0, 0, 0, 2, values, IM2P_OUTPUT_LEGACY_FINAL) == 0);
    assert(output[0] == values[0] && output[1] == values[1] && context.seen_count == 2);
    assert(write_output(&context, 0, 0, 0, 2, values, IM2P_OUTPUT_LEGACY_FINAL) == -1);
    std::puts("SCU LEGACY PROVIDER DOMAIN PASS invalid=3 valid=1 duplicate=1");
}
''')
    checked(flags+[callback, '-Wl,--gc-sections', '-pthread', '-o', out/'callback'], 'callback-compile')
    checked([out/'callback'], 'callback-run')

    cmake = (host/'CMakeLists.txt').read_text()
    start = cmake.index('    set(_GGML_GEMMINI_IM2P_PROBE_SOURCE ')
    end = cmake.index('    if(NOT _GGML_GEMMINI_IM2P_ACTIVATION_BITS STREQUAL', start)
    probe = cmake[start:end]
    # Run the actual CMake probe/ABI/revision gate; fake archives supply identity only.
    for name, abi, revision, success in [
        ('valid', 5, 'IM2P_SCU_NUMERICAL_REVISION', True),
        ('old-abi', 4, 'IM2P_SCU_NUMERICAL_REVISION', False),
        ('old-revision', 5, '"signed-wrap-v1"', False),
        ('null-revision', 5, 'nullptr', False),
    ]:
        case = out/name; case.mkdir()
        identity = case/'identity.cpp'
        identity.write_text('#include "im2p_sim.h"\n' +
            f'extern "C" uint32_t im2p_sim_abi_version() {{ return {abi}; }}\n' +
            f'extern "C" const char *im2p_compiled_numerical_semantics_revision() {{ return {revision}; }}\n' +
            '\n'.join(f'extern "C" uint32_t {symbol}() {{ return {value}; }}' for symbol,value in [
                ('im2p_sim_activation_bits',8), ('im2p_sim_activation_storage_bytes',1),
                ('im2p_sim_weight_bits',8), ('im2p_sim_weight_storage_bytes',1), ('im2p_sim_dim',16)])+'\n')
        checked(['g++', '-I'+str(ROOT/'sim/include'), '-c', identity, '-o', case/'identity.o'], name+'-identity')
        checked(['ar', 'rcs', case/'identity.a', case/'identity.o'], name+'-archive')
        (case/'CMakeLists.txt').write_text('cmake_minimum_required(VERSION 3.16)\nproject(scu_identity CXX)\n'+
            f'set(IM2P_SIM_ROOT "{ROOT}")\nset(GGML_GEMMINI_IM2P_SIM_ARCHIVE "{case}/identity.a")\n'+probe)
        result = checked(['cmake', '-S', case, '-B', case/'build'], name+'-probe', 0 if success else 1)
        marker = 'ABI mismatch' if name == 'old-abi' else 'numerical revision mismatch'
        if not success and marker not in result.stdout:
            raise AssertionError(f'{name} failed before the intended gate')
    files = [adapter, source/'residual/rmd/rmd-im2p-executor.cpp', host/'CMakeLists.txt',
             ROOT/'sim/include/im2p_sim.h', generated/'ggml-gemmini-matmul-config.hpp',
             generated/'gemmini_params.h']
    report = {'result':'PASS', 'scope':'host gates and parser only; no numerical RTL or device execution',
              'commands':commands,
              'source_sha256':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}}
    (out/'results.json').write_text(json.dumps(report, indent=2)+'\n')
    print('SCU HOST COMPANION PASS: actual host gate / legacy callback / CMake ABI5+revision')


if __name__ == '__main__':
    main()
