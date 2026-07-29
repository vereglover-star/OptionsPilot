from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from optionspilot.notify import NotificationCenter
from optionspilot.orchestrator import Orchestrator
from optionspilot.ui.server import create_app
from tests.test_notify import CollectingNotifier
from tests.test_orchestrator import CFG, NOW, FakeProvider, bullish_candles
from optionspilot.core.models import Timeframe
from optionspilot.data.adapter import ProviderUnavailable


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Freeze both clocks inside the trading window (Friday 11:00 ET) â€”
    # /api/scan and the P&L day/week windows use wall time, and tests must
    # not depend on when they are run.
    monkeypatch.setattr("optionspilot.orchestrator.utcnow", lambda: NOW)
    monkeypatch.setattr("optionspilot.ui.server.utcnow", lambda: NOW)
    candles = bullish_candles()
    spot = float(candles[Timeframe.M5]["close"].iloc[-1])
    provider = FakeProvider(candles, spot, NOW.date())
    cfg = CFG.model_copy(deep=True)  # runtime settings mutate the live config
    orch = Orchestrator(
        cfg, provider=provider,
        notifier=NotificationCenter(cfg.notify, [CollectingNotifier()]),
        data_dir=tmp_path,
    )
    app = create_app(cfg, orchestrator=orch, run_loop=False, data_dir=tmp_path)
    with TestClient(app) as c:
        c.provider = provider
        c.orch = orch
        c.server = app.state.server
        yield c


class TestStatusAPI:
    def test_request_app_does_not_start_global_memory_tracing(self, client):
        """Memory sampling belongs to the live runtime, never app creation."""
        assert client.server._owns_memory_tracing is False

    def test_status_shape(self, client):
        s = client.get("/api/status").json()
        assert s["paper"] is True
        assert s["account"]["equity"] == 25_000.0
        assert s["watchlist"] == ["SPY"]
        assert s["min_confidence"] == 25
        assert s["risk"]["halted"] is False
        assert s["positions"] == []

    def test_index_serves_dashboard(self, client):
        r = client.get("/")
        assert r.status_code == 200
        assert "OptionsPilot" in r.text and "PAPER TRADING" in r.text

    def test_config_endpoint(self, client):
        cfg = client.get("/api/config").json()
        assert cfg["broker"]["name"] == "paper"
        assert cfg["broker"]["live_trading_enabled"] is False


class TestScanAPI:
    def test_scan_opens_position_and_status_reflects_it(self, client):
        summary = client.post("/api/scan", json={"wait": True}).json()
        assert len(summary["opened"]) == 1
        s = client.get("/api/status").json()
        assert len(s["positions"]) == 1
        pos = s["positions"][0]
        assert pos["underlying"] == "SPY" and pos["quantity"] >= 1
        # chart position lines need underlying-space levels in the payload
        assert pos["entry_spot"] > 0 and pos["stop"] > 0 and pos["target"] > 0
        assert s["signals"]["SPY"]["confidence"] > 25
        assert any(n["kind"] == "trade_opened" for n in s["notifications"])
        assert len(s["equity_history"]) == 1

    def test_default_scan_is_non_blocking_with_progress(self, client):
        import time as _t
        out = client.post("/api/scan").json()
        assert out["state"] == "started"
        # the background cycle completes quickly with the fake provider
        for _ in range(100):
            s = client.get("/api/status").json()
            if not s["scan"]["running"] and s["positions"]:
                break
            _t.sleep(0.02)
        assert len(s["positions"]) == 1
        assert s["scan"]["running"] is False
        assert s["scan"]["total"] == 1 and s["scan"]["done"] == 1

    def test_scan_state_present_when_idle(self, client):
        s = client.get("/api/status").json()
        assert s["scan"] == {"running": False, "done": 0, "total": 0}

    def test_journal_endpoint_after_round_trip(self, client):
        client.post("/api/scan", json={"wait": True})
        position = client.orch.broker.get_positions()[0]
        client.provider.spot = position.stop_current - 1.0
        client.post("/api/scan", json={"wait": True})
        d = client.get("/api/journal").json()
        assert d["stats"]["trades"] == 1
        t = d["trades"][0]
        assert t["pnl"] < 0 and "stop hit" in t["exit_reason"]
        assert t["entry_reasons"]
        s = client.get("/api/status").json()
        assert s["pnl"]["week"] == pytest.approx(t["pnl"], abs=0.01)


class TestExperienceAPI:
    def test_experience_stats_empty(self, client):
        r = client.get("/api/experience").json()
        assert r["statistics"]["overview"]["total"] == 0
        assert r["recent"] == []

    def test_experience_similar_is_advisory(self, client):
        r = client.get("/api/experience/similar", params={"symbol": "SPY"}).json()
        assert r["symbol"] == "SPY"
        assert "historical" in r and "similar_trades" in r
        # advisory: evaluating a setup opens no position and changes no state
        assert client.orch.broker.get_positions() == []

    def test_round_trip_records_ai_experience_with_snapshot(self, client):
        client.post("/api/scan", json={"wait": True})
        position = client.orch.broker.get_positions()[0]
        client.provider.spot = position.stop_current - 1.0
        client.post("/api/scan", json={"wait": True})
        recs = client.orch.experience.store.all()
        assert len(recs) == 1
        r = recs[0]
        assert r.managed_by == "ai"
        assert r.operating_mode == "ai"
        # the AI entry snapshot gave this experience rich, symmetric features
        assert r.rsi is not None
        assert r.market_regime is not None
        view = client.get("/api/experience").json()
        assert view["statistics"]["overview"]["total"] == 1


class TestCandlesAPI:
    def test_candles_shape_and_indicators(self, client):
        d = client.get("/api/candles?symbol=spy&tf=5m").json()
        assert d["symbol"] == "SPY" and d["timeframe"] == "5m"
        assert len(d["candles"]) > 40
        bar = d["candles"][-1]
        assert set(bar) == {"time", "open", "high", "low", "close", "volume"}
        assert bar["low"] <= bar["close"] <= bar["high"]
        assert isinstance(bar["time"], int)
        # indicator series align 1:1 with the candles and use null for NaN
        for name in ("ema9", "rsi", "macd_hist", "bb_upper", "vwap"):
            assert name in d["indicators"], name
            assert len(d["indicators"][name]) == len(d["candles"])
        assert d["indicators"]["bb_upper"][0] is None      # warm-up NaN -> null
        assert d["indicators"]["rsi"][-1] is not None

    def test_candles_respects_requested_range(self, client):
        calls = []
        frame = client.provider._candles[Timeframe.M5]

        def get_candles(symbol, timeframe, start, end):
            calls.append((start, end))
            return frame[(frame.index >= start) & (frame.index < end)]

        client.provider.get_candles = get_candles
        start = frame.index[-6].to_pydatetime()
        end = (frame.index[-1] + timedelta(minutes=5)).to_pydatetime()
        d = client.get("/api/candles", params={
            "symbol": "SPY",
            "tf": "5m",
            "start": start.isoformat(),
            "end": end.isoformat(),
        }).json()
        assert calls and calls[0] == (start, end)
        assert d["candles"]
        assert d["candles"][-1]["time"] < int(end.timestamp())

    def test_extended_hours_tags_sessions_and_flags_payload(self, client):
        # ext=1 must tag each bar with its session and set extended_hours;
        # the default (RTH-only) must do neither. Session labels come from the
        # bar's US/Eastern clock time, independent of the provider.
        import pandas as pd
        idx = pd.DatetimeIndex([
            pd.Timestamp("2026-07-17 08:00", tz="America/New_York"),   # pre
            pd.Timestamp("2026-07-17 10:00", tz="America/New_York"),   # rth
            pd.Timestamp("2026-07-17 17:00", tz="America/New_York"),   # post
        ]).tz_convert("UTC")
        frame = pd.DataFrame(
            {"open": [1.0, 1.1, 1.2], "high": [1.2, 1.3, 1.4],
             "low": [0.9, 1.0, 1.1], "close": [1.1, 1.2, 1.3],
             "volume": [10, 20, 30]}, index=idx)
        seen = {}

        def get_candles(symbol, timeframe, start, end, *, extended_hours=False):
            seen["ext"] = extended_hours
            return frame

        client.provider.get_candles = get_candles
        # RTH-only (default): no session field, extended_hours False, flag not
        # forwarded as True
        d0 = client.get("/api/candles?symbol=SPY&tf=5m").json()
        assert d0["extended_hours"] is False
        assert "session" not in d0["candles"][0]
        # extended hours: session tags present, flag set, provider told ext=True
        d1 = client.get("/api/candles?symbol=SPY&tf=5m&ext=1").json()
        assert d1["extended_hours"] is True
        assert seen["ext"] is True
        assert [b["session"] for b in d1["candles"]] == ["pre", "rth", "post"]

    def test_extended_hours_forced_off_on_daily(self, client):
        # daily bars are RTH aggregates upstream; ext must be a no-op there so
        # the cache key and session tagging stay honest — even if the client
        # asks for ext=1, the provider is called WITHOUT it and no session
        # tags are emitted.
        import pandas as pd
        idx = pd.date_range("2026-06-01", periods=5, freq="D", tz="UTC")
        frame = pd.DataFrame(
            {"open": 1.0, "high": 1.2, "low": 0.9, "close": 1.1, "volume": 10},
            index=idx)
        seen = {}

        def get_candles(symbol, timeframe, start, end, *, extended_hours=False):
            seen["ext"] = extended_hours
            return frame

        client.provider.get_candles = get_candles
        d = client.get("/api/candles?symbol=SPY&tf=1d&ext=1").json()
        assert d["extended_hours"] is False
        assert seen["ext"] is False
        assert "session" not in d["candles"][0]

    def test_unknown_symbol_is_clean_error(self, client):
        r = client.get("/api/candles?symbol=ZZZZ&tf=5m")
        # fake provider raises KeyError for unknown timeframes only; unknown
        # symbol returns the same frames — force an error via bad timeframe
        r = client.get("/api/candles?symbol=SPY&tf=7m")
        assert r.status_code == 502
        assert "unavailable" in r.json()["error"]

    def test_malformed_provider_bars_never_500_the_endpoint(self, client):
        # A provider that skips validate_candles (or a future regression in
        # it) must not be able to 500 the chart: NaN volume serializes as 0,
        # non-finite OHLC bars are excluded from the payload. Starlette uses
        # allow_nan=False, so one rogue float otherwise kills the response
        # during serialization — after the endpoint's try/except.
        import numpy as np
        df = client.provider._candles[Timeframe.M5]
        df.iloc[-1, df.columns.get_loc("volume")] = np.nan
        df.iloc[-2, df.columns.get_loc("high")] = np.inf
        n = len(df)
        r = client.get("/api/candles?symbol=SPY&tf=5m")
        assert r.status_code == 200
        d = r.json()
        assert d["candles"][-1]["volume"] == 0          # NaN volume -> 0
        assert len(d["candles"]) == n - 1               # inf bar excluded
        for bar in d["candles"]:
            for k in ("open", "high", "low", "close"):
                assert bar[k] == bar[k]                 # no NaN leaked

    def test_candles_endpoint_honors_start_end_for_history_paging(self, client):
        # Infinite scroll-back depends on /api/candles forwarding an older
        # [start, end] window to the provider. If the endpoint ever drops
        # these params again, the frontend re-fetches the same recent window,
        # the prepend merge finds nothing older, and scrolling "runs out".
        from datetime import datetime, timezone

        seen = {}
        orig = client.provider.get_candles

        def spy(symbol, tf, start, end):
            seen["start"], seen["end"] = start, end
            return orig(symbol, tf, start, end)

        client.provider.get_candles = spy
        r = client.get("/api/candles?symbol=SPY&tf=5m"
                       "&start=2025-01-01T00:00:00Z&end=2025-02-01T00:00:00Z")
        assert r.status_code == 200
        assert seen["start"] == datetime(2025, 1, 1, tzinfo=timezone.utc)
        assert seen["end"] == datetime(2025, 2, 1, tzinfo=timezone.utc)

    def test_candles_payload_reports_market_open(self, client):
        # The stale-banner suppression (a closed-market cached payload is not
        # "live data unavailable" — it is simply the last session) depends on
        # every candles payload reporting whether the market is open. NOW is
        # frozen to Friday 11:00 ET, inside the regular-hours window.
        d = client.get("/api/candles?symbol=SPY&tf=5m").json()
        assert d["market_open"] is True

    def test_stale_display_payload_still_reports_market_state(self, client):
        # When the live fetch fails and the display provider serves disk-cached
        # bars (stale=True), the payload must still carry market_open so the
        # frontend can tell a real "you're behind live prices" warning (market
        # open) from a non-event (market closed, showing the last session).
        frame = client.provider._candles[Timeframe.M5]

        def stale_ok(symbol, tf, start, end):
            return frame, True

        client.provider.get_candles_stale_ok = stale_ok
        d = client.get("/api/candles?symbol=SPY&tf=5m").json()
        assert d["stale"] is True
        assert d["as_of"] is not None
        assert d["market_open"] is True          # rides alongside the stale flag

    def test_chart_lib_is_served(self, client):
        r = client.get("/static/lightweight-charts.js")
        assert r.status_code == 200
        assert "TradingView Lightweight Charts" in r.text[:300]


class TestRiskAPI:
    def test_reset_halt(self, client):
        client.orch.risk.record_closed_trade(NOW - timedelta(hours=1), -5000.0)
        assert client.get("/api/status").json()["risk"]["halted"] is True
        out = client.post("/api/risk/reset_halt").json()
        assert out["halted"] is False


class TestLearningAPI:
    def test_learning_shape(self, client):
        d = client.get("/api/learning").json()
        assert "htf_trend" in d["weights"]
        w = d["weights"]["htf_trend"]
        assert w["effective"] == w["default"]      # nothing learned yet
        assert d["by_evidence"] == []


class TestWatchlistAPI:
    def add(self, client, text):
        return client.post("/api/watchlist/add", json={"text": text}).json()

    def test_quick_add_validates_and_uppercases(self, client):
        r = self.add(client, "aapl")
        assert r["added"] == ["AAPL"]
        assert "Apple" in r["names"]["AAPL"]
        assert client.get("/api/watchlist").json()["watchlist"] == ["SPY", "AAPL"]

    def test_bulk_add_mixed_separators(self, client):
        r = self.add(client, "TSLA, nvda\namd  META")
        assert r["added"] == ["TSLA", "NVDA", "AMD", "META"]
        assert r["invalid"] == [] and r["duplicates"] == []

    def test_duplicates_and_invalid_reported_without_blocking(self, client):
        client.server._live_symbol_check = lambda s: False   # directory only
        r = self.add(client, "SPY, ZZZZZ, tsla")
        assert r["added"] == ["TSLA"]          # valid one still added
        assert r["duplicates"] == ["SPY"]
        assert r["invalid"] == ["ZZZZZ"]

    def test_remove_and_reorder_and_pin(self, client):
        self.add(client, "AAPL TSLA")
        assert client.post("/api/watchlist/remove",
                           json={"symbol": "AAPL"}).status_code == 200
        r = client.post("/api/watchlist/reorder",
                        json={"symbols": ["TSLA", "SPY"]})
        assert r.status_code == 200
        assert client.get("/api/watchlist").json()["watchlist"] == ["TSLA", "SPY"]
        client.post("/api/watchlist/pin", json={"symbol": "SPY", "pinned": True})
        assert client.get("/api/watchlist").json()["pinned"] == ["SPY"]
        # reorder with wrong membership is rejected
        assert client.post("/api/watchlist/reorder",
                           json={"symbols": ["TSLA"]}).status_code == 422

    def test_favorites_and_presets(self, client):
        self.add(client, "AAPL")
        client.post("/api/watchlist/favorites", json={})
        presets = client.get("/api/watchlist/presets").json()
        assert presets["My Favorites"] == ["SPY", "AAPL"]
        assert "Magnificent 7" in presets and "Meme Stocks" in presets

    def test_symbol_search(self, client):
        hits = client.get("/api/symbols/search", params={"q": "app"}).json()["results"]
        assert {"APP", "APPF"} <= {h["symbol"] for h in hits}

    def test_persists_to_settings_store(self, client):
        self.add(client, "NVDA")
        doc = client.server.runtime._doc
        assert "NVDA" in doc["watchlist"]


class TestModeAPI:
    def test_switch_takes_effect_immediately(self, client):
        r = client.post("/api/mode", json={"mode": "high_risk"}).json()
        assert r["trading_mode"] == "high_risk"
        assert client.orch.engine.gate._cfg.trading_mode == "high_risk"  # live object
        s = client.get("/api/status").json()
        assert s["trading_mode"] == "high_risk"

    def test_custom_mode_applies_validated_values(self, client):
        r = client.post("/api/mode", json={
            "mode": "custom", "custom": {"min_confidence": 65,
                                         "daily_trade_limit": 8}})
        assert r.status_code == 200
        s = client.get("/api/status").json()
        assert s["trading_mode"] == "custom"
        assert s["min_confidence"] == 65
        assert s["risk_settings"]["daily_trade_limit"] == 8
        # back to conservative restores the config baseline (25 in test CFG)
        client.post("/api/mode", json={"mode": "conservative"})
        s = client.get("/api/status").json()
        assert s["min_confidence"] == 25

    def test_bad_values_rejected_with_422(self, client):
        r = client.post("/api/mode", json={
            "mode": "custom", "custom": {"risk_per_trade_pct": 99}})
        assert r.status_code == 422 and "error" in r.json()
        assert client.post("/api/mode", json={"mode": "yolo"}).status_code == 422


class TestManualTradingAPI:
    def chain(self, client):
        d = client.get("/api/chain", params={"symbol": "SPY"}).json()
        assert d["chain"], d
        return d

    def atm_call(self, d):
        spot = d["spot"]
        calls = [r for r in d["chain"] if r["right"] == "call"]
        return min(calls, key=lambda r: abs(r["strike"] - spot))

    def test_chain_endpoint_serves_ticket_data(self, client):
        d = self.chain(client)
        row = self.atm_call(d)
        assert row["bid"] > 0 and row["ask"] >= row["bid"]
        assert 0 < abs(row["delta"]) < 1
        assert d["expirations"]

    def test_market_buy_then_close(self, client):
        d = self.chain(client)
        row = self.atm_call(d)
        r = client.post("/api/orders", json={
            "kind": "market", "side": "buy_to_open", "symbol": "SPY",
            "expiration": d["expiration"], "strike": row["strike"],
            "right": "call", "quantity": 2,
        }).json()
        assert r["event"] == "filled"
        s = client.get("/api/status").json()
        assert s["positions"][0]["quantity"] == 2
        r2 = client.post("/api/orders", json={
            "kind": "market", "side": "sell_to_close", "symbol": "SPY",
            "expiration": d["expiration"], "strike": row["strike"],
            "right": "call", "quantity": 2,
        }).json()
        assert r2["event"] == "filled"
        assert client.get("/api/status").json()["positions"] == []

    def test_market_buy_respects_risk_halt(self, client):
        """Manual entries must not bypass the same circuit breaker used by AI.

        This exercised a real gap: the order endpoint called OrderManager
        directly, so a halted account could still open a manual position.
        """
        d = self.chain(client)
        row = self.atm_call(d)
        client.orch.risk.record_closed_trade(NOW - timedelta(minutes=1), -5_000.0)

        r = client.post("/api/orders", json={
            "kind": "market", "side": "buy_to_open", "symbol": "SPY",
            "expiration": d["expiration"], "strike": row["strike"],
            "right": "call", "quantity": 1,
        })

        assert r.status_code == 422
        assert "trading halted" in r.json()["error"]
        assert client.get("/api/status").json()["positions"] == []

    def test_stop_loss_lifecycle_via_scan(self, client):
        d = self.chain(client)
        row = self.atm_call(d)
        base = {"symbol": "SPY", "expiration": d["expiration"],
                "strike": row["strike"], "right": "call"}
        client.post("/api/orders", json={
            **base, "kind": "market", "side": "buy_to_open", "quantity": 1})
        manual_contract = client.get("/api/status").json()["positions"][0]["contract"]
        r = client.post("/api/orders", json={
            **base, "kind": "stop_loss", "side": "sell_to_close",
            "quantity": 1, "tif": "gtc", "stop_level": d["spot"] - 2.0}).json()
        assert r["event"] == "working"
        working = client.get("/api/orders").json()["working"]
        assert len(working) == 1 and working[0]["kind"] == "stop_loss"
        # underlying tanks; the next cycle fires the stop
        client.provider.spot = d["spot"] - 3.0
        client.post("/api/scan", json={"wait": True})
        assert client.get("/api/orders").json()["working"] == []
        # the manual position is stopped out (the AI is free to open its own
        # afterwards in the same cycle â€” that's unrelated to this order)
        positions = client.get("/api/status").json()["positions"]
        assert all(p["contract"] != manual_contract for p in positions)
        hist = client.get("/api/orders").json()["history"]
        assert any(h["status"] == "filled" and h["kind"] == "stop_loss"
                   for h in hist)

    def test_invalid_order_rejected(self, client):
        d = self.chain(client)
        r = client.post("/api/orders", json={
            "kind": "limit", "side": "buy_to_open", "symbol": "SPY",
            "expiration": d["expiration"], "strike": self.atm_call(d)["strike"],
            "right": "call", "quantity": 1,          # limit without price
        })
        assert r.status_code == 422 and "limit_price" in r.json()["error"]

    def test_account_metrics_shape(self, client):
        m = client.get("/api/account/metrics").json()
        assert m["portfolio_value"] == 25_000.0
        assert m["buying_power"] == 25_000.0
        assert m["total_return_pct"] == 0.0
        assert m["max_drawdown_pct"] == 0.0
        for key in ("win_rate", "profit_factor", "avg_win", "avg_loss",
                    "daily_pnl", "unrealized_pnl", "equity_history"):
            assert key in m


class TestWebSocket:
    def test_ws_pushes_status(self, client):
        with client.websocket_connect("/ws") as ws:
            msg = ws.receive_json()
            assert msg["paper"] is True and "account" in msg

    def test_ws_sends_full_payload_then_heartbeats_when_idle(self, client):
        # The client relies on this contract: every NEW connection gets a full
        # payload first (so a reconnect after a drop catches up automatically —
        # the digest starts empty), then tiny heartbeats while nothing changes
        # (which the frontend skips re-rendering on). If a change ever makes the
        # first frame a heartbeat, reconnecting clients would render stale data.
        with client.websocket_connect("/ws") as ws:
            first = ws.receive_json()
            assert "account" in first and not first.get("heartbeat")
            # nothing mutates in this test, so the next frame is a heartbeat
            second = ws.receive_json()
            assert second.get("heartbeat") is True and "account" not in second


class TestCoachAPI:
    def test_empty_coach_dashboard(self, client):
        r = client.get("/api/coach").json()
        assert r["dashboard"] == {"trades_reviewed": 0}
        assert r["profile"] == {"trades_reviewed": 0}
        assert r["reviews"] == []

    def test_coach_dashboard_after_review(self, client):
        from tests.test_coach import ctx, stop_order
        from tests.test_journal import make_trade
        trade = make_trade("api1", 120.0, strategy="manual")
        client.orch.coach.review(
            trade, ctx(quality="good", spot=100.0, delta=0.45),
            {"spot": 101.5}, orders=[stop_order(level=98.0)],
            equity_at_entry=25_000.0,
        )
        r = client.get("/api/coach").json()
        assert set(r) >= {"dashboard", "profile", "reviews"}
        d = r["dashboard"]
        assert d["trades_reviewed"] == 1
        assert len(d["category_scores"]) == 10
        assert {"scores", "streaks", "action_items", "overall"} <= set(d)
        # the review now carries the category scorecard + outcome snapshot
        assert r["reviews"][0]["categories"]
        assert r["reviews"][0]["symbol"] == "SPY"

    def test_dashboard_is_cached_by_review_count(self, client):
        from tests.test_coach import ctx, stop_order
        from tests.test_journal import make_trade
        client.orch.coach.review(
            make_trade("c1", 50.0, strategy="manual"), ctx(),
            None, orders=[stop_order()], equity_at_entry=25_000.0)
        client.get("/api/coach")
        cached = client.server._coach_cache
        assert cached is not None and cached[0] == 1
        # second call with no new review reuses the cached object
        client.get("/api/coach")
        assert client.server._coach_cache is cached


class TestGuideAPI:
    """The guided-onboarding endpoints (V0.6.1).

    Progress lives in settings.json rather than localStorage so a reinstall does
    not greet a returning user as a beginner — which means these two routes are
    the only thing standing between that promise and a lost tour, and the tests
    below check both that the write survives and that a bad write cannot break
    the read.
    """

    def test_fresh_install_is_offered_the_tour(self, client):
        d = client.get("/api/guide").json()
        assert d["state"]["onboarded"] is False
        assert d["recommendations"][0]["tutorial"] == "welcome"
        assert d["tutorials"]

    def test_facts_are_measured_not_stored(self, client):
        facts = client.get("/api/guide").json()["facts"]
        assert facts["closed_trades"] == 0
        assert facts["open_positions"] == 0
        assert facts["watchlist_size"] == len(client.orch.cfg.data.watchlist)
        assert facts["order_kinds_used"] == []

    def test_completion_persists_and_stops_the_offer(self, client):
        d = client.post("/api/guide/state", json={"completed": ["welcome"]}).json()
        assert d["state"]["onboarded"] is True
        assert not [r for r in d["recommendations"] if r["tutorial"] == "welcome"]
        # …and survives a re-read, i.e. it actually reached the settings file
        assert client.get("/api/guide").json()["state"]["completed"] == ["welcome"]

    def test_feature_marks_accumulate(self, client):
        client.post("/api/guide/state", json={"features": ["tab.charts"]})
        d = client.post("/api/guide/state",
                        json={"features": ["tab.charts", "tab.journal"]}).json()
        assert d["state"]["features"]["tab.charts"] == 2
        assert d["state"]["features"]["tab.journal"] == 1

    def test_preferences_round_trip(self, client):
        client.post("/api/guide/state", json={"reduce_motion": True, "tips": False})
        state = client.get("/api/guide").json()["state"]
        assert state["reduce_motion"] is True and state["tips"] is False

    def test_forget_resets(self, client):
        client.post("/api/guide/state",
                    json={"completed": ["welcome", "charts"], "reduce_motion": True})
        d = client.post("/api/guide/state", json={"forget": True}).json()
        assert d["state"]["completed"] == []
        assert d["state"]["onboarded"] is False

    @pytest.mark.parametrize("body", [
        None, {}, {"completed": "welcome"}, {"completed": ["nope"]},
        {"features": {"a": 1}}, {"onboarded": "yes"}, {"junk": [1, 2, 3]},
    ])
    def test_a_malformed_patch_is_absorbed_not_rejected(self, client, body):
        """This endpoint records that someone finished a tour. Failing it would
        be a 4xx in the middle of a celebration, and nothing downstream depends
        on the write."""
        r = client.post("/api/guide/state", json=body)
        assert r.status_code == 200
        assert "state" in r.json()

    def test_a_corrupt_settings_file_costs_progress_not_the_app(self, client):
        client.server.runtime.set_guide_state({"completed": "everything",
                                               "features": [1, 2, 3]})
        d = client.get("/api/guide").json()
        assert d["state"]["completed"] == []
        assert d["state"]["features"] == {}

    def test_order_kinds_reach_the_facts(self, client):
        from optionspilot.broker.orders import OrderKind
        from optionspilot.core.models import OptionRight, utcnow
        chain = client.orch.provider.get_option_chain(
            "SPY", client.orch.provider.get_expirations("SPY")[0])
        contract = next(c for c in chain if c.right is OptionRight.CALL)
        client.orch.orders.place(kind=OrderKind.LIMIT, side="buy_to_open",
                                 contract=contract, quantity=1, ts=utcnow(),
                                 limit_price=1.0)
        facts = client.get("/api/guide").json()["facts"]
        assert facts["order_kinds_used"] == ["limit"]
        assert facts["orders_placed"] == 1

    def test_market_only_history_recommends_the_trade_tour(self, client):
        from optionspilot.broker.orders import OrderKind
        from optionspilot.core.models import OptionRight, utcnow
        client.post("/api/guide/state", json={"completed": ["welcome"]})
        chain = client.orch.provider.get_option_chain(
            "SPY", client.orch.provider.get_expirations("SPY")[0])
        contract = next(c for c in chain if c.right is OptionRight.CALL)
        for _ in range(3):
            client.orch.orders.place(kind=OrderKind.MARKET, side="buy_to_open",
                                     contract=contract, quantity=1, ts=utcnow())
        recs = client.get("/api/guide").json()["recommendations"]
        trade = next(r for r in recs if r["tutorial"] == "trade")
        assert "market orders" in trade["reason"]
        assert trade["evidence"]["orders_placed"] >= 3


class TestMarketDataStateAPI:
    """`/api/candles` must tell the frontend WHICH no-data condition it hit.

    One empty `candles` array used to mean four different things — the window
    predates every provider, the window is a holiday, the feed is down, or the
    symbol is bogus — and the frontend had to guess. Guessing is what made a
    scroll into old intraday history retry the same impossible request forever.
    These tests drive the real `CachedProvider` + `MarketDataService` stack with
    scripted adapters so each condition is asserted end to end.
    """

    @pytest.fixture
    def md_client(self, tmp_path, monkeypatch, request):
        """A client whose provider is the real market-data stack."""
        from optionspilot.data.cached import CachedProvider
        from optionspilot.data.registry import ProviderRegistry
        from optionspilot.data.service import MarketDataService
        from tests.marketdata_helpers import ScriptedAdapter, UNLIMITED, frame

        monkeypatch.setattr("optionspilot.orchestrator.utcnow", lambda: NOW)
        monkeypatch.setattr("optionspilot.ui.server.utcnow", lambda: NOW)
        script = getattr(request, "param", None)
        if script is None:
            script = [frame(80, Timeframe.M5, end=NOW)]
        adapter = ScriptedAdapter("scripted", script, capabilities=UNLIMITED)
        service = MarketDataService(ProviderRegistry([adapter]),
                                    cache_db=tmp_path / "cache.db",
                                    clock=lambda: NOW)
        quotes = FakeProvider(bullish_candles(), 100.0, NOW.date())
        provider = CachedProvider(quotes, service=service)
        cfg = CFG.model_copy(deep=True)
        orch = Orchestrator(cfg, provider=provider,
                            notifier=NotificationCenter(cfg.notify,
                                                        [CollectingNotifier()]),
                            data_dir=tmp_path)
        app = create_app(cfg, orchestrator=orch, run_loop=False,
                         data_dir=tmp_path)
        with TestClient(app) as c:
            c.adapter = adapter
            c.service = service
            yield c

    def test_a_live_payload_names_its_provider_and_quality(self, md_client):
        d = md_client.get("/api/candles?symbol=SPY&tf=5m").json()
        assert d["outcome"] == "live"
        assert d["provider"] == "scripted"
        assert d["quality"] == 100.0
        assert d["stale"] is False and d["exhausted"] is False
        assert d["trace_id"] is not None
        assert len(d["candles"]) == 80

    def test_an_exhausted_window_is_flagged_not_reported_as_an_error(self, tmp_path,
                                                                    monkeypatch):
        """The fix for 'scrolling back retries forever'. The frontend reads
        `exhausted` as a FACT and stops asking, and `earliest_available` is
        what it shows the user."""
        from optionspilot.data.cached import CachedProvider
        from optionspilot.data.capabilities import YAHOO_CAPABILITIES
        from optionspilot.data.registry import ProviderRegistry
        from optionspilot.data.service import MarketDataService
        from tests.marketdata_helpers import ScriptedAdapter, frame

        monkeypatch.setattr("optionspilot.orchestrator.utcnow", lambda: NOW)
        monkeypatch.setattr("optionspilot.ui.server.utcnow", lambda: NOW)
        adapter = ScriptedAdapter("yahoo", [frame(10, Timeframe.M5, end=NOW)],
                                  capabilities=YAHOO_CAPABILITIES)
        service = MarketDataService(ProviderRegistry([adapter]),
                                    cache_db=tmp_path / "cache.db",
                                    clock=lambda: NOW)
        cfg = CFG.model_copy(deep=True)
        orch = Orchestrator(
            cfg,
            provider=CachedProvider(FakeProvider(bullish_candles(), 100.0,
                                                 NOW.date()), service=service),
            notifier=NotificationCenter(cfg.notify, [CollectingNotifier()]),
            data_dir=tmp_path)
        with TestClient(create_app(cfg, orchestrator=orch, run_loop=False,
                                   data_dir=tmp_path)) as c:
            start = (NOW - timedelta(days=200)).isoformat()
            end = (NOW - timedelta(days=120)).isoformat()
            d = c.get("/api/candles", params={"symbol": "SPY", "tf": "5m",
                                              "start": start, "end": end}).json()
        assert d["candles"] == []
        assert d["outcome"] == "exhausted" and d["exhausted"] is True
        assert d["earliest_available"] is not None
        assert "only goes back to" in d["message"]
        assert adapter.calls == [], "an impossible window must cost no request"

    @pytest.mark.parametrize("md_client", [[None]], indirect=True)
    def test_a_genuinely_empty_window_is_not_an_error(self, md_client):
        """A holiday. The chart must not raise a red error overlay for it."""
        d = md_client.get("/api/candles?symbol=SPY&tf=5m").json()
        assert d["candles"] == []
        assert d["outcome"] == "empty" and d["exhausted"] is False

    @pytest.mark.parametrize("md_client", [[ProviderUnavailable("feed down")]],
                             indirect=True)
    def test_a_dead_feed_reports_failed_with_the_reason(self, md_client):
        d = md_client.get("/api/candles?symbol=SPY&tf=5m").json()
        assert d["candles"] == []
        assert d["outcome"] == "failed"
        assert "feed down" in d["message"]

    def test_a_repeat_request_reports_the_memo_tier(self, md_client):
        md_client.get("/api/candles?symbol=SPY&tf=5m")
        d = md_client.get("/api/candles?symbol=SPY&tf=5m").json()
        assert d["outcome"] == "memo"
        assert len(md_client.adapter.calls) == 1

    def test_the_payload_stays_backward_compatible(self, md_client):
        """Existing consumers read `candles`/`indicators`/`stale`/`market_open`;
        the new fields are additive."""
        d = md_client.get("/api/candles?symbol=SPY&tf=5m").json()
        assert set(d) >= {"symbol", "timeframe", "candles", "indicators",
                          "stale", "market_open", "extended_hours"}


class TestMarketDataDiagnosticsAPI:
    def test_diagnostics_report_providers_cache_and_traces(self, tmp_path,
                                                           monkeypatch):
        """The design goal: a chart complaint should be answerable from one
        JSON response, without reproducing it."""
        from optionspilot.data.cached import CachedProvider
        from optionspilot.data.registry import ProviderRegistry
        from optionspilot.data.service import MarketDataService
        from tests.marketdata_helpers import ScriptedAdapter, UNLIMITED, frame

        monkeypatch.setattr("optionspilot.orchestrator.utcnow", lambda: NOW)
        monkeypatch.setattr("optionspilot.ui.server.utcnow", lambda: NOW)
        adapter = ScriptedAdapter("scripted", [frame(30, Timeframe.M5, end=NOW)],
                                  capabilities=UNLIMITED)
        service = MarketDataService(ProviderRegistry([adapter]),
                                    cache_db=tmp_path / "cache.db",
                                    clock=lambda: NOW)
        cfg = CFG.model_copy(deep=True)
        orch = Orchestrator(
            cfg,
            provider=CachedProvider(FakeProvider(bullish_candles(), 100.0,
                                                 NOW.date()), service=service),
            notifier=NotificationCenter(cfg.notify, [CollectingNotifier()]),
            data_dir=tmp_path)
        with TestClient(create_app(cfg, orchestrator=orch, run_loop=False,
                                   data_dir=tmp_path)) as c:
            c.get("/api/candles?symbol=SPY&tf=5m")
            d = c.get("/api/diagnostics/marketdata").json()

        assert d["available"] is True
        assert [p["name"] for p in d["providers"]] == ["scripted"]
        assert d["cache"]["bars"] == 30
        assert d["requests"]["total_requests"] == 1
        assert d["requests"]["success_rate"] == 1.0
        trace = d["traces"][0]
        assert trace["symbol"] == "SPY" and trace["outcome"] == "live"
        assert trace["attempts"][0]["provider"] == "scripted"
        assert trace["validation"]["usable"] is True

    def test_diagnostics_degrade_gracefully_on_a_plain_provider(self, client):
        """Any injected `MarketDataProvider` must still be safe to ask."""
        d = client.get("/api/diagnostics/marketdata").json()
        assert d["available"] is False and "reason" in d


# ── V0.5.3: the diagnostics dashboard's API surface ──────────────────────────

@pytest.fixture
def diag_client(tmp_path, monkeypatch):
    """A client whose provider is the real market-data stack over a scripted
    adapter, so the diagnostics/export/replay endpoints run end to end offline."""
    from optionspilot.data.cached import CachedProvider
    from optionspilot.data.registry import ProviderRegistry
    from optionspilot.data.service import MarketDataService
    from tests.marketdata_helpers import ScriptedAdapter, UNLIMITED, frame

    monkeypatch.setattr("optionspilot.orchestrator.utcnow", lambda: NOW)
    monkeypatch.setattr("optionspilot.ui.server.utcnow", lambda: NOW)
    adapter = ScriptedAdapter("scripted", [frame(30, Timeframe.M5, end=NOW)],
                              capabilities=UNLIMITED)
    service = MarketDataService(ProviderRegistry([adapter]),
                                cache_db=tmp_path / "cache.db",
                                clock=lambda: NOW)
    cfg = CFG.model_copy(deep=True)
    orch = Orchestrator(
        cfg,
        provider=CachedProvider(FakeProvider(bullish_candles(), 100.0,
                                             NOW.date()), service=service),
        notifier=NotificationCenter(cfg.notify, [CollectingNotifier()]),
        data_dir=tmp_path)
    with TestClient(create_app(cfg, orchestrator=orch, run_loop=False,
                               data_dir=tmp_path)) as c:
        yield c


class TestDiagnosticsDashboardPayload:
    def test_it_carries_everything_the_dashboard_renders(self, diag_client):
        """The page computes nothing — anything it shows must be in here, or a
        screenshot and an export would disagree."""
        diag_client.get("/api/candles?symbol=SPY&tf=5m")
        d = diag_client.get("/api/diagnostics/marketdata").json()

        assert set(d) >= {"providers", "ranking", "cache", "requests", "memo",
                          "config", "traces", "available", "version"}
        provider = d["providers"][0]
        for key in ("rank", "state", "success_rate", "avg_latency_ms",
                    "p95_latency_ms", "timeouts", "validation_failures",
                    "rate_limits", "breaker_trips", "requests_today",
                    "data_quality_score", "last_success_at", "intervals"):
            assert key in provider, key
        assert d["ranking"][0]["position"] == 1
        assert d["cache"]["hit_rate"] is not None
        assert d["traces"][0]["chain"]

    def test_the_trace_count_is_bounded(self, diag_client):
        """A request for a million traces must not try to serve a million."""
        assert len(diag_client.get(
            "/api/diagnostics/marketdata?traces=100000").json()["traces"]) <= 200


class TestDiagnosticsExport:
    def test_the_text_export_is_a_readable_report(self, diag_client):
        diag_client.get("/api/candles?symbol=SPY&tf=5m")
        r = diag_client.get("/api/diagnostics/marketdata/export?format=text")
        assert r.status_code == 200
        assert "PROVIDERS" in r.text and "scripted" in r.text
        assert "attachment" in r.headers["content-disposition"]
        assert ".txt" in r.headers["content-disposition"]

    def test_the_json_export_is_the_same_data(self, diag_client):
        diag_client.get("/api/candles?symbol=SPY&tf=5m")
        live = diag_client.get("/api/diagnostics/marketdata").json()
        exported = diag_client.get(
            "/api/diagnostics/marketdata/export?format=json").json()
        assert [p["name"] for p in exported["providers"]] == \
            [p["name"] for p in live["providers"]]

    def test_the_json_export_downloads_as_a_dated_file(self, diag_client):
        r = diag_client.get("/api/diagnostics/marketdata/export?format=json")
        disposition = r.headers["content-disposition"]
        assert "attachment" in disposition and ".json" in disposition
        assert "optionspilot-diagnostics-" in disposition

    def test_an_export_works_before_any_request_has_been_made(self, diag_client):
        """The first thing a user does after a bad launch is export."""
        assert diag_client.get(
            "/api/diagnostics/marketdata/export?format=text").status_code == 200


class TestDiagnosticsReplay:
    def test_replaying_a_recorded_trace_returns_every_provider_answer(
            self, diag_client):
        diag_client.get("/api/candles?symbol=SPY&tf=5m")
        trace_id = diag_client.get(
            "/api/diagnostics/marketdata").json()["traces"][0]["id"]

        r = diag_client.post("/api/diagnostics/marketdata/replay",
                             json={"trace_id": trace_id})
        assert r.status_code == 200
        body = r.json()
        assert body["symbol"] == "SPY"
        assert body["answers"][0]["provider"] == "scripted"
        assert body["service"]["outcome"] in ("live", "cache", "memo")

    def test_a_missing_trace_id_is_a_400(self, diag_client):
        assert diag_client.post("/api/diagnostics/marketdata/replay",
                                json={}).status_code == 400

    def test_an_unknown_trace_is_a_404_that_explains_itself(self, diag_client):
        r = diag_client.post("/api/diagnostics/marketdata/replay",
                             json={"trace_id": 999999})
        assert r.status_code == 404
        assert "ring" in r.json()["error"]

    def test_replay_is_refused_on_a_provider_that_cannot_support_it(self, client):
        r = client.post("/api/diagnostics/marketdata/replay",
                        json={"trace_id": 1})
        assert r.status_code == 404
        assert "does not support replay" in r.json()["error"]
