"""CandleCache — the app's durable local copy of market history.

This is not only a speed optimisation: it is the last tier of the fallback
chain, so when every provider is unreachable these bars are what stands between
the user and a blank chart. The tests below cover the roundtrip behaviour AND
the properties that make it trustworthy storage — atomicity, integrity
checking, corruption recovery, and versioned schema evolution.
"""

import sqlite3
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from optionspilot.core.models import Timeframe
from optionspilot.core.sqlite import connect as sqlite_connect
from optionspilot.data.cache import SCHEMA_VERSION, CacheCorrupt, CandleCache
from tests.conftest import make_candles


def dt(h, m=0):
    return datetime(2026, 1, 5, h, m, tzinfo=timezone.utc)


def test_store_load_roundtrip(tmp_path):
    df = make_candles([100, 101, 102, 101.5], start="2026-01-05 14:30")
    with CandleCache(tmp_path / "c.db") as cache:
        assert cache.store("spy", Timeframe.M5, df) == 4
        out = cache.load("SPY", Timeframe.M5, dt(0), dt(23))
        assert len(out) == 4
        assert out["close"].tolist() == [100, 101, 102, 101.5]
        assert out.index.tz is not None


def test_upsert_deduplicates(tmp_path):
    df = make_candles([100, 101], start="2026-01-05 14:30")
    with CandleCache(tmp_path / "c.db") as cache:
        cache.store("SPY", Timeframe.M5, df)
        cache.store("SPY", Timeframe.M5, df)  # same bars again
        out = cache.load("SPY", Timeframe.M5, dt(0), dt(23))
        assert len(out) == 2


def test_timeframes_are_isolated(tmp_path):
    df = make_candles([100, 101], start="2026-01-05 14:30")
    with CandleCache(tmp_path / "c.db") as cache:
        cache.store("SPY", Timeframe.M5, df)
        assert cache.load("SPY", Timeframe.M15, dt(0), dt(23)).empty


def test_range_query_is_half_open(tmp_path):
    df = make_candles([100, 101, 102], start="2026-01-05 14:30", freq="5min")
    with CandleCache(tmp_path / "c.db") as cache:
        cache.store("SPY", Timeframe.M5, df)
        out = cache.load("SPY", Timeframe.M5, dt(14, 30), dt(14, 40))
        assert len(out) == 2  # 14:30 and 14:35; 14:40 excluded


def test_coverage(tmp_path):
    df = make_candles([100, 101, 102], start="2026-01-05 14:30")
    with CandleCache(tmp_path / "c.db") as cache:
        assert cache.coverage("SPY", Timeframe.M5) is None
        cache.store("SPY", Timeframe.M5, df)
        lo, hi = cache.coverage("SPY", Timeframe.M5)
        assert lo == dt(14, 30) and hi == dt(14, 40)


def test_usable_from_other_threads(tmp_path):
    """The live app stores/loads from ThreadPoolExecutor workers and FastAPI
    threadpool threads while the connection is created on the main thread.
    sqlite3's default check_same_thread=True made every cross-thread call
    raise ProgrammingError — swallowed by callers' best-effort excepts,
    silently disabling the disk cache (and the Charts tab's stale fallback)
    in exactly the threaded mode that ships. Regression test: real work
    from a worker thread must succeed, not raise."""
    import threading

    df = make_candles([100, 101, 102], start="2026-01-05 14:30")
    with CandleCache(tmp_path / "c.db") as cache:
        errors = []

        def worker():
            try:
                cache.store("SPY", Timeframe.M5, df)
                out = cache.load("SPY", Timeframe.M5, dt(0), dt(23))
                assert len(out) == 3
            except Exception as exc:  # noqa: BLE001 — the assertion under test
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []


# ── provider attribution (schema v2) ─────────────────────────────────────────

def test_bars_record_which_provider_supplied_them(tmp_path):
    """Attribution is what makes "these two sources disagree" diagnosable
    later instead of mysterious."""
    df = make_candles([100, 101, 102], start="2026-01-05 14:30")
    with CandleCache(tmp_path / "c.db") as cache:
        cache.store("SPY", Timeframe.M5, df, provider="yahoo")
        cache.store("QQQ", Timeframe.M5, df, provider="stooq")
        assert cache.stats()["by_provider"] == {"yahoo": 3, "stooq": 3}


def test_stats_describe_the_cache(tmp_path):
    df = make_candles([100, 101, 102], start="2026-01-05 14:30")
    with CandleCache(tmp_path / "c.db") as cache:
        cache.store("SPY", Timeframe.M5, df, provider="yahoo")
        cache.store("SPY", Timeframe.M15, df, provider="yahoo")
        stats = cache.stats()
        assert stats["bars"] == 6 and stats["symbols"] == 1
        assert stats["timeframes"] == 2
        assert stats["schema_version"] == SCHEMA_VERSION
        assert stats["bytes"] > 0


def test_purge_scopes_to_symbol_and_timeframe(tmp_path):
    df = make_candles([100, 101], start="2026-01-05 14:30")
    with CandleCache(tmp_path / "c.db") as cache:
        cache.store("SPY", Timeframe.M5, df)
        cache.store("SPY", Timeframe.M15, df)
        cache.store("QQQ", Timeframe.M5, df)
        assert cache.purge("SPY", Timeframe.M5) == 2
        assert cache.load("SPY", Timeframe.M5, dt(0), dt(23)).empty
        assert len(cache.load("SPY", Timeframe.M15, dt(0), dt(23))) == 2
        assert len(cache.load("QQQ", Timeframe.M5, dt(0), dt(23))) == 2


def test_load_newest_ignores_the_window(tmp_path):
    """The last-resort tier: when no window can be served, the newest bars we
    hold are still better than a blank chart."""
    df = make_candles([100, 101, 102, 103], start="2026-01-05 14:30")
    with CandleCache(tmp_path / "c.db") as cache:
        cache.store("SPY", Timeframe.M5, df)
        out = cache.load_newest("SPY", Timeframe.M5, limit=2)
        assert len(out) == 2
        assert out.index.is_monotonic_increasing
        assert out["close"].tolist() == [102, 103]


# ── durability ───────────────────────────────────────────────────────────────

class FaultyConnection:
    """Wraps a real sqlite3 connection and injects a fault into one method.

    sqlite3.Connection methods are read-only C slots, so they cannot be
    monkeypatched in place; delegating through a proxy is the only way to
    simulate a disk failure against otherwise-real SQL.
    """

    def __init__(self, conn, method, fault, *, after=0):
        self._conn = conn
        self._method = method
        self._fault = fault
        self._after = after
        self.calls = 0

    def __getattr__(self, name):
        target = getattr(self._conn, name)
        if name != self._method:
            return target

        def wrapped(*args, **kwargs):
            self.calls += 1
            if self.calls > self._after:
                return self._fault(target, *args, **kwargs)
            return target(*args, **kwargs)

        return wrapped

    def __enter__(self):
        return self._conn.__enter__()

    def __exit__(self, *exc):
        return self._conn.__exit__(*exc)


def test_a_store_is_atomic(tmp_path, monkeypatch):
    """A crash mid-write must never leave half a frame on disk."""
    df = make_candles([100, 101, 102, 103], start="2026-01-05 14:30")
    with CandleCache(tmp_path / "c.db") as cache:
        real_conn = cache._conn

        def half_then_fail(target, sql, rows):
            target(sql, list(rows)[:2])                    # write half...
            raise sqlite3.OperationalError("disk full")    # ...then fail

        cache._conn = FaultyConnection(real_conn, "executemany", half_then_fail)
        assert cache.store("SPY", Timeframe.M5, df) == 0
        cache._conn = real_conn
        assert cache.load("SPY", Timeframe.M5, dt(0), dt(23)).empty


def test_a_failed_store_never_raises(tmp_path):
    """A cache write failing must not take down a chart that already has its
    data — the bars are in hand; persisting them is a bonus."""
    df = make_candles([100, 101], start="2026-01-05 14:30")

    def locked(target, *args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    with CandleCache(tmp_path / "c.db") as cache:
        cache._conn = FaultyConnection(cache._conn, "executemany", locked)
        assert cache.store("SPY", Timeframe.M5, df) == 0


def test_stored_bars_are_validated_on_the_way_out(tmp_path):
    """The file is untrusted storage. A bad row that reached it — from an
    older build, or a partially-recovered file — must not reach a chart."""
    df = make_candles([100, 101, 102], start="2026-01-05 14:30")
    with CandleCache(tmp_path / "c.db") as cache:
        cache.store("SPY", Timeframe.M5, df)
        cache._conn.execute(
            "UPDATE candles SET high = -1, low = -5 "
            "WHERE ts = (SELECT MIN(ts) FROM candles)")
        cache._conn.commit()
        out = cache.load("SPY", Timeframe.M5, dt(0), dt(23))
        assert len(out) == 2                   # the impossible bar is dropped


# ── corruption handling ──────────────────────────────────────────────────────

def test_a_corrupt_file_is_quarantined_and_replaced_on_open(tmp_path):
    """A damaged cache must degrade to a COLD cache, not crash the app on
    launch — and the old file is moved, never deleted, in case it holds
    something recoverable."""
    path = tmp_path / "c.db"
    path.write_bytes(b"this is definitely not a sqlite database" * 40)
    cache = CandleCache(path)
    try:
        df = make_candles([100, 101], start="2026-01-05 14:30")
        assert cache.store("SPY", Timeframe.M5, df) == 2
        assert cache.stats()["rebuilds"] == 1
        quarantined = list(tmp_path.glob("c.db.corrupt-*"))
        assert len(quarantined) == 1 and quarantined[0].stat().st_size > 0
    finally:
        cache.close()


def test_rebuild_can_be_refused(tmp_path):
    """An operator (or a test) may prefer a loud failure to a silent reset."""
    path = tmp_path / "c.db"
    path.write_bytes(b"not a database" * 40)
    with pytest.raises(CacheCorrupt):
        CandleCache(path, allow_rebuild=False)


def test_runtime_corruption_self_heals_for_the_next_call(tmp_path):
    """The alternative is every candle read failing until the user finds and
    deletes a file they do not know exists."""
    with CandleCache(tmp_path / "c.db") as cache:
        cache.store("SPY", Timeframe.M5,
                    make_candles([100, 101], start="2026-01-05 14:30"))

        def malformed(target, *args, **kwargs):
            raise sqlite3.DatabaseError("database disk image is malformed")

        cache._conn = FaultyConnection(cache._conn, "execute", malformed)
        out = cache.load("SPY", Timeframe.M5, dt(0), dt(23))
        assert out.empty                        # the rebuilt cache is cold...
        assert cache.stats()["rebuilds"] == 1   # ...but the app kept running
        assert cache.store("SPY", Timeframe.M5,
                           make_candles([100], start="2026-01-05 14:30")) == 1


def test_an_ordinary_sql_error_does_not_wipe_the_cache(tmp_path):
    """Only real corruption justifies throwing the file away; a programming
    error must surface as itself rather than silently resetting user state."""
    with CandleCache(tmp_path / "c.db") as cache:
        cache.store("SPY", Timeframe.M5,
                    make_candles([100, 101], start="2026-01-05 14:30"))
        assert cache._query("SELECT nonexistent FROM candles", ()) == []
        assert cache.stats()["rebuilds"] == 0
        assert len(cache.load("SPY", Timeframe.M5, dt(0), dt(23))) == 2


# ── schema evolution ─────────────────────────────────────────────────────────

def test_a_v1_cache_opens_migrates_and_keeps_its_rows(tmp_path):
    """An existing cache.db from an older build must not be discarded — it is
    the user's warm start, and rebuilding it costs hundreds of requests."""
    path = tmp_path / "c.db"
    conn = sqlite_connect(path, wal=True)
    conn.executescript(
        "CREATE TABLE candles ("
        " symbol TEXT NOT NULL, timeframe INTEGER NOT NULL, ts INTEGER NOT NULL,"
        " open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL,"
        " close REAL NOT NULL, volume REAL NOT NULL,"
        " PRIMARY KEY (symbol, timeframe, ts)) WITHOUT ROWID;")
    conn.execute("INSERT INTO candles VALUES ('SPY',5,1767623400,1,2,0.5,1.5,10)")
    conn.execute("PRAGMA user_version = 1")
    conn.commit()
    conn.close()

    with CandleCache(path) as cache:
        stats = cache.stats()
        assert stats["schema_version"] == SCHEMA_VERSION
        assert stats["rebuilds"] == 0
        assert cache.coverage("SPY", Timeframe.M5) is not None
        assert stats["by_provider"] == {"unknown": 1}      # pre-attribution rows


def test_a_newer_schema_is_refused_rather_than_corrupted(tmp_path):
    """Downgrading OptionsPilot must not silently rewrite a future schema."""
    path = tmp_path / "c.db"
    conn = sqlite_connect(path, wal=True)
    conn.execute("PRAGMA user_version = %d" % (SCHEMA_VERSION + 5))
    conn.commit()
    conn.close()
    with pytest.raises(RuntimeError, match="newer than this build"):
        CandleCache(path, allow_rebuild=False)


# ── V0.5.3: cache intelligence ───────────────────────────────────────────────
#
# "Is the cache working?" used to be answerable only as "there are N bars in
# it", which says nothing about whether any of them were ever served. These
# cover the counters that make the cache's VALUE measurable, plus the retention
# pruning that bounds the file for a user who charts hundreds of symbols.

def test_metrics_count_hits_and_misses(tmp_path):
    df = make_candles([100, 101, 102], start="2026-01-05 14:30")
    with CandleCache(tmp_path / "c.db") as cache:
        cache.store("SPY", Timeframe.M5, df)
        cache.load("SPY", Timeframe.M5, dt(0), dt(23))     # hit
        cache.load("QQQ", Timeframe.M5, dt(0), dt(23))     # miss
        stats = cache.stats()
    assert stats["reads"] == 2
    assert stats["hits"] == 1
    assert stats["misses"] == 1
    assert stats["hit_rate"] == 0.5


def test_every_hit_is_one_upstream_request_that_did_not_happen(tmp_path):
    """The number worth putting in front of a user."""
    df = make_candles([100, 101], start="2026-01-05 14:30")
    with CandleCache(tmp_path / "c.db") as cache:
        cache.store("SPY", Timeframe.M5, df)
        for _ in range(5):
            cache.load("SPY", Timeframe.M5, dt(0), dt(23))
        assert cache.stats()["provider_requests_saved"] == 5


def test_writes_are_counted(tmp_path):
    df = make_candles([100, 101, 102], start="2026-01-05 14:30")
    with CandleCache(tmp_path / "c.db") as cache:
        cache.store("SPY", Timeframe.M5, df)
        cache.store("QQQ", Timeframe.M5, df)
        stats = cache.stats()
    assert stats["writes"] == 2
    assert stats["bars_written"] == 6


def test_a_last_resort_read_is_recorded_as_stale(tmp_path):
    """`load_newest` is only reached as the final tier before a blank chart, so
    everything it returns is knowingly out of date."""
    df = make_candles([100, 101], start="2026-01-05 14:30")
    with CandleCache(tmp_path / "c.db") as cache:
        cache.store("SPY", Timeframe.M5, df)
        cache.load_newest("SPY", Timeframe.M5)
        assert cache.stats()["stale_reads"] == 1


def test_stats_report_the_span_actually_held(tmp_path):
    df = make_candles([100, 101, 102], start="2026-01-05 14:30")
    with CandleCache(tmp_path / "c.db") as cache:
        cache.store("SPY", Timeframe.M5, df)
        stats = cache.stats()
    assert stats["oldest_bar"].startswith("2026-01-05")
    assert stats["newest_bar"].startswith("2026-01-05")


def test_stats_are_json_serializable(tmp_path):
    import json

    df = make_candles([100, 101], start="2026-01-05 14:30")
    with CandleCache(tmp_path / "c.db") as cache:
        cache.store("SPY", Timeframe.M5, df, provider="yahoo")
        cache.load("SPY", Timeframe.M5, dt(0), dt(23))
        json.dumps(cache.stats())


def test_prune_evicts_old_bars_and_counts_them(tmp_path):
    old = make_candles([100, 101], start="2020-01-05 14:30")
    with CandleCache(tmp_path / "c.db") as cache:
        cache.store("SPY", Timeframe.M5, old)
        assert cache.prune(retention_days=30) == 2
        assert cache.stats()["evictions"] == 2
        assert cache.load("SPY", Timeframe.M5,
                          datetime(2019, 1, 1, tzinfo=timezone.utc),
                          datetime(2027, 1, 1, tzinfo=timezone.utc)).empty


def test_prune_keeps_bars_inside_the_retention_window(tmp_path):
    recent = make_candles([100, 101],
                          start=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"))
    with CandleCache(tmp_path / "c.db") as cache:
        cache.store("SPY", Timeframe.M5, recent)
        assert cache.prune(retention_days=30) == 0


def test_retention_is_off_by_default(tmp_path):
    """History is small, and the deeper the cache the better the last tier
    before a blank chart — so bounding it is opt-in."""

    old = make_candles([100, 101], start="2020-01-05 14:30")
    with CandleCache(tmp_path / "c.db") as cache:
        cache.store("SPY", Timeframe.M5, old)
    with CandleCache(tmp_path / "c.db") as cache:
        assert cache.stats()["bars"] == 2
        assert cache.config.retention_days is None


def test_configured_retention_prunes_on_open(tmp_path):
    from optionspilot.data.config import CacheConfig

    old = make_candles([100, 101], start="2020-01-05 14:30")
    with CandleCache(tmp_path / "c.db") as cache:
        cache.store("SPY", Timeframe.M5, old)
    with CandleCache(tmp_path / "c.db",
                     config=CacheConfig(retention_days=30)) as cache:
        assert cache.stats()["bars"] == 0
        assert cache.stats()["evictions"] == 2


def test_an_oversized_cache_is_flagged(tmp_path):
    from optionspilot.data.config import CacheConfig

    df = make_candles([100, 101], start="2026-01-05 14:30")
    with CandleCache(tmp_path / "c.db",
                     config=CacheConfig(warn_bytes=1)) as cache:
        cache.store("SPY", Timeframe.M5, df)
        assert cache.stats()["oversized"] is True


class TestMigration3CollapsesDailyConventions:
    """Repairing an already-poisoned cache.db in place.

    Fixing the adapters stops NEW divergence; it does nothing for the rows
    already written. Every existing install has a cache holding a row per
    provider-convention per trading day, and until those collapse onto one
    instant the frame's spacing still says "wrong interval served" and 1D still
    refuses to draw. Rewriting rather than deleting keeps decades of otherwise
    correct end-of-day history: the prices were never wrong, only the instants
    they were filed under.
    """

    @staticmethod
    def _poisoned(path):
        """A v2 cache holding the real defect: SPY daily written twice, once at
        the Yahoo session open and once at yfinance's exchange midnight."""
        conn = sqlite_connect(path)
        conn.executescript(
            "CREATE TABLE IF NOT EXISTS candles ("
            " symbol TEXT NOT NULL, timeframe INTEGER NOT NULL, ts INTEGER NOT NULL,"
            " open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL,"
            " close REAL NOT NULL, volume REAL NOT NULL, provider TEXT,"
            " fetched_at INTEGER, PRIMARY KEY (symbol, timeframe, ts))"
            " WITHOUT ROWID;")
        rows = []
        for day in range(1, 6):
            # 2026-07-0X, EDT: 13:30 UTC is 09:30 ET, 04:00 UTC is 00:00 ET
            base = datetime(2026, 7, day, tzinfo=timezone.utc)
            for hour, provider, fetched in ((13.5, "yahoo", 200),
                                            (4.0, "yfinance", 100)):
                ts = int((base.timestamp()) + hour * 3600)
                rows.append(("SPY", 1440, ts, 100.0, 101.0, 99.0, 100.5, 10.0,
                             provider, fetched))
        # an intraday row, to prove the migration leaves it alone
        rows.append(("SPY", 5, 1784923200, 1.0, 2.0, 0.5, 1.5, 3.0, "yahoo", 1))
        conn.executemany(
            "INSERT INTO candles VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
        conn.execute("PRAGMA user_version=2")
        conn.commit()
        conn.close()

    def test_two_rows_per_day_become_one(self, tmp_path):
        path = tmp_path / "c.db"
        self._poisoned(path)
        cache = CandleCache(path)                      # opening runs migration 3
        rows = cache._query(
            "SELECT ts FROM candles WHERE timeframe=1440 ORDER BY ts", ())
        assert len(rows) == 5                          # was 10
        cache.close()

    def test_the_survivors_are_one_day_apart(self, tmp_path):
        """The property that makes 1D validate again."""
        path = tmp_path / "c.db"
        self._poisoned(path)
        cache = CandleCache(path)
        ts = [r[0] for r in cache._query(
            "SELECT ts FROM candles WHERE timeframe=1440 ORDER BY ts", ())]
        assert all(b - a == 86400 for a, b in zip(ts, ts[1:]))
        cache.close()

    def test_they_land_on_exchange_midnight(self, tmp_path):
        path = tmp_path / "c.db"
        self._poisoned(path)
        cache = CandleCache(path)
        ts = [r[0] for r in cache._query(
            "SELECT ts FROM candles WHERE timeframe=1440", ())]
        for t in ts:
            local = datetime.fromtimestamp(t, timezone.utc).astimezone(
                ZoneInfo("America/New_York"))
            assert (local.hour, local.minute) == (0, 0)
        cache.close()

    def test_the_attributed_newer_row_wins(self, tmp_path):
        path = tmp_path / "c.db"
        self._poisoned(path)
        cache = CandleCache(path)
        rows = cache._query(
            "SELECT provider, fetched_at FROM candles WHERE timeframe=1440", ())
        assert {r[0] for r in rows} == {"yahoo"}       # fetched_at 200 > 100
        cache.close()

    def test_intraday_rows_are_untouched(self, tmp_path):
        """The defect is a daily+ one; rewriting minute bars would be churn
        with a real chance of damage."""
        path = tmp_path / "c.db"
        self._poisoned(path)
        cache = CandleCache(path)
        rows = cache._query("SELECT ts FROM candles WHERE timeframe=5", ())
        assert rows == [(1784923200,)]
        cache.close()

    def test_a_clean_cache_survives_the_migration_unchanged(self, tmp_path):
        """Idempotence: the common case is a cache that needs no repair."""
        path = tmp_path / "c.db"
        cache = CandleCache(path)
        cache.store("SPY", Timeframe.D1,
                    make_candles([100.0] * 5, start="2026-07-01 04:00",
                                 freq="1D"), provider="yahoo")
        before = cache._query("SELECT ts FROM candles ORDER BY ts", ())
        cache.close()
        reopened = CandleCache(path)
        assert reopened._query("SELECT ts FROM candles ORDER BY ts", ()) == before
        reopened.close()
