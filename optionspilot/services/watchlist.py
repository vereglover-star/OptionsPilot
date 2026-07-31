"""WatchlistService — parse, validate and edit the scanned symbol list.

The interesting logic here is the *parse-and-classify* step, and it is the
reason this is a service rather than a route. A user pastes twelve tickers from
a chat message; some are already on the list, one is a typo, one arrives after
the 30-symbol cap is reached, and one is a real ticker the bundled directory has
never heard of. Answering that with a single boolean, or a single error string,
means a user who pasted twelve symbols and got eight cannot find out which four
went missing or why. So the result is four disjoint buckets, and every client
gets the same four.

The 30-symbol cap is a real constraint, not a tidiness rule: a scan cycle costs
seconds per symbol on the free feed, so the cap is what stops the cycle loop
falling behind its own interval. It stays in `config/runtime.py` where it has
always been, because the same number bounds the persisted document.

Nothing in `data/` is imported. The symbol directory and the live-quote fallback
both arrive injected — the directory because a future host might bundle a
different one, and the quote check because reaching a provider from inside a
validation routine is exactly the kind of hidden I/O that makes a service
untestable.
"""

from __future__ import annotations

from optionspilot.services.viewmodels import WatchlistEditView, WatchlistView


class WatchlistService:
    """Edits the watchlist through `RuntimeSettings`, never around it.

    `runtime` is duck-typed to `set_watchlist / pinned / favorites`, `directory`
    to `parse_symbols / is_known / company_name`, and `verify_symbol` to
    `(str) -> bool`. `lock` is the caller's re-entrant orchestrator lock: the
    watchlist is read by the cycle loop every pass, so an edit and a scan must
    never interleave.
    """

    def __init__(self, config, runtime, lock, *, directory, verify_symbol,
                 max_symbols: int, on_added=None, log=None):
        self._cfg = config
        self._runtime = runtime
        self._lock = lock
        self._dir = directory
        self._verify = verify_symbol
        self._max = max_symbols
        self._on_added = on_added
        self._log = log

    # ── reads ────────────────────────────────────────────────────────────────

    def view(self, *, quotes: dict, signals: dict, meta: dict) -> WatchlistView:
        """The watchlist as a screen shows it.

        Quotes, signals and symbol metadata are passed in rather than reached
        for: they are owned by the scan pipeline and the metadata refresher, and
        a watchlist service that fetched them would be a second consumer of live
        state that the status payload already publishes.
        """
        with self._lock:
            return WatchlistView(
                watchlist=list(self._cfg.data.watchlist),
                pinned=self._runtime.pinned(),
                favorites=self._runtime.favorites(),
                max=self._max,
                meta=dict(meta),
                quotes=dict(quotes),
                signals=dict(signals),
            )

    # ── edits ────────────────────────────────────────────────────────────────

    def add(self, text: str) -> WatchlistEditView:
        """Parse free-form input (single ticker, comma/space/newline lists,
        pasted from anywhere), validate each symbol, add the valid ones."""
        requested = self._dir.parse_symbols(text)
        if not requested:
            return WatchlistEditView(
                added=[], invalid=[], duplicates=[], over_cap=[], names={},
                error="no ticker symbols found in the input")

        added: list[str] = []
        invalid: list[str] = []
        duplicates: list[str] = []
        over_cap: list[str] = []
        names: dict[str, str] = {}

        with self._lock:
            current = list(self._cfg.data.watchlist)
            for symbol in requested:
                if symbol in current:
                    duplicates.append(symbol)
                elif len(current) >= self._max:
                    over_cap.append(symbol)
                elif self._dir.is_known(symbol) or self._verify(symbol):
                    current.append(symbol)
                    added.append(symbol)
                    names[symbol] = self._dir.company_name(symbol)
                else:
                    invalid.append(symbol)
            if added:
                self._runtime.set_watchlist(self._cfg, current)

        error = None
        if over_cap:
            error = (f"watchlist is capped at {self._max} symbols "
                     f"(scan time grows with each one)")
        if added:
            if self._on_added is not None:
                self._on_added(added)
            if self._log is not None:
                self._log.info("watchlist add: +%s (invalid: %s, dupes: %s)",
                               added, invalid, duplicates)
        return WatchlistEditView(added=added, invalid=invalid,
                                 duplicates=duplicates, over_cap=over_cap,
                                 names=names, error=error)

    def remove(self, symbol: str) -> dict:
        symbol = symbol.upper()
        with self._lock:
            current = [s for s in self._cfg.data.watchlist if s != symbol]
            if len(current) == len(self._cfg.data.watchlist):
                return {"error": f"{symbol} is not on the watchlist"}
            # Raises if this would empty the list — an empty watchlist is a
            # cycle loop with nothing to do, which looks identical to a broken
            # one. RuntimeSettings owns that rule; this does not re-check it.
            self._runtime.set_watchlist(self._cfg, current)
        if self._log is not None:
            self._log.info("watchlist remove: %s", symbol)
        return {"removed": symbol, "watchlist": current}

    def reorder(self, symbols: list[str]) -> dict:
        """Reorder only — never add or drop.

        The set-equality check is what makes this safe to call from a drag
        handler: a reorder that could also remove would turn a mis-dropped row
        into silent data loss, and the client would have no idea it had asked
        for one.
        """
        symbols = [s.upper() for s in symbols]
        with self._lock:
            if sorted(symbols) != sorted(self._cfg.data.watchlist):
                return {"error": "reorder must contain exactly the current symbols"}
            self._runtime.set_watchlist(self._cfg, symbols)
        return {"watchlist": symbols}
