# Marks `tests/` as an importable package so `from tests.conftest import ...`
# (and the other intra-suite helper imports) resolve consistently across
# environments — not just where the project root happens to be on sys.path.
