"""EventStore: append/replay/subscribe + transactional batching."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fruit_market.state.events import (
    CountSet,
    Event,
    ItemTaught,
    OrderPaid,
    OrderReserved,
)
from fruit_market.state.store import EventStore

if TYPE_CHECKING:
    from pathlib import Path


def _store(tmp: Path) -> EventStore:
    return EventStore(tmp / "events.db")


def test_append_returns_monotonic_offsets(tmp_path: Path) -> None:
    store = _store(tmp_path)
    o1 = store.append(ItemTaught(item_id="i1", name="banana", price_cents=100, initial_count=6))
    o2 = store.append(ActiveSet := CountSet(item_id="i1", count=5, source="model"))
    o3 = store.append(CountSet(item_id="i1", count=4, source="model"))
    assert o1 < o2 < o3
    assert ActiveSet  # silence pyright unused
    store.close()


def test_replay_yields_every_event_in_order(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.append(ItemTaught(item_id="i1", name="banana", price_cents=100, initial_count=6))
    store.append(CountSet(item_id="i1", count=4, source="model"))
    events: list[Event] = [ev for _, ev in store.replay()]
    assert [type(e).__name__ for e in events] == ["ItemTaught", "CountSet"]
    store.close()


def test_replay_since_skips_earlier(tmp_path: Path) -> None:
    store = _store(tmp_path)
    a = store.append(ItemTaught(item_id="i1", name="banana", price_cents=100, initial_count=6))
    store.append(CountSet(item_id="i1", count=4, source="model"))
    after = [ev for _, ev in store.replay(since=a)]
    assert len(after) == 1
    assert isinstance(after[0], CountSet)
    store.close()


def test_subscribe_fires_for_every_append(tmp_path: Path) -> None:
    store = _store(tmp_path)
    captured: list[Event] = []
    unsubscribe = store.subscribe(lambda _o, ev: captured.append(ev))
    store.append(ItemTaught(item_id="i1", name="banana", price_cents=100, initial_count=6))
    store.append(CountSet(item_id="i1", count=4, source="model"))
    assert len(captured) == 2
    unsubscribe()
    store.append(CountSet(item_id="i1", count=3, source="model"))
    assert len(captured) == 2  # post-unsubscribe append is silent
    store.close()


def test_subscriber_exception_does_not_break_writer(tmp_path: Path) -> None:
    store = _store(tmp_path)

    def boom(_offset: int, _event: Event) -> None:
        raise RuntimeError("subscriber blew up")

    store.subscribe(boom)
    # Must not raise. The broken subscriber is swallowed by design;
    # the write itself still commits.
    offset = store.append(
        ItemTaught(item_id="i1", name="banana", price_cents=100, initial_count=6)
    )
    assert offset > 0
    assert len(list(store.replay())) == 1
    store.close()


def test_transaction_commits_all_or_nothing(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.append(ItemTaught(item_id="i1", name="banana", price_cents=100, initial_count=6))

    # Successful batch: both events visible.
    with store.transaction() as tx:
        tx.append(
            OrderReserved(
                order_id="o1",
                item_id="i1",
                qty=2,
                total_cents=200,
                customer_phone="+15551234567",
            )
        )
        tx.append(CountSet(item_id="i1", count=4, source="manual"))
    types = [type(e).__name__ for _, e in store.replay()]
    assert types == ["ItemTaught", "OrderReserved", "CountSet"]

    # Failed batch: nothing visible from the batch.
    try:
        with store.transaction() as tx:
            tx.append(OrderPaid(order_id="o1", stripe_session_id="cs_test_1"))
            raise RuntimeError("simulated mid-batch failure")
    except RuntimeError:
        pass
    types = [type(e).__name__ for _, e in store.replay()]
    assert types == ["ItemTaught", "OrderReserved", "CountSet"]  # unchanged
    store.close()


def test_transaction_defers_subscriber_notification_until_commit(tmp_path: Path) -> None:
    store = _store(tmp_path)
    seen: list[Event] = []
    store.subscribe(lambda _o, ev: seen.append(ev))

    with store.transaction() as tx:
        tx.append(ItemTaught(item_id="i1", name="banana", price_cents=100, initial_count=6))
        tx.append(CountSet(item_id="i1", count=4, source="manual"))
        # Mid-batch, subscriber must not have seen anything yet:
        assert seen == []

    # On commit, subscriber is notified for both, in order.
    assert [type(e).__name__ for e in seen] == ["ItemTaught", "CountSet"]
    store.close()
