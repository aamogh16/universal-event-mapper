"""Who a proposal actually affects, counted deterministically.

This exists because of a real Klaviyo nuance that makes campaigns necessary
rather than decorative:

    A newly created flow only catches people who enter the trigger condition
    FROM NOW ON. It does nothing for the members who are ALREADY lapsed.

So an uncovered signal usually deserves two proposals, not one:
  - a flow, to handle every future occurrence
  - a campaign, to clear the backlog of people already in that state

The counts here are computed by query, never by the model. The agent is
allowed to decide what to say to these people; it is not allowed to guess how
many there are.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from universal_events import store

from .signals import DONOR_LAPSE_DAYS, LAPSE_DAYS, LAPSE_MIN_PRIOR, NEGATIVE_METRICS


@dataclass
class Audience:
    description: str
    profiles: list[Any]
    basis: str  # the rule used, so the number is auditable

    @property
    def size(self) -> int:
        return len(self.profiles)

    @property
    def sample_names(self) -> list[str]:
        return [p.display_name for p in self.profiles[:5]]

    @property
    def total_value(self) -> float:
        return sum(
            sum(e.value or 0 for e in store.events_for_profile(p.profile_key, limit=200))
            for p in self.profiles
        )


def _lapsed(vertical: str, days: int) -> list[Any]:
    min_prior = LAPSE_MIN_PRIOR.get(vertical, 3)
    return [
        p
        for p in store.profiles_with_history(source_key=vertical)
        if p.days_since_last_event >= days and p.event_count >= min_prior
    ]


def resolve(signal: Any) -> Audience:
    """Everyone currently matching the condition the signal describes."""
    vertical = signal.vertical or "fitness"

    if signal.kind == "lapsed_member":
        days = LAPSE_DAYS
        profiles = _lapsed(vertical, days)
        return Audience(
            description=f"Members with no activity in {days}+ days",
            profiles=profiles,
            basis=f"no event in {days} days, and at least "
            f"{LAPSE_MIN_PRIOR.get(vertical, 3)} prior events",
        )

    if signal.kind == "lapsed_donor":
        days = DONOR_LAPSE_DAYS
        profiles = _lapsed(vertical, days)
        return Audience(
            description=f"Donors with no gift in {days}+ days",
            profiles=profiles,
            basis=f"no event in {days} days, and at least "
            f"{LAPSE_MIN_PRIOR.get(vertical, 3)} prior gifts",
        )

    if signal.kind in ("metric_trend", "repeat_negative"):
        metric = signal.trigger_metric
        hotspot = (signal.evidence or {}).get("hotspot") or (
            signal.evidence or {}
        ).get("common_context")
        window = int((signal.evidence or {}).get("window_days") or 14)
        events = store.events_in_window(
            metric_name=metric, since_days=window, source_key=vertical
        )
        if hotspot:
            events = [
                e
                for e in events
                if (e.properties.get("Class Name") or e.properties.get("Appointment Type"))
                == hotspot
            ]
        keys = {e.profile_key for e in events}
        profiles = [
            p for p in store.profiles_with_history(source_key=vertical)
            if p.profile_key in keys
        ]
        label = f"{metric} in the last {window} days"
        if hotspot:
            label += f" for {hotspot}"
        return Audience(
            description=f"People with a {label}",
            profiles=profiles,
            basis=f"at least one '{metric}' event in {window} days"
            + (f", filtered to {hotspot}" if hotspot else ""),
        )

    return Audience(description="No audience rule for this signal", profiles=[], basis="")


def render_for_prompt(audience: Audience) -> str:
    if not audience.size:
        return "(no one currently matches)"
    lines = [
        f"Audience: {audience.description}",
        f"Size: {audience.size} profiles (counted by query, not estimated)",
        f"Rule used: {audience.basis}",
    ]
    if audience.sample_names:
        lines.append(f"Examples: {', '.join(audience.sample_names)}")
    if audience.total_value:
        lines.append(f"Combined lifetime value: ${audience.total_value:,.0f}")
    return "\n".join(lines)
