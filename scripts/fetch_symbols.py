"""Regenerate the bundled US symbol directory (optionspilot/data_assets/symbols.csv).

Sources: Nasdaq Trader symbol directories (public, no key):
  - nasdaqlisted.txt  (NASDAQ listings)
  - otherlisted.txt   (NYSE / AMEX / ARCA listings)

Keeps common stocks and ETFs with plain 1-5 letter symbols, drops test issues
and preferred/when-issued/units symbology. Run occasionally to refresh:
    .venv\\Scripts\\python scripts\\fetch_symbols.py
"""

from __future__ import annotations

import csv
import re
import urllib.request
from pathlib import Path

SOURCES = {
    "nasdaq": "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt",
    "other": "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt",
}
OUT = Path(__file__).resolve().parents[1] / "optionspilot" / "data_assets" / "symbols.csv"
PLAIN = re.compile(r"^[A-Z]{1,5}$")


def fetch(url: str) -> list[str]:
    with urllib.request.urlopen(url, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace").splitlines()


def main() -> None:
    rows: dict[str, str] = {}

    for line in fetch(SOURCES["nasdaq"])[1:]:
        parts = line.split("|")
        if len(parts) < 7 or parts[3] == "Y":      # test issue
            continue
        symbol, name = parts[0].strip(), parts[1].strip()
        if PLAIN.match(symbol) and name:
            rows.setdefault(symbol, name)

    for line in fetch(SOURCES["other"])[1:]:
        parts = line.split("|")
        if len(parts) < 8 or parts[6] == "Y":      # test issue
            continue
        symbol, name = parts[0].strip(), parts[1].strip()
        if PLAIN.match(symbol) and name:
            rows.setdefault(symbol, name)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["symbol", "name"])
        for symbol in sorted(rows):
            writer.writerow([symbol, rows[symbol]])
    print(f"wrote {len(rows)} symbols -> {OUT}")


if __name__ == "__main__":
    main()
