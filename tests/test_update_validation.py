"""Validation layer — the gate between 'downloaded' and 'about to run installer'."""

from __future__ import annotations

from optionspilot.update.validation import sha256_file, validate

NAME = "OptionsPilot-Setup-v0.5.0.exe"


def _write(tmp_path, data=b"installer-bytes", name=NAME):
    p = tmp_path / name
    p.write_bytes(data)
    return p


class TestValidate:
    def test_ok_with_matching_size(self, tmp_path):
        p = _write(tmp_path, b"abc")
        r = validate(p, expected_size=3)
        assert r.ok and not r.failures

    def test_missing_file(self, tmp_path):
        r = validate(tmp_path / "nope.exe", expected_size=10)
        assert not r.ok
        assert "missing" in r.message.lower()

    def test_size_mismatch(self, tmp_path):
        p = _write(tmp_path, b"abc")
        r = validate(p, expected_size=999)
        assert not r.ok
        assert "incomplete" in r.message.lower()

    def test_empty_file_rejected_without_size(self, tmp_path):
        p = _write(tmp_path, b"")
        r = validate(p)                      # no expected size
        assert not r.ok

    def test_bad_name_rejected(self, tmp_path):
        p = _write(tmp_path, b"abc", name="totally-not-setup.exe")
        r = validate(p, expected_size=3)
        assert not r.ok
        assert "installer" in r.message.lower()

    def test_sha256_enforced_when_given(self, tmp_path):
        p = _write(tmp_path, b"abc")
        good = sha256_file(p)
        assert validate(p, expected_size=3, expected_sha256=good).ok
        assert not validate(p, expected_size=3, expected_sha256="deadbeef").ok

    def test_records_all_checks(self, tmp_path):
        p = _write(tmp_path, b"abc")
        r = validate(p, expected_size=3)
        names = {c[0] for c in r.checks}
        assert {"exists", "name", "size"} <= names


def test_sha256_file_matches_hashlib(tmp_path):
    import hashlib
    p = tmp_path / "f.bin"
    p.write_bytes(b"hello world" * 1000)
    assert sha256_file(p) == hashlib.sha256(b"hello world" * 1000).hexdigest()
