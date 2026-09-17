"""Deterministic signal detection -- what changed in the data.

No LLM here on purpose. Noticing "this member stopped booking" or "this rate
tripled" is arithmetic, and arithmetic should not be probabilistic. The model's
job starts later, at deciding what to DO about a signal.

This module is also where the trigger/sweep split becomes real. The two
patterns differ by the SCOPE OF DATA they can see, not merely by timing:

    detect_for_event()  -> one event, one profile, instantly.
                           Finds person-level problems.
    detect_aggregate()  -> many profiles over time windows.
                           Finds population-level problems no single
                           event could reveal.

That difference is the reason a real system needs both.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from universal_events import store

Scope = Literal["profile", "aggregate"]
Severity = Literal["low", "medium", "high"]

NEGATIVE_METRICS = {
    "Class No-Show",
    "Appointment No-Show",
    "Reservation No-Show",
}

# Windows and thresholds. Named constants so the numbers the agent cites are
# traceable to a rule rather than invented.
LAPSE_DAYS = 21
# Minimum prior events before absence is meaningful. Per-vertical, because
# cadence differs: a gym member books weekly, a donor gives twice a year.
# A single global threshold silently hides exactly the lapsed donors we want.
LAPSE_MIN_PRIOR = {"fitness": 4, "nonprofit": 3, "dental": 2, "restaurant": 3}
DONOR_LAPSE_DAYS = 60
REPEAT_NEGATIVE_WINDOW_DAYS = 30
REPEAT_NEGATIVE_THRESHOLD = 3
TREND_WINDOW_DAYS = 14
TREND_MIN_VOLUME = 15
TREND_RATIO = 1.75


@dataclass
class Signal:
    """Something worth a second look, with the evidence attached."""

    kind: str
    scope: Scope
    title: str
    detail: str
    evidence: dict[str, Any] = field(default_factory=dict)
    severity: Severity = "medium"
    vertical: str | None = None
    profile_key: str | None = None
    profile_name: str | None = None
    trigger_metric: str | None = None
    origin: str = "sweep"

    @property
    def id(self) -> str:
        return f"{self.kind}:{self.profile_key or self.vertical or 'all'}"


# ------------------------------------------------------------ trigger pattern


def detect_for_event(
    profile_key: str,
    metric_name: str,
    *,
    source_key: str | None = None,
) -> list[Signal]:
    """React to one incoming event. Profile-scoped only."""
    signals: list[Signal] = []
    history = store.events_for_profile(profile_key, limit=200)
    if not history:
        return signals

    newest = history[0]
    name = " ".join(p for p in (newest.first_name, newest.last_name) if p) or profile_key

    if metric_name in NEGATIVE_METRICS:
        recent = [
            e
            for e in history
            if e.metric_name == metric_name
            and e.age_days <= REPEAT_NEGATIVE_WINDOW_DAYS
        ]
        if len(recent) >= REPEAT_NEGATIVE_THRESHOLD:
            classes = [
                e.properties.get("Class Name")
                or e.properties.get("Appointment Type")
                or e.properties.get("Seating Area")
                for e in recent
            ]
            common = max(set(filter(None, classes)), key=classes.count, default=None)
            signals.append(
                Signal(
                    kind="repeat_negative",
                    scope="profile",
                    title=f"{name} has {len(recent)} {metric_name.lower()}s in {REPEAT_NEGATIVE_WINDOW_DAYS} days",
                    detail=(
                        f"This is their {_ordinal(len(recent))} {metric_name.lower()} this month"
                        + (f", all for {common}" if common else "")
                        + ". A single win-back email is unlikely to change a pattern."
                    ),
                    evidence={
                        "occurrences": len(recent),
                        "window_days": REPEAT_NEGATIVE_WINDOW_DAYS,
                        "metric": metric_name,
                        "common_context": common,
                        "total_events": len(history),
                    },
                    severity="high",
                    vertical=source_key,
                    profile_key=profile_key,
                    profile_name=name,
                    trigger_metric=metric_name,
                    origin="trigger",
                )
            )
    return signals


# ---------------------------------------------------------- recurring pattern


def _ordinal(n: int) -> str:
    """1 -> 1st, 2 -> 2nd, 3 -> 3rd, 11 -> 11th."""
    if 10 <= n % 100 <= 20:
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }"


def _lapsed_profiles(source_key: str, lapse_days: int, label: str) -> list[Signal]:
    signals: list[Signal] = []
    min_prior = LAPSE_MIN_PRIOR.get(source_key, 3)
    for profile in store.profiles_with_history(source_key=source_key):
        if profile.days_since_last_event < lapse_days:
            continue
        if profile.event_count < min_prior:
            continue
        history = store.events_for_profile(profile.profile_key, limit=200)
        lifetime = sum(e.value or 0 for e in history)
        signals.append(
            Signal(
                kind=f"lapsed_{label}",
                scope="profile",
                title=f"{profile.display_name} has been quiet for {profile.days_since_last_event} days",
                detail=(
                    f"{profile.event_count} prior events, last on "
                    f"{profile.last_seen.date()}."
                    + (f" ${lifetime:,.0f} lifetime value." if lifetime else "")
                ),
                evidence={
                    "days_quiet": profile.days_since_last_event,
                    "prior_events": profile.event_count,
                    "lifetime_value": round(lifetime, 2),
                    "last_seen": str(profile.last_seen.date()),
                    "recent_metrics": [e.metric_name for e in history[:5]],
                },
                severity="high" if lifetime >= 250 else "medium",
                vertical=source_key,
                profile_key=profile.profile_key,
                profile_name=profile.display_name,
                origin="sweep",
            )
        )
    # Highest lifetime value first -- that is the one worth a human's attention.
    signals.sort(key=lambda s: s.evidence.get("lifetime_value", 0), reverse=True)
    return signals


def _context_of(event: Any) -> str | None:
    props = event.properties
    return (
        props.get("Class Name")
        or props.get("Appointment Type")
        or props.get("Seating Area")
        or None
    )


def _metric_trend(source_key: str, metric_name: str) -> list[Signal]:
    """Compare a negative metric's rate across two adjacent windows.

    Evaluated per-segment as well as overall, and the sharpest qualifying
    segment wins. A vertical-wide average dilutes a real problem: a tripling
    in one class reads as a mild 2.8x bump once healthy classes are mixed in,
    and "the 5:30pm class tripled" is both more actionable and more accurate
    than "no-shows are up a bit".
    """

    def window(lo: int, hi: int, context: str | None) -> tuple[int, int]:
        events = store.events_in_window(
            since_days=hi, until_days=lo, source_key=source_key
        )
        if context is not None:
            events = [e for e in events if _context_of(e) == context]
        bad = sum(1 for e in events if e.metric_name == metric_name)
        good = sum(1 for e in events if e.metric_name.endswith("Booked"))
        return bad, bad + good

    recent_bad_events = store.events_in_window(
        metric_name=metric_name, since_days=TREND_WINDOW_DAYS, source_key=source_key
    )
    if not recent_bad_events:
        return []

    contexts = {c for c in (_context_of(e) for e in recent_bad_events) if c}
    candidates: list[tuple[str | None, float, int, int, int, int]] = []

    for context in [None, *sorted(contexts)]:
        recent_bad, recent_total = window(0, TREND_WINDOW_DAYS, context)
        prior_bad, prior_total = window(TREND_WINDOW_DAYS, TREND_WINDOW_DAYS * 2, context)
        if recent_total < TREND_MIN_VOLUME or prior_total < TREND_MIN_VOLUME:
            continue
        if not prior_bad:
            continue
        recent_rate = recent_bad / recent_total * 100
        prior_rate = prior_bad / prior_total * 100
        if prior_rate <= 0 or recent_rate / prior_rate < TREND_RATIO:
            continue
        candidates.append(
            (context, recent_rate / prior_rate, recent_bad, recent_total, prior_bad, prior_total)
        )

    if not candidates:
        return []

    # Prefer a named segment over the vertical-wide average, then the sharpest.
    candidates.sort(key=lambda c: (c[0] is None, -c[1]))
    hotspot, ratio, recent_bad, recent_total, prior_bad, prior_total = candidates[0]
    recent_rate = recent_bad / recent_total * 100
    prior_rate = prior_bad / prior_total * 100
    label = hotspot or f"{source_key} overall"

    affected = {
        e.profile_key for e in recent_bad_events if hotspot is None or _context_of(e) == hotspot
    }

    return [
        Signal(
            kind="metric_trend",
            scope="aggregate",
            title=(
                f"{metric_name} rate for {label} is {ratio:.1f}x the prior "
                f"{TREND_WINDOW_DAYS} days"
            ),
            detail=(
                f"{recent_rate:.0f}% of bookings ended in a no-show over the last "
                f"{TREND_WINDOW_DAYS} days ({recent_bad}/{recent_total}), versus "
                f"{prior_rate:.0f}% ({prior_bad}/{prior_total}) the "
                f"{TREND_WINDOW_DAYS} days before"
                + (f", concentrated in {hotspot}" if hotspot else "")
                + f". {len(affected)} distinct members affected. This is a "
                "population-level pattern, not one member's habit."
            ),
            evidence={
                "segment": label,
                "recent_rate_pct": round(recent_rate, 1),
                "prior_rate_pct": round(prior_rate, 1),
                "ratio": round(ratio, 2),
                "recent": f"{recent_bad}/{recent_total}",
                "prior": f"{prior_bad}/{prior_total}",
                "window_days": TREND_WINDOW_DAYS,
                "hotspot": hotspot,
                "affected_profiles": len(affected),
            },
            severity="high" if ratio >= 2.5 else "medium",
            vertical=source_key,
            trigger_metric=metric_name,
            origin="sweep",
        )
    ]


def detect_aggregate(vertical: str | None = None) -> list[Signal]:
    """Scheduled sweep: look across all profiles and time windows."""
    signals: list[Signal] = []
    verticals = [vertical] if vertical else ["fitness", "nonprofit", "dental", "restaurant"]

    for source in verticals:
        for metric in NEGATIVE_METRICS:
            signals.extend(_metric_trend(source, metric))

    if not vertical or vertical == "fitness":
        signals.extend(_lapsed_profiles("fitness", LAPSE_DAYS, "member")[:3])
    if not vertical or vertical == "nonprofit":
        signals.extend(_lapsed_profiles("nonprofit", DONOR_LAPSE_DAYS, "donor")[:3])

    order = {"high": 0, "medium": 1, "low": 2}
    signals.sort(key=lambda s: (order.get(s.severity, 3), s.scope != "aggregate"))
    return signals
