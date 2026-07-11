"""OptionsPilot CLI — `python -m optionspilot <command>`.

Commands:
  run                       start the live paper-trading loop
  scan                      run one scan cycle now and print the outcome
  status                    account, positions, risk status
  journal [--last N]        recent trades + aggregate stats
  backtest SYMBOL [...]     backtest on downloaded history, write reports
  learn                     run a learning cycle over the journal
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from optionspilot import __version__
from optionspilot.config import load_config
from optionspilot.core.logging_setup import setup_logging


def _bootstrap(args):
    cfg_path = Path(args.config)
    cfg = load_config(cfg_path if cfg_path.exists() else None)
    setup_logging(cfg.logging)
    return cfg


def cmd_run(args) -> int:
    from optionspilot.orchestrator import Orchestrator

    cfg = _bootstrap(args)
    print(f"OptionsPilot {__version__} — PAPER TRADING (no real money)")
    print(f"watchlist: {cfg.data.watchlist} | min confidence: "
          f"{cfg.engine.min_confidence}% | scan every {cfg.engine.scan_interval_seconds}s")
    print("Ctrl+C to stop.\n")
    Orchestrator(cfg).run_forever()
    return 0


def cmd_scan(args) -> int:
    from optionspilot.orchestrator import Orchestrator

    cfg = _bootstrap(args)
    orch = Orchestrator(cfg)
    summary = orch.run_cycle()
    print(json.dumps(summary, indent=2, default=str))
    if not orch.market_open(datetime.now(timezone.utc)):
        print("\nnote: market is closed — entries are vetoed by trading hours.")
    return 0


def cmd_status(args) -> int:
    from optionspilot.broker import PaperBroker
    from optionspilot.journal import TradeJournal
    from optionspilot.risk import RiskManager

    cfg = _bootstrap(args)
    broker = PaperBroker(cfg.broker, Path("data") / "paper.db",
                         cfg.risk.starting_balance)
    acct = broker.get_account()
    print(f"cash      {acct.cash:>12,.2f}")
    print(f"equity    {acct.equity:>12,.2f}")
    print(f"realized  {acct.realized_pnl:>+12,.2f}")
    positions = broker.get_positions()
    print(f"\nopen positions: {len(positions)}")
    for p in positions:
        print(f"  {p.contract.symbol} x{p.quantity} @ {p.avg_price:.2f} "
              f"({p.direction.value}) stop {p.stop_current} target {p.target}")
    stats = TradeJournal(Path("data") / "journal.db").stats()
    print(f"\njournal: {stats}")
    return 0


def cmd_journal(args) -> int:
    from optionspilot.journal import TradeJournal

    _bootstrap(args)
    journal = TradeJournal(Path("data") / "journal.db")
    trades = journal.all()[-args.last:]
    for t in trades:
        print(f"{t.entry_ts:%Y-%m-%d %H:%M} {t.symbol:5s} {t.direction.value:5s} "
              f"x{t.quantity} conf {t.confidence:.0f}% pnl {t.pnl:+9.2f}  "
              f"{t.exit_reason[:50]}")
    print(f"\n{journal.stats()}")
    return 0


def cmd_backtest(args) -> int:
    from optionspilot.backtest import Backtester
    from optionspilot.core.models import Timeframe
    from optionspilot.data import YFinanceProvider
    from optionspilot.journal import TradeJournal

    cfg = _bootstrap(args)
    if args.min_confidence is not None:
        cfg = cfg.model_copy(deep=True)
        cfg.engine.min_confidence = args.min_confidence
    provider = YFinanceProvider()
    end = datetime.now(timezone.utc)
    candles = {}
    for s in {*cfg.engine.entry_timeframes, *cfg.engine.htf_trend_timeframes}:
        tf = Timeframe.from_string(s)
        window = {1: 5, 5: 10, 15: min(args.days, 55), 60: 60, 240: 100, 1440: 300}
        candles[tf] = provider.get_candles(
            args.symbol, tf, end - timedelta(days=window[tf.minutes]), end)
        print(f"  {tf}: {len(candles[tf])} bars")
    journal = TradeJournal(Path("data") / f"backtest_{args.symbol.lower()}.db")
    report = Backtester(cfg).run(args.symbol.upper(), candles, journal=journal)
    j = report.save_json(Path("data") / "reports" / f"{args.symbol.lower()}.json")
    h = report.save_html(Path("data") / "reports" / f"{args.symbol.lower()}.html")
    print(f"\ntrades {report.n_trades} | net {report.net_profit:+.2f} "
          f"({report.net_profit_pct:+.2f}%) | win rate {report.win_rate:.0%} | "
          f"PF {report.profit_factor} | maxDD {report.max_drawdown_pct}%")
    print(f"reports: {j}\n         {h}")
    return 0


def cmd_ui(args) -> int:
    from optionspilot.ui.desktop import launch

    cfg = _bootstrap(args)
    print("OptionsPilot desktop — PAPER TRADING (no real money). Close the "
          "window to stop; all state persists.")
    launch(cfg)
    return 0


def cmd_serve(args) -> int:
    from optionspilot.ui.server import serve

    cfg = _bootstrap(args)
    serve(cfg, port=args.port, run_loop=not args.no_loop)
    return 0


def cmd_learn(args) -> int:
    from optionspilot.journal import TradeJournal
    from optionspilot.learning import LearningEngine, WeightStore

    _bootstrap(args)
    journal = TradeJournal(Path("data") / "journal.db")
    engine = LearningEngine(journal, min_sample=args.min_sample)
    weights, rationale = engine.recommend_weights(
        WeightStore(Path("data") / "learning" / "weights.json").current() or None
    )
    version = WeightStore(Path("data") / "learning" / "weights.json").save(
        weights, rationale)
    print(f"weights v{version}:")
    for line in rationale:
        print(f"  {line}")
    print("\nperformance by evidence:")
    for s in engine.by_evidence():
        print(f"  {s.label:20s} n={s.trades:3d} wr={s.win_rate:.0%} "
              f"exp={s.expectancy:+8.2f}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="optionspilot",
        description="AI options paper-trading system (no real money).",
    )
    parser.add_argument("--config", default="config.yaml",
                        help="path to config.yaml (default: ./config.yaml)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("run", help="start the live paper-trading loop (headless)")
    sub.add_parser("ui", help="open the desktop app (dashboard + live loop)")
    p = sub.add_parser("serve", help="serve the dashboard in a browser")
    p.add_argument("--port", type=int, default=8787)
    p.add_argument("--no-loop", action="store_true",
                   help="don't run scan cycles in the background")
    sub.add_parser("scan", help="run one scan cycle and print the outcome")
    sub.add_parser("status", help="account, positions, risk status")
    p = sub.add_parser("journal", help="recent trades + stats")
    p.add_argument("--last", type=int, default=20)
    p = sub.add_parser("backtest", help="backtest a symbol on recent history")
    p.add_argument("symbol")
    p.add_argument("--days", type=int, default=25)
    p.add_argument("--min-confidence", type=float, default=None)
    p = sub.add_parser("learn", help="run a learning cycle over the journal")
    p.add_argument("--min-sample", type=int, default=20)

    args = parser.parse_args(argv)
    handler = {
        "run": cmd_run, "ui": cmd_ui, "serve": cmd_serve,
        "scan": cmd_scan, "status": cmd_status,
        "journal": cmd_journal, "backtest": cmd_backtest, "learn": cmd_learn,
    }[args.command]
    try:
        return handler(args)
    except KeyboardInterrupt:
        print("\nstopped.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
