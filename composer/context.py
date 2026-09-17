"""Context assembly -- what the agent knows before it reasons.

Deliberately a named, separate step. Mai's description of what the Composer
team wants starts with "the agent pulls the customer's context", and the demo
shows this bundle on screen BEFORE the proposal. That is the difference
between "the AI said something" and "the AI read 14 events, 2 prior
corrections, and the live flow, then said something".

Keeping this tight is also the whole cost thesis: a cheap model does fine when
the context is well built, so the engineering belongs here rather than in
model size.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from universal_events import store

from . import feedback
from .feedback import Correction
from .patch import render_outline


@dataclass
class ContextBundle:
    signal: Any
    # None means no existing automation covers this signal.
    flow: dict | None
    profile_events: list[Any] = field(default_factory=list)
    lifetime_value: float = 0.0
    tenure_days: int = 0
    segment_peers: int = 0
    corrections: list[Correction] = field(default_factory=list)
    sibling_flows: list[dict] = field(default_factory=list)

    @property
    def summary_lines(self) -> list[str]:
        """Short lines the UI shows as proof the agent is grounded."""
        lines: list[str] = []
        if self.profile_events:
            lines.append(f"{len(self.profile_events)} events for this profile")
        if self.tenure_days:
            lines.append(f"{self.tenure_days} days of history")
        if self.lifetime_value:
            lines.append(f"${self.lifetime_value:,.0f} lifetime value")
        if self.segment_peers:
            lines.append(f"{self.segment_peers} similar profiles in segment")
        if self.flow is None:
            lines.append(f"no flow covers '{self.signal.kind}' — {len(self.sibling_flows)} other flow(s) exist")
        else:
            lines.append(
                f"flow v{self.flow.get('version')} ({len(self.flow.get('steps') or [])} steps)"
            )
        lines.append(
            f"{len(self.corrections)} learned correction(s) applied"
            if self.corrections
            else "no learned corrections yet"
        )
        return lines

    def to_prompt(self) -> str:
        signal = self.signal
        parts = [
            "## What was noticed",
            f"{signal.title}",
            f"{signal.detail}",
            "",
            f"Evidence: {signal.evidence}",
            f"Detected by: {signal.origin} "
            f"({'reacting to a single event' if signal.origin == 'trigger' else 'scheduled sweep across profiles'})",
            f"Scope: {signal.scope}",
            "",
        ]

        if self.flow is None:
            parts += [
                "## Existing automations in this account",
                "NONE of these are responsible for the signal above:",
            ]
            for other in self.sibling_flows:
                trigger = (other.get("trigger") or {}).get("metric")
                parts.append(f"- \"{other.get('name')}\" (triggered by: {trigger})")
            parts += [
                "",
                "There is no automation handling this signal at all. Do not patch "
                "an unrelated flow to cover it -- that would fire for the wrong "
                "people. Draft a new one.",
                "",
            ]
        else:
            parts += [
                "## The live flow being audited",
                "```",
                *render_outline(self.flow),
                "```",
                "",
            ]

        if self.profile_events:
            parts += ["## This profile's recent history"]
            for event in self.profile_events[:12]:
                extra = ""
                context = event.properties.get("Class Name") or event.properties.get(
                    "Appointment Type"
                )
                if context:
                    extra = f" ({context})"
                money = f" ${event.value:,.0f}" if event.value else ""
                parts.append(
                    f"- {event.occurred_at.date()}  {event.metric_name}{extra}{money}"
                    f"  [{event.age_days}d ago]"
                )
            if self.lifetime_value:
                parts.append(f"Lifetime value: ${self.lifetime_value:,.2f}")
            parts.append("")

        if self.segment_peers:
            parts += [
                "## Segment",
                f"{self.segment_peers} other profiles show the same pattern, so this "
                "is a flow-level problem rather than one person's habit.",
                "",
            ]

        parts += [
            "## Operating constraints the user has already given you",
            feedback.render_for_prompt(self.corrections),
            "",
        ]
        return "\n".join(parts)


def build(signal: Any, flow: dict | None, sibling_flows: list[dict] | None = None) -> ContextBundle:
    """Gather everything relevant to one signal + flow pair."""
    events: list[Any] = []
    lifetime = 0.0
    tenure = 0

    if signal.profile_key:
        events = store.events_for_profile(signal.profile_key, limit=200)
        lifetime = sum(e.value or 0 for e in events)
        if events:
            tenure = (events[0].occurred_at - events[-1].occurred_at).days

    peers = 0
    if signal.scope == "aggregate":
        peers = int(signal.evidence.get("affected_profiles") or 0)

    return ContextBundle(
        signal=signal,
        flow=flow,
        profile_events=events,
        lifetime_value=lifetime,
        tenure_days=tenure,
        segment_peers=peers,
        corrections=feedback.for_vertical(signal.vertical),
        sibling_flows=sibling_flows or [],
    )
