"""Validate a downloaded installer before it is ever executed.

This is the security gate between "a file arrived" and "we are about to run an
installer with admin rights." Today it enforces what we can verify offline and
for free:

  * the file exists and is a regular file;
  * its size matches the size GitHub reported for the asset (guards against a
    truncated or padded download);
  * the name still matches the trusted installer pattern.

It is deliberately structured as an ordered list of independent *checks* so the
future security work the milestone calls for slots in without touching callers:

  * **SHA-256 hash verification** — pass ``expected_sha256`` (from a checksums
    asset published alongside the installer) and it is enforced here.
  * **Authenticode signature verification** — add a check that shells out to
    ``signtool verify`` / WinVerifyTrust and append it to :func:`validate`;
    every caller already treats a failed :class:`ValidationResult` as "do not
    install", so no call site changes.

Pure and offline: hashing reads the local file only. No network here.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from optionspilot.core.logging_setup import get_logger
from optionspilot.update.github_api import INSTALLER_RE

log = get_logger("update")

_HASH_CHUNK = 1024 * 1024


@dataclass
class ValidationResult:
    """The verdict of :func:`validate`. ``ok`` gates whether install proceeds."""

    ok: bool
    checks: list[tuple[str, bool, str]] = field(default_factory=list)  # (name, passed, detail)

    @property
    def failures(self) -> list[tuple[str, bool, str]]:
        return [c for c in self.checks if not c[1]]

    @property
    def message(self) -> str:
        if self.ok:
            return "Update verified."
        first = self.failures[0]
        return first[2] or f"Validation check {first[0]!r} failed."


def sha256_file(path: Path | str) -> str:
    """Streaming SHA-256 of a file (hex). Future hash-verification input."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(_HASH_CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def validate(path: Path | str, *, expected_size: int | None = None,
             expected_sha256: str | None = None,
             expected_name: str | None = None) -> ValidationResult:
    """Run every applicable check against a downloaded installer.

    A check is only run when it has something to check against (e.g. a size is
    only compared when ``expected_size`` is known), so the layer is honest about
    what it actually verified — the caller can inspect ``checks`` to see.
    """
    path = Path(path)
    checks: list[tuple[str, bool, str]] = []

    exists = path.is_file()
    checks.append(("exists", exists,
                   "" if exists else "The downloaded update file is missing."))
    if not exists:
        return ValidationResult(ok=False, checks=checks)

    name_ref = expected_name or path.name
    name_ok = bool(INSTALLER_RE.search(name_ref))
    checks.append(("name", name_ok,
                   "" if name_ok else
                   "The downloaded file is not a recognised OptionsPilot installer."))

    actual_size = path.stat().st_size
    if expected_size is not None and expected_size > 0:
        size_ok = actual_size == expected_size
        checks.append(("size", size_ok, "" if size_ok else
                       f"The download is incomplete "
                       f"({actual_size:,} of {expected_size:,} bytes)."))
    else:
        # No reference size — at least reject an obviously empty file.
        nonempty = actual_size > 0
        checks.append(("nonempty", nonempty,
                       "" if nonempty else "The downloaded update file is empty."))

    if expected_sha256:
        actual = sha256_file(path)
        hash_ok = actual.lower() == expected_sha256.lower()
        checks.append(("sha256", hash_ok, "" if hash_ok else
                       "The update failed its integrity check (hash mismatch)."))

    ok = all(passed for _, passed, _ in checks)
    if not ok:
        log.warning("update validation failed: %s",
                    [(n, d) for n, p, d in checks if not p])
    return ValidationResult(ok=ok, checks=checks)
