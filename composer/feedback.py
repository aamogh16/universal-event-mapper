"""Learned corrections: what the user has already told us.

When a proposal is rejected with feedback, we keep the lesson, not just the
rejection. Every later proposal is generated with these constraints in the
prompt, so the user does not repeat themselves.

Scoping is per-vertical plus global. "Our donors are older, don't use SMS" is
a nonprofit rule, not a universal one -- applying it to the gym would be
overcorrecting. Retrieval is a plain filter, no embeddings: the rule set is
small, and a deterministic lookup cannot surprise us mid-demo.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from universal_events.config import PROJECT_ROOT

STORE_PATH = PROJECT_ROOT / "corrections.json"


class Correction(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    rule: str                      # imperative, e.g. "Never propose SMS steps"
    raw_feedback: str = ""         # what the user actually typed
    vertical: str | None = None    # None means global
    source_proposal_id: str | None = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    times_applied: int = 0

    @property
    def scope_label(self) -> str:
        return self.vertical or "all verticals"


def _load() -> list[Correction]:
    if not STORE_PATH.exists():
        return []
    try:
        raw = json.loads(STORE_PATH.read_text(encoding="utf-8"))
        return [Correction(**item) for item in raw]
    except (ValueError, OSError, TypeError):
        return []


def _save(items: list[Correction]) -> None:
    STORE_PATH.write_text(
        json.dumps([c.model_dump() for c in items], indent=2), encoding="utf-8"
    )


def reset() -> None:
    if STORE_PATH.exists():
        STORE_PATH.unlink()


def all_corrections() -> list[Correction]:
    return _load()


def add(
    rule: str,
    *,
    raw_feedback: str = "",
    vertical: str | None = None,
    source_proposal_id: str | None = None,
) -> Correction:
    """Record a lesson. Near-duplicates are skipped rather than stacked."""
    items = _load()
    normalised = rule.strip().lower()
    for existing in items:
        if existing.rule.strip().lower() == normalised and existing.vertical == vertical:
            return existing
    correction = Correction(
        rule=rule.strip(),
        raw_feedback=raw_feedback,
        vertical=vertical,
        source_proposal_id=source_proposal_id,
    )
    items.append(correction)
    _save(items)
    return correction


def for_vertical(vertical: str | None) -> list[Correction]:
    """Corrections that apply here: this vertical's rules plus global ones."""
    return [c for c in _load() if c.vertical is None or c.vertical == vertical]


def mark_applied(ids: list[str]) -> None:
    items = _load()
    changed = False
    for item in items:
        if item.id in ids:
            item.times_applied += 1
            changed = True
    if changed:
        _save(items)


def render_for_prompt(corrections: list[Correction]) -> str:
    """Constraints block for the audit prompt."""
    if not corrections:
        return "(none yet)"
    return "\n".join(
        f"- {c.rule}  [scope: {c.scope_label}]" for c in corrections
    )
