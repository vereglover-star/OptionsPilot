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
import re
from dataclasses import dataclass, field
from pathlib import Path

from optionspilot.core.logging_setup import get_logger
from optionspilot.update.github_api import INSTALLER_RE
from optionspilot.update.models import Assurance

log = get_logger("update")

_HASH_CHUNK = 1024 * 1024

#: One line of `sha256sum` output: 64 hex digits, whitespace, then the file
#: name. The optional `*` marks binary mode and is part of the standard format.
_SUM_LINE = re.compile(r"^\s*([0-9a-fA-F]{64})\s+\*?(.+?)\s*$")


@dataclass
class ValidationResult:
    """The verdict of :func:`validate`. ``ok`` gates whether install proceeds."""

    ok: bool
    checks: list[tuple[str, bool, str]] = field(default_factory=list)  # (name, passed, detail)
    #: HOW the file was verified, not merely whether. See models.Assurance —
    #: "verified" must not be sayable about a file whose digest nobody checked.
    assurance: Assurance = Assurance.FAILED

    @property
    def failures(self) -> list[tuple[str, bool, str]]:
        return [c for c in self.checks if not c[1]]

    @property
    def message(self) -> str:
        if self.ok:
            return self.assurance.summary
        first = self.failures[0]
        return first[2] or f"Validation check {first[0]!r} failed."


def sha256_file(path: Path | str) -> str:
    """Streaming SHA-256 of a file (hex)."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(_HASH_CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def parse_sha256sums(text: str) -> dict[str, str]:
    """Parse a `SHA256SUMS` manifest into ``{filename: digest}``.

    Accepts the standard `sha256sum` output format, in both text and binary
    (`*`) modes, and tolerates blank lines and `#` comments. Unparseable lines
    are skipped rather than raising: a manifest that gains a trailing note
    should not make a good digest unreadable.

    Returns an empty mapping for input that contains no usable line at all —
    the caller treats that as a FAILURE, not as "no checksum available",
    because a published-but-unreadable manifest is a reason for suspicion.
    """
    sums: dict[str, str] = {}
    for line in (text or "").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = _SUM_LINE.match(line)
        if match is None:
            continue
        digest, name = match.group(1).lower(), match.group(2).strip()
        # Manifests are usually generated in the directory holding the files,
        # but tolerate a path prefix rather than failing to find the entry.
        sums[Path(name.replace("\\", "/")).name] = digest
    return sums


def digest_for(text: str, filename: str) -> str | None:
    """The digest a manifest publishes for one file name, or None."""
    return parse_sha256sums(text).get(Path(filename).name)


def validate(path: Path | str, *, expected_size: int | None = None,
             expected_sha256: str | None = None,
             expected_name: str | None = None,
             checksums_published: bool = False) -> ValidationResult:
    """Run every applicable check against a downloaded installer.

    A check is only run when it has something to check against (e.g. a size is
    only compared when ``expected_size`` is known), so the layer is honest about
    what it actually verified — the caller can inspect ``checks`` to see.

    ``checksums_published`` distinguishes the two reasons ``expected_sha256``
    can be ``None``, and they are not the same reason at all:

    * **No manifest exists.** True of every release before V0.9.0. The install
      proceeds at :attr:`Assurance.SIZE_ONLY` and the user is told so. Failing
      here would strand every existing installation on its current version —
      the update client doing the checking is the OLD one.
    * **A manifest exists but does not cover this file.** The release went to
      the trouble of publishing checksums and this download is not among them.
      That is a discrepancy, not an absence, and it FAILS.
    """
    path = Path(path)
    checks: list[tuple[str, bool, str]] = []

    exists = path.is_file()
    checks.append(("exists", exists,
                   "" if exists else "The downloaded update file is missing."))
    if not exists:
        return ValidationResult(ok=False, checks=checks,
                                assurance=Assurance.FAILED)

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
        hash_ok = actual.lower() == expected_sha256.strip().lower()
        checks.append(("sha256", hash_ok, "" if hash_ok else
                       "The update failed its integrity check (hash mismatch). "
                       "The downloaded file is not the one this release "
                       "published, so it will not be installed."))
    elif checksums_published:
        # A manifest was published and this file is not in it (or it could not
        # be read). Absence of a digest HERE is evidence, not a missing feature.
        checks.append(("sha256_missing", False,
                       "This release publishes checksums, but none covers the "
                       "downloaded file. The update will not be installed."))

    ok = all(passed for _, passed, _ in checks)
    if not ok:
        assurance = Assurance.FAILED
        log.warning("update validation failed: %s",
                    [(n, d) for n, p, d in checks if not p])
    elif expected_sha256:
        assurance = Assurance.HASH_VERIFIED
    else:
        assurance = Assurance.SIZE_ONLY
        log.info("update validated at reduced assurance: no published checksum "
                 "for %s", path.name)
    return ValidationResult(ok=ok, checks=checks, assurance=assurance)
