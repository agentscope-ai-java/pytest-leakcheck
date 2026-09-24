#!/usr/bin/env python3
"""Run assert_no_leak repeatedly against a known leak and a known non-leak,
and report the *measured* hit rate and false-positive rate.

    .venv/bin/python examples/leak_demo.py

This is what backs the numbers quoted in the README. It is not a unit test
(the numbers are a statistical property, not a guarantee), but it does need
the package installed (`pip install -e '.[dev]'`) to import it.
"""

from __future__ import annotations

import time

from pytest_leakcheck import LeakDetectedError, assert_no_leak

TRIALS = 30
ITERATIONS = 150
SAMPLES = 10
GC_PASSES = 3
THRESHOLD_BYTES_PER_ITERATION = 1024  # the library default
LEAK_SIZE_BYTES = 20 * 1024  # ~20x the default threshold, per call

# Module-level sink that the "leaky" call retains into forever, and the
# "non-leaky" call deliberately does not touch.
_retained: list[bytes] = []


def leaky_call() -> None:
    _retained.append(bytes(LEAK_SIZE_BYTES))


def non_leaky_call() -> None:
    _ = bytes(LEAK_SIZE_BYTES)  # allocated, then immediately dropped


def run_trials(fn: object, trials: int) -> int:
    flagged = 0
    for _ in range(trials):
        _retained.clear()
        try:
            assert_no_leak(  # type: ignore[arg-type]
                fn,  # type: ignore[arg-type]
                iterations=ITERATIONS,
                samples=SAMPLES,
                gc_passes=GC_PASSES,
                threshold_bytes_per_iteration=THRESHOLD_BYTES_PER_ITERATION,
            )
        except LeakDetectedError:
            flagged += 1
    _retained.clear()
    return flagged


def main() -> None:
    print(
        f"Running assert_no_leak {TRIALS}x against a known leak "
        f"and {TRIALS}x against a known non-leak."
    )
    print(
        f"(iterations={ITERATIONS}, samples={SAMPLES}, gc_passes={GC_PASSES}, "
        f"leak size={LEAK_SIZE_BYTES}B/call, threshold={THRESHOLD_BYTES_PER_ITERATION}B/iteration)\n"
    )

    start = time.monotonic()
    hits = run_trials(leaky_call, TRIALS)
    false_positives = run_trials(non_leaky_call, TRIALS)
    elapsed = time.monotonic() - start

    print(f"  hit rate (leak correctly flagged):       {hits}/{TRIALS} ({hits / TRIALS:.0%})")
    print(
        f"  false-positive rate (non-leak flagged):  {false_positives}/{TRIALS} "
        f"({false_positives / TRIALS:.0%})"
    )
    print(f"\n  ({elapsed:.1f}s total)")


if __name__ == "__main__":
    main()
