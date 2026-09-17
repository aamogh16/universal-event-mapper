"""The shared vocabulary every mapping strategy speaks.

A mapper's job is to turn an arbitrary third-party payload into a
``MappingResult``: who the person is, what happened, and what's worth keeping.
Rendering that into Klaviyo's wire format happens in one place
(``MappingResult.to_klaviyo_payload``) so all three strategies -- config, LLM,
and heuristic -- are guaranteed to produce identical request shapes.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

Strategy = Literal["config", "llm", "heuristic"]

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


class FieldTrace(BaseModel):
    """One source-field -> destination decision.

    This exists purely so the demo can show *why* a mapping looks the way it
    does. It is the visual payoff of the whole project: `client.email` became
    `profile.email`, and here is the value that moved.
    """

    source_path: str
    destination: str
    value: Any = None
    note: str = ""


class ProfileIdentity(BaseModel):
    """How Klaviyo will recognise this person.

    Klaviyo needs at least one of email / phone_number / external_id to attach
    an event to a profile (and will create the profile if it doesn't exist).
    """

    email: str | None = None
    phone_number: str | None = None
    external_id: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    properties: dict[str, Any] = Field(default_factory=dict)

    @property
    def is_resolvable(self) -> bool:
        return bool(self.email or self.phone_number or self.external_id)

    @property
    def primary(self) -> str:
        return self.email or self.phone_number or self.external_id or "<unidentified>"

    def klaviyo_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {}
        for key in ("email", "phone_number", "external_id", "first_name", "last_name"):
            value = getattr(self, key)
            if value:
                attrs[key] = value
        if self.properties:
            attrs["properties"] = self.properties
        return attrs


class MappingResult(BaseModel):
    """A payload, understood."""

    metric_name: str
    identity: ProfileIdentity
    properties: dict[str, Any] = Field(default_factory=dict)

    value: float | None = None
    value_currency: str | None = None
    occurred_at: datetime | None = None
    unique_id: str | None = None

    strategy: Strategy
    confidence: float = 1.0
    reasoning: str = ""
    field_traces: list[FieldTrace] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    # Populated when the LLM path runs, so the demo can show what it cost.
    model_used: str | None = None
    tokens_used: int | None = None
    latency_ms: int | None = None

    def to_klaviyo_payload(self) -> dict[str, Any]:
        """Render to the Klaviyo Events API body.

        Shape per POST https://a.klaviyo.com/api/events (revision 2026-07-15).
        """
        attributes: dict[str, Any] = {
            "metric": {
                "data": {
                    "type": "metric",
                    "attributes": {"name": self.metric_name},
                }
            },
            "profile": {
                "data": {
                    "type": "profile",
                    "attributes": self.identity.klaviyo_attributes(),
                }
            },
            # Klaviyo requires `properties` to be present, even if empty.
            "properties": self.properties or {},
        }

        occurred = self.occurred_at
        if occurred is not None:
            # Klaviyo wants ISO 8601; a naive datetime is ambiguous, so pin UTC.
            if occurred.tzinfo is None:
                occurred = occurred.replace(tzinfo=timezone.utc)
            attributes["time"] = occurred.isoformat()

        if self.value is not None:
            attributes["value"] = self.value
            if self.value_currency:
                attributes["value_currency"] = self.value_currency

        if self.unique_id:
            # Klaviyo deduplicates on (profile, metric, unique_id), which makes
            # replaying a demo safe: the same payload twice won't double-count.
            attributes["unique_id"] = self.unique_id

        return {"data": {"type": "event", "attributes": attributes}}


def humanize(raw: str) -> str:
    """Turn a machine field/event name into a Klaviyo-style metric name.

    ``appointment.completed`` -> ``Appointment Completed``
    ``class_no_show``         -> ``Class No Show``
    ``reservationSeated``     -> ``Reservation Seated``

    Metric names are what Elias would actually read in the Klaviyo UI, so this
    matters more than it looks.
    """
    if not raw:
        return ""
    text = str(raw)
    # Split camelCase before normalising separators.
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    text = re.sub(r"[._\-/]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    small = {"a", "an", "and", "at", "by", "for", "in", "of", "on", "or", "the", "to"}
    words = []
    for i, word in enumerate(text.split(" ")):
        lower = word.lower()
        if i > 0 and lower in small:
            words.append(lower)
        elif word.isupper() and len(word) <= 4:
            words.append(word)  # preserve acronyms like RSVP, SMS
        else:
            words.append(word[:1].upper() + word[1:])
    return " ".join(words)
