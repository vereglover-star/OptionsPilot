"""Torture test for the market-data subsystem.

`scripts/chart_check.py` proves the chart behaves correctly. This proves the
layer underneath it behaves correctly *under abuse* — the conditions that
produced every historical blank-chart report but that a functional test never
reaches: concurrency, repetition, hostile providers, corrupt storage, and
windows nothing can serve.

Two halves:

  **Offline scenarios** (the default; no network, deterministic) drive
  `MarketDataService` against scripted adapters and a real on-disk cache.
  Every scenario asserts an invariant, not a snapshot, so this is safe to run
  in CI and safe to run repeatedly.

  **Live probes** (`--live`) exercise the real provider chain against the real
  internet: rapid symbol/timeframe switching, deep history paging, and every
  timeframe end to end. Skipped by default because it depends on the network
  and on the market's state.

Exit code is 0 only if every scenario holds. Run:

    python scripts/marketdata_stress.py                 # offline, ~20s
    python scripts/marketdata_stress.py --live          # + real providers
    python scripts/marketdata_stress.py --iterations 50 # heavier
"""
from __future__ import annotations

import argparse
import random
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from optionspilot.core.models import Timeframe                     # noqa: E402
from optionspilot.data.adapter import (                            # noqa: E402
    HistoryAdapter, ProviderRateLimited, ProviderSymbolError, ProviderUnavailable,
)
from optionspilot.data.cache import CandleCache                    # noqa: E402
from optionspilot.data.capabilities import (                       # noqa: E402
    IntervalSpec, ProviderCapabilities, YAHOO_CAPABILITIES,
)
from optionspilot.data.diagnostics import (                        # noqa: E402
    OUTCOME_EXHAUSTED, OUTCOME_FAILED, OUTCOME_LIVE, OUTCOME_STALE,
)
from optionspilot.data.registry import ProviderRegistry            # noqa: E402
from optionspilot.data.service import MarketDataService            # noqa: E402

NOW = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)
UNLIMITED = ProviderCapabilities(
    intervals={tf: IntervalSpec(native=str(tf)) for tf in Timeframe},
    extended_hours=True)

_results: list[tuple[bool, str]] = []


def check(cond: bool, label: str) -> None:
    print(("  ok   " if cond else "  FAIL ") + label)
    _results.append((bool(cond), label))


def bars(n: int = 200, timeframe: Timeframe = Timeframe.M5,
         end: datetime = NOW) -> pd.DataFrame:
    idx = pd.date_range(end=end, periods=n,
                        freq=pd.Timedelta(minutes=timeframe.minutes), tz="UTC")
    base = np.arange(n, dtype=float)
    return pd.DataFrame({"open": 100 + base, "high": 101 + base,
                         "low": 99 + base, "close": 100.5 + base,
                         "volume": 1000.0}, index=idx.rename("ts"))


class Chaos(HistoryAdapter):
    """A provider that misbehaves on purpose.

    `mode` selects the misbehaviour: flapping failures, unbounded latency,
    rate limiting, garbage frames, or silent empties. Everything is counted so
    a scenario can assert on how many requests the abuse actually cost.
    """

    def __init__(self, name: str, mode: str = "healthy", *, priority: int = 100,
                 capabilities: ProviderCapabilities | None = None,
                 latency: float = 0.0, timeframe: Timeframe = Timeframe.M5,
                 seed: int = 0):
        self.provider_name = name
        self.provider_priority = priority
        self.capabilities = capabilities or UNLIMITED
        self.min_request_interval = 0.0
        self.mode = mode
        self.latency = latency
        self.timeframe = timeframe
        self.requests = 0
        self._rng = random.Random(seed)
        self._counter = threading.Lock()
        super().__init__()

    def _fetch_native(self, symbol, spec, start, end, prepost):
        with self._counter:
            self.requests += 1
            n = self.requests
        if self.latency:
            time.sleep(self.latency)
        if self.mode == "dead":
            raise ProviderUnavailable(f"{self.provider_name} is down")
        if self.mode == "flapping" and n % 3 != 0:
            raise ProviderUnavailable("intermittent failure")
        if self.mode == "ratelimited":
            raise ProviderRateLimited("quota exceeded", retry_after=30.0)
        if self.mode == "unknown_symbol":
            raise ProviderSymbolError(f"no such symbol {symbol}")
        if self.mode == "empty":
            return pd.DataFrame()
        if self.mode == "garbage":
            frame = bars(50, self.timeframe)
            frame.iloc[10, frame.columns.get_loc("high")] = -1.0    # impossible
            frame.iloc[20, frame.columns.get_loc("close")] = np.inf
            return frame
        if self.mode == "wrong_interval":
            return bars(50, Timeframe.D1)
        if self.mode == "duplicates":
            frame = bars(60, self.timeframe)
            return pd.concat([frame, frame.iloc[20:30]]).sort_index()
        if self.mode == "future":
            return bars(60, self.timeframe, end=NOW + timedelta(days=30))
        return bars(200, self.timeframe)

    def _probe(self) -> None:
        if self.mode == "dead":
            raise ProviderUnavailable("probe failed")


def service(*adapters, cache=None, now=NOW) -> MarketDataService:
    return MarketDataService(ProviderRegistry(list(adapters)), cache=cache,
                             clock=lambda: now)


def ask(svc, symbol="SPY", tf=Timeframe.M5, days=5, **kw):
    return svc.get_history(symbol, tf, NOW - timedelta(days=days), NOW, **kw)


# ── offline scenarios ────────────────────────────────────────────────────────

def scenario_rapid_switching(iterations: int) -> None:
    """A user hammering symbols and timeframes must never wedge the service,
    and must never spend more requests than distinct views."""
    print("\nrapid symbol + timeframe switching")
    adapter = Chaos("primary")
    svc = service(adapter)
    symbols = ["SPY", "QQQ", "IWM", "NVDA", "AAPL"]
    tfs = [Timeframe.M1, Timeframe.M5, Timeframe.M15, Timeframe.H1, Timeframe.D1]
    rng = random.Random(1234)
    outcomes = set()
    for _ in range(iterations * 10):
        adapter.timeframe = tf = rng.choice(tfs)
        result = ask(svc, rng.choice(symbols), tf)
        outcomes.add(result.outcome)
        if result.outcome not in (OUTCOME_LIVE, "memo"):
            break
    check(outcomes <= {OUTCOME_LIVE, "memo"},
          f"{iterations * 10} rapid switches all served ({sorted(outcomes)})")
    check(adapter.requests <= len(symbols) * len(tfs),
          f"requests bounded by distinct views ({adapter.requests} for "
          f"{len(symbols) * len(tfs)} views)")
    check(len(svc._mem) <= len(symbols) * len(tfs), "memo did not grow unbounded")


def scenario_repeated_refresh(iterations: int) -> None:
    """The live poll: 100 refreshes of one view must cost one request while
    the TTL holds, and must never return a different bar count."""
    print("\nrepeated refresh of one view")
    adapter = Chaos("primary")
    svc = service(adapter)
    counts = {ask(svc).bars for _ in range(iterations * 10)}
    check(len(counts) == 1, f"every refresh returned the same frame ({counts})")
    check(adapter.requests == 1,
          f"{iterations * 10} refreshes cost 1 upstream request "
          f"({adapter.requests})")


def scenario_concurrency(iterations: int) -> None:
    """Parallel scans + chart loads must deduplicate, not stampede."""
    print("\nconcurrent requests")
    adapter = Chaos("primary", latency=0.05)
    svc = service(adapter)
    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(lambda _: ask(svc), range(64)))
    check(all(r.bars == 200 for r in results), "all 64 concurrent callers served")
    check(adapter.requests == 1,
          f"64 concurrent callers shared one fetch ({adapter.requests})")

    # different keys must NOT serialize behind each other
    adapter2 = Chaos("primary", latency=0.05)
    svc2 = service(adapter2)
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=16) as pool:
        pool.map(lambda i: ask(svc2, f"SYM{i}"), range(16))
    elapsed = time.monotonic() - started
    check(elapsed < 0.05 * 16 * 0.7,
          f"distinct symbols fetched in parallel ({elapsed:.2f}s for 16 x 50ms)")


def scenario_provider_failures(iterations: int) -> None:
    """Every hostile-provider shape must fail over rather than surface."""
    print("\nhostile providers")
    # Unusable answers must fail over...
    for mode in ("dead", "empty", "ratelimited", "unknown_symbol",
                 "wrong_interval", "future"):
        bad = Chaos("bad", mode, priority=10)
        good = Chaos("good", priority=20)
        result = ask(service(bad, good))
        check(result.provider == "good" and result.bars == 200,
              f"a '{mode}' primary fails over to the healthy secondary")

    # ...but a REPAIRABLE answer must be repaired and used, not thrown away.
    # Two glitch bars in fifty is a normal upstream hiccup; failing over would
    # cost a request and gain nothing, and would eventually trip the breaker on
    # a provider that is working fine.
    dirty = Chaos("dirty", "garbage", priority=10)
    result = ask(service(dirty, Chaos("good", priority=20)))
    frame = result.frame
    check(result.provider == "dirty" and 45 <= result.bars < 50
          and (frame[["open", "high", "low", "close"]] > 0).all().all()
          and np.isfinite(frame.to_numpy()).all(),
          f"a repairable frame is repaired and served, not failed over "
          f"({result.bars} bars kept, quality "
          f"{result.report.score if result.report else '?'})")

    every = [Chaos(f"bad{i}", "dead", priority=10 + i) for i in range(3)]
    result = ask(service(*every))
    check(result.outcome == OUTCOME_FAILED and result.frame.empty,
          "all providers dead -> an explicit failure, not a silent empty")
    check("bad0" in result.message,
          f"the failure explains itself ({result.message[:70]}...)")


def scenario_flapping(iterations: int) -> None:
    """An intermittently-failing provider must still deliver, via retry."""
    print("\nflapping provider")
    served = 0
    for _ in range(iterations):
        flaky = Chaos("flaky", "flapping", seed=1)
        good = Chaos("good", priority=200)
        if ask(service(flaky, good)).bars == 200:
            served += 1
    check(served == iterations,
          f"{served}/{iterations} flapping-provider requests still served data")


def scenario_circuit_breaker(iterations: int) -> None:
    """A dead provider must stop being asked, and must come back by itself."""
    print("\ncircuit breaker + self-healing")
    dead = Chaos("dead", "dead", priority=10)
    svc = service(dead)
    for _ in range(10):
        svc.invalidate()
        ask(svc)
    check(dead.requests <= 6,
          f"a dead provider stopped being asked after the breaker tripped "
          f"({dead.requests} requests for 10 attempts)")
    check(svc.registry.candidates("SPY", Timeframe.M5) == [],
          "the dead provider is out of rotation")
    dead.mode = "healthy"
    svc.registry.force_open("dead", -1.0)          # cooldown lapsed
    svc.invalidate()
    check(ask(svc).outcome == OUTCOME_LIVE,
          "a recovered provider is picked up automatically, with no restart")


def scenario_exhaustion(iterations: int) -> None:
    """Scrolling past the provider floor must terminate, cost nothing, and say
    so — the root cause of 'history retries forever'."""
    print("\nhistory exhaustion")
    adapter = Chaos("yahoo", capabilities=YAHOO_CAPABILITIES)
    svc = service(adapter)
    for _ in range(iterations * 5):
        result = svc.get_history("SPY", Timeframe.M5, NOW - timedelta(days=300),
                                 NOW - timedelta(days=120))
    check(result.outcome == OUTCOME_EXHAUSTED and result.exhausted,
          "an out-of-depth window reports 'exhausted'")
    check(adapter.requests == 0,
          f"{iterations * 5} impossible requests cost 0 upstream calls "
          f"({adapter.requests})")
    check(result.earliest_available is not None,
          f"the real floor is reported ({result.earliest_available})")


def scenario_offline(iterations: int, tmp: Path) -> None:
    """Total outage with a warm cache must degrade to stale bars, and the
    trading path must still fail closed."""
    print("\noffline / total outage")
    cache = CandleCache(tmp / "offline.db")
    cache.store("SPY", Timeframe.M5, bars(200, end=NOW - timedelta(days=3)),
                provider="yahoo")
    svc = service(Chaos("dead", "dead"), cache=cache)
    display = ask(svc, allow_stale=True)
    check(display.outcome == OUTCOME_STALE and display.bars > 0,
          "a display surface gets clearly-flagged stale bars")
    strict = ask(svc, allow_stale=False)
    check(strict.frame.empty and not strict.stale,
          "the trading path still fails closed (no stale data, ever)")
    cache.close()


def scenario_slow_provider(iterations: int) -> None:
    """A slow provider must not block a faster one behind it, and must not
    make every later request slow."""
    print("\nslow provider")
    slow = Chaos("slow", "dead", priority=10, latency=0.15)
    fast = Chaos("fast", priority=20)
    svc = service(slow, fast)
    started = time.monotonic()
    for _ in range(6):
        svc.invalidate()
        ask(svc)
    elapsed = time.monotonic() - started
    check(ask(svc, days=6).provider == "fast", "the fast provider still answers")
    check(elapsed < 6 * 0.15 * 4,
          f"the slow provider's cost is bounded by the breaker ({elapsed:.2f}s)")


def scenario_corrupt_cache(iterations: int, tmp: Path) -> None:
    """A corrupt cache must degrade to a cold cache, never crash the app."""
    print("\ncorrupt cache")
    path = tmp / "corrupt.db"
    path.write_bytes(b"absolutely not a sqlite database" * 64)
    try:
        cache = CandleCache(path)
        svc = service(Chaos("primary"), cache=cache)
        result = ask(svc)
        check(result.bars == 200, "the app still serves data with a corrupt cache")
        check(cache.stats()["rebuilds"] == 1, "the corrupt file was rebuilt once")
        check(any(p.name.startswith("corrupt.db.corrupt-") for p in tmp.iterdir()),
              "the damaged file was quarantined, not deleted")
        cache.close()
    except Exception as exc:  # noqa: BLE001
        check(False, f"a corrupt cache crashed the service: {exc}")


def scenario_bad_data(iterations: int) -> None:
    """Garbage must never reach a chart, whatever shape it arrives in."""
    print("\nmalformed data")
    for mode, label in (("duplicates", "duplicate bars"),
                        ("future", "future timestamps"),
                        ("garbage", "impossible prices"),
                        ("wrong_interval", "wrong interval")):
        adapter = Chaos("only", mode)
        result = ask(service(adapter))
        frame = result.frame
        if frame.empty:
            check(True, f"{label}: rejected outright")
            continue
        clean = (frame.index.is_unique and frame.index.is_monotonic_increasing
                 and np.isfinite(frame.to_numpy()).all()
                 and (frame[["open", "high", "low", "close"]] > 0).all().all()
                 and frame.index.max() <= pd.Timestamp(NOW) + pd.Timedelta(minutes=2))
        check(clean, f"{label}: sanitized before reaching the caller")


def scenario_memory(iterations: int) -> None:
    """A long session browsing many symbols must plateau, not grow."""
    print("\nmemory / unbounded growth")
    adapter = Chaos("primary")
    svc = service(adapter)
    for i in range(1200):
        ask(svc, f"SYM{i}")
    from optionspilot.data.service import MEM_CACHE_MAX
    check(len(svc._mem) <= MEM_CACHE_MAX,
          f"memo capped at {MEM_CACHE_MAX} ({len(svc._mem)} entries after 1200 symbols)")
    from optionspilot.data.diagnostics import MAX_TRACES
    check(len(svc.diagnostics._traces) <= MAX_TRACES,
          f"diagnostics ring capped at {MAX_TRACES} "
          f"({len(svc.diagnostics._traces)} traces)")


def scenario_thread_safety(iterations: int, tmp: Path) -> None:
    """Cache, registry and diagnostics are touched from many threads at once."""
    print("\nthread safety")
    cache = CandleCache(tmp / "threads.db")
    svc = service(Chaos("a", "flapping", priority=10, seed=7),
                  Chaos("b", priority=20), cache=cache)
    errors: list[str] = []

    def worker(i: int) -> None:
        try:
            for _ in range(6):
                svc.get_history(f"SYM{i % 7}",
                                [Timeframe.M5, Timeframe.M15, Timeframe.H1][i % 3],
                                NOW - timedelta(days=5), NOW, allow_stale=bool(i % 2))
                svc.health()
                svc.diagnostics.recent(5)
        except Exception:  # noqa: BLE001
            errors.append(traceback.format_exc())

    with ThreadPoolExecutor(max_workers=12) as pool:
        list(pool.map(worker, range(48)))
    check(not errors, f"48 threads x 6 requests raised nothing ({len(errors)} errors)")
    if errors:
        print(errors[0][:800])
    cache.close()


def scenario_diagnostics_integrity(iterations: int) -> None:
    """Every request must leave exactly one trace, whatever happened."""
    print("\ndiagnostics integrity")
    svc = service(Chaos("a", "flapping", priority=10, seed=3),
                  Chaos("b", "dead", priority=20))
    for i in range(40):
        svc.invalidate()
        ask(svc, f"SYM{i % 5}")
    summary = svc.diagnostics.summary()
    check(summary["total_requests"] == 40,
          f"40 requests -> 40 traces ({summary['total_requests']})")
    check(sum(summary["outcomes"].values()) == 40,
          "every trace has exactly one terminal outcome")
    import json
    check(bool(json.dumps(svc.diagnostics.recent(50))),
          "the whole trace ring is JSON-serializable")


# ── live probes ──────────────────────────────────────────────────────────────

def live_probes() -> None:
    """The real provider chain against the real internet."""
    from optionspilot.data import build_provider
    from optionspilot.orchestrator import WINDOW_DAYS

    print("\nLIVE: real providers")
    now = datetime.now(timezone.utc)
    provider = build_provider(None)

    served = []
    for tf in Timeframe:
        result = provider.get_history("SPY", tf,
                                      now - timedelta(days=WINDOW_DAYS[tf]), now)
        served.append((str(tf), result.outcome, result.bars,
                       result.report.score if result.report else None))
    ok = [s for s in served if s[2] > 0]
    check(len(ok) == len(served),
          f"all {len(served)} timeframes served real bars "
          f"({[(s[0], s[2]) for s in served]})")
    check(all(s[3] == 100.0 for s in served if s[3] is not None),
          "every served frame validated at full quality")

    print("  ...deep daily history")
    deep = provider.get_history("SPY", Timeframe.D1,
                                now - timedelta(days=7300), now)
    check(deep.bars > 4000, f"20 years of daily bars ({deep.bars})")

    print("  ...intraday exhaustion")
    old = provider.get_history("SPY", Timeframe.M5, now - timedelta(days=300),
                               now - timedelta(days=120))
    check(old.exhausted, f"a 4-month-old 5m window reports exhausted ({old.message[:60]})")

    print("  ...rapid switching")
    symbols = ["SPY", "QQQ", "IWM", "NVDA", "AAPL", "MSFT", "AMD", "TSLA"]
    started = time.monotonic()
    blanks = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        def one(sym):
            r = provider.get_history(sym, Timeframe.M5,
                                     now - timedelta(days=5), now)
            return sym, r.bars
        for sym, n in pool.map(one, symbols * 3):
            if n == 0:
                blanks.append(sym)
    check(not blanks, f"24 concurrent live loads, 0 blanks "
                      f"({time.monotonic() - started:.1f}s)")

    health = provider.health()
    print(f"  provider health: "
          f"{[(p['name'], p['available'], p['failure_rate']) for p in health['providers']]}")
    check(health["requests"]["success_rate"] >= 0.95,
          f"live success rate {health['requests']['success_rate']:.1%}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iterations", type=int, default=10,
                    help="scale factor for the repetition-based scenarios")
    ap.add_argument("--live", action="store_true",
                    help="also probe the real providers over the network")
    args = ap.parse_args()

    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="optionspilot-stress-"))
    started = time.monotonic()
    try:
        scenario_rapid_switching(args.iterations)
        scenario_repeated_refresh(args.iterations)
        scenario_concurrency(args.iterations)
        scenario_provider_failures(args.iterations)
        scenario_flapping(args.iterations)
        scenario_circuit_breaker(args.iterations)
        scenario_exhaustion(args.iterations)
        scenario_offline(args.iterations, tmp)
        scenario_slow_provider(args.iterations)
        scenario_corrupt_cache(args.iterations, tmp)
        scenario_bad_data(args.iterations)
        scenario_memory(args.iterations)
        scenario_thread_safety(args.iterations, tmp)
        scenario_diagnostics_integrity(args.iterations)
        if args.live:
            live_probes()
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    failed = [label for ok, label in _results if not ok]
    print(f"\n{len(_results) - len(failed)}/{len(_results)} scenarios held "
          f"in {time.monotonic() - started:.1f}s")
    if failed:
        print("FAILED:")
        for label in failed:
            print(f"  - {label}")
        return 1
    print("OK: the market-data subsystem held under every stress scenario.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
