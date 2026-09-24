"""pytest-leakcheck: catch memory leaks in ordinary tests, statistically.

Two independent checks:

- :func:`assert_no_leak` — call a function many times and fail if the
  memory it keeps alive trends upward with call count (a Theil-Sen slope
  over :mod:`tracemalloc` samples, not a single before/after diff).
- :func:`assert_reclaimed` — assert that one specific object is actually
  garbage-collected once nothing outside a factory call still references
  it. No threshold; a deterministic yes/no.

Both work as plain callables (no pytest required) and as pytest fixtures
of the same name, once this package is installed — the ``pytest11`` entry
point registers them automatically.
"""

from __future__ import annotations

from ._core import (
    DEFAULT_GC_PASSES,
    LeakDetectedError,
    LeakReport,
    assert_no_leak,
    force_gc,
    ols_r_squared,
    theil_sen_slope,
)
from ._reclaim import ObjectRetainedError, ReclaimResult, assert_reclaimed

__all__ = [
    "DEFAULT_GC_PASSES",
    "LeakDetectedError",
    "LeakReport",
    "ObjectRetainedError",
    "ReclaimResult",
    "assert_no_leak",
    "assert_reclaimed",
    "force_gc",
    "ols_r_squared",
    "theil_sen_slope",
]

__version__ = "0.1.0"
