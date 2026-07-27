"""Human-readable diagnostics — the thing a user pastes into a bug report.

`service.health()` is complete and machine-shaped. This module renders the same
payload as plain text that a person can read and a maintainer can act on
without opening a JSON viewer:

    render(health_payload) -> str

The design rule here is narrow and worth stating, because it is the reason this
is a separate module rather than a method: **it renders, it does not compute.**
Every number comes from the payload as given. If the text and the dashboard
ever disagree, that is a bug in one renderer, not a difference of opinion about
what the numbers mean.

## What is deliberately not in it

No stack traces, no file paths beyond the cache's own name, no symbol history
beyond what the trace ring already holds, and no configuration values that
could carry a secret. A diagnostics export is something a user will paste into
a public issue tracker, so it has to be safe to paste into a public issue
tracker.
"""

from __future__ import annotations

from datetime import datetime, timezone

RULE = "=" * 72
THIN = "-" * 72


def render(health: dict, *, traces: int = 10, title: str = "") -> str:
    """A complete, readable diagnostics report for `service.health()` output
    (optionally including `traces`, as the API returns them)."""
    out: list[str] = []
    stamp = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    out.append(RULE)
    out.append(title or "OptionsPilot — market data diagnostics")
    out.append(f"generated {stamp}")
    out.append(RULE)

    if not health.get("available", True):
        out.append("")
        out.append(f"Diagnostics unavailable: {health.get('reason', 'unknown')}")
        return "\n".join(out) + "\n"

    out.extend(_providers(health.get("providers") or []))
    out.extend(_requests(health.get("requests") or {}))
    out.extend(_cache(health.get("cache")))
    out.extend(_recent(health.get("traces") or [], traces))
    out.append("")
    out.append(THIN)
    out.append("Paste this whole report into the issue. It contains no "
               "credentials, no file contents and no personal data.")
    return "\n".join(out) + "\n"


def _section(name: str) -> list[str]:
    return ["", name, THIN]


def _providers(providers: list[dict]) -> list[str]:
    out = _section("PROVIDERS (best first)")
    if not providers:
        out.append("  none registered")
        return out
    for position, p in enumerate(providers, start=1):
        state = p.get("state", "?")
        flag = {"closed": "ok", "open": "OUT OF ROTATION",
                "half_open": "probing"}.get(state, state)
        out.append(f"  {position}. {p.get('name', '?'):<12} {flag}"
                   f"   rank {p.get('rank')}  (priority {p.get('priority')})")
        out.append(f"       requests {p.get('requests', 0)}"
                   f"  ok {p.get('successes', 0)}"
                   f"  failed {p.get('failures', 0)}"
                   f"  empty {p.get('empties', 0)}"
                   f"  today {p.get('requests_today', 0)}")
        out.append(f"       latency  avg {p.get('avg_latency_ms', 0)}ms"
                   f"  p95 {p.get('p95_latency_ms', 0)}ms"
                   f"   success rate {_pct(p.get('success_rate'))}")
        out.append(f"       quality  {p.get('data_quality_score')}"
                   f"   timeouts {p.get('timeouts', 0)}"
                   f"   validation failures {p.get('validation_failures', 0)}"
                   f"   rate limits {p.get('rate_limits', 0)}")
        out.append(f"       breaker  trips {p.get('breaker_trips', 0)}"
                   f"   {_cooldown(p)}")
        out.append(f"       last ok  {p.get('last_success_at') or 'never'}")
        if p.get("last_error"):
            out.append(f"       last err {p['last_error']}")
        intervals = p.get("intervals") or []
        if intervals:
            out.append(f"       serves   {', '.join(intervals)}")
    return out


def _cooldown(p: dict) -> str:
    open_for = p.get("circuit_open_for")
    limited = p.get("rate_limited_for")
    if open_for:
        return f"open for another {open_for}s"
    if limited:
        return f"rate limited for another {limited}s"
    return "closed"


def _requests(req: dict) -> list[str]:
    out = _section("REQUESTS (this session)")
    if not req:
        out.append("  no requests recorded")
        return out
    out.append(f"  total {req.get('total_requests', 0)}"
               f"   served {req.get('served', 0)}"
               f"   success rate {_pct(req.get('success_rate'))}"
               f"   live rate {_pct(req.get('live_rate'))}")
    out.append(f"  duration  avg {req.get('avg_duration_ms', 0)}ms"
               f"   slowest {req.get('slowest_ms', 0)}ms")
    outcomes = req.get("outcomes") or {}
    if outcomes:
        out.append("  outcomes  " + "  ".join(
            f"{k}={v}" for k, v in outcomes.items() if v))
    per_provider = req.get("provider_requests") or {}
    if per_provider:
        out.append("  answered  " + "  ".join(
            f"{k}={v}" for k, v in per_provider.items()))
    return out


def _cache(cache: dict | None) -> list[str]:
    out = _section("CACHE")
    if not cache:
        out.append("  disabled (no local candle cache in this session)")
        return out
    out.append(f"  {cache.get('bars', 0)} bars"
               f"   {cache.get('symbols', 0)} symbols"
               f"   {cache.get('timeframes', 0)} timeframes"
               f"   {_bytes(cache.get('bytes'))}"
               f"   schema v{cache.get('schema_version')}")
    out.append(f"  reads {cache.get('reads', 0)}"
               f"   hits {cache.get('hits', 0)}"
               f"   hit rate {_pct(cache.get('hit_rate'))}"
               f"   stale reads {cache.get('stale_reads', 0)}"
               f"   avg age {_duration(cache.get('avg_age_seconds'))}")
    out.append(f"  writes {cache.get('writes', 0)}"
               f"   bars written {cache.get('bars_written', 0)}"
               f"   evictions {cache.get('evictions', 0)}"
               f"   rebuilds {cache.get('rebuilds', 0)}"
               f"   errors {cache.get('errors', 0)}")
    out.append(f"  upstream requests saved: "
               f"{cache.get('provider_requests_saved', 0)}")
    span_from, span_to = cache.get("oldest_bar"), cache.get("newest_bar")
    if span_from:
        out.append(f"  covering  {span_from}  ..  {span_to}")
    if cache.get("oversized"):
        out.append("  NOTE: the cache file has passed its configured warning "
                   "size — consider setting market_data.cache.retention_days.")
    return out


def _recent(traces: list[dict], limit: int) -> list[str]:
    out = _section(f"RECENT REQUESTS (newest {min(limit, len(traces))})")
    if not traces:
        out.append("  none recorded")
        return out
    for t in traces[:limit]:
        out.append(f"  #{t.get('id')} {t.get('at', '')}"
                   f"  {t.get('symbol')} {t.get('timeframe')}"
                   f" -> {t.get('outcome')}"
                   f"  {t.get('bars', 0)} bars"
                   f"  {t.get('duration_ms', 0)}ms"
                   f"  via {t.get('provider') or '-'}")
        chain = t.get("chain")
        if chain and chain != "-":
            out.append(f"        chain: {chain}")
        if t.get("message"):
            out.append(f"        {t['message']}")
    return out


# ── formatting helpers ───────────────────────────────────────────────────────

def _pct(value) -> str:
    if value is None:
        return "n/a"
    return f"{float(value) * 100:.1f}%"


def _bytes(value) -> str:
    if not value:
        return "size unknown"
    size = float(value)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"  # pragma: no cover — the loop always returns


def _duration(seconds) -> str:
    if not seconds:
        return "0s"
    seconds = float(seconds)
    if seconds < 90:
        return f"{seconds:.0f}s"
    if seconds < 5400:
        return f"{seconds / 60:.0f}m"
    if seconds < 172800:
        return f"{seconds / 3600:.1f}h"
    return f"{seconds / 86400:.1f}d"


__all__ = ["render"]
