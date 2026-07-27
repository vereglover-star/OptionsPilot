"""Semantic version parsing and ordering.

The single most important correctness rule of an updater: **never compare
version strings lexicographically.** `"0.4.10"` is newer than `"0.4.9"` even
though it sorts *before* it as text, and `"0.5.0"` is newer than `"0.4.99"`.
This module parses `MAJOR.MINOR.PATCH[-prerelease][+build]` into integer
components and orders them per the Semantic Versioning 2.0.0 rules:

    0.4.9  <  0.4.10  <  0.5.0  <  1.0.0
    1.0.0-beta.1  <  1.0.0-beta.2  <  1.0.0-rc.1  <  1.0.0

A leading ``v`` (as GitHub tags carry — ``v0.5.0``) is accepted and ignored.
Build metadata (``+...``) is parsed but, per SemVer, ignored for ordering.

Pure standard library, no dependencies — the app ships no ``packaging`` at
runtime, and reimplementing the small subset we need keeps the updater
self-contained and offline-testable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import total_ordering

# MAJOR.MINOR.PATCH, optional -prerelease, optional +build. Prerelease/build are
# dot-separated identifiers of [0-9A-Za-z-]; leading 'v'/'V' tolerated.
_SEMVER_RE = re.compile(
    r"^\s*[vV]?"
    r"(?P<major>0|[1-9]\d*)\."
    r"(?P<minor>0|[1-9]\d*)\."
    r"(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<prerelease>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+(?P<build>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"\s*$"
)


@total_ordering
@dataclass(frozen=True)
class Version:
    """An immutable, correctly-orderable semantic version.

    Construct via :meth:`parse` (tolerant of a leading ``v``) or directly.
    Instances are hashable and comparable; ``==`` ignores build metadata,
    matching SemVer precedence rules.
    """

    major: int
    minor: int
    patch: int
    prerelease: tuple[str, ...] = field(default_factory=tuple)
    build: tuple[str, ...] = field(default_factory=tuple)

    # ── construction ─────────────────────────────────────────────────────────
    @classmethod
    def parse(cls, text: str) -> "Version":
        """Parse ``MAJOR.MINOR.PATCH[-pre][+build]`` (leading ``v`` allowed).

        Raises :class:`ValueError` on anything that is not a valid SemVer core —
        callers that must not raise (the background checker) catch this and treat
        the release as unusable rather than crashing.
        """
        if not isinstance(text, str):
            raise ValueError(f"version must be a string, got {type(text).__name__}")
        m = _SEMVER_RE.match(text)
        if not m:
            raise ValueError(f"not a semantic version: {text!r}")
        pre = tuple(m.group("prerelease").split(".")) if m.group("prerelease") else ()
        build = tuple(m.group("build").split(".")) if m.group("build") else ()
        return cls(int(m.group("major")), int(m.group("minor")),
                   int(m.group("patch")), pre, build)

    @classmethod
    def try_parse(cls, text: str) -> "Version | None":
        """:meth:`parse` that returns ``None`` instead of raising."""
        try:
            return cls.parse(text)
        except (ValueError, TypeError):
            return None

    # ── properties ───────────────────────────────────────────────────────────
    @property
    def is_prerelease(self) -> bool:
        return bool(self.prerelease)

    @property
    def is_stable(self) -> bool:
        return not self.prerelease

    @property
    def core(self) -> tuple[int, int, int]:
        return (self.major, self.minor, self.patch)

    # ── ordering (SemVer 2.0.0 §11) ──────────────────────────────────────────
    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        # Build metadata is ignored for precedence/equality per SemVer.
        return self.core == other.core and self.prerelease == other.prerelease

    def __hash__(self) -> int:
        return hash((self.core, self.prerelease))

    def __lt__(self, other: "Version") -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        if self.core != other.core:
            return self.core < other.core
        # A version WITH a prerelease is *lower* than the same core WITHOUT one.
        if self.prerelease and not other.prerelease:
            return True
        if not self.prerelease and other.prerelease:
            return False
        return self._pre_key() < other._pre_key()

    def _pre_key(self) -> tuple:
        """Comparable key for the prerelease identifiers (SemVer §11.4):
        numeric identifiers compare numerically and rank below alphanumeric
        ones; a shorter run of otherwise-equal identifiers ranks lower."""
        key: list[tuple[int, object]] = []
        for ident in self.prerelease:
            if ident.isdigit():
                # (0, n): numeric identifiers always sort before alphanumeric.
                key.append((0, int(ident)))
            else:
                key.append((1, ident))
        return tuple(key)

    # ── formatting ───────────────────────────────────────────────────────────
    def base(self) -> str:
        """``MAJOR.MINOR.PATCH`` with no prerelease/build suffix."""
        return f"{self.major}.{self.minor}.{self.patch}"

    def __str__(self) -> str:
        s = self.base()
        if self.prerelease:
            s += "-" + ".".join(self.prerelease)
        if self.build:
            s += "+" + ".".join(self.build)
        return s

    def with_v(self) -> str:
        """Git-tag form, e.g. ``v0.5.0``."""
        return "v" + str(self)
