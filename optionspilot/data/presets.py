"""One-click watchlist presets.

Static, deliberately liquid, options-friendly names. "My Favorites" is not
here — it's the user's own saved list, stored in runtime settings.
"""

PRESETS: dict[str, list[str]] = {
    "Magnificent 7": ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA"],
    "S&P 500 Leaders": ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META",
                        "AVGO", "JPM", "LLY", "V"],
    "AI Stocks": ["NVDA", "MSFT", "GOOGL", "META", "PLTR", "AMD", "AVGO", "SMCI"],
    "Semiconductors": ["NVDA", "AMD", "AVGO", "INTC", "MU", "TSM", "QCOM", "ARM"],
    "EV Stocks": ["TSLA", "RIVN", "LCID", "NIO", "GM", "F"],
    "Banking": ["JPM", "BAC", "WFC", "C", "GS", "MS"],
    "Healthcare": ["UNH", "JNJ", "LLY", "PFE", "ABBV", "MRK"],
    "Energy": ["XOM", "CVX", "COP", "SLB", "OXY"],
    "Meme Stocks": ["GME", "AMC", "PLTR", "HOOD", "COIN", "RIOT"],
}
