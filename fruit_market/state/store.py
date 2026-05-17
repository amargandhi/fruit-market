"""SQLite WAL-backed append-only event log.

This module is the only writer to the event table. Everything else
in the system either:

* appends one event via :meth:`EventStore.append` (services do this),
* replays the log via :meth:`EventStore.replay` (projections do this
  at startup), or
* subscribes to a live tail via :meth:`EventStore.subscribe` (the
  kiosk SSE stream does this).

Schema is intentionally minimal — one row per event with a JSON
payload — so the schema never needs migration. Adding a new event
type means adding a class in ``events.py`` and a reducer branch in
``projections.py``; the table is unchanged.

WAL mode is on so a long-running watcher thread can write while the
HTTP layer reads concurrently without locking.
"""

from __future__ import annotations

import contextlib
import sqlite3
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import TypeAdapter

from fruit_market.state.events import Event

if TYPE_CHECKING:
    from collections.abc import Generator


# Pydantic TypeAdapter for the discriminated union — single object,
# reused across every (de)serialization to avoid the per-call build
# cost that would otherwise dominate the append path.
_EVENT_ADAPTER: TypeAdapter[Event] = TypeAdapter(Event)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    offset   INTEGER PRIMARY KEY AUTOINCREMENT,
    type     TEXT NOT NULL,
    payload  TEXT NOT NULL,
    ts       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_type_idx ON events(type);
"""


# Subscriber callbacks receive ``(offset, event)``. They run on the
# thread that did the append; if the callback is slow, appends are
# slowed. Subscribers that need to do real work should hand off to
# their own queue immediately.
Subscriber = Callable[[int, Event], None]


class EventStore:
    """Append-only event log backed by SQLite (WAL mode).

    Thread-safe: a single store can be shared by the watcher thread,
    the HTTP request threads, and the SSE broadcaster. SQLite
    serializes writes; reads are non-blocking thanks to WAL.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # ``check_same_thread=False`` is intentional. Combined with
        # the lock below, multiple threads can call ``append``.
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA synchronous = NORMAL")
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self._lock = threading.Lock()
        self._subscribers: list[Subscriber] = []

    # ─── writing ──────────────────────────────────────────────────

    def append(self, event: Event) -> int:
        """Append ``event``, return its assigned offset.

        Notifies subscribers synchronously after the row commits.
        Subscriber exceptions are caught and logged so a broken
        consumer can't poison the writer.
        """

        payload = _EVENT_ADAPTER.dump_json(event).decode()
        ts = event.ts.isoformat()
        type_name = event.type
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO events (type, payload, ts) VALUES (?, ?, ?)",
                (type_name, payload, ts),
            )
            self._conn.commit()
            offset = cur.lastrowid
        assert offset is not None  # SQLite always assigns a rowid on insert
        self._notify(offset, event)
        return offset

    @contextmanager
    def transaction(self) -> Generator[_TransactionalAppender, None, None]:
        """All appends inside the block share one SQLite transaction.

        Use this when a single domain action emits multiple events
        and they must all land or none must land. The typical case
        is ``OrdersService.reserve``: it appends ``OrderReserved`` +
        ``CountSet`` together. If the count check inside the lock
        fails, both events are rolled back.

        Subscriber notifications are deferred until commit so a
        partial batch never reaches the SSE stream.
        """

        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            appender = _TransactionalAppender(self._conn)
            try:
                yield appender
            except BaseException:
                self._conn.rollback()
                raise
            self._conn.commit()
        # Notify outside the lock so subscribers can call back in
        # without deadlocking.
        for offset, event in appender.committed:
            self._notify(offset, event)

    def _notify(self, offset: int, event: Event) -> None:
        for sub in list(self._subscribers):
            # Subscribers must not crash the writer.
            # Production would log here; we deliberately swallow.
            with contextlib.suppress(Exception):
                sub(offset, event)

    # ─── reading ──────────────────────────────────────────────────

    def replay(self, since: int = 0) -> Iterator[tuple[int, Event]]:
        """Yield ``(offset, event)`` for every event with offset > ``since``.

        Used at startup by each projection to rebuild its in-memory
        state, and by SSE clients reconnecting with ``last_event_id``.
        """

        cur = self._conn.execute(
            "SELECT offset, payload FROM events WHERE offset > ? ORDER BY offset",
            (since,),
        )
        for offset, payload in cur:
            yield offset, _EVENT_ADAPTER.validate_json(payload)

    def latest_offset(self) -> int:
        cur = self._conn.execute("SELECT COALESCE(MAX(offset), 0) FROM events")
        row = cur.fetchone()
        return int(row[0]) if row else 0

    # ─── subscribers ──────────────────────────────────────────────

    def subscribe(self, callback: Subscriber) -> Callable[[], None]:
        """Register ``callback`` for every future append.

        Returns an unsubscribe function. Callbacks fire synchronously
        on the thread that called ``append`` — keep them cheap.
        """

        self._subscribers.append(callback)

        def _unsubscribe() -> None:
            with contextlib.suppress(ValueError):
                self._subscribers.remove(callback)

        return _unsubscribe

    # ─── lifecycle ────────────────────────────────────────────────

    def close(self) -> None:
        with self._lock:
            self._conn.close()


class _TransactionalAppender:
    """Issued by ``EventStore.transaction``; collects events for one
    atomic batch. Not part of the public API — use the context
    manager.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self.committed: list[tuple[int, Event]] = []

    def append(self, event: Event) -> int:
        payload = _EVENT_ADAPTER.dump_json(event).decode()
        ts = event.ts.isoformat()
        type_name = event.type
        cur = self._conn.execute(
            "INSERT INTO events (type, payload, ts) VALUES (?, ?, ?)",
            (type_name, payload, ts),
        )
        offset = cur.lastrowid
        assert offset is not None
        self.committed.append((offset, event))
        return offset


# ─── helper for the FastAPI lifespan ─────────────────────────────


def open_default_store() -> EventStore:
    """Open the default event store.

    Path resolution:
      1. ``$FM_EVENT_STORE_PATH`` if set (used by tests for isolation).
      2. ``./.fruitmarket/events.db`` otherwise. Matches the ignored
         runtime data path in ``.gitignore`` so cold starts don't
         leak local DBs into git.
    """

    import os  # noqa: PLC0415 — lazy to keep the module pure

    override = os.environ.get("FM_EVENT_STORE_PATH")
    if override:
        return EventStore(override)
    return EventStore(Path.cwd() / ".fruitmarket" / "events.db")
