"""Deterministic flow auditing.

Two jobs:
  1. The offline fallback when no LLM is reachable. The demo must still
     produce a real, correct proposal with the network unplugged.
  2. A quality floor. These findings are things we KNOW are wrong, so the
     LLM never has to rediscover them and can't score worse than this.

Every finding carries a concrete EditOp, so the rules path yields an
applicable patch rather than advice.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .patch import EditOp, walk_steps

# Flows reacting to a miss should reach the person while the miss is still
# fresh; a multi-day wait arrives after they have already moved on.
FAST_FOLLOWUP_HOURS = 24
STALE_REENGAGEMENT_HOURS = 24 * 90

ECOMMERCE_WORDS = (
    "order", "purchase", "shop", "cart", "checkout", "shipping", "discount code",
    "buy", "restock", "sale",
)

NEGATIVE_TRIGGERS = {"Class No-Show", "Appointment No-Show", "Reservation No-Show"}


@dataclass
class Finding:
    code: str
    step_id: str | None
    issue: str
    why: str
    severity: str = "medium"
    ops: list[EditOp] = field(default_factory=list)


def _first_delay(flow: dict) -> dict | None:
    """The leading delay, if the flow opens with one.

    Only a delay in first position counts: that is the gap between the trigger
    firing and the first contact, which is what these rules reason about.
    """
    steps = flow.get("steps") or []
    first = steps[0] if steps else None
    return first if first and first.get("type") == "delay" else None


def _has_channel(flow: dict, kind: str) -> bool:
    return any(s.get("type") == kind for s in walk_steps(flow.get("steps") or []))


def _sms_step(vertical: str | None, context: str | None = None) -> dict:
    body = {
        "fitness": "Hi {{ first_name }}, we missed you"
        + (f" at {context}" if context else "")
        + ". Want us to hold you a spot this week? Reply YES.",
        "dental": "Hi {{ first_name }}, it's time for your cleaning. "
        "Reply BOOK and we'll find you a slot.",
        "restaurant": "Hi {{ first_name }}, sorry we missed you. "
        "Want us to hold a table this week?",
    }.get(vertical or "", "Hi {{ first_name }}, we'd love to see you back soon.")
    return {"type": "sms", "body": body, "send_if": "sms_consent == true"}


def audit_flow(flow: dict, signal: Any | None = None) -> list[Finding]:
    """Lint a flow. Ordered most to least severe."""
    findings: list[Finding] = []
    vertical = flow.get("vertical")
    trigger = (flow.get("trigger") or {}).get("metric")
    context = None
    if signal is not None:
        context = (signal.evidence or {}).get("hotspot") or (
            signal.evidence or {}
        ).get("common_context")

    # 1. Slow first touch on a flow that reacts to a miss.
    delay = _first_delay(flow)
    if trigger in NEGATIVE_TRIGGERS and delay and delay.get("hours", 0) > FAST_FOLLOWUP_HOURS:
        hours = delay["hours"]
        findings.append(
            Finding(
                code="slow_first_touch",
                step_id=delay.get("id"),
                issue=f"First contact waits {hours / 24:.0f} days after a no-show.",
                why=(
                    f"The flow is triggered by '{trigger}', but nothing reaches the "
                    f"person for {hours / 24:.0f} days. By then the decision to "
                    "disengage has already been made. A same-day nudge recovers "
                    "far more bookings than a delayed apology."
                ),
                severity="high",
                ops=[
                    EditOp(
                        op="set_field",
                        step_id=delay.get("id"),
                        field="hours",
                        value=FAST_FOLLOWUP_HOURS,
                        rationale=f"{hours / 24:.0f} days is too late after a no-show",
                    )
                ],
            )
        )

    # 2. Only one channel, on a flow trying to recover someone.
    if _has_channel(flow, "email") and not _has_channel(flow, "sms"):
        anchor = delay.get("id") if delay else (flow.get("steps") or [{}])[0].get("id")
        findings.append(
            Finding(
                code="single_channel",
                step_id=anchor,
                issue="Email is the only channel, even for consented profiles.",
                why=(
                    "Every recipient gets email only. Profiles that have given SMS "
                    "consent are reachable on a channel with far higher open rates, "
                    "and the flow never uses it."
                ),
                severity="medium",
                ops=[
                    EditOp(
                        op="insert_after",
                        step_id=anchor,
                        step=_sms_step(vertical, context),
                        rationale="add an SMS touch, gated on consent",
                    )
                ],
            )
        )

    # 3. Splits that drop people on the floor.
    for step in walk_steps(flow.get("steps") or []):
        if step.get("type") != "split":
            continue
        for branch in ("true_branch", "false_branch"):
            if step.get(branch):
                continue
            side = branch.replace("_branch", "")
            findings.append(
                Finding(
                    code="dead_end_branch",
                    step_id=step.get("id"),
                    issue=f"The '{side}' branch of {step.get('id')} is empty.",
                    why=(
                        "Recipients routed down this branch receive nothing and exit "
                        "silently. The split implies a decision was intended here, so "
                        "the empty side is an unfinished flow rather than a choice."
                    ),
                    severity="medium",
                    ops=[
                        EditOp(
                            op="fill_branch",
                            step_id=step.get("id"),
                            branch=side,
                            value=[
                                {
                                    "type": "email",
                                    "subject": "Good to see you back",
                                    "body": "Great to see you again, {{ first_name }}. "
                                    "We're glad you're back.",
                                }
                            ],
                            rationale="close the dead-end branch",
                        )
                    ],
                )
            )

    # 4. Ecommerce copy in a non-ecommerce business.
    for step in walk_steps(flow.get("steps") or []):
        if step.get("type") not in ("email", "sms"):
            continue
        blob = f"{step.get('subject', '')} {step.get('body', '')} {step.get('cta', '')}".lower()
        hits = [w for w in ECOMMERCE_WORDS if w in blob]
        if hits:
            findings.append(
                Finding(
                    code="ecommerce_copy",
                    step_id=step.get("id"),
                    issue=f"Copy uses ecommerce language ({', '.join(hits[:3])}).",
                    why=(
                        f"This is a {vertical} business with no orders or carts, but the "
                        "copy was cloned from an ecommerce template. It reads as a "
                        "mistake to the recipient and undermines trust."
                    ),
                    severity="high",
                    ops=[
                        EditOp(
                            op="set_field",
                            step_id=step.get("id"),
                            field="subject",
                            value="Time for your next visit",
                            rationale="remove ecommerce framing",
                        ),
                        EditOp(
                            op="set_field",
                            step_id=step.get("id"),
                            field="body",
                            value="Hi {{ first_name }}, it's been about six months "
                            "since your last cleaning. Ready to book your next visit?",
                            rationale="rewrite for a dental practice",
                        ),
                        EditOp(
                            op="set_field",
                            step_id=step.get("id"),
                            field="cta",
                            value="Book my cleaning",
                            rationale="match the actual action",
                        ),
                    ],
                )
            )

    # 5. Re-engagement that waits longer than the audience actually stays warm.
    #
    # Evidence-gated on purpose. A blanket "long delay is bad" rule flags a
    # 180-day dental recall, which is clinically correct -- so this only fires
    # when observed behaviour contradicts the delay: the audience goes quiet
    # measurably earlier than the flow waits.
    observed = (signal.evidence or {}).get("days_quiet") if signal is not None else None
    delay_days = (delay or {}).get("hours", 0) / 24
    if (
        delay
        and observed
        and delay_days >= STALE_REENGAGEMENT_HOURS / 24
        and delay_days > observed * 1.15
    ):
        hours = delay["hours"]
        findings.append(
            Finding(
                code="stale_reengagement",
                step_id=delay.get("id"),
                issue=f"Re-engagement waits {hours / 24:.0f} days.",
                why=(
                    f"The flow waits {hours / 24:.0f} days before reaching out"
                    + (
                        f", but this audience goes quiet around day {observed}"
                        if observed
                        else ""
                    )
                    + ". The first contact lands well after attention has faded."
                ),
                severity="high",
                ops=[
                    EditOp(
                        op="set_field",
                        step_id=delay.get("id"),
                        field="hours",
                        value=24 * 45,
                        rationale="reach out while the relationship is still warm",
                    )
                ],
            )
        )

    order = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda f: order.get(f.severity, 3))
    return findings
