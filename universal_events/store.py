"""Local event mirror (SQLite).  [STUB -- not implemented]

Why mirror what we already sent to Klaviyo: Part 2's agent needs to sweep
event history per profile on a schedule. Klaviyo's Get Events endpoint can do
that, but it is rate limited and filters by profile_id (not email), which means
an extra lookup per profile. A local mirror keeps the agent loop fast, free,
and demoable with no network.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_key   TEXT    NOT NULL,   -- email, else phone, else external_id
    email         TEXT,
    first_name    TEXT,
    metric_name   TEXT    NOT NULL,
    properties    TEXT    NOT NULL,   -- JSON
    value         REAL,
    occurred_at   TEXT    NOT NULL,   -- ISO 8601
    source_key    TEXT,
    strategy      TEXT,
    created_at    TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_profile ON events(profile_key, occurred_at);
"""


@dataclass
class StoredEvent:
    profile_key: str
    email: str | None
    first_name: str | None
    metric_name: str
    properties: dict[str, Any]
    value: float | None
    occurred_at: datetime
    source_key: str | None
    strategy: str | None


def init_db(path: Path | None = None) -> None:
    """Create the schema if absent. TODO."""
    raise NotImplementedError


def record_event(mapping: Any, *, source_key: str | None = None) -> None:
    """Persist one mapped event. TODO."""
    raise NotImplementedError


def profiles_with_history(min_events: int = 1) -> list[str]:
    """Profile keys that have at least `min_events` events. TODO."""
    raise NotImplementedError


def events_for_profile(profile_key: str, limit: int = 100) -> list[StoredEvent]:
    """Events for one profile, newest first. TODO."""
    raise NotImplementedError


def seed_demo_history() -> int:
    """Backdate a realistic history so Part 2 has something to find. TODO.

    The canonical demo case: a gym member with regular bookings that stop
    three weeks ago. Without seeded history the agent has nothing to flag on a
    fresh database, so this is what makes Part 2 demoable on the spot.
    """
    raise NotImplementedError
