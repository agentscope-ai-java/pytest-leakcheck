# pytest-leakcheck

A pytest fixture (and plain function) for catching memory leaks in ordinary
tests, so a leak fails CI the day it's introduced.

```
Running assert_no_leak 30x against a known leak and 30x against a known non-leak.
(iterations=150, samples=10, gc_passes=3, leak size=20480B/call, threshold=1024B/iteration)

  hit rate (leak correctly flagged):       30/30 (100%)
  false-positive rate (non-leak flagged):  0/30 (0%)

  (0.7s total)
```

That's `.venv/bin/python examples/leak_demo.py`, measured on this machine
(macOS, Apple Silicon, CPython 3.14.7) — see
[What it does not do](#what-it-does-not-do) for what this number does and
doesn't tell you.

## The problem

Most memory leaks are found in production, weeks after the change that
caused them, by someone staring at a memory graph that only goes up. The bug
that caused it usually looked completely ordinary in review: a cache with no
eviction, a listener registered without a matching removal, a request
handler that appends to a module-level list "just for debugging." Nothing in
an ordinary unit test catches this — the test calls the function once,
asserts the return value, and moves on. It has no opinion about whether the
function also quietly kept a reference to its argument.

The obvious fix — measure memory before and after a single call — doesn't
work:

- **One before/after diff is noise, not signal.** GC scheduling, lazy
  imports and module-level caches filling in on first use, and the
  allocator's own arena growth all move the numbers by more than most real
  leaks do in a single call. A one-shot measurement can't tell "grew because
  it leaked" from "grew because that was the first call and something warmed
  up."
- **A leak that grows slowly is invisible in one diff and obvious over a
  hundred calls.** A handler that retains 50 bytes per request looks like
  noise after one call and like a straight line after two hundred.

Because the noise is bigger than a single measurement, the only way to see a
real leak is to watch it happen across many calls and check whether memory
use *trends upward with call count* — which is what this library does.

### The verified gap this fills

- **[`pytest-memray`](https://github.com/bloomberg/pytest-memray)** is
  single-run, snapshot-based: it runs the test body once and flags
  allocations "not freed by the time the test body returns" (its own
  wording). That catches an allocation still live at teardown; it has no
  concept of a *trend across repeated runs*, so it can't distinguish "leaks
  50 bytes per call, forever" from "allocated a 50-byte cache entry once and
  stopped," because it never calls anything more than once to find out.
- **[`pytest-leaks`](https://pypi.org/project/pytest-leaks/)** does repeat
  the test and compares `sys.gettotalrefcount()` before and after — closer
  to the right shape — but `gettotalrefcount()` only exists on a
  **debug build of Python** (`python-dbg` / a `--with-pydebug` build), which
  most projects don't run in CI. The project is also stale: it has an open
  issue reporting it doesn't work on pytest 6, filed against a project that
  hasn't seen a compatible release since, while current pytest is 8.x.

Neither does statistical trend-vs-noise analysis across repeated runs on a
normal, non-debug Python build. That's the gap this package fills.

### This is not memray's full toolkit, either

Beyond `pytest-memray`, the broader [`memray`](https://github.com/bloomberg/memray)
profiler is a different, complementary tool: you already suspect a leak (a
memory graph going up, a bug report) and want to know *what* is holding the
reference, with flame graphs and allocation call stacks. It's deliberately
heavier for that job.

pytest-leakcheck is the opposite shape: a small, dependency-free assertion
that lives inside an ordinary test, whose only job is to fail CI the day a
leak is introduced. If you're diagnosing a leak you already know about,
reach for memray instead — this library will tell you *that* something
grows, not *why*.

## Install

```sh
pip install pytest-leakcheck
```

Python >= 3.11. No runtime dependencies beyond pytest itself (see
[A note on "zero dependencies"](#a-note-on-zero-dependencies) below).
Installing the package registers it as a pytest plugin automatically — no
`-p` flag or `conftest.py` entry needed.

## Use

```python
def test_handler_does_not_retain(assert_no_leak):
    assert_no_leak(lambda: handle(make_request()), iterations=200)
```

`assert_no_leak` and `assert_reclaimed` are both available as pytest
fixtures (shown above) and as plain functions you can call from anywhere,
pytest or not:

```python
from pytest_leakcheck import assert_no_leak

assert_no_leak(lambda: handle(make_request()), iterations=200)
```

For a sharper claim than "the trend across many calls doesn't look like
growth," assert that one specific object is actually collectible:

```python
def test_the_request_object_itself_is_collectible(assert_reclaimed):
    assert_reclaimed(lambda: handle(make_request()))
```

`assert_no_leak` and `assert_reclaimed` answer different questions — see
[Choosing between them](#choosing-between-them).

Per-test defaults via a marker, overridable per call:

```python
@pytest.mark.leakcheck(iterations=500, threshold_bytes_per_iteration=256)
def test_it(assert_no_leak):
    assert_no_leak(lambda: handle(make_request()))          # uses the marker's defaults
    assert_no_leak(lambda: handle(make_request()), iterations=50)  # overrides just this one
```

## The central problem: this measurement is inherently noisy

Most of this library's code exists to make that noise not matter, and it's
worth understanding *how*, not just calling the function.

**Forced GC, more than once.** `assert_no_leak` calls `gc.collect()`
several times (`gc_passes`, default 3) before every measurement, not once.
One pass does not always collect everything — an object finalized during a
collection can itself create new unreachable garbage (a `__del__` dropping
the last reference to something else, or one reference cycle becoming
collectible only after a different cycle nearby has already been torn
down). Repeating the call is the documented way to mop that up.

**A discarded warmup phase.** The first calls to almost any function are
not representative: lazy imports, module-level caches filling in, and
per-process one-time setup all allocate once and then stop. `assert_no_leak`
runs and discards `warmup_iterations` calls (default: 10% of `iterations`,
minimum 10) before it starts measuring, so that one-time cost is never
mistaken for a leak. `tests/test_trend.py::TestAssertNoLeak::test_bounded_cache_does_not_trip_once_it_stabilizes`
exercises this directly: a cache that fills to a fixed size and then stops
growing does not trip the check, because warmup absorbs the fill-up phase.

**A trend across many samples, not a before/after diff.** `assert_no_leak`
calls `fn` `iterations` times, taking `samples` measurements (default 10) at
evenly spaced points along the way — each preceded by the forced,
multi-pass collection above — and fits a slope: bytes retained per
iteration. A genuine leak grows roughly monotonically with call count, so
nearly every pair of samples agrees on the slope's sign and rough size;
noise shows up as one or two samples that disagree with the rest.

The slope itself is the **Theil-Sen estimator**: the median of the slopes
between *every* pair of samples, not an ordinary least-squares fit through
all of them. The reason is concrete — with 10 samples, an OLS line can be
dragged badly off course by a single outlier (a GC pause, an allocator arena
resize landing mid-run), because that one bad point pulls on every term of
the fit. The same outlier can only ever be one endpoint of a minority of the
pairwise slopes that feed the median, so it gets outvoted instead.
`tests/test_trend.py::TestTheilSenSlope::test_resists_a_single_outlier`
demonstrates this directly: a single 5,000,000-byte spurious dip injected
into an otherwise perfectly linear 100-bytes/iteration trend barely moves
the Theil-Sen slope, while the same corruption visibly drags the OLS fit off
course.

**Why `tracemalloc`, not process RSS.** `assert_no_leak` measures
`tracemalloc.get_traced_memory()` — the number of bytes currently allocated
through Python's own memory allocator — rather than OS-level process memory
(`resource.getrusage().ru_maxrss` or similar). Process RSS is influenced by
the allocator's own arena growth and fragmentation, background threads, and
whatever else the process happens to be doing, none of which is the thing
under test. `tracemalloc` instead accounts each allocation exactly, in
bytes, at the point of `malloc`, so "current traced memory went up between
two forced-GC points" is a much more direct signal of "something is being
kept alive" than "the process is a bit bigger than it was."

### `assert_reclaimed`: the crisper signal

`assert_no_leak` says "the trend across many calls doesn't look like
growth." It can't say more than that, because "how much growth is too much"
is a threshold you chose. `assert_reclaimed` asks a sharper, binary question
about one specific object: after you drop your only reference to it, does
the garbage collector actually reclaim it? There is no threshold to tune —
either it does, within the allotted passes, or it doesn't.

It's implemented with `weakref.finalize` — a callback the collector fires,
not a value polled in a loop — as the sole liveness signal, and the object
is constructed in a nested stack frame (`_make_and_register`, inside
`assert_reclaimed`) so that nothing in `assert_reclaimed`'s own frame, which
stays alive across the whole polling loop, ever holds a reference to it.

`assert_reclaimed` only tells you about the one object you hand its
`factory`. It says nothing about aggregate growth from listeners, caches, or
many different retained types — that's what `assert_no_leak` is for.

### CPython and the WeakRef hazard

The JS sibling of this library ([`leak-check`](../leak-check)) found and
fixed a real bug while building its equivalent of `assert_reclaimed`: the
first implementation created a `WeakRef`, dropped the strong reference, and
polled `ref.deref() === undefined` in the same loop that called `gc()`. On
V8, that pattern reliably prevented the object from ever appearing
collected — per the JS spec, calling `deref()` keeps the target alive "for
the remainder of the current job," so polling it every pass re-adds that
hold before the engine gets a chance to actually let go.

We checked whether CPython's `weakref.ref` has the same hazard, rather than
assuming it does or doesn't — see
`tests/test_reclaim.py::TestWeakrefPollingHazard`. **It does not.** Calling
a `weakref.ref` (`ref()`) returns a temporary strong reference scoped to
that one expression; there is no "held alive for the rest of the job"
semantics in CPython's object model. In
`test_polling_ref_call_in_a_loop_still_sees_it_collected`, an object is
constructed, its only strong reference dropped, and a `weakref.ref` to it
is called *inside the same loop as `gc.collect()`* — exactly the pattern
that broke the JS version — and it is reliably seen as collected on the
very first pass, every time this was run. A second test,
`test_polling_and_finalize_agree_on_which_pass_collects_it`, runs both a
polled `weakref.ref` and a `weakref.finalize` callback in the same process
and confirms they agree on which pass collects the object.

Given that, using `weakref.finalize` here is a design choice, not a
workaround for a CPython hazard: it gives a callback-based signal that's
simpler to plumb through the pass-counting and timeout logic than a polling
loop would be, and it keeps the two `assert_reclaimed` implementations
(this one and the JS one) structurally similar for anyone reading both. If
you write your own `weakref.ref`-polling liveness check elsewhere in
CPython, the measurement above says you don't have to worry about this
particular hazard — but see the caveats below: this was checked on one
CPython version (3.14.7, this machine's only interpreter), not swept across
implementations or versions, and PyPy's or GraalPy's semantics were not
checked at all.

### Choosing between them

Use `assert_no_leak` when you want to guard a code path against *any* kind
of retained-memory growth (the usual case — "this handler shouldn't leak").
Use `assert_reclaimed` when you can name one specific object (the request,
a listener, a session) and want a yes/no answer about whether *that* object
is collectible, with no threshold involved. They compose fine in the same
test file and check different things.

## Options

### `assert_no_leak(fn, **options)`

| Option | Default | What it does |
| --- | --- | --- |
| `iterations` | 200 | How many times `fn` is called while measuring. |
| `warmup_iterations` | 10% of `iterations`, min 10 | Calls made and discarded before measurement starts. |
| `samples` | 10 (min 4) | How many measurements are taken across the measured iterations. |
| `gc_passes` | 3 | Forced `gc.collect()` passes before every measurement. |
| `threshold_bytes_per_iteration` | 1024 (1 KiB) | Slope at or above which the trend is reported as a leak. |
| `label` | `None` | Included in error messages and the returned report, for readability only. |

Returns a `LeakReport` (slope, R², samples, thresholds used, config) if it
doesn't look like a leak; raises `LeakDetectedError` (`.report` holds the
same fields) if it does.

### `assert_reclaimed(factory, **options)`

| Option | Default | What it does |
| --- | --- | --- |
| `gc_passes` | 6 | Forced `gc.collect()` passes attempted before giving up. |
| `timeout_s` | 3.0 | Hard wall-clock cap, regardless of `gc_passes`. |

Returns a `ReclaimResult` (`passes`, `elapsed_ms`) — the pass on which the
object was confirmed collected — or raises `ObjectRetainedError`
(`.gc_passes`, `.elapsed_ms`, `.timed_out`) if it never was.

### `@pytest.mark.leakcheck(**options)`

Sets defaults for both the `assert_no_leak` and `assert_reclaimed` fixtures
on a test, overridable per call (see [Use](#use)). Only pass keys the
fixture you're using actually accepts — the marker applies its kwargs to
whichever fixture you call, unfiltered, so mixing `assert_no_leak`-only and
`assert_reclaimed`-only options on a test that uses both will raise a
`TypeError` from whichever call doesn't recognize the extra key.

## Choosing iterations and threshold

`threshold_bytes_per_iteration` is the knob that trades false positives for
false negatives: lower it to catch smaller leaks, at the cost of flagging
more legitimate per-call variance; raise it if your environment is noisy or
the function under test has real (non-leaking) per-call footprint
variation. The default, 1 KiB/iteration, is a starting point, not a
universal answer — tune it against your own function using
`examples/leak_demo.py` as a template.

More `iterations` and `samples` make the Theil-Sen slope more robust, at
the cost of a slower test. See the measured numbers at the top of this
README and the caveats in the next section before treating any of this as
a guarantee.

## What it does not do

- **It is a statistical check, not a proof.** `assert_no_leak`'s verdict is
  "the trend across N calls does/doesn't look like growth above this
  threshold," not a guarantee about all possible inputs or call patterns.
- **We measured one leak shape, on one machine, one Python version — not a
  general reliability number.** The hit-rate/false-positive numbers at the
  top of this README are real, but they're for one leak magnitude
  (retaining ~20 KB/call against a 1 KiB/iteration default threshold — a
  20x-over-threshold leak) on one machine (macOS, Apple Silicon, CPython
  3.14.7). We have not measured behavior near the threshold boundary, on
  Linux CI runners, under different system load, or on other CPython
  versions (CI runs 3.11–3.14, but the numbers in this README were only
  measured on 3.14.7). Don't read the top-line numbers as "this never
  misses a leak" — read them as "this specific, clearly-over-threshold leak
  was caught this often, this many times, on this machine."
- **A leak much smaller than the threshold, or spread over far more
  iterations than you run, will not trip.** If a real leak retains 10
  bytes/iteration and your threshold is 1024, it will pass — correctly, by
  the definition you gave it, but that may not be the definition you
  wanted.
- **`assert_reclaimed` only covers the one object you hand it.** It proves
  nothing about other objects the same code path might retain.
- **This only catches growth `tracemalloc` can see: Python-level
  allocations through CPython's own allocator.** A leak entirely inside a C
  extension that allocates with raw `malloc()` and never touches a Python
  object (so `tracemalloc` never sees the allocation) is invisible to
  `assert_no_leak`. Most realistic leaks in Python code — objects, lists,
  dicts, strings, class instances kept alive by a stray reference — go
  through the tracked allocator and are visible; a leak entirely inside
  unmanaged C memory is not what this tool is for.
- **It does not tell you what's holding the reference.** `LeakDetectedError`
  and `ObjectRetainedError` tell you *that* something is retained, with
  numbers, not which line did it. That's `memray`'s job.
- **No debug build required, unlike `pytest-leaks`.** This is worth stating
  explicitly since it's the whole reason this package exists: everything
  here runs against a normal, non-debug CPython build (this was verified on
  a standard Homebrew 3.14.7 build, not a `--with-pydebug` one).

## A note on "zero dependencies"

The `assert_no_leak` / `assert_reclaimed` functions in
`pytest_leakcheck._core` and `pytest_leakcheck._reclaim` use only the
standard library — no third-party import anywhere in either module, and
they work with no pytest installed at all. `pytest` itself is a declared
dependency only because this package *is* a pytest plugin
(`pytest_leakcheck.plugin`, registered via the `pytest11` entry point) —
the same reason essentially every pytest plugin on PyPI (`pytest-cov`,
`pytest-mock`, `pytest-memray` included) lists `pytest` as a dependency: a
plugin cannot run without the host it plugs into. If you only want the
plain functions and don't want pytest as a dependency at all, vendor
`_core.py` and `_reclaim.py` — they have no imports outside `gc`,
`statistics`, `time`, `tracemalloc`, `weakref`, and `dataclasses`.

## Develop

```sh
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest -q
.venv/bin/python -m mypy src --strict
.venv/bin/python examples/leak_demo.py   # measures the numbers quoted above
```

## License

MIT
