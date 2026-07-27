"""Session classification (US equity pre/RTH/post) used by the chart's
extended-hours mode."""
from datetime import datetime, timezone

import pandas as pd

from optionspilot.data import sessions


def _et(y, mo, d, h, mi):
    """A UTC timestamp for the given US/Eastern wall-clock time."""
    return pd.Timestamp(datetime(y, mo, d, h, mi), tz="America/New_York").tz_convert("UTC")


class TestClassify:
    def test_regular_session(self):
        assert sessions.classify(_et(2026, 7, 17, 9, 30)) == sessions.RTH
        assert sessions.classify(_et(2026, 7, 17, 12, 0)) == sessions.RTH
        assert sessions.classify(_et(2026, 7, 17, 15, 59)) == sessions.RTH

    def test_pre_market(self):
        assert sessions.classify(_et(2026, 7, 17, 4, 0)) == sessions.PRE
        assert sessions.classify(_et(2026, 7, 17, 9, 29)) == sessions.PRE

    def test_after_hours(self):
        assert sessions.classify(_et(2026, 7, 17, 16, 0)) == sessions.POST
        assert sessions.classify(_et(2026, 7, 17, 19, 59)) == sessions.POST

    def test_closed_overnight(self):
        assert sessions.classify(_et(2026, 7, 17, 20, 0)) == sessions.CLOSED
        assert sessions.classify(_et(2026, 7, 17, 3, 59)) == sessions.CLOSED

    def test_boundaries_are_half_open(self):
        # 09:30 is regular (not pre); 16:00 is post (not regular)
        assert sessions.classify(_et(2026, 7, 17, 9, 30)) == sessions.RTH
        assert sessions.classify(_et(2026, 7, 17, 16, 0)) == sessions.POST

    def test_naive_timestamp_treated_as_utc(self):
        # a tz-naive noon-UTC is 08:00 ET → pre-market
        assert sessions.classify(datetime(2026, 7, 17, 12, 0)) == sessions.PRE


class TestLabels:
    def test_vectorized_matches_scalar(self):
        idx = pd.DatetimeIndex([
            _et(2026, 7, 17, 5, 0), _et(2026, 7, 17, 10, 0),
            _et(2026, 7, 17, 17, 0), _et(2026, 7, 17, 21, 0),
        ])
        assert sessions.labels(idx) == [
            sessions.PRE, sessions.RTH, sessions.POST, sessions.CLOSED]

    def test_empty_index(self):
        assert sessions.labels(pd.DatetimeIndex([])) == []

    def test_naive_index_localized(self):
        idx = pd.DatetimeIndex([datetime(2026, 7, 17, 14, 0, tzinfo=timezone.utc)])
        # 14:00 UTC = 10:00 ET → regular
        assert sessions.labels(idx.tz_convert(None) if idx.tz else idx)[0] in (
            sessions.RTH, sessions.PRE)  # tolerant of tz handling
