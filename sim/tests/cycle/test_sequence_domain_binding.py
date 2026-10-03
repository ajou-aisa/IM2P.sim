from __future__ import annotations

import ctypes as C
import subprocess
from pathlib import Path

import pytest

from sim.cycle import sequence_binding as binding

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def library(tmp_path_factory: pytest.TempPathFactory) -> Path:
    build = tmp_path_factory.mktemp("sequence-domain-binding-build")
    subprocess.run(["cmake", "-S", str(ROOT / "sim/cycle"), "-B", str(build),
                    "-DIM2P_CYCLE_BUILD_TESTS=OFF"],
                   check=True, capture_output=True, text=True, timeout=30)
    subprocess.run(
        ["cmake", "--build", str(build), "--target", "im2p_cycle_model_shared", "-j2"],
        check=True, capture_output=True, text=True, timeout=120,
    )
    return next(build.glob("libim2p_cycle_model.*"))


def test_domain_layout_matches_public_c11_header(tmp_path: Path) -> None:
    # Given: the public C11 header, independent of the Python declarations.
    probe = r"""
    #include "im2p_cycle_sequence.h"
    #include <stddef.h>
    _Static_assert(IM2P_CYCLE_SEQUENCE_DOMAIN_ABI_VERSION == 2, "domain version");
    _Static_assert(sizeof(im2p_cycle_sequence_domain_row_t) == 8, "domain row");
    _Static_assert(sizeof(im2p_cycle_sequence_domain_tag_t) == 64, "domain tag");
    _Static_assert(offsetof(im2p_cycle_sequence_domain_tag_t, preload_accumulate) == 56, "accumulate");
    _Static_assert(sizeof(im2p_cycle_sequence_domain_bank_t) == 12, "domain bank");
    _Static_assert(sizeof(im2p_cycle_sequence_domain_snapshot_t) == 528, "domain snapshot");
    _Static_assert(offsetof(im2p_cycle_sequence_domain_snapshot_t, rows) == 44, "domain rows");
    _Static_assert(offsetof(im2p_cycle_sequence_domain_snapshot_t, tags) == 96, "domain tags");
    _Static_assert(offsetof(im2p_cycle_sequence_domain_snapshot_t, banks) == 480, "domain banks");
    _Static_assert(IM2P_CYCLE_SEQUENCE_ROW_PRESSURE_ABI_VERSION == 1, "row pressure version");
    _Static_assert(sizeof(im2p_cycle_sequence_row_pressure_t) == 32, "row pressure");
    _Static_assert(offsetof(im2p_cycle_sequence_row_pressure_t, generation) == 8, "row generation");
    _Static_assert(offsetof(im2p_cycle_sequence_row_pressure_t, cursor) == 16, "row cursor");
    _Static_assert(offsetof(im2p_cycle_sequence_row_pressure_t, row_count) == 24, "row count");
    _Static_assert(offsetof(im2p_cycle_sequence_row_pressure_t, max_row_occupancy) == 28, "row peak");
    """
    layouts = ((binding.DomainRow, 8), (binding.DomainTag, 64),
               (binding.DomainBank, 12), (binding.DomainSnapshot, 528),
               (binding.RowPressure, 32))
    offsets = ((binding.DomainTag, "preload_accumulate", 56),
               (binding.DomainSnapshot, "rows", 44),
               (binding.DomainSnapshot, "tags", 96),
               (binding.DomainSnapshot, "banks", 480),
               (binding.RowPressure, "generation", 8),
               (binding.RowPressure, "cursor", 16),
               (binding.RowPressure, "row_count", 24),
               (binding.RowPressure, "max_row_occupancy", 28))

    # When: C11 compiles those claims against the header.
    subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-I",
                    str(ROOT / "sim/include"), "-x", "c", "-c", "-",
                    "-o", str(tmp_path / "domain-layout.o")],
                   input=probe, check=True, capture_output=True, text=True, timeout=15)

    # Then: ctypes uses the same sizes and offsets.
    assert all(C.sizeof(structure) == size for structure, size in layouts)
    assert all(getattr(structure, field).offset == offset for structure, field, offset in offsets)


def test_domain_snapshot_reads_one_native_lifetime_without_advancing(library: Path) -> None:
    # Given: an A4D16 native session and two queued works with distinct IDs.
    first = binding.Work(0, 80, 81, 96, tile_i=5, tile_j=6, tile_k=6,
                         activation_stride_bytes=96, weight_stride_bytes=81,
                         output_stride_bytes=324, scale_stride_elements=81)
    second = binding.Work(1, 1, 1, 1)
    with binding.SequenceSession(library, "a4w4-d16-hp1") as session:
        cold = session.domain_snapshot()
        assert (cold.abi_version, cold.struct_size, cold.generation, cold.cursor,
                cold.resource_ready, cold.row_count, cold.tag_count,
                cold.max_tag_occupancy, cold.ready_violation_mask) == (2, 528, 1, 0, 1, 0, 0, 0, 0)
        assert bytes(session.domain_snapshot()) == bytes(cold)
        assert session.offer(first, 0) == binding.Code.OK
        assert session.advance_until(1) == binding.Code.INCOMPLETE
        assert session.offer(second, 1) == binding.Code.OK
        active = session.domain_snapshot()
        assert active.cursor == 1 and active.resource_ready == 0
        seen_rows = seen_valid = seen_replacement = seen_accumulation = seen_banks = seen_violation = False
        while not session.status().has_report:
            snapshot = session.domain_snapshot()
            assert snapshot.cursor == session.status().cursor
            assert snapshot.tag_count <= 6 and snapshot.row_count <= 6
            assert snapshot.max_tag_occupancy >= snapshot.tag_count
            seen_rows |= snapshot.row_count > 0
            seen_violation |= snapshot.ready_violation_mask != 0
            seen_banks |= any(bank.pending or bank.queued or bank.pipe_valid_mask for bank in snapshot.banks)
            for tag in snapshot.tags[:snapshot.tag_count]:
                assert (tag.origin_generation, tag.origin_ordinal, tag.origin_work_id) == (1, 1, 0)
                if tag.rob_valid:
                    seen_valid = True
                    assert tag.preload_dst != (1 << 32) - 1
                    seen_replacement |= tag.preload_accumulate == 0
                    seen_accumulation |= tag.preload_accumulate == 1
            assert session.status().cursor < 20_000
            assert session.advance_until(session.status().cursor + 1) == binding.Code.INCOMPLETE
        ready = session.domain_snapshot()
        assert seen_rows and seen_valid and seen_replacement and seen_accumulation and seen_banks
        assert seen_violation
        assert ready.resource_ready == 1 and ready.ready_violation_mask == 0
        assert ready.tag_count and ready.tags[0].rob_valid == 0
        before = bytes(session.status()), bytes(session.counters())
        assert bytes(session.domain_snapshot()) == bytes(ready)
        assert (bytes(session.status()), bytes(session.counters())) == before
        first_report = session.pop_report()
        assert isinstance(first_report, binding.Report) and first_report.logical_work_id == 0
        assert session.advance_until(ready.cursor + 1) == binding.Code.INCOMPLETE
        assert session.status().accepted_work_id == 1
        assert session.advance_until(ready.cursor + 1000) == binding.Code.OK
        second_ready = session.domain_snapshot()
        assert second_ready.max_tag_occupancy >= 2
        assert second_ready.cursor == ready.cursor + 1000
        second_report = session.pop_report()
        assert isinstance(second_report, binding.Report) and second_report.logical_work_id == 1
    with pytest.raises(binding.SequenceError) as closed:
        session.domain_snapshot()
    assert closed.value.code == binding.Code.INVALID


def test_domain_snapshot_rejects_malformed_output_without_mutation(library: Path) -> None:
    # Given: the native V2 initializer and an unchanged live session.
    with binding.SequenceSession(library, "a4w4-d16-hp1") as session:
        lib, handle = session._native()
        snapshot = binding.DomainSnapshot()
        lib.im2p_cycle_sequence_domain_snapshot_init(C.byref(snapshot))
        assert (snapshot.abi_version, snapshot.struct_size) == (2, C.sizeof(snapshot))
        status = bytes(session.status())
        counters = bytes(session.counters())

        # When/Then: each malformed version, size, and null pointer is typed INVALID.
        for field, bad in (("abi_version", 1), ("abi_version", 3),
                           ("struct_size", 527), ("struct_size", 529)):
            setattr(snapshot, field, bad)
            sentinel = bytes(snapshot)
            raw = lib.im2p_cycle_sequence_get_domain_snapshot(handle, C.byref(snapshot))
            with pytest.raises(binding.SequenceError) as caught:
                session._checked(raw, "get_domain_snapshot")
            assert caught.value.code == binding.Code.INVALID
            assert bytes(snapshot) == sentinel
            lib.im2p_cycle_sequence_domain_snapshot_init(C.byref(snapshot))
        sentinel = bytes(snapshot)
        for caller, output in ((None, C.byref(snapshot)), (handle, None)):
            raw = lib.im2p_cycle_sequence_get_domain_snapshot(caller, output)
            with pytest.raises(binding.SequenceError) as caught:
                session._checked(raw, "get_domain_snapshot")
            assert caught.value.code == binding.Code.INVALID
            assert bytes(snapshot) == sentinel
        assert (bytes(session.status()), bytes(session.counters())) == (status, counters)


def test_row_pressure_keeps_generation_peak_after_drain(library: Path) -> None:
    # Given: two independent sessions; only one receives a row-producing work.
    first = binding.Work(0, 80, 81, 96, tile_i=5, tile_j=6, tile_k=6,
                         activation_stride_bytes=96, weight_stride_bytes=81,
                         output_stride_bytes=324, scale_stride_elements=81)
    with binding.SequenceSession(library, "a4w4-d16-hp1") as left, binding.SequenceSession(
        library, "a4w4-d16-hp1",
    ) as right:
        cold = left.row_pressure()
        assert (cold.abi_version, cold.struct_size, cold.generation, cold.cursor,
                cold.row_count, cold.max_row_occupancy) == (1, 32, 1, 0, 0, 0)
        assert bytes(right.row_pressure()) == bytes(cold)
        assert left.offer(first, 0) == binding.Code.OK
        assert left.advance_until(1) == binding.Code.INCOMPLETE
        while left.row_pressure().row_count == 0:
            assert left.status().cursor < 20_000
            assert left.advance_until(left.status().cursor + 1) == binding.Code.INCOMPLETE
        active = left.row_pressure()
        assert active.row_count > 0 and active.max_row_occupancy >= active.row_count
        before = bytes(left.status()), bytes(left.counters())
        assert bytes(left.row_pressure()) == bytes(active)
        assert (bytes(left.status()), bytes(left.counters())) == before
        assert left.advance_until(20_000) == binding.Code.OK
        ready = left.row_pressure()
        assert ready.row_count == 0 and ready.max_row_occupancy >= active.row_count
        assert ready.cursor == left.status().cursor
        assert (right.row_pressure().cursor, right.row_pressure().max_row_occupancy) == (0, 0)
        assert isinstance(left.pop_report(), binding.Report)
        assert left.reset() == binding.Code.OK
        reset = left.row_pressure()
        assert (reset.generation, reset.row_count, reset.max_row_occupancy) == (2, 0, 0)
    with pytest.raises(binding.SequenceError) as closed:
        left.row_pressure()
    assert closed.value.code == binding.Code.INVALID


def test_row_pressure_rejects_bad_output_without_mutation(library: Path) -> None:
    # Given: a live native handle and the public row-pressure initializer.
    with binding.SequenceSession(library, "a4w4-d16-hp1") as session:
        lib, handle = session._native()
        pressure = binding.RowPressure()
        lib.im2p_cycle_sequence_row_pressure_init(C.byref(pressure))
        assert (pressure.abi_version, pressure.struct_size) == (1, C.sizeof(pressure))
        before = bytes(session.status()), bytes(session.counters())

        # When/Then: exact version/size and non-null pointers gate output writes.
        for field, bad in (("abi_version", 0), ("abi_version", 2),
                           ("struct_size", 31), ("struct_size", 33)):
            setattr(pressure, field, bad)
            sentinel = bytes(pressure)
            raw = lib.im2p_cycle_sequence_get_row_pressure(handle, C.byref(pressure))
            with pytest.raises(binding.SequenceError) as caught:
                session._checked(raw, "get_row_pressure")
            assert caught.value.code == binding.Code.INVALID
            assert bytes(pressure) == sentinel
            lib.im2p_cycle_sequence_row_pressure_init(C.byref(pressure))
        sentinel = bytes(pressure)
        for caller, output in ((None, C.byref(pressure)), (handle, None)):
            raw = lib.im2p_cycle_sequence_get_row_pressure(caller, output)
            with pytest.raises(binding.SequenceError) as caught:
                session._checked(raw, "get_row_pressure")
            assert caught.value.code == binding.Code.INVALID
            assert bytes(pressure) == sentinel
        assert (bytes(session.status()), bytes(session.counters())) == before
