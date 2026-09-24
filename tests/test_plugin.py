from __future__ import annotations

import pytest

pytest.importorskip("pytest", minversion="7.0")


def test_fixture_passes_for_a_non_leaking_test(pytester: pytest.Pytester) -> None:
    pytester.makepyfile(
        """
        def test_it(assert_no_leak):
            def non_leaky():
                _ = bytes(1024)

            assert_no_leak(non_leaky, iterations=20, warmup_iterations=4, samples=4, gc_passes=1)
        """
    )
    result = pytester.runpytest()
    result.assert_outcomes(passed=1)


def test_fixture_fails_a_leaking_test(pytester: pytest.Pytester) -> None:
    pytester.makepyfile(
        """
        sink = []

        def test_it(assert_no_leak):
            def leaky():
                sink.append(bytes(4096))

            assert_no_leak(
                leaky,
                iterations=40,
                warmup_iterations=5,
                samples=4,
                gc_passes=1,
                threshold_bytes_per_iteration=512,
            )
        """
    )
    result = pytester.runpytest()
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*leak detected*"])


def test_leakcheck_marker_supplies_defaults(pytester: pytest.Pytester) -> None:
    pytester.makepyfile(
        """
        import pytest

        @pytest.mark.leakcheck(iterations=16, warmup_iterations=2, samples=4, gc_passes=1)
        def test_it(assert_no_leak):
            report = assert_no_leak(lambda: None)
            assert report.iterations == 16
            assert report.warmup_iterations == 2
        """
    )
    result = pytester.runpytest()
    result.assert_outcomes(passed=1)


def test_marker_defaults_can_be_overridden_per_call(pytester: pytest.Pytester) -> None:
    pytester.makepyfile(
        """
        import pytest

        @pytest.mark.leakcheck(iterations=16, samples=4, gc_passes=1)
        def test_it(assert_no_leak):
            report = assert_no_leak(lambda: None, iterations=24)
            assert report.iterations == 24
        """
    )
    result = pytester.runpytest()
    result.assert_outcomes(passed=1)


def test_assert_reclaimed_fixture_passes_for_unreferenced_object(
    pytester: pytest.Pytester,
) -> None:
    pytester.makepyfile(
        """
        class Widget:
            pass

        def test_it(assert_reclaimed):
            result = assert_reclaimed(Widget)
            assert result.passes >= 1
        """
    )
    result = pytester.runpytest()
    result.assert_outcomes(passed=1)


def test_assert_reclaimed_fixture_fails_when_referenced(
    pytester: pytest.Pytester,
) -> None:
    pytester.makepyfile(
        """
        class Widget:
            pass

        held = []

        def test_it(assert_reclaimed):
            def factory():
                obj = Widget()
                held.append(obj)
                return obj

            assert_reclaimed(factory, gc_passes=2, timeout_s=1.0)
        """
    )
    result = pytester.runpytest()
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*was not collected*"])


def test_plugin_is_auto_registered_via_entry_point(pytester: pytest.Pytester) -> None:
    pytester.makepyfile(
        """
        def test_fixtures_exist(request):
            assert request.getfixturevalue("assert_no_leak") is not None
            assert request.getfixturevalue("assert_reclaimed") is not None
        """
    )
    # No -p pytest_leakcheck.plugin needed: the pytest11 entry point in
    # pyproject.toml registers it as soon as the package is installed.
    result = pytester.runpytest()
    result.assert_outcomes(passed=1)
