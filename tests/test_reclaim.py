from __future__ import annotations

import gc
import weakref

import pytest

from pytest_leakcheck import ObjectRetainedError, assert_reclaimed


class Widget:
    pass


class SelfReferencing:
    """Deliberately forms a reference cycle, so collecting it depends on
    the cycle collector rather than pure refcounting."""

    def __init__(self) -> None:
        self.self_ref = self


class TestAssertReclaimed:
    def test_confirms_a_genuinely_unreferenced_object(self) -> None:
        result = assert_reclaimed(Widget)
        assert result.passes >= 1
        assert result.elapsed_ms >= 0

    def test_confirms_a_reference_cycle_is_collected(self) -> None:
        # Only the cycle collector (not refcounting alone) can free this.
        result = assert_reclaimed(SelfReferencing)
        assert result.passes >= 1

    def test_raises_when_the_caller_keeps_a_reference(self) -> None:
        held: list[object] = []

        def factory() -> object:
            obj = Widget()
            held.append(obj)  # the leak: a reference escapes the factory
            return obj

        with pytest.raises(ObjectRetainedError) as exc_info:
            assert_reclaimed(factory, gc_passes=3, timeout_s=1.0)

        assert exc_info.value.gc_passes == 3
        held.clear()  # don't leak it into the rest of the test session

    def test_respects_the_timeout(self) -> None:
        held: list[object] = []

        def factory() -> object:
            obj = Widget()
            held.append(obj)
            return obj

        with pytest.raises(ObjectRetainedError) as exc_info:
            assert_reclaimed(factory, gc_passes=1_000_000, timeout_s=0.05)

        assert exc_info.value.timed_out is True
        held.clear()


class TestWeakrefPollingHazard:
    """CPython-specific investigation: the JS sibling of this library found
    that polling `WeakRef.prototype.deref()` in a loop can itself prevent
    the object from ever appearing collected, because the JS spec keeps the
    referent alive "for the remainder of the current job" every time
    `deref()` is called. This class checks — empirically, not from
    documentation alone — whether CPython's `weakref.ref` has the same
    hazard.

    Measured result (see README for the narrative): it does not. Calling a
    `weakref.ref` returns a *temporary* strong reference for the duration
    of that expression only; CPython's refcounting reclaims the object as
    soon as that expression's reference is dropped, same as any other
    temporary. These tests pin that finding down as a regression check —
    if a future CPython ever changed this, they would fail.
    """

    def test_polling_ref_call_in_a_loop_still_sees_it_collected(self) -> None:
        def make() -> object:
            return Widget()

        obj = make()
        ref = weakref.ref(obj)
        del obj

        collected_on_pass = None
        for pass_number in range(1, 21):
            gc.collect()
            # This is exactly the pattern the JS version found hazardous:
            # calling the weak reference inside the same loop that also
            # calls the collector.
            if ref() is None:
                collected_on_pass = pass_number
                break

        assert collected_on_pass == 1, (
            "expected the object to already be gone by the first pass; "
            f"got pass {collected_on_pass!r} — if this fails, CPython's "
            "weakref semantics changed and the README's claim is stale"
        )

    def test_polling_and_finalize_agree_on_which_pass_collects_it(self) -> None:
        # Run both signals on separately-constructed objects, in the same
        # process, and compare which gc.collect() call confirms each.
        collected_via_finalize = False

        def mark() -> None:
            nonlocal collected_via_finalize
            collected_via_finalize = True

        def make_and_register_finalize() -> None:
            obj = Widget()
            weakref.finalize(obj, mark)

        def make_and_get_ref() -> weakref.ReferenceType[Widget]:
            obj = Widget()
            return weakref.ref(obj)

        make_and_register_finalize()
        ref = make_and_get_ref()

        finalize_pass = None
        ref_pass = None
        for pass_number in range(1, 21):
            gc.collect()
            if finalize_pass is None and collected_via_finalize:
                finalize_pass = pass_number
            if ref_pass is None and ref() is None:
                ref_pass = pass_number
            if finalize_pass is not None and ref_pass is not None:
                break

        assert finalize_pass == ref_pass == 1
