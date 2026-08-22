#!/usr/bin/env python3
"""IM2P.sim source-tree and architecture contract checks.

This checker does not replace BSC type elaboration or scheduling. It catches
project-structure regressions and several syntax mistakes that previously
survived lightweight review.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import NoReturn

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
TESTS = ROOT / "tests"
SYNTH = ROOT / "synth"

EXPECTED_SRC = {
    "common/Config.bsv",
    "common/Types.bsv",
    "common/Arithmetic.bsv",
    "array/PE.bsv",
    "array/InputSkew.bsv",
    "array/SystolicArray.bsv",
    "array/SystolicArrayTiled.bsv",
    "array/SystolicArrayInt4x64.bsv",
    "array/SystolicArrayInt8x64.bsv",
    "array/SystolicArrayInt16x64.bsv",
    "array/SystolicEngine.bsv",
    "vector/Scale.bsv",
    "vector/VectorUnit.bsv",
    "accumulator/Accumulator.bsv",
    "control/ExecuteCmd.bsv",
    "control/ExecuteController.bsv",
    "control/WorkTypes.bsv",
    "control/WorkScheduler.bsv",
    "control/MatmulScheduler.bsv",
    "io/HostMemoryTypes.bsv",
    "core/IM2PCore.bsv",
}

EXPECTED_TESTS = {
    "TestVectorUtils.bsv",
    "TbArithmetic.bsv",
    "TbPE.bsv",
    "TbInputSkew.bsv",
    "TbSystolicArray.bsv",
    "TbVectorUnit.bsv",
    "TbAccumulator.bsv",
    "TbExecuteController.bsv",
    "TbIM2PCore.bsv",
    "TbIM2PCoreGrouped.bsv",
    "TbIM2PCoreActivationBuffer.bsv",
    "TbIM2PCoreMatrix.bsv",
    "TbIM2PCoreMultiwidth.bsv",
    "TbIM2PCoreMatrixScale.bsv",
    "TbIM2PCoreExternal.bsv",
    "TbIM2PCoreOutputAddressing.bsv",
    "TbIM2PLookahead.bsv",
    "TbIM2PLookaheadScale.bsv",
    "TbMatmulLookahead.bsv",
    "TbMatmulScheduler.bsv",
    "TbWorkScheduler.bsv",
    "TbSystolicArrayWeightBanks.bsv",
    "TbSystolicEngineWeightBanks.bsv",
    "TbFloatCore.bsv",
    "TbSynthInt8x16.bsv",
    "TbSynthInt8x32.bsv",
    "TbSynthInt8x64.bsv",
}

EXPECTED_SYNTH = {
    "SynthInt8.bsv",
    "SynthInt4x16.bsv",
    "SynthInt8x16.bsv",
    "SynthInt16x16.bsv",
    "SynthInt4x32.bsv",
    "SynthInt8x32.bsv",
    "SynthInt16x32.bsv",
    "SynthInt4x64.bsv",
    "SynthInt8x64.bsv",
    "SynthInt16x64.bsv",
    "SynthFp16.bsv",
    "SynthFp32.bsv",
}

INTEGER_SYNTH_TOPS = (
    "SynthInt8.bsv",
    "SynthInt4x16.bsv",
    "SynthInt8x16.bsv",
    "SynthInt16x16.bsv",
    "SynthInt4x32.bsv",
    "SynthInt8x32.bsv",
    "SynthInt16x32.bsv",
    "SynthInt4x64.bsv",
    "SynthInt8x64.bsv",
    "SynthInt16x64.bsv",
)

PUBLIC_FRONTEND_ROUTES = (
    "q8_0_unpacked_to_h1",
    "q8_h0",
    "q8_h2",
    "q8_h1",
    "q8_hp1",
    "q8_hp2",
    "q8_channel",
    "q8_channel_dense_sidecar",
    "unknown",
)

PUBLIC_FRONTEND_ARTIFACTS = {
    "block_q8_h1",
    "block_q8_hp1",
    "q8_0_unpacked_to_h1",
    "q8_channel",
    "q8_channel_dense_sidecar",
    "q8_channel_row_base",
    "q8_channel_row_count",
    "q8_channel_row_stride",
    "q8_h0",
    "q8_h1",
    "q8_h1_block_count",
    "q8_h1_blocks",
    "q8_h1_count",
    "q8_h1_rows",
    "q8_h2",
    "q8_h2_block_count",
    "q8_h2_blocks",
    "q8_h2_blocks_per_row",
    "q8_h2_count",
    "q8_hp1",
    "q8_hp1_block_count",
    "q8_hp1_blocks",
    "q8_hp1_blocks_per_row",
    "q8_hp1_count",
    "q8_hp2",
    "q8_hp2_block_count",
    "q8_hp2_blocks",
    "q8_hp2_blocks_per_row",
    "q8_hp2_count",
}

STANDARD_PACKAGES = {
    "Assert",
    "FIFOF",
    "FloatingPoint",
    "RegFile",
    "Vector",
}

LEGACY_SYMBOLS = {
    "BankedVectorMem",
    "DirectVectorUnit",
    "VectorAccumulator",
    "OutputCollector",
    "IM2PScaleCore",
    "IM2PDirectCore",
    "KQuantIM2PCore",
    "mkKQuantIM2PCore",
    "KBlockScheduler",
    "MeshWithDelays",
    "blockK",
    "DefaultScaleBlocks",
    "MAX_SCALE_BLOCKS",
    "TooManyScaleBlocks",
    "scaleBlocks",
    "startBlock",
    "blockDone",
    "ScaleControl",
    "ScaleMode",
}

PACKAGE_RE = re.compile(r"\bpackage\s+(\w+)\s*;")
IMPORT_RE = re.compile(r"\bimport\s+(\w+)::\*\s*;")
MODULE_RE = re.compile(r"\bmodule\b.*?\bendmodule\b", re.DOTALL)
VEC_CALL_RE = re.compile(r"\bvec\s*\(")


def relative_files(directory: Path) -> set[str]:
    return {path.relative_to(directory).as_posix() for path in directory.rglob("*.bsv")}


def fail(message: str) -> NoReturn:
    print(f"STATIC CHECK: FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return re.sub(r"//.*", "", text)


def package_name(path: Path) -> str:
    match = PACKAGE_RE.search(path.read_text(encoding="utf-8"))
    if not match:
        fail(f"package declaration missing: {path.relative_to(ROOT)}")
    return match.group(1)


def check_balanced_delimiters(path: Path, text: str) -> None:
    clean = strip_comments(text)
    pairs = {"(": ")", "[": "]", "{": "}"}
    stack: list[tuple[str, int]] = []

    for index, char in enumerate(clean):
        if char in pairs:
            stack.append((char, index))
        elif char in pairs.values():
            if not stack or pairs[stack[-1][0]] != char:
                fail(f"unbalanced delimiter in {path.relative_to(ROOT)}")
            stack.pop()

    if stack:
        fail(f"unbalanced delimiter in {path.relative_to(ROOT)}")

    for begin, end in (
        ("package", "endpackage"),
        ("module", "endmodule"),
        ("interface", "endinterface"),
        ("typeclass", "endtypeclass"),
        ("instance", "endinstance"),
    ):
        begins = len(re.findall(rf"\b{begin}\b", clean))
        ends = len(re.findall(rf"\b{end}\b", clean))
        if begins != ends:
            fail(
                f"{begin}/{end} count mismatch in "
                f"{path.relative_to(ROOT)} ({begins}/{ends})"
            )


def check_module_local_typedefs(path: Path, text: str) -> None:
    clean = strip_comments(text)
    for module_block in MODULE_RE.findall(clean):
        if re.search(r"\btypedef\b", module_block):
            fail(
                "typedef declared inside module body: "
                f"{path.relative_to(ROOT)}; move it to package scope"
            )


def detect_cycle(graph: dict[str, set[str]]) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str, trail: list[str]) -> None:
        if node in visiting:
            fail("dependency cycle: " + " -> ".join(trail + [node]))
        if node in visited:
            return

        visiting.add(node)
        for dependency in sorted(graph.get(node, set())):
            visit(dependency, trail + [node])
        visiting.remove(node)
        visited.add(node)

    for node in sorted(graph):
        visit(node, [])


def require_substrings(path: Path, required: tuple[str, ...]) -> None:
    text = path.read_text(encoding="utf-8")
    for token in required:
        if token not in text:
            fail(f"{path.relative_to(ROOT)} missing required concept: {token}")


def require_regex(path: Path, pattern: str, concept: str) -> None:
    text = path.read_text(encoding="utf-8")
    if re.search(pattern, text, flags=re.DOTALL) is None:
        fail(f"{path.relative_to(ROOT)} missing required contract: {concept}")


def check_integer_width_contracts() -> None:
    config_path = SRC / "common/Config.bsv"
    config = strip_comments(config_path.read_text(encoding="utf-8"))
    accumulator_width = re.findall(
        r"\btypedef\s+(\d+)\s+DefaultAccumulatorWidth\s*;", config
    )
    if accumulator_width != ["64"]:
        fail(
            "DefaultAccumulatorWidth must be exactly one signed-64 production "
            f"definition, got {accumulator_width}"
        )

    for filename in INTEGER_SYNTH_TOPS:
        path = SYNTH / filename
        clean = strip_comments(path.read_text(encoding="utf-8"))
        interface = re.search(
            rf"\bmodule\s+mk{re.escape(path.stem)}\s*\(\s*"
            r"IM2PCoreIfc#\((.*?)\)\s*\)\s*;",
            clean,
            flags=re.DOTALL,
        )
        if interface is None:
            fail(f"integer synth interface declaration missing: synth/{filename}")
        if interface.group(1).count("Int#(DefaultAccumulatorWidth)") != 1:
            fail(
                "integer synth accumulator/output-request lane must be signed "
                f"DefaultAccumulatorWidth (64-bit): synth/{filename}"
            )


def check_frontend_and_output_contracts() -> None:
    frontend_header = ROOT / "frontend/include/im2p_gemmini_frontend.hpp"
    frontend_header_text = strip_comments(frontend_header.read_text(encoding="utf-8"))
    mode_match = re.search(
        r"enum class Mode\s*:\s*uint8_t\s*\{(.*?)\};",
        frontend_header_text,
        flags=re.DOTALL,
    )
    if mode_match is None:
        fail("frontend Mode declaration missing")
    modes = [
        item.strip().split("=")[0].strip()
        for item in mode_match.group(1).split(",")
        if item.strip()
    ]
    if modes != ["full", "stripe_pipeline"]:
        fail(f"frontend modes must be exactly full/stripe_pipeline, got {modes}")

    route_match = re.search(
        r"enum class Route\s*:\s*uint8_t\s*\{(.*?)\};",
        frontend_header_text,
        flags=re.DOTALL,
    )
    if route_match is None:
        fail("public frontend Route declaration missing")
    routes = tuple(
        item.strip().split("=")[0].strip()
        for item in route_match.group(1).split(",")
        if item.strip()
    )
    if routes != PUBLIC_FRONTEND_ROUTES:
        fail(
            "public frontend routes must match the exact A8/Q8 allowlist, "
            f"got {routes}"
        )

    frontend_source = ROOT / "frontend/src/im2p_gemmini_frontend.cpp"
    frontend_artifact_text = frontend_header_text + "\n" + strip_comments(
        frontend_source.read_text(encoding="utf-8")
    )
    artifacts = {
        token.lower()
        for token in re.findall(
            r"\b(?:block_)?q(?:4|8|16)_[a-z0-9_]+\b",
            frontend_artifact_text,
            flags=re.IGNORECASE,
        )
    }
    if artifacts != PUBLIC_FRONTEND_ARTIFACTS:
        fail(
            "public frontend artifacts must match the exact Q8 allowlist\n"
            f"  missing={sorted(PUBLIC_FRONTEND_ARTIFACTS - artifacts)}\n"
            f"  extra={sorted(artifacts - PUBLIC_FRONTEND_ARTIFACTS)}"
        )
    forbidden_precision = re.search(r"(?i)(?:q4_|q16_)", frontend_artifact_text)
    if forbidden_precision:
        fail(
            "Q4/Q16 frontend route or artifact support is TODO, found "
            f"{forbidden_precision.group(0)}"
        )

    simulator_path = ROOT / "sim/src/simulator.rs"
    require_regex(
        simulator_path,
        r"type WriteProviderV2\s*=.*?\*const i32",
        "ABI v2 signed-32 provider callback",
    )
    require_regex(
        simulator_path,
        r"type WriteProviderV3\s*=.*?\*const i64",
        "ABI v3 signed-64 provider callback",
    )
    require_regex(
        simulator_path,
        r"pub fn write_output\(.*?values:\s*&\[i64\].*?"
        r"WriteProvider::V2\(callback\).*?saturating_i64_to_i32.*?"
        r"WriteProvider::V3\(callback\).*?values\.as_ptr\(\)",
        "signed-64 provider transport with saturation only at the V2 boundary",
    )

    require_substrings(
        ROOT / "sim/src/simulator/matmul/memory.rs",
        ("values: &[i64]", "saturating_i64_to_i32"),
    )
    require_substrings(
        ROOT / "sim/src/simulator/striped/provider.rs",
        ("Vec<i64>", "im2p_output_write_request_i64"),
    )

    require_substrings(
        ROOT / "sim/src/matrix.rs",
        ("saturating_i64_to_i32", "i32::MIN", "i32::MAX"),
    )
    require_substrings(
        ROOT / "sim/include/im2p_sim.h",
        (
            "const int64_t *values",
            "ABI v3 keeps raw output storage signed 32-bit",
            "ABI v2 remains frozen and callable",
        ),
    )

    require_substrings(
        frontend_source,
        (
            "q8_h2 is deprecated",
            "q8_hp2 is unsupported",
            "im2p_publish_stripe_v3",
        ),
    )

    for path in (
        ROOT / "README.md",
        ROOT / "frontend/README.md",
        ROOT / "docs/VERIFICATION.md",
    ):
        require_substrings(
            path,
            (
                "FULL",
                "PIPELINE",
                "A8/Q8",
                "Q4",
                "Q16",
                "H2",
                "HP2",
                "mixed precision",
                "ABI v3",
            ),
        )


def check_exsia_integration_contracts() -> None:
    gemmini = ROOT.parent / "llama.cpp-gemmini/ggml/src/ggml-gemmini"
    orchestration = gemmini / "ggml-gemmini.cpp"
    adapter = gemmini / "ggml-gemmini-im2p.cpp"
    exsia = gemmini / "quants/act/exsia/exsia.cpp"
    if not all(path.is_file() for path in (orchestration, adapter, exsia)):
        return

    require_regex(
        adapter,
        r"Result gate_route\(.*?if \(weight_bits != 8\).*?"
        r"if \(exsia\).*?activation_bits == 8.*?rmd_enabled.*?cpu_direct_rmd",
        "ExSIA production A8/Q8-only fail-closed gate",
    )
    require_regex(
        orchestration,
        r"if \(full_requested\).*?install_sink\(\).*?quantize_activation\(src1, args\)"
        r".*?full\.execution->finish\(quantize_ok\).*?"
        r"if \(!pipeline_requested\).*?start_exsia_stripe_pipeline\(args\).*?"
        r"install_sink\(\).*?quantize_activation\(src1, args\).*?"
        r"started\.pipeline->finish\(quantize_ok\)",
        "ExSIA FULL/PIPELINE lifecycle without a third production mode",
    )
    notify_after_fold = re.compile(
        r"mark_folding_committed\(.*?\).*?notify_stripe_ready\(",
        flags=re.DOTALL,
    )
    notify_count = len(notify_after_fold.findall(exsia.read_text(encoding="utf-8")))
    if notify_count != 2:
        fail(
            "ExSIA sequential and local-pipeline paths must each publish "
            f"immediately after folding commit, found {notify_count}"
        )


def main() -> None:
    stray_bsc_artifacts = sorted(
        path.relative_to(ROOT)
        for root in (SRC, TESTS, SYNTH)
        for suffix in ("*.bo", "*.ba")
        for path in root.rglob(suffix)
    )
    if stray_bsc_artifacts:
        fail(f"BSC artifacts outside build/: {stray_bsc_artifacts}")

    actual_src = relative_files(SRC)
    actual_tests = relative_files(TESTS)
    actual_synth = relative_files(SYNTH)

    if actual_src != EXPECTED_SRC:
        fail(
            "src tree mismatch\n"
            f"  missing={sorted(EXPECTED_SRC - actual_src)}\n"
            f"  extra={sorted(actual_src - EXPECTED_SRC)}"
        )
    if actual_tests != EXPECTED_TESTS:
        fail(
            "test tree mismatch\n"
            f"  missing={sorted(EXPECTED_TESTS - actual_tests)}\n"
            f"  extra={sorted(actual_tests - EXPECTED_TESTS)}"
        )
    if actual_synth != EXPECTED_SYNTH:
        fail(
            "synth tree mismatch\n"
            f"  missing={sorted(EXPECTED_SYNTH - actual_synth)}\n"
            f"  extra={sorted(actual_synth - EXPECTED_SYNTH)}"
        )

    if (SRC / "memory").exists():
        fail("src/memory must not exist; Accumulator owns its storage")

    all_bsv = (
        sorted(SRC.rglob("*.bsv"))
        + sorted(TESTS.glob("*.bsv"))
        + sorted(SYNTH.glob("*.bsv"))
    )

    package_to_path: dict[str, Path] = {}
    for path in all_bsv:
        name = package_name(path)
        if name != path.stem:
            fail(f"package/file mismatch: {path.relative_to(ROOT)} declares {name}")
        if name in package_to_path:
            fail(f"duplicate package: {name}")
        package_to_path[name] = path

        text = path.read_text(encoding="utf-8")
        clean = strip_comments(text)

        check_balanced_delimiters(path, text)
        check_module_local_typedefs(path, text)

        if "\t" in text:
            fail(f"tab character found: {path.relative_to(ROOT)}")
        if any(line.rstrip() != line for line in text.splitlines()):
            fail(f"trailing whitespace found: {path.relative_to(ROOT)}")
        if VEC_CALL_RE.search(clean):
            fail(
                f"unsupported vec(...) constructor in {path.relative_to(ROOT)}; "
                "use TestVectorUtils or explicit Vector initialization"
            )

    graph: dict[str, set[str]] = {}
    for name, path in package_to_path.items():
        imports = set(IMPORT_RE.findall(path.read_text(encoding="utf-8")))
        unresolved = imports - STANDARD_PACKAGES - set(package_to_path)
        if unresolved:
            fail(
                f"unresolved imports in {path.relative_to(ROOT)}: {sorted(unresolved)}"
            )
        graph[name] = imports & set(package_to_path)
    detect_cycle(graph)

    # 모든 package는 적어도 하나의 synthesis top 또는 Tb* testbench에서
    # 도달 가능해야 한다. 연결되지 않은 placeholder/helper가 남는 것을 막는다.
    root_packages = {
        name for name in package_to_path if name.startswith(("Synth", "Tb"))
    }
    reachable: set[str] = set()

    def mark_reachable(name: str) -> None:
        if name in reachable:
            return
        reachable.add(name)
        for dependency in graph.get(name, set()):
            mark_reachable(dependency)

    for root_package in root_packages:
        mark_reachable(root_package)

    unused_packages = set(package_to_path) - reachable
    if unused_packages:
        fail(f"packages unreachable from synth/test tops: {sorted(unused_packages)}")

    # Makefile의 실행 목록이 실제 testbench/synthesis package와 일치하는지 확인한다.
    makefile_text = (ROOT / "Makefile").read_text(encoding="utf-8")
    expected_test_tops = {
        f"mk{Path(name).stem}" for name in EXPECTED_TESTS if name.startswith("Tb")
    }
    expected_synth_tops = {f"mk{Path(name).stem}" for name in EXPECTED_SYNTH}

    generated_multiwidth_tops = {
        top
        for top in expected_synth_tops
        if re.fullmatch(r"mkSynthInt(?:4|8|16)x(?:16|32|64)", top)
    }
    for top in sorted(
        expected_test_tops | (expected_synth_tops - generated_multiwidth_tops)
    ):
        if not re.search(rf"\b{re.escape(top)}\b", makefile_text):
            fail(f"Makefile top list missing: {top}")
    if "TOP=mkSynthInt$(1)x$(2)" not in makefile_text:
        fail("Makefile multiwidth top template missing")
    for top in sorted(generated_multiwidth_tops):
        match = re.fullmatch(r"mkSynthInt(4|8|16)x(16|32|64)", top)
        assert match is not None
        target = f"verilator-int{match.group(1)}x{match.group(2)}"
        if target not in makefile_text:
            fail(f"Makefile explicit multiwidth target missing: {target}")
    if not re.search(r"BSC_SIM_COMMON\s*:=[^\n]*\\\n\s*-check-assert", makefile_text):
        fail("Bluesim tests must compile dynamicAssert checks")

    check_integer_width_contracts()
    check_frontend_and_output_contracts()
    check_exsia_integration_contracts()

    source_text = "\n".join(
        strip_comments(path.read_text(encoding="utf-8")) for path in SRC.rglob("*.bsv")
    )
    for symbol in LEGACY_SYMBOLS:
        if symbol in source_text:
            fail(f"legacy symbol reintroduced: {symbol}")

    vector_path = SRC / "vector/VectorUnit.bsv"
    require_substrings(
        vector_path,
        (
            "VectorOp",
            "VectorScaleCapability",
            "transformVectorElement",
            "contributions",
        ),
    )
    vector_clean = strip_comments(vector_path.read_text(encoding="utf-8"))
    for forbidden in (
        "RowAddress",
        "RegFile",
        "accumulatorAdd",
        "accumulate",
        "writeRow",
        "readRow",
    ):
        if forbidden in vector_clean:
            fail(f"VectorUnit owns forbidden accumulator concern: {forbidden}")

    accumulator_path = SRC / "accumulator/Accumulator.bsv"
    require_substrings(
        accumulator_path,
        ("RegFile", "rowAddresses", "commit", "readRow", "writeRow"),
    )
    accumulator_clean = strip_comments(accumulator_path.read_text(encoding="utf-8"))
    for forbidden in ("VectorOp", "VectorMultiply", "VectorShift", "scale"):
        if forbidden in accumulator_clean:
            fail(f"Accumulator knows vector transform concern: {forbidden}")

    scale_path = SRC / "vector/Scale.bsv"
    require_substrings(
        scale_path,
        (
            "VectorScaleCapability",
            "VectorTransform",
            "vectorScalingSupported",
            "transformVectorElement",
        ),
    )

    core_path = SRC / "core/IM2PCore.bsv"
    require_substrings(
        core_path,
        (
            "VectorScaleCapability#(input_t)",
            "boundedCountPadding",
            "accRowsMinusOne",
            "destinationRowAddressesReg",
            "BoundedIndex#(arrayDim) row",
            "configureScaling",
            "ScaleRowRequest",
            "scaleRequestValid",
            "scaleRequestContext",
            "scaleRequestBlock",
            "scaleRequestKind",
            "putScaleRow",
            "currentScaleRowReg",
            "nextScaleRowReg",
            "executionScaleRowReg",
            "startPendingExecution",
            "issueDeferredScaleDemand",
        ),
    )

    systolic_array_path = SRC / "array/SystolicArray.bsv"
    require_substrings(
        systolic_array_path,
        (
            "BoundedIndex#(arrayDim) row",
            "valueOf(arrayDim) - 1",
        ),
    )

    engine_path = SRC / "array/SystolicEngine.bsv"
    require_substrings(
        engine_path,
        (
            "BoundedIndex#(arrayDim) row",
            "controller.currentRowOffsets",
            "controller.noteArrayOutputs",
        ),
    )

    controller_path = SRC / "control/ExecuteController.bsv"
    require_substrings(
        controller_path,
        (
            "committedRows[column] < issuedRows[column]",
            "allCommittedAfterWriteback",
        ),
    )

    core_files = sorted((SRC / "core").glob("*.bsv"))
    if [path.name for path in core_files] != ["IM2PCore.bsv"]:
        fail("core/ must contain exactly one architectural core: IM2PCore")

    core_text = strip_comments(core_files[0].read_text(encoding="utf-8"))
    for constructor in ("mkMatmulScheduler", "mkWorkScheduler"):
        if core_text.count(f"<- {constructor}") != 1:
            fail(f"IM2PCore must instantiate exactly one {constructor}")

    high_level_rust = [
        ROOT / "sim/src/simulator/matmul.rs",
        ROOT / "sim/src/simulator/striped.rs",
    ]
    for path in high_level_rust:
        text = path.read_text(encoding="utf-8")
        if "execute_tile(" in text:
            fail(f"high-level API wraps execute_tile: {path.relative_to(ROOT)}")
        if re.search(r"\b(std::thread|thread::|sleep|Instant|SystemTime)\b", text):
            fail(f"host timing dependency in {path.relative_to(ROOT)}")

    bridge_path = ROOT / "sim/ffi/im2p_verilator.cpp"
    bridge_text = bridge_path.read_text(encoding="utf-8")
    if re.search(r"\buint64_t\s+cycles\s*;", bridge_text):
        fail("simulation bridge owns a forbidden performance cycle counter")
    cycle_getter = re.search(
        r'extern "C" uint64_t im2p_cycle_count\(.*?\n\}',
        bridge_text,
        flags=re.DOTALL,
    )
    if cycle_getter is None or "rtlCycleCount" not in cycle_getter.group(0):
        fail("im2p_cycle_count must read IM2PCore RTL telemetry")

    work_stats_path = ROOT / "sim/src/simulator/matmul/stats.rs"
    work_stats_text = work_stats_path.read_text(encoding="utf-8")
    if re.search(
        r"work_total_cycles:\s*self\.cycles\(\)",
        work_stats_text,
    ):
        fail("WorkStats total cycle source must be RTL per-work telemetry")
    if "last_completed_work_cycles" not in work_stats_text:
        fail("WorkStats must read RTL last-completed-work telemetry")

    cycle_assignment = re.compile(
        r"\b(?:total|compute|drain|wait|overlap)_cycles\s*="
        r"[^;\n]*(?:chrono|steady_clock|high_resolution_clock|elapsed)",
        flags=re.IGNORECASE,
    )
    for path in [
        bridge_path,
        *(ROOT / "sim/src").rglob("*.rs"),
        *(ROOT / "frontend/src").rglob("*.cpp"),
    ]:
        if cycle_assignment.search(path.read_text(encoding="utf-8")):
            fail(
                "wall-clock value assigned to performance cycles: "
                f"{path.relative_to(ROOT)}"
            )

    source_text = "\n".join(
        strip_comments(path.read_text(encoding="utf-8"))
        for path in [*SRC.rglob("*.bsv"), *(ROOT / "sim/src").rglob("*.rs")]
    )
    forbidden_architecture = re.search(
        r"\b(DMA|Dma|Scratchpad|TLB|Rob|ROB|RoCC)\b",
        source_text,
    )
    if forbidden_architecture:
        fail(f"forbidden architecture symbol: {forbidden_architecture.group(1)}")

    for synth in SYNTH.glob("*.bsv"):
        text = synth.read_text(encoding="utf-8")
        if "mkIM2PCore" not in text or "IM2PCoreIfc" not in text:
            fail(f"synth top does not use the single IM2PCore: {synth.name}")
        if strip_comments(text).count("<- mkIM2PCore") != 1:
            fail(f"synth top must instantiate exactly one IM2PCore: {synth.name}")

    makefile_path = ROOT / "Makefile"
    makefile_text = makefile_path.read_text(encoding="utf-8")

    expected_test_tops = {
        "mk" + Path(name).stem for name in EXPECTED_TESTS if name.startswith("Tb")
    }
    expected_synth_tops = {"mk" + Path(name).stem for name in EXPECTED_SYNTH}

    for top in sorted(
        expected_test_tops | (expected_synth_tops - generated_multiwidth_tops)
    ):
        if top not in makefile_text:
            fail(f"Makefile does not include expected top: {top}")

    for target in ("bsv-test-one:", "rtl-one:", "verify:"):
        if target not in makefile_text:
            fail(f"Makefile missing developer target: {target[:-1]}")

    helper_import_count = 0
    for path in TESTS.glob("Tb*.bsv"):
        if "import TestVectorUtils::*;" in path.read_text(encoding="utf-8"):
            helper_import_count += 1
    if helper_import_count < 2:
        fail("TestVectorUtils exists but is not meaningfully shared")

    # Array baseline must remain independent of downstream transform/state layers.
    for path in (SRC / "array").glob("*.bsv"):
        imports = set(IMPORT_RE.findall(path.read_text(encoding="utf-8")))
        forbidden = imports & {"Scale", "VectorUnit", "Accumulator", "IM2PCore"}
        if forbidden:
            fail(
                f"array layer imports downstream package in {path.name}: "
                f"{sorted(forbidden)}"
            )

    print(
        "STATIC CHECK: PASS\n"
        f"  src packages : {len(EXPECTED_SRC)}\n"
        f"  test packages: {len(EXPECTED_TESTS)}\n"
        f"  testbenches  : {len(EXPECTED_TESTS) - 1}\n"
        f"  synth tops   : {len(EXPECTED_SYNTH)}\n"
        "  architecture : SystolicEngine -> VectorUnit -> Accumulator\n"
        "  core         : single IM2PCore with runtime VectorOp scaling"
    )


if __name__ == "__main__":
    main()
