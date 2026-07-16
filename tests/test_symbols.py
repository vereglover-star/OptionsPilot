from optionspilot.data import symbols as sym
from optionspilot.data.presets import PRESETS


class TestParse:
    def test_single_ticker_any_case(self):
        assert sym.parse_symbols("aapl") == ["AAPL"]

    def test_comma_list(self):
        assert sym.parse_symbols("AAPL, TSLA, NVDA, AMD, META") == \
            ["AAPL", "TSLA", "NVDA", "AMD", "META"]

    def test_newline_list(self):
        assert sym.parse_symbols("AAPL\nTSLA\nNVDA\nAMD\nMETA") == \
            ["AAPL", "TSLA", "NVDA", "AMD", "META"]

    def test_mixed_separators_and_cashtags(self):
        raw = "$aapl tsla;NVDA,\n amd\t$META  aapl"
        assert sym.parse_symbols(raw) == ["AAPL", "TSLA", "NVDA", "AMD", "META"]

    def test_duplicates_collapse_preserving_order(self):
        assert sym.parse_symbols("tsla AAPL tsla aapl") == ["TSLA", "AAPL"]

    def test_garbage_yields_nothing(self):
        assert sym.parse_symbols("12345 !!! 🚀🚀") == []


class TestDirectory:
    def test_known_symbols(self):
        for s in ("AAPL", "SPY", "TSLA", "GME"):
            assert sym.is_known(s) and sym.is_known(s.lower())
        assert not sym.is_known("ZZZZZ")

    def test_company_name(self):
        assert "Apple" in sym.company_name("aapl")
        assert sym.company_name("ZZZZZ") == ""

    def test_search_spec_example(self):
        # typing "app" must surface AAPL, APP, APPF
        hits = {r["symbol"] for r in sym.search("app", limit=8)}
        assert {"APP", "APPF"} <= hits          # prefix matches
        assert "AAPL" in {r["symbol"] for r in sym.search("aapl")}

    def test_search_exact_symbol_first(self):
        assert sym.search("APP")[0]["symbol"] == "APP"

    def test_search_by_company_name(self):
        assert any(r["symbol"] == "TSLA" for r in sym.search("tesla"))

    def test_search_empty(self):
        assert sym.search("") == []


class TestPresets:
    def test_all_preset_symbols_are_valid(self):
        for name, symbols in PRESETS.items():
            assert symbols, name
            for s in symbols:
                assert sym.is_known(s), f"{name}: {s} not in directory"
