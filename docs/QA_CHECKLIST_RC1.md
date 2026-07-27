# OptionsPilot v0.3.0 — Release Candidate 1 Manual QA Checklist

**Purpose:** Systematic manual verification of RC1 during live market hours, beyond
what the automated suite (`pytest`, `scripts/chart_check.py`, `scripts/browser_check.py`)
already covers. Automated checks prove the code paths execute without error; this
checklist proves the *product* behaves correctly for a real user watching a real
screen.

**How to use this document**

- Work top to bottom, in order — later sections assume earlier ones passed.
- Check exactly one box per test: `[ ]` Pass, `[ ]` Fail, `[ ]` N/A (with a reason
  in Notes — e.g. market closed, feature intentionally out of scope for RC1).
- Every test lists an **Expected result**. If actual behavior differs at all,
  mark Fail and write what actually happened in Notes — don't fix silently and
  re-check; capture it first so nothing gets lost.
- Run `.\scripts\dev.ps1` (or the packaged exe) before starting. Note the build
  identity below so results are reproducible.

**Build under test**

| Field | Value |
|---|---|
| Version | v0.3.0-RC1 |
| Branch / commit | `v3-ui` @ ______________ |
| Date tested | ______________ |
| Tester | ______________ |
| Market session (open/closed, and which market) | ______________ |
| Automated suite result (`.\scripts\verify.ps1`) | PASS / FAIL: ______________ |

---

## 1. Charts

### 1.1 Loading & data integrity

| # | Test | Expected result | Result | Notes |
|---|---|---|---|---|
| 1.1.1 | Load SPY on the Charts tab | Candles render within ~2s, most-recent bar on the right, no console errors | [ ] Pass [ ] Fail [ ] N/A | |
| 1.1.2 | Load QQQ, IWM, META, NVDA, TSLA, AAPL, MSFT, AMZN, GOOGL in turn | Each renders real candle data (not flat/zero), volume histogram present under price panel | [ ] Pass [ ] Fail [ ] N/A | |
| 1.1.3 | Enter an invalid ticker (e.g. `ZZZZZ9`) | Error overlay appears with a Retry button, no blank/frozen chart | [ ] Pass [ ] Fail [ ] N/A | |
| 1.1.4 | After the invalid-ticker error, load a valid symbol | Chart recovers fully, no leftover error state | [ ] Pass [ ] Fail [ ] N/A | |
| 1.1.5 | Cycle through every timeframe button (1m 2m 3m 5m 10m 15m 30m 1H 2H 4H 1D 1W 1M) on one symbol | Each loads distinct, plausible candle data; no error overlay; label matches the button clicked | [ ] Pass [ ] Fail [ ] N/A | |
| 1.1.6 | Rapidly click through 5+ symbols in quick succession | Chart settles on the **last** symbol clicked; no flicker between two different symbols' data | [ ] Pass [ ] Fail [ ] N/A | |
| 1.1.7 | Rapidly click through timeframes on one symbol | Chart settles on the last timeframe clicked, no mixed-timeframe artifacts | [ ] Pass [ ] Fail [ ] N/A | |

### 1.2 History & scrolling

| # | Test | Expected result | Result | Notes |
|---|---|---|---|---|
| 1.2.1 | Scroll/drag left toward the oldest visible bar | Older bars load and prepend seamlessly; no jump, no gap, no duplicate bars | [ ] Pass [ ] Fail [ ] N/A | |
| 1.2.2 | Keep scrolling left repeatedly | Continues paging further into history without breaking or erroring | [ ] Pass [ ] Fail [ ] N/A | |
| 1.2.3 | Scroll left until history is exhausted (very old symbol/timeframe combo) | Stops cleanly — no infinite spinner, no repeated failed requests | [ ] Pass [ ] Fail [ ] N/A | |
| 1.2.4 | Zoom in/out (mouse wheel or pinch) at various scroll positions | Chart stays rendered and legible at every zoom level, no vanishing candles | [ ] Pass [ ] Fail [ ] N/A | |

### 1.3 Indicators

| # | Test | Expected result | Result | Notes |
|---|---|---|---|---|
| 1.3.1 | Toggle EMA on/off | Overlay line appears/disappears without disturbing candles | [ ] Pass [ ] Fail [ ] N/A | |
| 1.3.2 | Toggle VWAP on/off | Same as above | [ ] Pass [ ] Fail [ ] N/A | |
| 1.3.3 | Toggle Bollinger Bands on/off | Band lines appear/disappear cleanly | [ ] Pass [ ] Fail [ ] N/A | |
| 1.3.4 | Toggle RSI on/off | Sub-panel appears/disappears without resizing the main chart oddly | [ ] Pass [ ] Fail [ ] N/A | |
| 1.3.5 | Toggle MACD on/off | Sub-panel appears/disappears cleanly | [ ] Pass [ ] Fail [ ] N/A | |
| 1.3.6 | Enable all five indicators at once, switch symbol | All five persist and recompute correctly for the new symbol | [ ] Pass [ ] Fail [ ] N/A | |

### 1.4 Drawing tools (new in this RC: fib, zone, note)

| # | Test | Expected result | Result | Notes |
|---|---|---|---|---|
| 1.4.1 | Click the Trendline tool, then two points on the chart | Tool arms instantly (no lag on click); line is created connecting the two points | [ ] Pass [ ] Fail [ ] N/A | |
| 1.4.2 | Click the Horizontal Ray tool, then one point | Ray is created extending from that price to the right edge | [ ] Pass [ ] Fail [ ] N/A | |
| 1.4.3 | Click the Rectangle/Zone tool, then two corner points | A shaded rectangle zone is created spanning the two corners | [ ] Pass [ ] Fail [ ] N/A | |
| 1.4.4 | Click the Fibonacci tool, then two points (swing high/low) | Fib retracement levels render between the two points with visible level labels (e.g. 0, 0.382, 0.5, 0.618, 1) | [ ] Pass [ ] Fail [ ] N/A | |
| 1.4.5 | Click the Note tool, then one point, then type text | A note marker is placed and shows the typed text | [ ] Pass [ ] Fail [ ] N/A | |
| 1.4.6 | Click an existing drawing to select it | Selection handles/highlight appear on that object only | [ ] Pass [ ] Fail [ ] N/A | |
| 1.4.7 | Click empty chart space | Drawing deselects | [ ] Pass [ ] Fail [ ] N/A | |
| 1.4.8 | Drag an endpoint of a selected trendline/fib | Object reshapes live, following the cursor without lag | [ ] Pass [ ] Fail [ ] N/A | |
| 1.4.9 | Drag the body of a zone (not an endpoint) | Whole zone moves together, shape preserved | [ ] Pass [ ] Fail [ ] N/A | |
| 1.4.10 | Select a drawing and press Delete | The drawing is removed immediately | [ ] Pass [ ] Fail [ ] N/A | |
| 1.4.11 | Duplicate a drawing (if a duplicate action exists) | A copy is created, independent of the original | [ ] Pass [ ] Fail [ ] N/A | |
| 1.4.12 | Change a drawing's color | Color updates immediately and visibly | [ ] Pass [ ] Fail [ ] N/A | |
| 1.4.13 | Lock a drawing, then try to drag it | Locked drawing does not move; still visible/selectable | [ ] Pass [ ] Fail [ ] N/A | |
| 1.4.14 | Hide a drawing | Drawing disappears from the chart but is not deleted (reappears if un-hidden) | [ ] Pass [ ] Fail [ ] N/A | |
| 1.4.15 | Rename a drawing/note (if supported) | New name is saved and displayed | [ ] Pass [ ] Fail [ ] N/A | |
| 1.4.16 | Create several drawings (one of each type), reload the page | All drawings persist exactly as left (position, color, lock, hidden state) | [ ] Pass [ ] Fail [ ] N/A | |
| 1.4.17 | Create a drawing, switch to a different symbol, switch back | Drawings are per-symbol — original symbol's drawings are intact, new symbol shows its own (or none) | [ ] Pass [ ] Fail [ ] N/A | |
| 1.4.18 | While panning/scrolling the chart, a drawing tool is armed but no point placed yet | Chart pans normally; arming a tool doesn't block navigation until the first click | [ ] Pass [ ] Fail [ ] N/A | |
| 1.4.19 | Manually corrupt drawing storage: in DevTools console run `localStorage.setItem('chDraw:SPY:1D','{not json')`, reload | Chart still loads and renders (does not brick); drawings for that symbol reset rather than crashing the page | [ ] Pass [ ] Fail [ ] N/A | |

### 1.5 Position / order price lines (new in this RC)

| # | Test | Expected result | Result | Notes |
|---|---|---|---|---|
| 1.5.1 | Open a paper position on a symbol, view its chart | An entry-price line renders at the correct level, labeled | [ ] Pass [ ] Fail [ ] N/A | |
| 1.5.2 | Position has a stop-loss configured | Stop-loss line renders at the correct level, visually distinct (e.g. color) from entry | [ ] Pass [ ] Fail [ ] N/A | |
| 1.5.3 | Position has a target configured | Target line renders at the correct level, visually distinct from entry and stop | [ ] Pass [ ] Fail [ ] N/A | |
| 1.5.4 | Modify the stop or target from the Positions/Trade UI | Corresponding chart line moves to the new level without a full chart reload | [ ] Pass [ ] Fail [ ] N/A | |
| 1.5.5 | Place a working (unfilled) stop or limit order | An order price line renders at the order's trigger/limit level, distinguishable from position lines | [ ] Pass [ ] Fail [ ] N/A | |
| 1.5.6 | Cancel the working order | Its price line disappears | [ ] Pass [ ] Fail [ ] N/A | |
| 1.5.7 | Close the position | Entry/stop/target lines disappear (or clearly mark closed) | [ ] Pass [ ] Fail [ ] N/A | |
| 1.5.8 | Multiple positions/orders open on the same symbol at once | All relevant lines render simultaneously without overlapping/label collision making them unreadable | [ ] Pass [ ] Fail [ ] N/A | |
| 1.5.9 | Have a position open, switch to a different symbol with no position | No stray price lines from the other symbol appear | [ ] Pass [ ] Fail [ ] N/A | |

### 1.6 Trade-tab collapsible chart

| # | Test | Expected result | Result | Notes |
|---|---|---|---|---|
| 1.6.1 | On the Trade page, collapse the chart panel | Chart hides, ticket area expands to use the space | [ ] Pass [ ] Fail [ ] N/A | |
| 1.6.2 | Expand it again | Chart reappears and renders correctly (not blank) | [ ] Pass [ ] Fail [ ] N/A | |
| 1.6.3 | Set collapsed/expanded, reload the app | Preference is remembered across reload | [ ] Pass [ ] Fail [ ] N/A | |
| 1.6.4 | Change the ticket's symbol | Trade chart follows the ticket's symbol automatically | [ ] Pass [ ] Fail [ ] N/A | |
| 1.6.5 | Change timeframe/indicators/drawings on the Charts tab, then open the Trade tab | Same drawings/indicators/timeframe carry over (single shared chart instance) | [ ] Pass [ ] Fail [ ] N/A | |

---

## 2. AI / Coach

| # | Test | Expected result | Result | Notes |
|---|---|---|---|---|
| 2.1 | View the AI dashboard/signals panel with the engine running | Signals populate for watched symbols, scores/rationale are legible | [ ] Pass [ ] Fail [ ] N/A | |
| 2.2 | Let the engine run through a full cycle | New signal data appears without a manual page refresh (WS push) | [ ] Pass [ ] Fail [ ] N/A | |
| 2.3 | Trigger the Coach on a manual trade candidate | Coach returns a rationale/verdict consistent with the visible score/gate state | [ ] Pass [ ] Fail [ ] N/A | |
| 2.4 | Coach evaluation with missing/incomplete market data for a symbol | Coach degrades gracefully (states missing context) rather than crashing or showing an unhandled error | [ ] Pass [ ] Fail [ ] N/A | |
| 2.5 | AI Mode position opened by the engine | Appears in Positions correctly tagged `managed_by=ai`, cannot be manually edited/closed from the manual order path | [ ] Pass [ ] Fail [ ] N/A | |
| 2.6 | Switch `operating_mode` between ai/human | `trading_mode` (conservative/high_risk/custom) is unaffected by the switch (orthogonal axes) | [ ] Pass [ ] Fail [ ] N/A | |

---

## 3. Paper trading

| # | Test | Expected result | Result | Notes |
|---|---|---|---|---|
| 3.1 | Place a manual market buy order | Order fills against paper account, position appears in Positions tab | [ ] Pass [ ] Fail [ ] N/A | |
| 3.2 | Place a manual limit order away from the market | Order shows as working/pending, not immediately filled | [ ] Pass [ ] Fail [ ] N/A | |
| 3.3 | Cancel a working order | Order disappears from working orders, no position created | [ ] Pass [ ] Fail [ ] N/A | |
| 3.4 | Close an open position (full close) | Position closes, P&L recorded, removed from open Positions | [ ] Pass [ ] Fail [ ] N/A | |
| 3.5 | Partially close a position (if supported) | Remaining quantity reflects correctly, P&L recorded on closed portion only | [ ] Pass [ ] Fail [ ] N/A | |
| 3.6 | Attempt to place an order that would violate a risk rule (e.g. oversized, no buying power) | Order is rejected with a clear reason, no position/order created | [ ] Pass [ ] Fail [ ] N/A | |
| 3.7 | Attempt to enable live trading via settings | Both `broker.live_trading_enabled` and `broker.i_understand_the_risks` gates required; no live broker adapter exists, so no real order can ever be placed | [ ] Pass [ ] Fail [ ] N/A | |
| 3.8 | Journal / trade history view | Filled/closed trades appear with correct entry/exit/P&L | [ ] Pass [ ] Fail [ ] N/A | |
| 3.9 | Restart the app with open positions/working orders | State persists exactly as left (positions, stops, targets, working orders) | [ ] Pass [ ] Fail [ ] N/A | |
| 3.10 | Order fill → stop pre-arm → position row → close-prefill flow end to end | Each step follows the previous without manual re-entry of data already known (e.g. close ticket prefilled with position qty/symbol) | [ ] Pass [ ] Fail [ ] N/A | |

---

## 4. UI / navigation

| # | Test | Expected result | Result | Notes |
|---|---|---|---|---|
| 4.1 | Visit every tab (Dashboard, Charts, Trade, Positions, Journal, Settings, etc.) | Each loads without a blank screen or console error | [ ] Pass [ ] Fail [ ] N/A | |
| 4.2 | Resize the window smaller/larger repeatedly | Layout reflows sensibly, chart remains rendered (not blank/clipped) | [ ] Pass [ ] Fail [ ] N/A | |
| 4.3 | Keyboard-only navigation (Tab/Shift+Tab through interactive elements) | Focus order is logical, nothing is unreachable | [ ] Pass [ ] Fail [ ] N/A | |
| 4.4 | Screen-reader spot check (or the `?` shortcuts overlay) | Live regions announce meaningful state changes; skip link works | [ ] Pass [ ] Fail [ ] N/A | |
| 4.5 | Use the app on a narrower/mobile-width viewport | Core flows (view chart, view positions, place a trade) remain usable | [ ] Pass [ ] Fail [ ] N/A | |
| 4.6 | Dark/light mode (if applicable) | Both render legibly, no invisible text/contrast failures | [ ] Pass [ ] Fail [ ] N/A | |

---

## 5. Performance

| # | Test | Expected result | Result | Notes |
|---|---|---|---|---|
| 5.1 | Leave the app open and idle on the Charts tab for 15+ minutes during market hours | Memory usage (Task Manager / DevTools) does not grow unbounded; auto-refresh keeps working | [ ] Pass [ ] Fail [ ] N/A | |
| 5.2 | Visit 10+ different symbol/timeframe combinations in one session | No visible slowdown by the 10th switch vs. the 1st | [ ] Pass [ ] Fail [ ] N/A | |
| 5.3 | Check the payload cache doesn't grow unbounded (DevTools console: inspect cache size if exposed, or rely on the automated LRU check) | Bounded — old entries evicted, not accumulating indefinitely | [ ] Pass [ ] Fail [ ] N/A | |
| 5.4 | Draw 10+ objects on one chart | No lag when selecting/dragging any of them | [ ] Pass [ ] Fail [ ] N/A | |
| 5.5 | Watch DevTools Network tab during idle chart viewing | No runaway/duplicate polling requests beyond the expected refresh cadence | [ ] Pass [ ] Fail [ ] N/A | |
| 5.6 | Open DevTools Performance/Memory profiler across a long session with many symbol switches | No detached-DOM-node growth (no per-switch canvas/listener leak) | [ ] Pass [ ] Fail [ ] N/A | |

---

## 6. Error handling & recovery

| # | Test | Expected result | Result | Notes |
|---|---|---|---|---|
| 6.1 | Disconnect network (Wi-Fi off or DevTools offline mode) while viewing a chart | Stale-data banner or error overlay appears, not a blank/frozen chart | [ ] Pass [ ] Fail [ ] N/A | |
| 6.2 | Reconnect network | Chart auto-recovers (WS reconnects, banner clears, data refreshes) within a few seconds | [ ] Pass [ ] Fail [ ] N/A | |
| 6.3 | Provider (yfinance) rate-limited or temporarily unavailable | Cached/stale data is served with a visible "stale" indicator rather than an error page | [ ] Pass [ ] Fail [ ] N/A | |
| 6.4 | Click Retry on a stale/error banner | Chart attempts a live refresh; clears the banner if data is now fresh | [ ] Pass [ ] Fail [ ] N/A | |
| 6.5 | Request a symbol with no historical data available | Clear "no history" state, not a crash or infinite spinner | [ ] Pass [ ] Fail [ ] N/A | |
| 6.6 | Put the machine to sleep with the app open, wake it later | App recovers state and refreshes rather than showing stale data indefinitely | [ ] Pass [ ] Fail [ ] N/A | |
| 6.7 | Minimize and restore the window/app | Chart is still correctly rendered on restore (no blank canvas) | [ ] Pass [ ] Fail [ ] N/A | |
| 6.8 | Force a malformed WS frame (if testable) or observe over a long session | Malformed frames are ignored, not a thrown exception that breaks the socket | [ ] Pass [ ] Fail [ ] N/A | |
| 6.9 | Corrupt `localStorage` for indicators (`localStorage.setItem('chInds','garbage')`), reload | App starts normally with default indicator settings, no crash | [ ] Pass [ ] Fail [ ] N/A | |
| 6.10 | Attempt an order that should be rejected by risk rules (see 3.6) mid-session | Rejection is clearly communicated in the UI, app remains usable afterward | [ ] Pass [ ] Fail [ ] N/A | |

---

## 7. Live market-hours validation

**Only meaningful with the market open.** If tested with the market closed, mark
N/A and note the closest verification you could do (e.g. simulated tick).

| # | Test | Expected result | Result | Notes |
|---|---|---|---|---|
| 7.1 | Watch the current (forming) candle on a 1m or 5m chart for a full bar period | Candle body/wick/legend OHLC updates smoothly in place; no flicker, no jump | [ ] Pass [ ] Fail [ ] N/A | |
| 7.2 | Watch across a bar boundary | A new candle is created at the boundary; the view does not reset/re-scroll | [ ] Pass [ ] Fail [ ] N/A | |
| 7.3 | Watch the volume histogram during a forming candle | Last volume bar grows in real time with the forming candle | [ ] Pass [ ] Fail [ ] N/A | |
| 7.4 | Enable all indicators, watch through a live bar | EMA/VWAP/BB/RSI/MACD all track the moving last bar correctly | [ ] Pass [ ] Fail [ ] N/A | |
| 7.5 | Current-price line on an open chart | Line follows the live last-traded price | [ ] Pass [ ] Fail [ ] N/A | |
| 7.6 | Open position's stop/target lines during live movement | Lines stay correctly anchored to their price levels as the candles move | [ ] Pass [ ] Fail [ ] N/A | |
| 7.7 | AI dashboard/signal panel during market hours | Signals update live via WS push without manual refresh | [ ] Pass [ ] Fail [ ] N/A | |
| 7.8 | Option chain — click Load/refresh during market hours | Bid/ask/IV reflect current live market values (compare against an external quote source) | [ ] Pass [ ] Fail [ ] N/A | |
| 7.9 | Option chain with no auto-refresh timer (by design in this RC) | Chain does **not** update automatically; confirm this is expected/acceptable, not a bug | [ ] Pass [ ] Fail [ ] N/A | |
| 7.10 | WS connection during an active session (check DevTools Network > WS frames) | Heartbeats arrive at the expected cadence when idle; full payload arrives on any reconnect | [ ] Pass [ ] Fail [ ] N/A | |
| 7.11 | Leave the app open across market open → close transition (if feasible) | No crash or stuck state at the transition; last regular-hours candle finalizes correctly | [ ] Pass [ ] Fail [ ] N/A | |

---

## Summary

| Category | Total tests | Passed | Failed | N/A |
|---|---|---|---|---|
| 1. Charts | 46 | | | |
| 2. AI / Coach | 6 | | | |
| 3. Paper trading | 10 | | | |
| 4. UI / navigation | 6 | | | |
| 5. Performance | 6 | | | |
| 6. Error handling & recovery | 10 | | | |
| 7. Live market-hours validation | 11 | | | |
| **Total** | **95** | | | |

**Release recommendation:** Ship / Hold — reason: ______________________________

**Blocking failures (must fix before merge to main):**

1.
2.
3.

**Non-blocking issues (log to `docs/TODO.md`):**

1.
2.
3.
