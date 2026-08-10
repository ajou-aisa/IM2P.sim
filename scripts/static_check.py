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
    "array/SystolicEngine.bsv",
    "vector/Scale.bsv",
    "vector/VectorUnit.bsv",
    "accumulator/Accumulator.bsv",
    "control/ExecuteCmd.bsv",
    "control/ExecuteController.bsv",
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
    "TbFloatCore.bsv",
    "TbSynthInt8x16.bsv",
    "TbSynthInt8x32.bsv",
}

EXPECTED_SYNTH = {
    "SynthInt8.bsv",
    "SynthInt8x16.bsv",
    "SynthInt8x32.bsv",
    "SynthFp16.bsv",
    "SynthFp32.bsv",
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
    return {
        path.relative_to(directory).as_posix()
        for path in directory.rglob("*.bsv")
    }


def fail(message: str) -> None:
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


def main() -> None:
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
            fail(
                f"package/file mismatch: {path.relative_to(ROOT)} "
                f"declares {name}"
            )
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
                f"unresolved imports in {path.relative_to(ROOT)}: "
                f"{sorted(unresolved)}"
            )
        graph[name] = imports & set(package_to_path)
    detect_cycle(graph)

    # 모든 package는 적어도 하나의 synthesis top 또는 Tb* testbench에서
    # 도달 가능해야 한다. 연결되지 않은 placeholder/helper가 남는 것을 막는다.
    root_packages = {
        name
        for name in package_to_path
        if name.startswith("Synth") or name.startswith("Tb")
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
        fail(
            "packages unreachable from synth/test tops: "
            f"{sorted(unused_packages)}"
        )

    # Makefile의 실행 목록이 실제 testbench/synthesis package와 일치하는지 확인한다.
    makefile_text = (ROOT / "Makefile").read_text(encoding="utf-8")
    expected_test_tops = {
        f"mk{Path(name).stem}"
        for name in EXPECTED_TESTS
        if name.startswith("Tb")
    }
    expected_synth_tops = {f"mk{Path(name).stem}" for name in EXPECTED_SYNTH}

    for top in sorted(expected_test_tops | expected_synth_tops):
        if not re.search(rf"\b{re.escape(top)}\b", makefile_text):
            fail(f"Makefile top list missing: {top}")

    source_text = "\n".join(
        strip_comments(path.read_text(encoding="utf-8"))
        for path in SRC.rglob("*.bsv")
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
    accumulator_clean = strip_comments(
        accumulator_path.read_text(encoding="utf-8")
    )
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
            "loadScaleBlock",
            "scaleTable",
            "executionScalesReg",
            "selectedBlockWide",
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

    for synth in SYNTH.glob("*.bsv"):
        text = synth.read_text(encoding="utf-8")
        if "mkIM2PCore" not in text or "IM2PCoreIfc" not in text:
            fail(f"synth top does not use the single IM2PCore: {synth.name}")

    makefile_path = ROOT / "Makefile"
    makefile_text = makefile_path.read_text(encoding="utf-8")

    expected_test_tops = {
        "mk" + Path(name).stem
        for name in EXPECTED_TESTS
        if name.startswith("Tb")
    }
    expected_synth_tops = {"mk" + Path(name).stem for name in EXPECTED_SYNTH}

    for top in sorted(expected_test_tops | expected_synth_tops):
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
