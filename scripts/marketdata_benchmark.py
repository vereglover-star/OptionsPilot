"""Benchmark every market-data provider and rank them on what they measure.

Answers one question with numbers instead of impressions: *which provider
should be first, and by how much?* For each provider it runs the same set of
requests directly against the adapter — no memo, no disk cache, no failover, so
each column is that provider's own performance and not the chain's — and
reports latency, throughput, data quality, agreement with the primary, and the
resulting health rank.

    python scripts/marketdata_benchmark.py                  # offline, synthetic
    python scripts/marketdata_benchmark.py --live           # the real providers
    python scripts/marketdata_benchmark.py --live --symbol QQQ --runs 5
    python scripts/marketdata_benchmark.py --live --json out.json

**Offline by default.** With no `--live` it benchmarks scripted in-process
adapters, which measures the framework's own overhead (validation, resampling,
normalisation, ranking) and runs in CI without touching a network. `--live`
talks to the real providers and is the one you want when deciding whether to
re-prioritise a chain — it is a measurement tool, not a test.

Memory and CPU are sampled via `resource`/`psutil` when available and reported
as deltas across the run; both are labelled "n/a" rather than guessed when the
platform cannot supply them.
"""
from __future__ import annotations

import argparse
import gc
import json
import os
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd                                              # noqa: E402

from optionspilot.core.models import Timeframe                   # noqa: E402
from optionspilot.data.adapter import (                          # noqa: E402
    HistoryAdapter, HistoryRequest, ProviderError,
)
from optionspilot.data.capabilities import (                     # noqa: E402
    YAHOO_CAPABILITIES, IntervalSpec, ProviderCapabilities,
)
from optionspilot.data.quality import disagreement, validate_history  # noqa: E402
from optionspilot.data.registry import ProviderRegistry, default_registry  # noqa: E402

DEFAULT_CASES = [
    (Timeframe.D1, 365),
    (Timeframe.H1, 30),
    (Timeframe.M15, 5),
    (Timeframe.M5, 2),
]


# ── resource sampling ────────────────────────────────────────────────────────

def _rss_bytes() -> int | None:
    """Resident memory, or None when the platform will not say."""
    try:
        import psutil                                    # noqa: PLC0415
        return psutil.Process(os.getpid()).memory_info().rss
    except Exception:  # noqa: BLE001 — psutil is optional
        pass
    try:
        import resource                                   # noqa: PLC0415
        usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # Linux reports KB, macOS bytes.
        return usage * 1024 if sys.platform.startswith("linux") else usage
    except Exception:  # noqa: BLE001 — not available on Windows
        return None


def _cpu_seconds() -> float:
    return time.process_time()


# ── offline fixtures ─────────────────────────────────────────────────────────

def _frame(bars: int, tf: Timeframe, seed: float = 100.0) -> pd.DataFrame:
    idx = pd.date_range(end=pd.Timestamp.now(tz="UTC").floor("min"),
                        periods=bars, freq=f"{tf.minutes}min")
    closes = [seed + (i % 17) * 0.05 for i in range(bars)]
    return pd.DataFrame({
        "open": closes, "high": [c + 0.1 for c in closes],
        "low": [c - 0.1 for c in closes], "close": closes,
        "volume": [1000.0] * bars}, index=idx)


class _ScriptedAdapter(HistoryAdapter):
    """An in-process provider with a fixed latency — the offline benchmark's
    subject. It exercises the whole framework path (clamping, validation,
    normalisation, health) without a network, so the numbers it produces are
    the cost of *our* code."""

    capabilities = ProviderCapabilities(intervals={
        tf: IntervalSpec(str(tf)) for tf in Timeframe})

    def __init__(self, name: str, priority: int, latency: float, bars: int):
        self.provider_name = name
        self.provider_priority = priority
        self._latency = latency
        self._bars = bars
        super().__init__()

    def _fetch_native(self, symbol, spec, start, end, prepost):
        if self._latency:
            time.sleep(self._latency)
        return _frame(self._bars, Timeframe.from_string(spec.native))


def _offline_registry() -> ProviderRegistry:
    return ProviderRegistry([
        _ScriptedAdapter("fast", 10, 0.002, 400),
        _ScriptedAdapter("medium", 20, 0.010, 400),
        _ScriptedAdapter("slow", 30, 0.040, 400),
    ])


# ── the benchmark ────────────────────────────────────────────────────────────

def bench_provider(adapter: HistoryAdapter, symbol: str,
                   cases: list[tuple[Timeframe, int]], runs: int,
                   reference: dict[str, pd.DataFrame] | None,
                   pause: float) -> dict:
    """Run every case `runs` times against one adapter."""
    now = datetime.now(timezone.utc)
    latencies: list[float] = []
    per_case: list[dict] = []
    total_bars = 0
    errors = 0
    qualities: list[float] = []
    disagreements: list[float] = []

    gc.collect()
    rss0, cpu0 = _rss_bytes(), _cpu_seconds()

    for tf, days in cases:
        if not adapter.supports_interval(tf):
            per_case.append({"timeframe": str(tf), "skipped": "interval "
                                                             "not served"})
            continue
        case_latency: list[float] = []
        case_bars = 0
        case_error = ""
        frame = None
        for _ in range(runs):
            request = HistoryRequest(symbol, tf, now - timedelta(days=days), now)
            t0 = time.perf_counter()
            try:
                frame = adapter.fetch_history(request, now=now)
            except ProviderError as exc:
                errors += 1
                case_error = f"{type(exc).__name__}: {exc}"[:120]
                break
            except Exception as exc:  # noqa: BLE001 — a benchmark must finish
                errors += 1
                case_error = f"InternalError: {exc}"[:120]
                break
            case_latency.append((time.perf_counter() - t0) * 1000.0)
            case_bars = len(frame)
            if pause:
                time.sleep(pause)

        entry: dict = {"timeframe": str(tf), "bars": case_bars,
                       "runs": len(case_latency)}
        if case_error:
            entry["error"] = case_error
        if case_latency:
            latencies.extend(case_latency)
            total_bars += case_bars
            entry["avg_ms"] = round(statistics.fmean(case_latency), 1)
            entry["min_ms"] = round(min(case_latency), 1)
            entry["max_ms"] = round(max(case_latency), 1)
        if frame is not None and not frame.empty:
            validated, report = validate_history(frame, tf, now=now,
                                                 context=f"bench {adapter.provider_name}")
            qualities.append(report.score)
            entry["quality"] = round(report.score, 1)
            key = str(tf)
            if reference is None:
                pass
            elif key not in reference:
                reference[key] = validated
            else:
                delta = disagreement(reference[key], validated)
                if delta is not None:
                    disagreements.append(delta)
                    entry["disagreement"] = round(delta, 6)
        per_case.append(entry)

    gc.collect()
    rss1, cpu1 = _rss_bytes(), _cpu_seconds()

    return {
        "provider": adapter.provider_name,
        "priority": adapter.provider_priority,
        "requests": len(latencies) + errors,
        "errors": errors,
        "avg_latency_ms": round(statistics.fmean(latencies), 1) if latencies else None,
        "median_latency_ms": round(statistics.median(latencies), 1) if latencies else None,
        "p95_latency_ms": (round(sorted(latencies)[int(0.95 * (len(latencies) - 1))], 1)
                           if latencies else None),
        "bars_total": total_bars,
        "bars_per_second": (round(total_bars / (sum(latencies) / 1000.0), 1)
                            if latencies and sum(latencies) else None),
        "avg_quality": round(statistics.fmean(qualities), 1) if qualities else None,
        "max_disagreement": (round(max(disagreements), 6) if disagreements else None),
        "rss_delta_bytes": (rss1 - rss0) if (rss0 is not None and rss1 is not None)
                           else None,
        "cpu_seconds": round(cpu1 - cpu0, 3),
        # The rank the live registry would give this provider after this run —
        # the point of the exercise, since it is what actually orders the chain.
        "health_rank": round(adapter.monitor.rank(), 2),
        "cases": per_case,
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--live", action="store_true",
                    help="benchmark the real shipped providers (uses network)")
    ap.add_argument("--symbol", default="SPY")
    ap.add_argument("--runs", type=int, default=3,
                    help="repetitions per case (default 3)")
    ap.add_argument("--pause", type=float, default=0.0,
                    help="seconds between live requests (be polite)")
    ap.add_argument("--json", metavar="PATH", help="also write the raw results")
    args = ap.parse_args()

    registry = default_registry() if args.live else _offline_registry()
    mode = "LIVE" if args.live else "offline (synthetic providers)"
    pause = args.pause if args.live else 0.0

    print(f"market-data benchmark — {mode}")
    print(f"symbol {args.symbol} · {args.runs} run(s) per case · "
          f"{len(DEFAULT_CASES)} cases\n")

    # Shared across providers so each one after the first is compared to the
    # first provider that answered the same case.
    reference: dict[str, pd.DataFrame] = {}
    results = [bench_provider(adapter, args.symbol, DEFAULT_CASES, args.runs,
                              reference, pause)
               for adapter in registry.adapters]

    header = (f"{'provider':<12}{'avg ms':>9}{'p95 ms':>9}{'bars/s':>10}"
              f"{'quality':>9}{'errors':>8}{'cpu s':>8}{'rank':>8}")
    print(header)
    print("-" * len(header))
    for r in sorted(results, key=lambda r: r["health_rank"]):
        print(f"{r['provider']:<12}"
              f"{_fmt(r['avg_latency_ms']):>9}"
              f"{_fmt(r['p95_latency_ms']):>9}"
              f"{_fmt(r['bars_per_second']):>10}"
              f"{_fmt(r['avg_quality']):>9}"
              f"{r['errors']:>8}"
              f"{r['cpu_seconds']:>8}"
              f"{r['health_rank']:>8}")

    print("\nRANKING (what the registry would use — lower is better)")
    for position, r in enumerate(sorted(results, key=lambda r: r["health_rank"]),
                                 start=1):
        note = ""
        if r["errors"]:
            note = f"  [{r['errors']} error(s)]"
        elif r["max_disagreement"]:
            note = f"  [differs {r['max_disagreement'] * 100:.2f}% from the reference]"
        print(f"  {position}. {r['provider']:<12} rank {r['health_rank']:<8}"
              f"(configured priority {r['priority']}){note}")

    mem = [r["rss_delta_bytes"] for r in results if r["rss_delta_bytes"] is not None]
    print(f"\nmemory delta across the run: "
          f"{(sum(mem) / 1024 / 1024):.1f} MB" if mem else
          "\nmemory delta: n/a on this platform (install psutil for a reading)")

    if args.json:
        Path(args.json).write_text(
            json.dumps({"mode": mode, "symbol": args.symbol,
                        "runs": args.runs, "results": results}, indent=2),
            encoding="utf-8")
        print(f"raw results written to {args.json}")

    # A benchmark reports; it does not judge. Only a provider that could not
    # answer at all is an actual failure worth a non-zero exit.
    dead = [r["provider"] for r in results if r["avg_latency_ms"] is None]
    if dead:
        print(f"\nWARNING: no successful requests from {', '.join(dead)}")
        return 1
    return 0


def _fmt(value) -> str:
    return "—" if value is None else f"{value:g}"


if __name__ == "__main__":
    sys.exit(main())
