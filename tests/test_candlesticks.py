from optionspilot.analysis import candlesticks as cs
from tests.conftest import df_from_ohlc


class TestSingleBar:
    def test_hammer(self):
        df = df_from_ohlc([
            (100.0, 100.6, 99.5, 100.2),          # ordinary bar
            (100.0, 100.6, 97.0, 100.5),          # long lower wick, body on top
        ])
        out = cs.hammer(df)
        assert not out.iloc[0] and out.iloc[1]

    def test_shooting_star(self):
        df = df_from_ohlc([
            (100.0, 100.6, 99.5, 100.2),
            (100.5, 103.5, 99.9, 100.0),          # long upper wick, body at bottom
        ])
        out = cs.shooting_star(df)
        assert not out.iloc[0] and out.iloc[1]

    def test_hammer_is_not_shooting_star(self):
        df = df_from_ohlc([(100.0, 100.6, 97.0, 100.5)])
        assert cs.hammer(df).iloc[0] and not cs.shooting_star(df).iloc[0]

    def test_doji(self):
        df = df_from_ohlc([
            (100.0, 101.0, 99.0, 100.05),         # tiny body, real range
            (100.0, 101.0, 99.0, 100.8),          # solid body
        ])
        out = cs.doji(df)
        assert out.iloc[0] and not out.iloc[1]

    def test_marubozu(self):
        df = df_from_ohlc([
            (100.0, 102.05, 99.98, 102.0),        # nearly all body, up
            (102.0, 102.02, 99.95, 100.0),        # nearly all body, down
            (100.0, 101.0, 99.0, 100.3),          # wicky bar
        ])
        out = cs.marubozu(df)
        assert out["bullish_marubozu"].tolist() == [True, False, False]
        assert out["bearish_marubozu"].tolist() == [False, True, False]


class TestTwoBar:
    def test_bullish_engulfing(self):
        df = df_from_ohlc([
            (101.0, 101.2, 99.8, 100.0),          # bearish
            (99.8, 101.7, 99.6, 101.5),           # bullish, engulfs prior body
        ])
        out = cs.engulfing(df)
        assert out["bullish_engulfing"].iloc[1]
        assert not out["bearish_engulfing"].iloc[1]

    def test_bearish_engulfing(self):
        df = df_from_ohlc([
            (100.0, 101.2, 99.8, 101.0),          # bullish
            (101.2, 101.4, 99.4, 99.6),           # bearish, engulfs prior body
        ])
        assert cs.engulfing(df)["bearish_engulfing"].iloc[1]

    def test_partial_cover_is_not_engulfing(self):
        df = df_from_ohlc([
            (101.0, 101.2, 99.8, 100.0),
            (100.5, 101.3, 100.3, 101.2),         # bullish but opens above prev close
        ])
        assert not cs.engulfing(df)["bullish_engulfing"].iloc[1]

    def test_inside_and_outside_bar(self):
        df = df_from_ohlc([
            (102.0, 105.0, 100.0, 103.0),
            (103.0, 104.0, 101.0, 102.0),         # inside
            (102.0, 106.0, 100.5, 105.0),         # outside vs bar 2
        ])
        assert cs.inside_bar(df).tolist() == [False, True, False]
        assert cs.outside_bar(df).tolist() == [False, False, True]


class TestThreeBar:
    def test_morning_star(self):
        df = df_from_ohlc([
            (102.0, 102.2, 99.8, 100.0),          # strong bearish
            (99.9, 100.3, 99.7, 100.05),          # small pause bar
            (100.2, 101.9, 100.1, 101.8),         # strong bullish past midpoint
        ])
        assert cs.morning_star(df).iloc[2]
        assert not cs.evening_star(df).iloc[2]

    def test_evening_star(self):
        df = df_from_ohlc([
            (100.0, 102.2, 99.9, 102.0),
            (102.1, 102.4, 101.9, 102.05),
            (101.9, 102.0, 100.1, 100.2),
        ])
        assert cs.evening_star(df).iloc[2]

    def test_three_white_soldiers(self):
        df = df_from_ohlc([
            (100.0, 101.6, 99.9, 101.5),
            (101.0, 102.9, 100.9, 102.8),         # opens inside prev body
            (102.2, 104.1, 102.1, 104.0),
        ])
        assert cs.three_white_soldiers(df).iloc[2]
        assert not cs.three_black_crows(df).iloc[2]

    def test_three_black_crows(self):
        df = df_from_ohlc([
            (104.0, 104.1, 102.4, 102.5),
            (103.0, 103.1, 101.1, 101.2),
            (101.8, 101.9, 99.9, 100.0),
        ])
        assert cs.three_black_crows(df).iloc[2]


class TestDetectAll:
    def test_shape_and_no_nans(self):
        df = df_from_ohlc([
            (100.0, 100.6, 99.5, 100.2),
            (100.0, 100.6, 97.0, 100.5),
            (100.5, 103.5, 99.9, 100.0),
        ])
        out = cs.detect_all(df)
        assert len(out) == len(df)
        assert out.dtypes.eq(bool).all()
        assert out["hammer"].iloc[1] and out["shooting_star"].iloc[2]
