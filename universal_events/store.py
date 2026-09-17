"""Local event mirror (SQLite).

Why mirror what we already sent to Klaviyo: the agent needs to sweep event
history per profile and compute aggregate trends. Klaviyo's Get Events
endpoint can do that, but it is rate limited and filters by profile_id (not
email), costing an extra lookup per profile. A local mirror keeps the agent
loop fast, free, and fully demoable with no network.
"""

from __future__ import annotations

import json
import random
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from .config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_key   TEXT    NOT NULL,
    email         TEXT,
    first_name    TEXT,
    last_name     TEXT,
    metric_name   TEXT    NOT NULL,
    properties    TEXT    NOT NULL,
    value         REAL,
    occurred_at   TEXT    NOT NULL,
    source_key    TEXT,
    strategy      TEXT,
    created_at    TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_profile  ON events(profile_key, occurred_at);
CREATE INDEX IF NOT EXISTS idx_events_metric   ON events(metric_name, occurred_at);
CREATE INDEX IF NOT EXISTS idx_events_source   ON events(source_key, occurred_at);
"""


@dataclass
class StoredEvent:
    id: int
    profile_key: str
    email: str | None
    first_name: str | None
    last_name: str | None
    metric_name: str
    properties: dict[str, Any]
    value: float | None
    occurred_at: datetime
    source_key: str | None
    strategy: str | None

    @property
    def age_days(self) -> int:
        return (_now() - self.occurred_at).days


@dataclass
class ProfileSummary:
    profile_key: str
    email: str | None
    first_name: str | None
    last_name: str | None
    source_key: str | None
    event_count: int
    first_seen: datetime
    last_seen: datetime

    @property
    def days_since_last_event(self) -> int:
        return (_now() - self.last_seen).days

    @property
    def display_name(self) -> str:
        parts = [p for p in (self.first_name, self.last_name) if p]
        return " ".join(parts) or self.profile_key


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(raw: str) -> datetime:
    dt = datetime.fromisoformat(raw)
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


@contextmanager
def _connect(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    target = Path(path or settings.event_db_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(path: Path | None = None) -> None:
    with _connect(path) as conn:
        conn.executescript(SCHEMA)


def reset(path: Path | None = None) -> None:
    """Drop all events. Used by `seed` so demos start identically."""
    with _connect(path) as conn:
        conn.executescript(SCHEMA)
        conn.execute("DELETE FROM events")


def _row_to_event(row: sqlite3.Row) -> StoredEvent:
    return StoredEvent(
        id=row["id"],
        profile_key=row["profile_key"],
        email=row["email"],
        first_name=row["first_name"],
        last_name=row["last_name"],
        metric_name=row["metric_name"],
        properties=json.loads(row["properties"]),
        value=row["value"],
        occurred_at=_parse_dt(row["occurred_at"]),
        source_key=row["source_key"],
        strategy=row["strategy"],
    )


def record_event(
    mapping: Any,
    *,
    source_key: str | None = None,
    path: Path | None = None,
) -> int:
    """Persist one mapped event. Accepts a MappingResult."""
    identity = mapping.identity
    occurred = mapping.occurred_at or _now()
    if occurred.tzinfo is None:
        occurred = occurred.replace(tzinfo=timezone.utc)

    init_db(path)
    with _connect(path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO events (profile_key, email, first_name, last_name,
                                metric_name, properties, value, occurred_at,
                                source_key, strategy, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                identity.primary,
                identity.email,
                identity.first_name,
                identity.last_name,
                mapping.metric_name,
                json.dumps(mapping.properties, default=str),
                mapping.value,
                occurred.isoformat(),
                source_key,
                mapping.strategy,
                _now().isoformat(),
            ),
        )
        return int(cursor.lastrowid or 0)


def events_for_profile(
    profile_key: str, limit: int = 100, path: Path | None = None
) -> list[StoredEvent]:
    """Events for one profile, newest first."""
    init_db(path)
    with _connect(path) as conn:
        rows = conn.execute(
            "SELECT * FROM events WHERE profile_key = ? "
            "ORDER BY occurred_at DESC LIMIT ?",
            (profile_key, limit),
        ).fetchall()
    return [_row_to_event(r) for r in rows]


def profiles_with_history(
    min_events: int = 1, source_key: str | None = None, path: Path | None = None
) -> list[ProfileSummary]:
    """Profiles having at least `min_events` events, most recently active first."""
    init_db(path)
    clause = "WHERE source_key = ?" if source_key else ""
    args: tuple = (source_key,) if source_key else ()
    with _connect(path) as conn:
        rows = conn.execute(
            f"""
            SELECT profile_key,
                   MAX(email)       AS email,
                   MAX(first_name)  AS first_name,
                   MAX(last_name)   AS last_name,
                   MAX(source_key)  AS source_key,
                   COUNT(*)         AS event_count,
                   MIN(occurred_at) AS first_seen,
                   MAX(occurred_at) AS last_seen
            FROM events {clause}
            GROUP BY profile_key
            HAVING COUNT(*) >= ?
            ORDER BY last_seen DESC
            """,
            (*args, min_events),
        ).fetchall()
    return [
        ProfileSummary(
            profile_key=r["profile_key"],
            email=r["email"],
            first_name=r["first_name"],
            last_name=r["last_name"],
            source_key=r["source_key"],
            event_count=r["event_count"],
            first_seen=_parse_dt(r["first_seen"]),
            last_seen=_parse_dt(r["last_seen"]),
        )
        for r in rows
    ]


def metric_counts(
    since_days: int, source_key: str | None = None, path: Path | None = None
) -> dict[str, int]:
    """Count events per metric over a window. Feeds aggregate trend detection."""
    init_db(path)
    cutoff = (_now() - timedelta(days=since_days)).isoformat()
    clause = "AND source_key = ?" if source_key else ""
    args: tuple = (source_key,) if source_key else ()
    with _connect(path) as conn:
        rows = conn.execute(
            f"SELECT metric_name, COUNT(*) AS n FROM events "
            f"WHERE occurred_at >= ? {clause} GROUP BY metric_name",
            (cutoff, *args),
        ).fetchall()
    return {r["metric_name"]: r["n"] for r in rows}


def events_in_window(
    metric_name: str | None = None,
    since_days: int = 30,
    until_days: int = 0,
    source_key: str | None = None,
    path: Path | None = None,
) -> list[StoredEvent]:
    """Events in [now-since_days, now-until_days). Used for period comparisons."""
    init_db(path)
    start = (_now() - timedelta(days=since_days)).isoformat()
    end = (_now() - timedelta(days=until_days)).isoformat()
    clauses = ["occurred_at >= ?", "occurred_at < ?"]
    args: list[Any] = [start, end]
    if metric_name:
        clauses.append("metric_name = ?")
        args.append(metric_name)
    if source_key:
        clauses.append("source_key = ?")
        args.append(source_key)
    with _connect(path) as conn:
        rows = conn.execute(
            f"SELECT * FROM events WHERE {' AND '.join(clauses)} ORDER BY occurred_at DESC",
            tuple(args),
        ).fetchall()
    return [_row_to_event(r) for r in rows]


def count(path: Path | None = None) -> int:
    init_db(path)
    with _connect(path) as conn:
        return int(conn.execute("SELECT COUNT(*) FROM events").fetchone()[0])
