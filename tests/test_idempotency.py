"""V0.9.2-C9 — per-key locking and request fingerprints (findings N-1, N-2).

`IdempotencyStore` had two defects that only appear under concurrency or under
a misbehaving client, which is why neither had a test.

**N-1: one lock for every key.** `execute` held a single store-wide `RLock`
across the callback — deliberately, because "execute once per key" is the whole
contract — but that also serialised *unrelated* mutations. A workspace update
waited behind an update check talking to GitHub. The contract is per key; the
lock is now per key too.

**N-2: no request fingerprint.** An idempotency key replayed with a *different*
body returned the first request's result and reported success. That is the one
case the idempotency-key specification says must fail: the client has reused a
key for a different operation, and silently answering with someone else's result
is worse than refusing, because nothing anywhere records that it happened.

The first three classes below are characterization — they pin behaviour that
existed before C9 and must survive it.
"""

from __future__ import annotations

import threading
import time

import pytest

from optionspilot.services.errors import Conflict
from optionspilot.services.idempotency import IdempotencyStore, fingerprint


@pytest.fixture
def store(tmp_path):
    s = IdempotencyStore(tmp_path / "idem.db")
    yield s
    s.close()


class TestTheExistingContractIsUnchanged:
    def test_a_first_call_executes_and_is_not_a_replay(self, store):
        calls = []
        result, replayed = store.execute("op", "k1",
                                         lambda: calls.append(1) or {"n": 1})
        assert result == {"n": 1} and replayed is False and len(calls) == 1

    def test_a_second_call_replays_without_re_executing(self, store):
        calls = []

        def run():
            calls.append(1)
            return {"n": len(calls)}

        first, _ = store.execute("op", "k1", run)
        second, replayed = store.execute("op", "k1", run)
        assert second == first and replayed is True
        assert len(calls) == 1, "the callback ran twice"

    def test_no_key_means_always_execute(self, store):
        calls = []
        for _ in range(3):
            _, replayed = store.execute("op", None,
                                        lambda: calls.append(1) or {})
            assert replayed is False
        assert len(calls) == 3

    def test_the_same_key_under_a_different_operation_is_a_different_record(
            self, store):
        store.execute("op.a", "k", lambda: {"which": "a"})
        result, replayed = store.execute("op.b", "k", lambda: {"which": "b"})
        assert result == {"which": "b"} and replayed is False

    def test_a_closed_store_refuses_rather_than_corrupting(self, store):
        store.close()
        with pytest.raises(RuntimeError, match="closed"):
            store.execute("op", "k", lambda: {})

    def test_records_survive_a_reopen(self, tmp_path):
        """Durable across a desktop restart — the point of it being SQLite."""
        path = tmp_path / "idem.db"
        first = IdempotencyStore(path)
        first.execute("op", "k", lambda: {"n": 1})
        first.close()
        second = IdempotencyStore(path)
        try:
            result, replayed = second.execute("op", "k", lambda: {"n": 2})
            assert result == {"n": 1} and replayed is True
        finally:
            second.close()


class TestTheFingerprintIsStable:
    def test_key_order_does_not_change_the_fingerprint(self):
        """A JSON object is unordered; two clients serialising the same request
        must not disagree about whether it is the same request."""
        assert fingerprint({"a": 1, "b": 2}) == fingerprint({"b": 2, "a": 1})

    def test_a_different_value_changes_it(self):
        assert fingerprint({"a": 1}) != fingerprint({"a": 2})

    def test_none_and_an_empty_body_are_distinguishable_from_content(self):
        assert fingerprint(None) != fingerprint({"a": 1})
        assert fingerprint(None) == fingerprint(None)

    def test_it_survives_values_json_cannot_encode(self):
        """Payloads reach this from a client, and a `default=str` fallback is
        what stops an un-encodable value turning a mutation into a 500."""
        from datetime import date

        assert isinstance(fingerprint({"when": date(2026, 1, 1)}), str)


class TestAReusedKeyWithADifferentRequestIsRefused:
    """Finding N-2."""

    def test_the_same_key_and_the_same_body_still_replays(self, store):
        body = {"symbol": "SPY", "quantity": 1}
        store.execute("orders.place", "k", lambda: {"ok": True},
                      fingerprint=fingerprint(body))
        result, replayed = store.execute("orders.place", "k",
                                         lambda: {"ok": False},
                                         fingerprint=fingerprint(body))
        assert result == {"ok": True} and replayed is True

    def test_the_same_key_with_a_different_body_raises_conflict(self, store):
        """The defect: this used to return the FIRST order's result and report
        success, so a client that reused a key for a different order was told
        its second order had been placed. It had not."""
        store.execute("orders.place", "k", lambda: {"ok": True},
                      fingerprint=fingerprint({"symbol": "SPY"}))
        with pytest.raises(Conflict) as caught:
            store.execute("orders.place", "k", lambda: {"ok": True},
                          fingerprint=fingerprint({"symbol": "QQQ"}))
        assert caught.value.code == "conflict"

    def test_the_refusal_does_not_run_the_callback(self, store):
        ran = []
        store.execute("op", "k", lambda: {}, fingerprint=fingerprint({"a": 1}))
        with pytest.raises(Conflict):
            store.execute("op", "k", lambda: ran.append(1),
                          fingerprint=fingerprint({"a": 2}))
        assert ran == []

    def test_a_record_written_before_fingerprints_existed_still_replays(
            self, store):
        """Migration reality: rows already on disk have no fingerprint.

        Refusing them would turn an upgrade into a wall of 409s for keys the
        user's client legitimately still holds. A NULL fingerprint cannot prove
        a mismatch, so it replays.
        """
        store.execute("op", "k", lambda: {"n": 1})          # no fingerprint
        result, replayed = store.execute("op", "k", lambda: {"n": 2},
                                         fingerprint=fingerprint({"a": 1}))
        assert result == {"n": 1} and replayed is True

    def test_a_caller_that_supplies_no_fingerprint_is_not_refused(self, store):
        """Not every mutation carries a body worth hashing, and a caller that
        opts out must not be worse off than before C9."""
        store.execute("op", "k", lambda: {"n": 1},
                      fingerprint=fingerprint({"a": 1}))
        result, replayed = store.execute("op", "k", lambda: {"n": 2})
        assert result == {"n": 1} and replayed is True


class TestUnrelatedKeysDoNotBlockEachOther:
    """Finding N-1, and it needs threads to see at all."""

    def test_a_slow_mutation_does_not_serialise_a_different_key(self, store):
        started = threading.Event()
        release = threading.Event()
        finished: list[str] = []

        def slow():
            started.set()
            release.wait(10)
            finished.append("slow")
            return {}

        def quick():
            finished.append("quick")
            return {}

        worker = threading.Thread(
            target=lambda: store.execute("op", "slow-key", slow))
        worker.start()
        try:
            assert started.wait(5), "the slow mutation never started"
            # The whole point: this must complete while the other key is held.
            store.execute("op", "other-key", quick)
            assert finished == ["quick"], (
                "a mutation on an unrelated key waited for the slow one — "
                "the store is still using a single global lock")
        finally:
            release.set()
            worker.join(10)
        assert finished == ["quick", "slow"]

    def test_the_same_key_is_still_serialised(self, store):
        """The contract that must NOT be relaxed: one execution per key.

        Two threads racing the same key must produce exactly one callback run
        and one replay.
        """
        runs: list[int] = []
        barrier = threading.Barrier(2)
        replays: list[bool] = []

        def contend():
            barrier.wait(10)
            _, replayed = store.execute("op", "same", lambda: (
                time.sleep(0.05), runs.append(1), {"n": 1})[-1])
            replays.append(replayed)

        threads = [threading.Thread(target=contend) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
        assert len(runs) == 1, "the callback ran twice for one key"
        assert sorted(replays) == [False, True]

    def test_the_lock_table_does_not_grow_without_bound(self, store):
        """A lock per key, kept forever, is a memory leak keyed by client
        input. They are reference-counted and dropped at zero."""
        for i in range(50):
            store.execute("op", f"k{i}", lambda: {})
        assert store.live_key_locks() == 0
