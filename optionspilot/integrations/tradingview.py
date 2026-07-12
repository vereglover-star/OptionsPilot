"""TradingView webhook alerts (inbound only — that is all TradingView offers).

Alert message format (configured by the user in TradingView's alert dialog):

    {"secret": "<tradingview_secret>", "symbol": "{{ticker}}",
     "note": "optional free text"}

Safety doctrine: an alert is a *prompt to evaluate now*, nothing more. The
symbol goes through the identical pipeline a scheduled scan uses — engine
confidence threshold, contract selection, risk manager, paper broker. A
webhook can therefore never place a trade the system wouldn't have taken by
itself; it only changes *when* the system looks.
"""

from __future__ import annotations

import hmac
import re
from dataclasses import dataclass

_SYMBOL_RE = re.compile(r"[A-Z][A-Z0-9.\-]{0,9}")
_MAX_NOTE = 200


@dataclass(frozen=True, slots=True)
class TradingViewAlert:
    symbol: str
    note: str = ""


def parse_alert(payload: object, secret: str) -> TradingViewAlert:
    """Validate and normalize a webhook payload. Raises ValueError with a
    reason on anything that isn't a well-formed, authenticated alert."""
    if not secret:
        raise ValueError("webhook secret not configured")
    if not isinstance(payload, dict):
        raise ValueError("payload must be a JSON object")
    supplied = payload.get("secret", "")
    if not isinstance(supplied, str) or not hmac.compare_digest(supplied, secret):
        raise ValueError("invalid secret")

    raw_symbol = str(payload.get("symbol", "")).strip()
    if not raw_symbol:
        raise ValueError("missing symbol")
    # TradingView tickers often carry the exchange: "NASDAQ:AAPL"
    symbol = raw_symbol.split(":")[-1].strip().upper()
    if not _SYMBOL_RE.fullmatch(symbol):
        raise ValueError(f"invalid symbol {raw_symbol!r}")

    note = str(payload.get("note", ""))[:_MAX_NOTE]
    return TradingViewAlert(symbol=symbol, note=note)
