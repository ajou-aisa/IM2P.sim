from __future__ import annotations

import ctypes as C
import gc
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from sim.cycle import sequence_binding as binding
from sim.cycle.cli import CompactRun, CompactRuns, Event, Hardware, Result, Timing

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def library(tmp_path_factory: pytest.TempPathFactory) -> Path:
    build = tmp_path_factory.mktemp("sequence-binding-build")
    subprocess.run(["cmake", "-S", str(ROOT / "sim/cycle"), "-B", str(build),
                    "-DIM2P_CYCLE_BUILD_TESTS=OFF"],
                   check=True, capture_output=True, text=True, timeout=30)
    subprocess.run(
        ["cmake", "--build", str(build), "--target", "im2p_cycle_model_shared", "-j2"],
        check=True, capture_output=True, text=True, timeout=120,
    )
    return next(build.glob("libim2p_cycle_model.*"))


def test_public_native_abi_layout_and_lifetime(library: Path, tmp_path: Path) -> None:
    # Given: the current public C header and real shared library.
    probe = r"""
    #include "im2p_cycle_sequence.h"
    #include <stddef.h>
    _Static_assert(sizeof(im2p_cycle_sequence_config_t) == 136, "config"); _Static_assert(offsetof(im2p_cycle_sequence_config_t, max_work_ids) == 128, "config tail");
    _Static_assert(sizeof(im2p_cycle_sequence_descriptor_t) == 112, "descriptor"); _Static_assert(offsetof(im2p_cycle_sequence_descriptor_t, compact_runs) == 96, "runs");
    _Static_assert(sizeof(im2p_cycle_sequence_status_t) == 112, "status"); _Static_assert(offsetof(im2p_cycle_sequence_status_t, stop_reason) == 104, "stop");
    _Static_assert(sizeof(im2p_cycle_sequence_tag_state_t) == 64, "tags"); _Static_assert(offsetof(im2p_cycle_sequence_tag_state_t, queue_len) == 48, "queue");
    _Static_assert(sizeof(im2p_cycle_sequence_report_t) == 200, "report"); _Static_assert(offsetof(im2p_cycle_sequence_report_t, counters) == 80, "counters");
    _Static_assert(sizeof(im2p_cycle_sequence_event_t) == 112, "event"); _Static_assert(offsetof(im2p_cycle_sequence_event_t, event) == 32, "event body");
    _Static_assert(sizeof(im2p_cycle_sequence_error_t) == 296, "error"); _Static_assert(offsetof(im2p_cycle_sequence_error_t, partial_counters) == 48, "partial");
    _Static_assert(offsetof(im2p_cycle_sequence_error_t, message) == 168, "error message");
    int main(void) {
      im2p_cycle_sequence_config_t config; im2p_cycle_sequence_config_init(&config);
      config.hardware = (im2p_cycle_hardware_t){8,8,16,32,32,4,4096,1024,16,64,4,2};
      im2p_cycle_sequence_t *handle = NULL; if (im2p_cycle_sequence_create(&config, &handle) != 0 || !handle) return 1;
      if (im2p_cycle_sequence_reset(handle) != 0) return 2;
      im2p_cycle_sequence_status_t status; im2p_cycle_sequence_status_init(&status);
      if (im2p_cycle_sequence_get_status(handle, &status) != 0 || status.generation != 1) return 3;
      if (im2p_cycle_sequence_reset(handle) != 0) return 4;
      if (im2p_cycle_sequence_get_status(handle, &status) != 0 || status.generation != 2) return 5;
      im2p_cycle_sequence_destroy(handle);
      return 0;
    }
    """
    executable = tmp_path / "native-sequence-probe"

    # When: C11 compiles and calls the public lifetime directly.
    subprocess.run(
        ["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-I", str(ROOT / "sim/include"),
         "-L", str(library.parent), "-Wl,-rpath," + str(library.parent),
         "-lim2p_cycle_model", "-x", "c", "-", "-o", str(executable)],
        input=probe, check=True, capture_output=True, text=True, timeout=15,
    )

    # Then: the ABI and explicit reset lifetime remain valid.
    subprocess.run([str(executable)], check=True, capture_output=True, text=True, timeout=15)


def test_python_layout_matches_public_c_abi() -> None:
    # Given: the public v1 ctypes declarations.
    layouts = (
        (Hardware, 48), (Timing, 32), (CompactRun, 16), (CompactRuns, 32),
        (Result, 120), (Event, 80), (binding.Config, 136), (binding.Descriptor, 112),
        (binding.Status, 112), (binding.TagState, 64), (binding.Report, 200),
        (binding.SequenceEvent, 112), (binding.Diagnostic, 296),
    )
    offsets = (
        (binding.Config, "max_work_ids", 128), (binding.Descriptor, "compact_runs", 96),
        (binding.Status, "stop_reason", 104), (binding.TagState, "queue_len", 48),
        (binding.Report, "counters", 80), (binding.SequenceEvent, "event", 32),
        (binding.Diagnostic, "partial_counters", 48), (binding.Diagnostic, "message", 168),
    )

    # When/Then: every size and displacement agrees with the independent C11 probe.
    assert all(C.sizeof(structure) == size for structure, size in layouts)
    assert all(getattr(structure, field).offset == offset for structure, field, offset in offsets)


def test_python_binding_opens_one_native_lifetime(library: Path) -> None:
    # Given: the real library and a resolved A8W8 D16 profile.
    # When: one Python context opens and queries the native session.
    with binding.SequenceSession(library, "a8w8-d16-hp1") as session:
        status = session.status()

    # Then: it owns a reset generation until context exit.
    assert status.generation == 1
    with pytest.raises(binding.SequenceError) as closed:
        session.status()
    assert closed.value.code == binding.Code.INVALID


def test_one_session_object_cannot_reopen_another_native_handle(library: Path) -> None:
    # Given: a Python session object whose first context has closed.
    session = binding.SequenceSession(library, "a8w8-d16-hp1")
    with session:
        first = session._handle.value

    # When/Then: reentry cannot silently create a second native lifetime.
    with pytest.raises(binding.SequenceError) as caught, session:
        pytest.fail("reentry opened a second handle")
    assert first is not None and caught.value.code == binding.Code.INVALID


def test_two_native_handles_keep_independent_status(library: Path) -> None:
    # Given: two Python session objects using the same library and work ID.
    left = binding.SequenceSession(library, "a8w8-d16-hp1")
    right = binding.SequenceSession(library, "a8w8-d16-hp1")

    # When: only the first native handle receives an offer.
    with left, right:
        assert left._handle.value != right._handle.value
        assert left._lib is not right._lib
        assert left.offer(binding.Work(11, 1, 1, 1), 0) == binding.Code.OK

        # Then: the second handle remains cold and can accept the same ID.
        assert right.status().has_pending == 0
        assert right.status().cursor == 0
        assert right.offer(binding.Work(11, 1, 1, 1), 0) == binding.Code.OK


def test_two_copied_offers_share_one_pointer_and_report_epochs(library: Path) -> None:
    # Given: a compact two-run producer work and one live session.
    runs = (binding.Run(0, 0xFFF, 0, 12), binding.Run(1, 0x3FF, 12, 10))
    work = binding.Work(41, 1, 1, 22, tile_k=2, original_k=42, runs=runs, submission=1)
    with binding.SequenceSession(library, "a8w8-d16-hp1") as session:
        pointer = session._handle.value
        native_library = session._lib
        before = session.status().cursor
        assert session.tag_state().cursor == before
        assert session.error().code == 0
        assert session.counters().logical_work_count == 0
        assert session.event_count() == 0
        assert session.status().cursor == before

        # When: the first offer copies runs, then its caller data is overwritten.
        assert session.offer(work, 0) == binding.Code.OK
        object.__setattr__(runs[0], "original_k_mask", 0)
        del work
        gc.collect()
        assert session.advance_until(1) == binding.Code.INCOMPLETE
        assert session.advance_until(1000) == binding.Code.OK
        first = session.pop_report()
        assert isinstance(first, binding.Report)
        assert first.logical_work_id == 41
        assert first.offered_cycle == 0
        assert first.accepted_cycle == 0
        assert first.result_ready_cycle <= first.final_scale_release_cycle <= first.resource_ready_cycle

        # Then: a second offer uses the same native handle and preserves absolute epochs.
        second_offer = session.status().cursor
        assert session.offer(binding.Work(42, 1, 1, 1, submission=1), second_offer) == binding.Code.OK
        assert session.advance_until(second_offer + 1000) == binding.Code.OK
        second = session.pop_report()
        assert isinstance(second, binding.Report)
        assert second.logical_work_id == 42
        assert second.offered_cycle == second_offer
        assert second.accepted_cycle >= second_offer
        assert second.resource_ready_cycle > first.resource_ready_cycle
        assert session._handle.value == pointer and session._lib is native_library
        assert session.counters().logical_work_count == 2
        assert session.reset() == binding.Code.OK
        assert session.status().generation == 2


def test_malformed_native_struct_and_run_extent_leave_status_unchanged(library: Path) -> None:
    # Given: one live handle and intentionally malformed public C views.
    with binding.SequenceSession(library, "a8w8-d16-hp1") as session:
        lib, handle = session._native()
        descriptor = binding.Descriptor()
        lib.im2p_cycle_sequence_descriptor_init(C.byref(descriptor))
        descriptor.logical_work_id = 3
        descriptor.m = descriptor.n = descriptor.k = 1
        before = bytes(session.status())

        # When: the versioned descriptor size is wrong.
        descriptor.struct_size -= 1
        raw = lib.im2p_cycle_sequence_offer(handle, C.byref(descriptor), 0)

        # Then: Python preserves INVALID and the C handle is unchanged.
        with pytest.raises(binding.SequenceError) as caught:
            binding.SequenceSession._checked(raw, "offer")
        assert caught.value.code == binding.Code.INVALID
        assert bytes(session.status()) == before

        # When: a run view declares one span but has no span pointer.
        descriptor.struct_size += 1
        view = CompactRuns(1, C.sizeof(CompactRuns), 32, 1, C.POINTER(CompactRun)())
        descriptor.compact_runs = C.pointer(view)
        raw = lib.im2p_cycle_sequence_offer(handle, C.byref(descriptor), 0)

        # Then: the same typed error leaves the same native state.
        with pytest.raises(binding.SequenceError) as caught:
            binding.SequenceSession._checked(raw, "offer")
        assert caught.value.code == binding.Code.INVALID
        assert bytes(session.status()) == before


def test_normal_backpressure_and_hard_limit_are_distinct(library: Path) -> None:
    # Given: a one-cycle work budget on a live session.
    with binding.SequenceSession(library, "a8w8-d16-hp1", settings=binding.Settings(max_work_cycles=1)) as session:
        assert session.pop_report() == binding.Code.WOULD_BLOCK
        assert session.offer(binding.Work(51, 1, 1, 22, tile_k=2, submission=1), 0) == binding.Code.OK
        assert session.reset() == binding.Code.WOULD_BLOCK
        assert session.offer(binding.Work(52, 1, 1, 1), 0) == binding.Code.WOULD_BLOCK

        # When: advancing exhausts the hard per-work budget.
        with pytest.raises(binding.SequenceError) as caught:
            session.advance_until(100)

        # Then: LIMIT carries a partial diagnostic; subsequent mutation is FAULTED.
        assert caught.value.code == binding.Code.LIMIT
        assert session.status().faulted == 1
        assert session.error().code == binding.Code.LIMIT
        assert session.error().active_work_id == 51
        with pytest.raises(binding.SequenceError) as rejected:
            session.offer(binding.Work(53, 1, 1, 1), 1)
        assert rejected.value.code == binding.Code.FAULTED
        assert session.reset() == binding.Code.OK
        assert session.status().last_discarded_generation == 1


def test_faulted_event_burst_has_no_report(library: Path) -> None:
    # Given: a valid event-recording work with a one-event native buffer.
    with binding.SequenceSession(library, "a8w8-d16-hp1", settings=binding.Settings(max_trace_events=1)) as session:
        assert session.offer(binding.Work(61, 1, 1, 22, tile_k=2, submission=1,
                                          record_events=True), 0) == binding.Code.OK

        # When: buffered events are drained until one edge exceeds capacity.
        fault = None
        for _ in range(1000):
            try:
                code = session.advance_until(1000)
            except binding.SequenceError as error:
                fault = error
                break
            assert code == binding.Code.WOULD_BLOCK
            assert len(session.read_events(1)) == 1

        # Then: FAULTED exposes only a partial diagnostic, never a completed report.
        assert fault is not None and fault.code == binding.Code.FAULTED
        assert session.status().faulted == 1
        assert session.status().has_report == 0
        assert session.error().code == binding.Code.FAULTED


def test_source_identity_open_publication_and_exception_close(library: Path, tmp_path: Path) -> None:
    # Given: a pinned source/library identity and a stale alternative.
    identity = binding.source_identity(library, "a8w8-d16-hp1")
    stale = replace(identity, library_sha256="0" * 64)
    with pytest.raises(binding.SequenceError) as caught, binding.SequenceSession(
        library, identity.profile, expected_identity=stale,
    ):
        pytest.fail("stale identity opened a handle")
    assert caught.value.code == binding.Code.INVALID

    # When: publication uses the original source/library snapshot.
    session = binding.SequenceSession(library, identity.profile, expected_identity=identity)
    with pytest.raises(binding.SequenceError, match="interrupted"), session:
        session.verify_identity()
        session.library = tmp_path / "missing-library"
        with pytest.raises(FileNotFoundError):
            session.verify_identity()
        raise binding.SequenceError(binding.Code.INVALID, "interrupted")

    # Then: exception unwinding destroys the handle and prevents reuse.
    with pytest.raises(binding.SequenceError) as closed:
        session.status()
    assert closed.value.code == binding.Code.INVALID


def test_input_overflow_and_unsupported_profile_remain_typed(library: Path) -> None:
    # Given: a valid session and a request beyond uint64.
    with binding.SequenceSession(library, "a8w8-d16-hp1") as session:
        before = bytes(session.status())

        # When/Then: ctypes cannot silently wrap the offered cycle.
        with pytest.raises(binding.SequenceError) as overflow:
            session.offer(binding.Work(71, 1, 1, 1), 1 << 64)
        assert overflow.value.code == binding.Code.OVERFLOW
        assert bytes(session.status()) == before

    # When/Then: an unsupported profile never opens a native handle.
    with pytest.raises(binding.SequenceError) as unsupported, binding.SequenceSession(
        library, "a2w2-d16-hp1",
    ):
        pytest.fail("unsupported profile opened a handle")
    assert unsupported.value.code == binding.Code.UNSUPPORTED


@pytest.mark.parametrize(("raw_flag", "expected"), [(1 << 32, binding.Code.OVERFLOW), (1, binding.Code.INVALID)])
def test_record_events_narrowing_rejects_without_admission(library: Path, raw_flag: int,
                                                           expected: binding.Code) -> None:
    # Given: one cold native session and a caller-mutated event flag.
    with binding.SequenceSession(library, "a8w8-d16-hp1") as session:
        before = bytes(session.status())
        work = binding.Work(81, 1, 1, 1)
        object.__setattr__(work, "record_events", raw_flag)

        # When: the malformed flag crosses the Python-to-ctypes boundary.
        try:
            code = session.offer(work, 0)
        except binding.SequenceError as error:
            code = error.code

        # Then: the exact code is preserved and native status is byte-identical.
        assert (code, bytes(session.status())) == (expected, before)


@pytest.mark.parametrize(("field", "replacement"), [
    ("timing", (999,)), ("settings", binding.Settings(read_ready_period=7)),
    ("hardware", (999,)), ("identity", None),
])
def test_open_session_snapshots_cannot_be_reassigned(
    library: Path, field: str, replacement: tuple[int, ...] | binding.Settings | None,
) -> None:
    # Given: a fully opened native session with bound configuration and source identity.
    with binding.SequenceSession(library, "a8w8-d16-hp1") as session:
        # When/Then: replacing a public snapshot is rejected before publication.
        with pytest.raises(AttributeError):
            setattr(session, field, replacement)
        session.verify_identity()


def test_resolved_hardware_u32_overflow_rejects_before_create(library: Path,
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: a resolved profile with one U32 timing field past its representable range.
    selection = binding.ProfileSelection(8, 8, 16, binding.Scu.HP1_LEFT_SHIFT)
    resolved = binding.resolve_profile(selection, binding.DEFAULT_CATALOG,
        binding.ROOT / "config/gemmini_host_memory_contracts/a8w8-d16-hp1.json")
    altered = replace(resolved, catalog=replace(resolved.catalog,
        scratchpad_read_delay=(1 << 32) + 4))
    monkeypatch.setattr(binding, "resolve_profile", lambda *_: altered)
    session = binding.SequenceSession(library, selection.name)

    # When: the altered resolver result reaches the Python-to-ctypes boundary.
    with pytest.raises(binding.SequenceError) as caught, session:
        pytest.fail("overflowed hardware opened a handle")

    # Then: the overflow is typed and no native handle was installed.
    assert caught.value.code == binding.Code.OVERFLOW
    assert session._handle.value is None
