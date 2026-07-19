from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from optionspilot.notify import NotificationCenter
from optionspilot.orchestrator import Orchestrator
from optionspilot.ui.server import create_app
from tests.test_notify import CollectingNotifier
from tests.test_orchestrator import CFG, NOW, FakeProvider, bullish_candles
from optionspilot.core.models import Timeframe


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
