"""Deterministic collectibility check for one specific object.

``assert_no_leak`` answers "does the trend across many calls look like
growth" — a statistical, threshold-based question. This module answers a
sharper, binary one about a single object: after the only reference to it
is dropped, does the garbage collector actually reclaim it? There is no
threshold to tune here — either it does, within the allotted passes, or it
doesn't.
"""

from __future__ import annotations

import gc
import time
import weakref
from collections.abc import Callable
from dataclasses import dataclass

__all__ = ["ObjectRetainedError", "ReclaimResult", "assert_reclaimed"]

DEFAULT_GC_PASSES = 6
DEFAULT_TIMEOUT_S = 3.0


@dataclass(frozen=True)
class ReclaimResult:
    """Result of :func:`assert_reclaimed`: which pass confirmed collection,
    and how long that took."""

    passes: int
    elapsed_ms: float


class ObjectRetainedError(AssertionError):
    """Raised by :func:`assert_reclaimed` when the object is still alive
    after every allotted pass (or the timeout), whichever comes first."""

    def __init__(self, gc_passes: int, elapsed_ms: float, timed_out: bool) -> None:
        self.gc_passes = gc_passes
        self.elapsed_ms = elapsed_ms
        self.timed_out = timed_out
        reason = "timed out" if timed_out else f"exhausted {gc_passes} gc passes"
        super().__init__(
            f"object was not collected — {reason} ({elapsed_ms:.1f}ms elapsed)"
        )


def assert_reclaimed(
    factory: Callable[[], object],
    *,
    gc_passes: int = DEFAULT_GC_PASSES,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> ReclaimResult:
    """Assert that the object ``factory()`` returns is collected once
    nothing outside this function still references it.

    ``factory`` is called from its own nested stack frame (see
    ``_make_and_register`` below), not from this function's frame, and its
    return value is never assigned to a local here. That matters: a
    reference sitting in *this* function's frame would stay alive across
    every loop iteration below it, defeating the whole check. This mirrors
    a real bug found while building the JS sibling of this library —
    see the README's "CPython and the WeakRef hazard" section for what we
    found (and didn't find) testing the equivalent pattern in CPython.

    Uses :class:`weakref.finalize` — a callback fired by the collector,
    not a value polled in a loop — as the sole liveness signal.

    :param factory: Called once, with no arguments, from an isolated stack
        frame. Its result must not be reachable from anywhere else, or
        this will (correctly) never see it collected.
    :param gc_passes: Forced ``gc.collect()`` passes attempted before
        giving up.
    :param timeout_s: Hard wall-clock cap, regardless of ``gc_passes``.
    :raises ObjectRetainedError: if the object is never confirmed
        collected.
    :returns: A :class:`ReclaimResult` naming the pass that confirmed it.
    """
    collected = False

    def _mark_collected(_ref: object = None) -> None:
        nonlocal collected
        collected = True

    def _make_and_register() -> None:
        # Isolated frame: `obj` is local to *this* call and goes out of
        # scope the moment it returns, so nothing in assert_reclaimed's own
        # frame (which stays alive across the whole polling loop below)
        # ever holds a reference to it.
        obj = factory()
        weakref.finalize(obj, _mark_collected)

    _make_and_register()

    start = time.monotonic()
    for pass_number in range(1, gc_passes + 1):
        gc.collect()
        if collected:
            elapsed_ms = (time.monotonic() - start) * 1000
            return ReclaimResult(passes=pass_number, elapsed_ms=elapsed_ms)
        if (time.monotonic() - start) > timeout_s:
            elapsed_ms = (time.monotonic() - start) * 1000
            raise ObjectRetainedError(pass_number, elapsed_ms, timed_out=True)

    elapsed_ms = (time.monotonic() - start) * 1000
    raise ObjectRetainedError(gc_passes, elapsed_ms, timed_out=False)
