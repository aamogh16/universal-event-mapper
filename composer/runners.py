"""The two execution patterns, side by side.

Mai named these as the standard approaches, and they differ by SCOPE OF DATA,
not merely by timing -- which is the real reason a production system needs
both:

  trigger  reacts to one event as it arrives. Sees a single profile. Fires in
           milliseconds. Catches "this person just did it for the third time".

  sweep    runs on a schedule. Sees every profile and multiple time windows.
           Catches "this rate tripled across ten members", which no single
           event could reveal.

On demo timing: the sweep interval is config (SWEEP_INTERVAL_SECONDS, 60 in
demo vs 86400 in production) and the seeded history is BACKDATED, so the
conditions are already true at t=0. Nothing waits for real time to pass; the
sweep's job is to discover what is already there.
"""

from __future__ import annotations

import threading
import time
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from universal_events.config import PROJECT_ROOT, settings

from . import proposals, signals


@dataclass
class RunRecord:
    """One execution of either pattern, for the visible run log."""

    origin: str                     # "trigger" | "sweep"
    at: str
    signals_found: int
    proposals_created: int
    duration_ms: int
    detail: str = ""

    @property
    def clock(self) -> str:
        return self.at[11:19]


# Persisted to disk: each CLI invocation is a separate process, so an
# in-memory log would be empty by the time anyone ran `runs` -- which is
# exactly the command meant to show both patterns side by side.
LOG_PATH = PROJECT_ROOT / "runs.json"


def _load_log() -> list[RunRecord]:
    if not LOG_PATH.exists():
        return []
    try:
        return [RunRecord(**item) for item in json.loads(LOG_PATH.read_text("utf-8"))]
    except (ValueError, OSError, TypeError):
        return []


def _append(record: RunRecord) -> None:
    entries = _load_log()
    entries.append(record)
    LOG_PATH.write_text(
        json.dumps([asdict(e) for e in entries[-200:]], indent=2), "utf-8"
    )


def reset_log() -> None:
    if LOG_PATH.exists():
        LOG_PATH.unlink()


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------- trigger


def on_event(
    profile_key: str, metric_name: str, *, source_key: str | None = None
) -> list[Any]:
    """Trigger pattern: called the moment an event lands. Returns proposals."""
    started = time.monotonic()
    found = signals.detect_for_event(profile_key, metric_name, source_key=source_key)
    created = []
    for signal in found:
        proposal = proposals.create_from_signal(signal, origin="trigger")
        if proposal:
            created.append(proposal)

    _append(
        RunRecord(
            origin="trigger",
            at=_stamp(),
            signals_found=len(found),
            proposals_created=len(created),
            duration_ms=int((time.monotonic() - started) * 1000),
            detail=f"{metric_name} for {profile_key}",
        )
    )
    return created


# ---------------------------------------------------------------- scheduled


def sweep_once(vertical: str | None = None) -> list[Any]:
    """Recurring pattern: one pass over aggregates and lapsed profiles."""
    started = time.monotonic()
    found = signals.detect_aggregate(vertical)
    created = []
    for signal in found:
        proposal = proposals.create_from_signal(signal, origin="sweep")
        if proposal:
            created.append(proposal)

    _append(
        RunRecord(
            origin="sweep",
            at=_stamp(),
            signals_found=len(found),
            proposals_created=len(created),
            duration_ms=int((time.monotonic() - started) * 1000),
            detail=f"scanned {vertical or 'all verticals'}",
        )
    )
    return created


class Sweeper:
    """Background scheduled sweep.

    Deliberately a plain thread rather than a cron dependency: the point is to
    demonstrate the pattern, and one fewer moving part is one fewer thing that
    can fail during a live demo.
    """

    def __init__(
        self,
        interval_seconds: int | None = None,
        on_proposals: Callable[[list[Any]], None] | None = None,
    ) -> None:
        self.interval = interval_seconds or settings.sweep_interval_seconds
        self.on_proposals = on_proposals
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_run_at: float | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def seconds_until_next(self) -> int:
        if self.last_run_at is None:
            return 0
        remaining = self.interval - (time.monotonic() - self.last_run_at)
        return max(0, int(remaining))

    def _loop(self) -> None:
        while not self._stop.is_set():
            created = sweep_once()
            self.last_run_at = time.monotonic()
            if created and self.on_proposals:
                self.on_proposals(created)
            # Wait in small slices so stop() is responsive.
            self._stop.wait(self.interval)

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        self._thread = None


def run_log(origin: str | None = None, limit: int = 20) -> list[RunRecord]:
    entries = [r for r in _load_log() if origin is None or r.origin == origin]
    return entries[-limit:]
