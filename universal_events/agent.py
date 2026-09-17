"""Part 2: the proactive agent.  [STUB -- not implemented]

The framing that matters: this is a BUSINESS notification, not a push
notification. Nobody asked it a question. It sweeps event history on a
schedule, decides whether anything is worth a human's attention, and if so
drafts the action.

Model choice is deliberate and should stay visible in the UI:
GEMINI_AGENT_MODEL defaults to gemini-3.5-flash-lite -- the cheapest model
available. The point being made is that noticing "this member stopped coming"
needs good context from the event stream, not a frontier model. Showing the
model name and token count next to each suggestion is what makes that
argument land instead of just being claimed.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Urgency = Literal["low", "medium", "high"]


class BusinessNotification(BaseModel):
    """What the agent returns for one profile."""

    should_flag: bool = Field(description="False means nothing here is worth surfacing")
    headline: str = Field(default="", description="One line, e.g. 'No class booked in 3 weeks'")
    rationale: str = Field(default="", description="The evidence from the event stream")
    suggested_action: str = Field(default="", description="What the business should do")
    draft_message: str = Field(default="", description="Ready-to-send copy, if applicable")
    urgency: Urgency = "low"

    # Populated for demo transparency, not by the model.
    model_used: str | None = None
    tokens_used: int | None = None
    cost_usd: float | None = None
    profile_key: str | None = None
    evaluated_at: datetime | None = None


def build_context(profile_key: str) -> str:
    """Compress a profile's event history into a compact prompt block. TODO.

    Keep this tight. The whole thesis is that a small model does fine when the
    context is well-built, so effort belongs here rather than in model size.
    """
    raise NotImplementedError


def evaluate_profile(profile_key: str) -> BusinessNotification:
    """Ask the cheap model whether anything here is worth flagging. TODO."""
    raise NotImplementedError


def sweep(min_events: int = 2) -> list[BusinessNotification]:
    """Run over every profile with history; return only the flagged ones. TODO."""
    raise NotImplementedError
