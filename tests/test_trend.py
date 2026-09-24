from __future__ import annotations

import pytest

from pytest_leakcheck import (
    LeakDetectedError,
    assert_no_leak,
    ols_r_squared,
    theil_sen_slope,
)


class TestTheilSenSlope:
    def test_perfect_line(self) -> None:
        points = [(float(i), 100.0 * i) for i in range(10)]
        assert theil_sen_slope(points) == pytest.approx(100.0)

    def test_flat_line_is_zero_slope(self) -> None:
        points = [(float(i), 500.0) for i in range(10)]
        assert theil_sen_slope(points) == pytest.approx(0.0)

    def test_resists_a_single_outlier(self) -> None:
        # A perfectly linear 100 bytes/iteration trend, with one point
        # (a 5,000,000-byte spurious dip) badly corrupted.
        points = [(float(i), 100.0 * i) for i in range(20)]
        corrupted = list(points)
        corrupted[10] = (corrupted[10][0], corrupted[10][1] - 5_000_000)

        theil_sen = theil_sen_slope(corrupted)
        # Ordinary least squares, for comparison — this is the thing the
        # Theil-Sen estimator is supposed to resist.
        n = len(corrupted)
        mean_x = sum(p[0] for p in corrupted) / n
        mean_y = sum(p[1] for p in corrupted) / n
        ss_xy = sum((x - mean_x) * (y - mean_y) for x, y in corrupted)
        ss_xx = sum((x - mean_x) ** 2 for x, _ in corrupted)
        ols_slope = ss_xy / ss_xx

        # The true (uncorrupted) slope is 100. Theil-Sen should land much
        # closer to it than OLS does.
        assert abs(theil_sen - 100.0) < abs(ols_slope - 100.0)
        # And concretely: Theil-Sen should still be recognizably close to
        # the true trend despite the corruption.
        assert theil_sen == pytest.approx(100.0, abs=20.0)

    def test_single_point_has_no_slope(self) -> None:
        assert theil_sen_slope([(0.0, 0.0)]) == 0.0

    def test_empty_has_no_slope(self) -> None:
        assert theil_sen_slope([]) == 0.0


class TestOlsRSquared:
    def test_perfect_fit_is_one(self) -> None:
        points = [(float(i), 10.0 * i + 3.0) for i in range(10)]
        assert ols_r_squared(points) == pytest.approx(1.0)

    def test_noisy_fit_is_visibly_worse_than_clean(self) -> None:
        clean = [(float(i), 10.0 * i) for i in range(10)]
        noisy = list(clean)
        noisy[3] = (noisy[3][0], noisy[3][1] + 1000)
        noisy[7] = (noisy[7][0], noisy[7][1] - 800)
        assert ols_r_squared(noisy) < ols_r_squared(clean)


class TestAssertNoLeak:
    def test_flags_a_growing_retention(self) -> None:
        sink: list[bytes] = []

        def leaky() -> None:
            sink.append(bytes(4096))

        with pytest.raises(LeakDetectedError) as exc_info:
            assert_no_leak(
                leaky,
                iterations=40,
                warmup_iterations=5,
                samples=4,
                gc_passes=1,
                threshold_bytes_per_iteration=512,
                label="growing-sink",
            )

        report = exc_info.value.report
        assert report.label == "growing-sink"
        assert report.slope_bytes_per_iteration >= report.threshold_bytes_per_iteration
        assert len(report.samples) == 4

    def test_passes_for_a_non_retaining_call(self) -> None:
        def non_leaky() -> None:
            _ = bytes(4096)

        report = assert_no_leak(
            non_leaky,
            iterations=40,
            warmup_iterations=5,
            samples=4,
            gc_passes=2,
            threshold_bytes_per_iteration=1024,
        )
        assert report.slope_bytes_per_iteration < report.threshold_bytes_per_iteration

    def test_bounded_cache_does_not_trip_once_it_stabilizes(self) -> None:
        # A cache that grows to a fixed size and then stops is not a leak —
        # only the *unbounded* growth phase before it fills up. Warmup
        # should absorb the fill-up.
        cache: dict[int, bytes] = {}

        def bounded_cache_hit() -> None:
            key = len(cache) % 8
            if key not in cache:
                cache[key] = bytes(4096)

        report = assert_no_leak(
            bounded_cache_hit,
            iterations=40,
            warmup_iterations=20,  # enough to fill all 8 slots
            samples=4,
            gc_passes=2,
            threshold_bytes_per_iteration=256,
        )
        assert report.slope_bytes_per_iteration < report.threshold_bytes_per_iteration

    def test_rejects_zero_iterations(self) -> None:
        with pytest.raises(ValueError):
            assert_no_leak(lambda: None, iterations=0)

    def test_minimum_samples_is_enforced(self) -> None:
        report = assert_no_leak(lambda: None, iterations=8, samples=1, gc_passes=1)
        assert len(report.samples) >= 4

    def test_default_warmup_is_ten_percent_minimum_ten(self) -> None:
        calls = 0

        def counter() -> None:
            nonlocal calls
            calls += 1

        assert_no_leak(counter, iterations=200, samples=4, gc_passes=1)
        # 20 warmup (10% of 200) + 200 measured
        assert calls == 220

    def test_report_records_configuration(self) -> None:
        report = assert_no_leak(
            lambda: None,
            iterations=20,
            warmup_iterations=3,
            samples=4,
            gc_passes=2,
            threshold_bytes_per_iteration=999,
        )
        assert report.iterations == 20
        assert report.warmup_iterations == 3
        assert report.gc_passes == 2
        assert report.threshold_bytes_per_iteration == 999

    def test_leaves_tracemalloc_state_as_it_found_it(self) -> None:
        import tracemalloc

        assert not tracemalloc.is_tracing()
        assert_no_leak(lambda: None, iterations=8, samples=4, gc_passes=1)
        assert not tracemalloc.is_tracing()

    def test_does_not_stop_tracemalloc_it_did_not_start(self) -> None:
        import tracemalloc

        tracemalloc.start()
        try:
            assert_no_leak(lambda: None, iterations=8, samples=4, gc_passes=1)
            assert tracemalloc.is_tracing()
        finally:
            tracemalloc.stop()
