"""Semantic version parsing and ordering — the correctness core of the updater."""

from __future__ import annotations

import pytest

from optionspilot.update.version import Version


class TestParse:
    def test_plain(self):
        v = Version.parse("1.2.3")
        assert (v.major, v.minor, v.patch) == (1, 2, 3)
        assert v.is_stable and not v.is_prerelease

    def test_leading_v_tolerated(self):
        assert Version.parse("v0.5.0") == Version.parse("0.5.0")

    def test_prerelease_and_build(self):
        v = Version.parse("1.0.0-beta.2+build.7")
        assert v.prerelease == ("beta", "2")
        assert v.build == ("build", "7")
        assert v.is_prerelease

    @pytest.mark.parametrize("bad", ["", "1", "1.2", "1.2.x", "abc", "01.2.3", None])
    def test_invalid_raises(self, bad):
        with pytest.raises((ValueError, TypeError)):
            Version.parse(bad)

    def test_try_parse_returns_none(self):
        assert Version.try_parse("not-a-version") is None
        assert Version.try_parse("v1.2.3") == Version.parse("1.2.3")


class TestOrdering:
    def test_numeric_not_lexical(self):
        # The whole point: 0.4.10 is NEWER than 0.4.9 despite sorting before it.
        assert Version.parse("0.4.10") > Version.parse("0.4.9")
        assert Version.parse("0.5.0") > Version.parse("0.4.99")
        assert Version.parse("1.0.0") > Version.parse("0.99.99")

    def test_full_chain(self):
        chain = ["0.4.9", "0.4.10", "0.5.0", "1.0.0", "1.0.1", "2.0.0"]
        versions = [Version.parse(v) for v in chain]
        assert versions == sorted(versions)

    def test_prerelease_lower_than_release(self):
        assert Version.parse("1.0.0-beta.1") < Version.parse("1.0.0")
        assert Version.parse("1.0.0") > Version.parse("1.0.0-rc.9")

    def test_prerelease_ordering(self):
        order = ["1.0.0-alpha", "1.0.0-alpha.1", "1.0.0-beta",
                 "1.0.0-beta.2", "1.0.0-beta.11", "1.0.0-rc.1", "1.0.0"]
        vs = [Version.parse(v) for v in order]
        assert vs == sorted(vs)

    def test_numeric_prerelease_below_alpha(self):
        # SemVer §11.4.3: numeric identifiers rank below alphanumeric ones.
        assert Version.parse("1.0.0-1") < Version.parse("1.0.0-alpha")

    def test_build_metadata_ignored(self):
        assert Version.parse("1.2.3+a") == Version.parse("1.2.3+b")
        assert not (Version.parse("1.2.3+a") < Version.parse("1.2.3+b"))

    def test_hashable(self):
        s = {Version.parse("1.0.0"), Version.parse("1.0.0"), Version.parse("1.0.1")}
        assert len(s) == 2


class TestFormatting:
    def test_str_roundtrip(self):
        for text in ["1.2.3", "0.5.0-beta.1", "1.0.0-rc.2"]:
            assert str(Version.parse(text)) == text

    def test_base_and_with_v(self):
        v = Version.parse("1.2.3-beta.1")
        assert v.base() == "1.2.3"
        assert Version.parse("0.5.0").with_v() == "v0.5.0"
