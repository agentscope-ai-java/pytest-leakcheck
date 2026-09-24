"""Trend-based leak detection: run ``fn`` many times, watch whether the
memory it keeps alive grows with call count.

See the package README for the reasoning behind every knob here. In short:
a single before/after measurement cannot tell "grew because it leaked" from
"grew because the allocator hadn't reclaimed something yet" — the only way
to see a real leak is to watch many calls and fit a trend.
"""

from __future__ import annotations

import gc
import statistics
import tracemalloc
from collections.abc import Callable, Sequence
from dataclasses import dataclass

__all__ = [
    "DEFAULT_GC_PASSES",
    "LeakDetectedError",
    "LeakReport",
    "assert_no_leak",
    "force_gc",
    "ols_r_squared",
    "theil_sen_slope",
]

DEFAULT_GC_PASSES = 3


def force_gc(passes: int = DEFAULT_GC_PASSES) -> None:
    """Run ``gc.collect()`` more than once.

    One pass does not always collect everything: an object finalized during
    a collection can itself create new unreachable garbage (a ``__del__``
    that drops the last reference to something else, or a reference cycle
    that becomes collectible only once a *different* cycle has already been
    torn down). A single ``gc.collect()`` call handles the cycles it can see
    in that pass; it does not loop until the heap is quiescent. Calling it
    several times in a row is the documented way to mop up what the first
    pass exposed but did not itself free.
    """
    for _ in range(max(1, passes)):
        gc.collect()


def theil_sen_slope(points: Sequence[tuple[float, float]]) -> float:
    """Median of the slopes between every pair of points.

    This is the Theil-Sen estimator. Unlike an ordinary least-squares fit,
    a single outlier point (a GC pause that lands mid-measurement, a
    just-in-time cache filling on one sample) can only ever be one endpoint
    of a minority of the pairwise slopes that feed the median, so it gets
    outvoted instead of dragging the whole line off course.
    """
    slopes: list[float] = []
    n = len(points)
    for i in range(n):
        x1, y1 = points[i]
        for j in range(i + 1, n):
            x2, y2 = points[j]
            if x2 != x1:
                slopes.append((y2 - y1) / (x2 - x1))
    if not slopes:
        return 0.0
    return statistics.median(slopes)


def ols_r_squared(points: Sequence[tuple[float, float]]) -> float:
    """Ordinary-least-squares R² for the same points, for diagnostics only.

    Not used to make the leak/no-leak decision — only returned in the
    report so a caller can see how noisy the raw samples were. A trend that
    is real but noisy can have a perfectly good Theil-Sen slope and a poor
    R², and that is expected, not a bug.
    """
    n = len(points)
    if n < 2:
        return 0.0
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    ss_xy = sum((x - mean_x) * (y - mean_y) for x, y in points)
    ss_xx = sum((x - mean_x) ** 2 for x in xs)
    if ss_xx == 0:
        return 0.0
    slope = ss_xy / ss_xx
    intercept = mean_y - slope * mean_x
    ss_tot = sum((y - mean_y) ** 2 for y in ys)
    if ss_tot == 0:
        return 1.0
    ss_res = sum((y - (slope * x + intercept)) ** 2 for x, y in points)
    return 1.0 - ss_res / ss_tot


@dataclass(frozen=True)
class LeakReport:
    """Result of :func:`assert_no_leak`, whether it passed or raised.

    ``LeakDetectedError.report`` carries the same object, so callers can
    inspect the numbers either way.
    """

    slope_bytes_per_iteration: float
    r_squared: float
    samples: tuple[tuple[int, int], ...]
    threshold_bytes_per_iteration: float
    iterations: int
    warmup_iterations: int
    gc_passes: int
    label: str | None = None


class LeakDetectedError(AssertionError):
    """Raised by :func:`assert_no_leak` when the fitted trend meets or
    exceeds the threshold."""

    def __init__(self, report: LeakReport) -> None:
        self.report = report
        where = f" ({report.label})" if report.label else ""
        super().__init__(
            f"leak detected{where}: retaining "
            f"{report.slope_bytes_per_iteration:,.1f} bytes/iteration, "
            f">= threshold {report.threshold_bytes_per_iteration:,.1f} "
            f"(R²={report.r_squared:.3f}, samples={report.samples})"
        )


def assert_no_leak(
    fn: Callable[[], object],
    *,
    iterations: int = 200,
    warmup_iterations: int | None = None,
    samples: int = 10,
    gc_passes: int = DEFAULT_GC_PASSES,
    threshold_bytes_per_iteration: float = 1024,
    label: str | None = None,
) -> LeakReport:
    """Call ``fn`` repeatedly and fail if traced memory trends upward with
    call count.

    Plain callable — works with or without pytest. Uses
    :mod:`tracemalloc` to measure bytes currently allocated by Python's own
    allocator (see the README for why, and what this does not cover).

    :param fn: Called with no arguments, ``iterations`` times while
        measuring (plus ``warmup_iterations`` times before that, discarded).
        Its return value is not held onto beyond the statement that calls
        it, so this function does not itself add to whatever ``fn`` retains.
    :param iterations: How many calls are made while measuring.
    :param warmup_iterations: Calls made and discarded before measurement
        starts, so one-time setup (lazy imports, module-level caches, a
        JIT-ish path warming up) isn't mistaken for a leak. Defaults to
        10% of ``iterations``, minimum 10.
    :param samples: How many measurement points are taken across the
        measured iterations (minimum 4 — Theil-Sen needs at least a
        handful of points to be meaningful).
    :param gc_passes: Forced ``gc.collect()`` passes before every
        measurement. See :func:`force_gc`.
    :param threshold_bytes_per_iteration: Slope at or above which the
        trend is reported as a leak.
    :param label: Included in the error message and report, for
        readability only.
    :raises LeakDetectedError: if the fitted slope meets or exceeds the
        threshold.
    :returns: A :class:`LeakReport` if it doesn't look like a leak.
    """
    if iterations < 1:
        raise ValueError("iterations must be >= 1")
    if warmup_iterations is None:
        warmup_iterations = max(10, iterations // 10)
    samples = max(4, samples)

    already_tracing = tracemalloc.is_tracing()
    if not already_tracing:
        tracemalloc.start()
    try:
        for _ in range(warmup_iterations):
            fn()
        force_gc(gc_passes)

        points: list[tuple[int, int]] = []
        completed = 0
        for sample_index in range(samples):
            target = iterations * (sample_index + 1) // samples
            while completed < target:
                fn()
                completed += 1
            force_gc(gc_passes)
            current, _peak = tracemalloc.get_traced_memory()
            points.append((completed, current))
    finally:
        if not already_tracing:
            tracemalloc.stop()

    slope = theil_sen_slope(points)
    r_squared = ols_r_squared(points)
    report = LeakReport(
        slope_bytes_per_iteration=slope,
        r_squared=r_squared,
        samples=tuple(points),
        threshold_bytes_per_iteration=threshold_bytes_per_iteration,
        iterations=iterations,
        warmup_iterations=warmup_iterations,
        gc_passes=gc_passes,
        label=label,
    )
    if slope >= threshold_bytes_per_iteration:
        raise LeakDetectedError(report)
    return report
