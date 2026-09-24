"""pytest integration: an ``assert_no_leak`` / ``assert_reclaimed`` fixture
pair, and a ``@pytest.mark.leakcheck(...)`` marker for per-test defaults.

Registered as a ``pytest11`` entry point (see ``pyproject.toml``), so it
activates automatically once the package is installed — no ``-p`` flag or
``conftest.py`` needed.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from ._core import LeakReport, assert_no_leak as _assert_no_leak
from ._reclaim import ReclaimResult, assert_reclaimed as _assert_reclaimed

__all__ = ["assert_no_leak", "assert_reclaimed"]


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "leakcheck(**kwargs): default keyword arguments for the "
        "assert_no_leak/assert_reclaimed fixtures in this test "
        "(e.g. @pytest.mark.leakcheck(iterations=500)).",
    )


def _marker_defaults(request: pytest.FixtureRequest) -> dict[str, Any]:
    marker = request.node.get_closest_marker("leakcheck")
    return dict(marker.kwargs) if marker else {}


@pytest.fixture
def assert_no_leak(
    request: pytest.FixtureRequest,
) -> Callable[..., LeakReport]:
    """Fixture wrapping :func:`pytest_leakcheck.assert_no_leak`.

    Any keyword given to ``@pytest.mark.leakcheck(...)`` on the test
    becomes a default, overridable per call::

        @pytest.mark.leakcheck(iterations=500, threshold_bytes_per_iteration=256)
        def test_handler_does_not_retain(assert_no_leak):
            assert_no_leak(lambda: handle(make_request()))
    """
    defaults = _marker_defaults(request)

    def _fixture(fn: Callable[[], object], **kwargs: Any) -> LeakReport:
        return _assert_no_leak(fn, **{**defaults, **kwargs})

    return _fixture


@pytest.fixture
def assert_reclaimed(
    request: pytest.FixtureRequest,
) -> Callable[..., ReclaimResult]:
    """Fixture wrapping :func:`pytest_leakcheck.assert_reclaimed`.

    Same marker-default behaviour as the ``assert_no_leak`` fixture.
    """
    defaults = _marker_defaults(request)

    def _fixture(factory: Callable[[], object], **kwargs: Any) -> ReclaimResult:
        return _assert_reclaimed(factory, **{**defaults, **kwargs})

    return _fixture
