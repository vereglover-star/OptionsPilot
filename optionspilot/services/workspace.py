"""WorkspaceService — where the user was, owned by the server.

Before V0.7.0 every one of these facts lived in the WebView's `localStorage`:
the selected symbol and timeframe, the indicator set, extended hours, auto
follow, the watchlist sort, whether the ticket's chart was open. That has two
consequences and the second is the one that matters.

The first is the obvious one: a second client cannot see any of it, so a phone
opening the same account starts on SPY 1d with default indicators no matter what
the desktop is showing. The second is that `localStorage` is not durable
storage. Clearing the WebView2 profile, restoring a backup, or reinstalling
silently discards it — the exact failure V0.6.1 refused to accept for onboarding
progress, for the exact same reason ("a returning user must not be greeted as a
beginner"), and workspace state had it all along.

So this service is the same shape as `services/guide.py`, on purpose:

  * **Pure.** No I/O, no clock, no network. `normalize` and `merge` are
    functions of their arguments; persistence is the caller's, through
    `RuntimeSettings`, into `settings.json` where the guide state already lives.
  * **Shape-validating, not value-policing.** `apply_control_state` crashed the
    whole app at startup on a hand-edited `{"providers": [1,2]}` because `or {}`
    is not a type check. Everything read here is validated for *shape*, and a
    field that fails is replaced by its default — the failure mode is "you lose
    a preference", never "the app will not start".
  * **It does not hold a second catalogue.** `tab` and the indicator names are
    frontend vocabulary; validating them against a Python list would be the
    two-catalogues drift the guide layer is explicitly built to avoid, so they
    are checked for type and length only. `timeframe` IS validated, because
    `core.models.Timeframe` is the domain's own vocabulary and the one place it
    is defined — and since M1-C2 so are `expiry` (a real ISO date) and the
    selected contract's `right` (`core.models.OptionRight`), on the same test.
  * **One cross-field invariant, checked on every read.** A selected contract
    belongs to an underlying, so a contract whose symbol is not the workspace
    symbol is dropped rather than carried. Every other rule here validates a
    field in isolation; this one cannot, because the failure it prevents is a
    ticket holding a SPY call while the workspace says QQQ.

What it deliberately does NOT own: chart drawings. Those are user work-product
with their own versioned on-disk shape and a per-symbol key, they are far larger
than a preference, and moving them is a migration rather than a default. They
remain in `localStorage` and remain the last client-trapped domain — see
`docs/ARCHITECTURE-PLATFORM.md` §7.
"""

from __future__ import annotations

import math
from datetime import date

from optionspilot.core.models import OptionRight, Timeframe
from optionspilot.services.viewmodels import WorkspaceView

#: How many recently-viewed symbols to remember. Small on purpose: this is a
#: "jump back to what I was looking at" list, not a history, and an unbounded
#: one would grow `settings.json` without ever being read past the first few.
MAX_RECENT = 12

#: Cap on saved named layouts, and on the length of a layout name. Both exist
#: because this document is written from client input and lands in the same
#: `settings.json` the trading mode lives in — an unbounded write from a client
#: is how a preferences file becomes a denial of service against startup.
MAX_LAYOUTS = 20
MAX_NAME = 60

#: Cap on any single free-form string (tab id, sort key, indicator name).
MAX_TOKEN = 40

#: Upper bound on a stored strike. Not a market rule — a bound on what a
#: preferences file may contain. `math.isfinite` rejects `inf` and `nan`; this
#: rejects the merely absurd, because everything in this document is written
#: from client input and lands in the same `settings.json` the trading mode
#: lives in.
MAX_STRIKE = 1_000_000

#: The shipped workspace. Every value here is the pre-V0.7.0 `localStorage`
#: default, so a user with no stored workspace sees exactly what they saw
#: before this service existed.
DEFAULTS: dict = {
    # "dashboard" — the `data-tab` value the frontend's landing button carries.
    # This is the ONE place a tab id appears in Python, and it is a default
    # rather than a catalogue. It was briefly "dash", which matched nothing:
    # `adopt()` skips a switch only when the stored tab IS the landing tab, so
    # every fresh profile called `switchTab("dash")` and relied on that
    # function's unknown-name early return to do nothing. Harmless and wrong,
    # which is the combination that survives review.
    "tab": "dashboard",
    "sidebar_collapsed": False,
    "symbol": "SPY",
    "timeframe": "1d",
    "indicators": [],
    "extended_hours": False,
    "auto_follow": False,
    "watchlist_sort": "custom",
    "ticket_chart_open": False,
    "recent_symbols": [],
    "layouts": {},
    # UI V2 M1-C2. The other two thirds of the context `UI_V2_DESIGN.md` §4.5
    # guarantees: "the symbol, timeframe, expiry and contract currently under
    # the user's attention, carried across every destination". Symbol and
    # timeframe were already here; these two complete it.
    #
    # "" and None are the empty selections rather than a sentinel expiry or a
    # zero strike, because "no contract is selected" is a real and common state
    # — it is what the ticket shows on first launch — and encoding it as a
    # value would make every consumer test for that value instead of for
    # absence.
    "expiry": "",
    "contract": None,
}


def _token(value, default: str) -> str:
    """A short, single-line identifier — or the default."""
    if not isinstance(value, str):
        return default
    cleaned = value.strip()
    if not cleaned or len(cleaned) > MAX_TOKEN:
        return default
    return cleaned


def _flag(value, default: bool) -> bool:
    return value if isinstance(value, bool) else default


def _symbol(value, default: str) -> str:
    if not isinstance(value, str):
        return default
    cleaned = value.strip().upper()
    # Deliberately permissive: this is a display preference, not a trade. A
    # symbol that no longer resolves shows an empty chart with its own error
    # state, which is a better outcome than refusing to restore the workspace.
    if not cleaned or len(cleaned) > 12 or not cleaned.replace(".", "").replace("-", "").isalnum():
        return default
    return cleaned


def _timeframe(value, default: str) -> str:
    """Validated against the domain's own vocabulary, unlike `tab`.

    A timeframe the backend cannot parse is not a cosmetic problem — it reaches
    `/api/candles`, which resolves it through `Timeframe.from_string` and 502s.
    """
    if not isinstance(value, str):
        return default
    try:
        # `str(Timeframe)` is the canonical label (`_TF_LABEL`); the enum has no
        # `.label` attribute. The first version of this line assumed it did and
        # caught AttributeError alongside ValueError, so EVERY timeframe fell
        # back to the default and the fallback looked like validation working.
        # Catch only what the parser actually raises.
        return str(Timeframe.from_string(value.strip()))
    except ValueError:
        return default


def _expiry(value, default: str) -> str:
    """An ISO date, or "" for none.

    Validated against `date.fromisoformat` for the same reason `timeframe` is
    validated against `Timeframe`: it is not cosmetic. An expiry reaches
    `/api/chain`, which parses it, and a value that cannot be parsed produces a
    server error in the middle of a chain load rather than an empty strip.
    """
    if not isinstance(value, str):
        return default
    cleaned = value.strip()
    if not cleaned:
        return ""
    try:
        return date.fromisoformat(cleaned).isoformat()
    except ValueError:
        return default


def _strike(value) -> float | None:
    # bool BEFORE the number check: `isinstance(True, int)` is True in Python,
    # so `"strike": true` would otherwise be stored as a $1.00 strike.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or not (0 < value <= MAX_STRIKE):
        return None
    return float(value)


def _contract(value, symbol: str) -> dict | None:
    """The selected chain row, or None.

    Four fields, and **all or nothing**: a contract missing its strike is not a
    partially-selected contract, it is a bug in whatever wrote it, and keeping
    the readable half would hand the ticket something it cannot price.

    `right` is validated against `core.models.OptionRight` on the same
    principle that admits `Timeframe` and excludes tab ids: it is the domain's
    own vocabulary, defined in exactly one place, so checking against it cannot
    become a second catalogue that drifts.

    The `symbol` argument enforces the cross-field invariant, and it is the one
    rule here that is a real decision: **a contract belongs to an underlying.**
    A stored SPY 450 call is meaningless once the workspace symbol is QQQ, and
    keeping it would break §4.5's FIRST guarantee (one symbol context) in order
    to serve its third (one contract selection). Checking it in `normalize`
    rather than only on the symbol-change path means a hand-edited file cannot
    smuggle a mismatch in either.
    """
    if not isinstance(value, dict):
        return None
    underlying = _symbol(value.get("symbol"), "")
    expiry = _expiry(value.get("expiry"), "")
    strike = _strike(value.get("strike"))
    right = value.get("right")
    right = right if right in {r.value for r in OptionRight} else None
    if not underlying or not expiry or strike is None or right is None:
        return None
    if underlying != symbol:
        return None
    return {"symbol": underlying, "expiry": expiry,
            "strike": strike, "right": right}


def _string_list(value, *, cap: int) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    out: list[str] = []
    for item in value:
        token = _token(item, "")
        if token and token not in out:
            out.append(token)
        if len(out) >= cap:
            break
    return out


def _symbol_list(value, *, cap: int) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    out: list[str] = []
    for item in value:
        symbol = _symbol(item, "")
        if symbol and symbol not in out:
            out.append(symbol)
        if len(out) >= cap:
            break
    return out


def _layouts(value) -> dict:
    """Named saved layouts: `{name: {...arbitrary client shape...}}`.

    The body is opaque on purpose — a layout is the client's own description of
    its panels, and a backend that understood it would be a second place that
    has to change every time the frontend gains a panel. What IS enforced is
    that it is a dict of dicts, bounded in count and name length, because those
    are the properties `settings.json` needs to stay loadable.
    """
    if not isinstance(value, dict):
        return {}
    out: dict = {}
    for name, body in value.items():
        if not isinstance(name, str) or not isinstance(body, dict):
            continue
        cleaned = name.strip()[:MAX_NAME]
        if cleaned:
            out[cleaned] = body
        if len(out) >= MAX_LAYOUTS:
            break
    return out


def normalize(state: dict | None) -> dict:
    """Any input at all -> a valid workspace document.

    Total by construction: there is no input for which this raises, because its
    callers are a startup read of a user-editable JSON file and a POST body.
    """
    raw = state if isinstance(state, dict) else {}
    symbol = _symbol(raw.get("symbol"), DEFAULTS["symbol"])
    return {
        "tab": _token(raw.get("tab"), DEFAULTS["tab"]),
        "sidebar_collapsed": _flag(raw.get("sidebar_collapsed"),
                                   DEFAULTS["sidebar_collapsed"]),
        "symbol": symbol,
        "timeframe": _timeframe(raw.get("timeframe"), DEFAULTS["timeframe"]),
        "expiry": _expiry(raw.get("expiry"), DEFAULTS["expiry"]),
        "contract": _contract(raw.get("contract"), symbol),
        "indicators": _string_list(raw.get("indicators"), cap=24),
        "extended_hours": _flag(raw.get("extended_hours"),
                                DEFAULTS["extended_hours"]),
        "auto_follow": _flag(raw.get("auto_follow"), DEFAULTS["auto_follow"]),
        "watchlist_sort": _token(raw.get("watchlist_sort"),
                                 DEFAULTS["watchlist_sort"]),
        "ticket_chart_open": _flag(raw.get("ticket_chart_open"),
                                   DEFAULTS["ticket_chart_open"]),
        "recent_symbols": _symbol_list(raw.get("recent_symbols"), cap=MAX_RECENT),
        "layouts": _layouts(raw.get("layouts")),
        "updated": raw.get("updated") if isinstance(raw.get("updated"), str) else None,
    }


def merge(current: dict | None, patch: dict | None, *, now: str | None = None) -> dict:
    """Apply a partial client update to a workspace document.

    Partial by design: a phone that only knows about `symbol` and `timeframe`
    must be able to say so without overwriting the desktop's panel layout with
    its own defaults — which is what a whole-document PUT from a less capable
    client would do. Unknown keys are dropped silently rather than rejected;
    this endpoint records where someone was looking, and failing it would be a
    4xx in the middle of scrolling a chart.

    `recent_symbols` is the one field with real merge semantics: a patch that
    sets `symbol` promotes that symbol to the head of the recents, so a client
    never has to maintain the list itself and two clients cannot disagree about
    its order.

    **A patch that changes the symbol drops the selected contract**, and it
    does so through `normalize`'s cross-field check rather than through a rule
    written here — so the invariant holds for a document read off disk as well
    as for one arriving from a client, and there is only one place it can be
    got wrong. A patch that sets `symbol` and `contract` together (selecting a
    chain row for a symbol the client just switched to) is normalised after
    both are applied, so the selection survives. `timeframe` and `expiry` are
    unaffected by a symbol change: §4.5-2 says timeframe survives one, and a
    date means the same thing under any underlying.
    """
    base = normalize(current)
    incoming = patch if isinstance(patch, dict) else {}
    merged = dict(base)

    for key in DEFAULTS:
        if key in incoming:
            merged[key] = incoming[key]

    merged = normalize(merged)

    symbol = merged["symbol"]
    if "symbol" in incoming and symbol:
        recents = [s for s in merged["recent_symbols"] if s != symbol]
        merged["recent_symbols"] = [symbol, *recents][:MAX_RECENT]

    if now is not None:
        merged["updated"] = now
    return merged


def view(state: dict | None) -> WorkspaceView:
    """The normalized document as the frozen view model clients receive."""
    doc = normalize(state)
    return WorkspaceView(**doc)


class WorkspaceService:
    """The stateful face of the above, over an injected preferences store.

    The store is duck-typed to `workspace_state()` / `set_workspace_state(dict)`
    — `RuntimeSettings` satisfies it, and so would anything a future mobile
    backend host wanted to put underneath, which is the point of not importing
    it here.
    """

    def __init__(self, store, *, clock=None):
        self._store = store
        self._clock = clock

    def _now(self) -> str | None:
        if self._clock is None:
            return None
        stamp = self._clock()
        return stamp.isoformat() if hasattr(stamp, "isoformat") else str(stamp)

    def get(self) -> WorkspaceView:
        return view(self._store.workspace_state())

    def update(self, patch: dict | None) -> WorkspaceView:
        merged = merge(self._store.workspace_state(), patch, now=self._now())
        self._store.set_workspace_state(merged)
        return view(merged)

    def reset(self) -> WorkspaceView:
        """Back to the shipped defaults.

        Saved layouts are cleared too: a "reset my workspace" that silently kept
        twenty stale layouts is the kind of half-reset a user then reports as
        "reset doesn't work". If layouts are ever worth keeping across a reset,
        that is a separate, named action.
        """
        fresh = normalize(None)
        if self._clock is not None:
            fresh["updated"] = self._now()
        self._store.set_workspace_state(fresh)
        return view(fresh)
