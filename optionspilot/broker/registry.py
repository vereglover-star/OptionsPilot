"""Broker registry — the single place a broker gets constructed, and the
extension point for live adapters.

Adding a real adapter later means: implement the `Broker` ABC (base.py),
register a factory here, and set `broker.name` in config with BOTH live-gate
flags true. Nothing else in the system changes — the orchestrator, position
manager, and backtester only ever see the `Broker` interface.

v1 ships exactly one working implementation (the paper simulator). The live
slots below are deliberately registered-but-unimplemented so that pointing
config at them fails loudly with guidance instead of failing mysteriously.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from optionspilot.broker.base import Broker, BrokerError
from optionspilot.broker.paper import PaperBroker
from optionspilot.config.settings import AppConfig
from optionspilot.core.logging_setup import get_logger

log = get_logger("broker")

BrokerFactory = Callable[..., Broker]

_ADAPTER_NOTES = {
    "alpaca": "Alpaca Trading API (alpaca.markets) — has options paper trading; "
              "a natural first live adapter",
    "tradier": "Tradier Brokerage API (documentation.tradier.com) — REST options "
               "trading with a sandbox environment",
    "webull": "Webull OpenAPI — requires an approved developer application from "
              "Webull before any integration is possible",
    "ibkr": "Interactive Brokers TWS API / Client Portal API — richest options "
            "support, heaviest integration",
}


def _unimplemented(name: str) -> BrokerFactory:
    def factory(*_args, **_kwargs) -> Broker:
        raise BrokerError(
            f"broker {name!r} is an extension slot, not yet an implementation "
            f"({_ADAPTER_NOTES[name]}). OptionsPilot v1 trades exclusively on "
            f"the built-in paper simulator: set broker.name to 'paper'."
        )
    return factory


REGISTRY: dict[str, BrokerFactory] = {
    "paper": PaperBroker,
    **{name: _unimplemented(name) for name in _ADAPTER_NOTES},
}


def create_broker(config: AppConfig, db_path: str | Path,
                  starting_cash: float) -> Broker:
    """Construct the configured broker. Fails closed: unknown names and
    unimplemented adapters raise BrokerError; the live gate is re-checked here
    even though config validation already enforces it (defense in depth)."""
    name = config.broker.name
    if name not in REGISTRY:
        raise BrokerError(
            f"unknown broker {name!r}; available: {sorted(REGISTRY)}"
        )
    if name != "paper":
        if not (config.broker.live_trading_enabled
                and config.broker.i_understand_the_risks):
            raise BrokerError(
                f"broker {name!r} requires live_trading_enabled and "
                "i_understand_the_risks both true in config"
            )
        log.warning("non-paper broker %r requested — v1 has no live adapters", name)
    return REGISTRY[name](config.broker, db_path, starting_cash)
