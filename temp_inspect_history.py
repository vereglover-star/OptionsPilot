from datetime import datetime, timedelta, timezone
from optionspilot.core.models import Timeframe
from optionspilot.data.yfinance_provider import YFinanceProvider

provider = YFinanceProvider(min_request_interval=0.0)
end = datetime(2026, 7, 22, tzinfo=timezone.utc)
for tf in [Timeframe.M1, Timeframe.M2, Timeframe.M3, Timeframe.M5, Timeframe.M10, Timeframe.M15, Timeframe.M30, Timeframe.H1, Timeframe.H2, Timeframe.H4, Timeframe.D1]:
    start = end - timedelta(days=365)
    df = provider.get_candles('SPY', tf, start, end)
    print(tf, len(df), df.index[0], df.index[-1])
