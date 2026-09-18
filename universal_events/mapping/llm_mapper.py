"""LLM-driven mapping: infer how to read a payload nobody has integrated.

Used when no config matches. Falls through to the offline heuristic engine if
the model is unreachable, so this path can fail without breaking ingestion.

THE DESIGN DECISION THAT MATTERS:
Ask the model for PATHS, not VALUES.

The model's job is to say "the email lives at `patient.owner.contact_email`",
not to echo the address. Values are then extracted deterministically by
paths.resolve(). Three reasons this matters:
  1. The model cannot hallucinate a customer's email into Klaviyo.
  2. The mapping is auditable -- you can show the path it chose.
  3. An inferred mapping can be SAVED as a YAML config (see to_yaml_config),
     so the LLM effectively writes the deterministic config once, and every
     later payload from that source runs config-driven and free.

That last point is the real pitch: the LLM is a one-time onboarding cost,
not a per-event cost.
"""

from __future__ import annotations

import json
import time
from typing import Any

from pydantic import BaseModel, Field

from ..config import settings
from .base import FieldTrace, MappingResult, ProfileIdentity
from .config_mapper import _coerce_datetime, _coerce_float
from .paths import MISSING, resolve, walk


class PropertyPath(BaseModel):
    label: str = Field(description="Human-readable Klaviyo property name")
    path: str = Field(description="Dotted path into the source payload")


class InferredMapping(BaseModel):
    """The schema the model is constrained to return."""

    metric_name: str
    email_path: str | None = None
    phone_path: str | None = None
    external_id_path: str | None = None
    first_name_path: str | None = None
    last_name_path: str | None = None
    # Some payloads only carry a combined name. Return that path here and it
    # gets split in code, rather than the whole name landing in first_name.
    full_name_path: str | None = None
    timestamp_path: str | None = None
    value_path: str | None = None
    # A PATH to the currency, like every other field -- not a literal code.
    # Left unnamed, the model returned "estimated_cost.iso_currency" as if it
    # were a code, which is the right instinct given everything else is a path.
    currency_path: str | None = None
    property_paths: list[PropertyPath] = Field(default_factory=list)
    confidence: float = 0.0
    reasoning: str = ""


SYSTEM_PROMPT = """\
You map arbitrary third-party webhook payloads onto Klaviyo events.

You are given one payload from a tool nobody has written an integration for. \
Work out who the person is, what happened, and which fields are worth keeping.

CRITICAL: return PATHS, never values. For the email you return \
"patient.owner.contact_email", not the address itself. Everything is extracted \
from the payload afterwards by code. This means you cannot put a wrong value \
into anyone's marketing profile, and it means the mapping you produce can be \
saved and reused for free on every later payload from this source.

Rules:
- Every path must exist in the payload shown to you. Use dotted notation, with \
numeric indexes for lists: "subject.telecom.0.value".
- The identifier is the person the business would market to. If a payload has \
both a dependent and a responsible adult -- a student and a guardian, a pet and \
an owner -- choose the ADULT / account holder, because that is who receives \
email and who pays.
- metric_name is a short human event name in Title Case describing what the \
CUSTOMER DID, in words the business would use. Not a field name and NOT the \
tool's internal event code. A door system reporting "lock.unlock" means the \
member CHECKED IN -- call it "Gym Check-In", not "Lock Unlocked". A scanner \
reporting "BODY_COMPOSITION" means they had a body scan. Translate machine \
vocabulary into what a marketer would recognise, and include the subject when \
it disambiguates: "PT Session Completed" beats "Appointment Completed".
- Pick properties a marketer would segment on: service type, staff, location, \
plan, amounts. Skip internal ids, GUIDs, URLs, schema versions and tokens.
- external_id_path must be YOUR SYSTEM'S ID FOR THE PERSON -- a customer id, \
client id, member id, patient id. It is NOT the id of the thing that happened. \
A session_uuid, booking_ref, appointment_id, order_id, encounter_id or \
transaction id identifies an EVENT, not a human: those change every time the \
same person does something, so using one as a profile identifier is wrong. \
If the payload has no stable per-person id, leave external_id_path null -- an \
email alone is a perfectly good identifier.
- If no path identifies a person, leave all identifier paths null. Do not \
invent one.
- If the payload has separate first/last name fields, use first_name_path and \
last_name_path. If it only has one combined name field ("display_name", \
"name"), leave those null and put that path in full_name_path instead.
- currency_path is a PATH to a currency field, not a currency code."""


def build_prompt(payload: dict[str, Any]) -> str:
    """Render the inference prompt: the payload plus its own path listing.

    The flattened path list is included because it removes any ambiguity about
    what a valid path looks like -- the model picks from real paths rather than
    reconstructing dotted notation from indented JSON.
    """
    body = json.dumps(payload, indent=2, default=str)
    if len(body) > 6000:
        body = body[:6000] + "\n... (truncated)"

    leaves = list(walk(payload))[:120]
    listing = "\n".join(
        f"  {path} = {str(value)[:60]!r}" for path, value in leaves
    )
    return (
        f"## Payload\n```json\n{body}\n```\n\n"
        f"## Every path in this payload\n{listing}\n\n"
        "## Your task\nMap this onto a Klaviyo event. Return paths only."
    )


# Field names that mean the number is in hundredths. The model returns paths,
# not values, so it cannot divide -- and a payload saying `price_paid_cents:
# 96000` would otherwise be reported as a $96,000 personal-training package.
MINOR_UNIT_HINTS = ("cents", "cent", "pence", "minor_units", "_minor", "subunit")


def _is_minor_units(path: str | None) -> bool:
    if not path:
        return False
    tail = path.rsplit(".", 1)[-1].lower()
    return any(hint in tail for hint in MINOR_UNIT_HINTS)


def _resolve(payload: dict[str, Any], path: str | None) -> tuple[Any, str | None]:
    """Resolve a model-supplied path, tolerating a missing or bad one."""
    if not path:
        return MISSING, None
    value = resolve(payload, path)
    if value is MISSING or value is None:
        return MISSING, None
    if isinstance(value, (str, list, dict)) and len(value) == 0:
        return MISSING, None
    return value, path


def map_with_llm(payload: dict[str, Any], *, model: str | None = None) -> MappingResult:
    """Infer a mapping for an unfamiliar payload using a model.

    Raises LLMMappingError on any failure, so the pipeline falls through to the
    deterministic heuristic rather than surfacing an error to the user.
    """
    if not settings.openai_configured:
        raise LLMMappingError("no OpenAI key configured")

    import openai

    chosen = model or settings.openai_audit_model
    started = time.monotonic()
    try:
        response = openai.OpenAI(api_key=settings.openai_api_key).responses.parse(
            model=chosen,
            input=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_prompt(payload)},
            ],
            text_format=InferredMapping,
        )
        inferred = response.output_parsed
    except Exception as exc:
        raise LLMMappingError(f"{type(exc).__name__}: {exc}") from exc

    if inferred is None:
        raise LLMMappingError("model returned no parsable mapping")

    latency = int((time.monotonic() - started) * 1000)
    traces: list[FieldTrace] = []
    warnings: list[str] = []

    # --- identity, resolved from paths (never from model-supplied values) ---
    identity_kwargs: dict[str, Any] = {}
    for field, path in (
        ("email", inferred.email_path),
        ("phone_number", inferred.phone_path),
        ("external_id", inferred.external_id_path),
        ("first_name", inferred.first_name_path),
        ("last_name", inferred.last_name_path),
    ):
        value, won = _resolve(payload, path)
        if value is MISSING:
            if path:
                warnings.append(f"{field}: path {path!r} did not resolve; skipped.")
            continue
        identity_kwargs[field] = str(value)
        traces.append(
            FieldTrace(source_path=won or "", destination=f"profile.{field}", value=value)
        )

    # A combined name field gets split here rather than by the model.
    if "first_name" not in identity_kwargs and inferred.full_name_path:
        full, won = _resolve(payload, inferred.full_name_path)
        if full is not MISSING and isinstance(full, str) and full.strip():
            parts = full.strip().split()
            identity_kwargs["first_name"] = parts[0]
            if len(parts) > 1:
                identity_kwargs["last_name"] = " ".join(parts[1:])
            traces.append(
                FieldTrace(
                    source_path=won or "",
                    destination="profile.first_name + last_name",
                    value=full,
                    note="split a combined name field",
                )
            )

    identity = ProfileIdentity(**identity_kwargs)
    if not identity.is_resolvable:
        raise LLMMappingError("inferred mapping identified no profile")

    metric_name = (inferred.metric_name or "").strip()
    if not metric_name:
        raise LLMMappingError("inferred mapping had no metric name")
    traces.append(
        FieldTrace(source_path="<inferred>", destination="metric.name", value=metric_name)
    )

    occurred_at = None
    ts, ts_path = _resolve(payload, inferred.timestamp_path)
    if ts is not MISSING:
        occurred_at = _coerce_datetime(ts)
        if occurred_at is None:
            warnings.append(f"Could not parse a timestamp from {ts_path!r}.")
        else:
            # Show the conversion, not just the input. A bare unix integer in
            # the "time" row hides the only interesting thing about it.
            traces.append(
                FieldTrace(
                    source_path=ts_path or "",
                    destination="time",
                    value=occurred_at.isoformat(),
                    note=f"parsed from {ts!r}",
                )
            )

    numeric = None
    currency = None
    raw_value, value_path = _resolve(payload, inferred.value_path)
    if raw_value is not MISSING:
        numeric = _coerce_float(raw_value)
        if numeric is None:
            warnings.append(f"Non-numeric value at {value_path!r}; omitted.")
        else:
            if _is_minor_units(value_path):
                numeric = numeric / 100
                warnings.append(
                    f"{value_path} is in minor units; divided by 100 to get "
                    f"{numeric:.2f}."
                )
            cur, cur_path = _resolve(payload, inferred.currency_path)
            currency = str(cur).upper() if cur is not MISSING else "USD"
            if cur is MISSING:
                warnings.append("No currency field resolved; assumed USD.")
            traces.append(
                FieldTrace(source_path=value_path or "", destination="value", value=numeric)
            )

    properties: dict[str, Any] = {}
    for entry in inferred.property_paths[:30]:
        value, won = _resolve(payload, entry.path)
        if value is MISSING or not entry.label:
            continue
        properties[entry.label] = value
        traces.append(
            FieldTrace(
                source_path=won or "",
                destination=f"properties['{entry.label}']",
                value=value,
            )
        )

    usage = getattr(response, "usage", None)
    tokens = None
    if usage is not None:
        tin = getattr(usage, "input_tokens", 0) or 0
        tout = getattr(usage, "output_tokens", 0) or 0
        tokens = tin + tout

    return MappingResult(
        metric_name=metric_name,
        identity=identity,
        properties=properties,
        value=numeric,
        value_currency=currency if numeric is not None else None,
        occurred_at=occurred_at,
        strategy="llm",
        confidence=max(0.0, min(1.0, inferred.confidence or 0.0)),
        reasoning=inferred.reasoning
        or "Mapping inferred from the payload's structure by a model.",
        field_traces=traces,
        warnings=warnings,
        model_used=chosen,
        tokens_used=tokens,
        latency_ms=latency,
    )


def to_yaml_config(
    inferred: InferredMapping, source_name: str, tool_name: str | None = None
) -> str:
    """Serialise an inferred mapping into a mappings/*.yaml config.

    This is what turns a one-off inference into a permanent, deterministic,
    zero-cost mapping: the model is a one-time onboarding cost, not a
    per-event cost.
    """
    lines = [
        f"# Inferred from one sample payload. Review before trusting.",
        f"source: {source_name}",
        f"tool: {tool_name or source_name}",
        "",
        "identity:",
    ]
    for field, path in (
        ("email", inferred.email_path),
        ("phone_number", inferred.phone_path),
        ("external_id", inferred.external_id_path),
        ("first_name", inferred.first_name_path),
        ("last_name", inferred.last_name_path),
    ):
        if path:
            lines.append(f"  {field}: [{path}]")

    lines += ["", "metric:", f"  default: {inferred.metric_name}"]
    if inferred.timestamp_path:
        lines += ["", "timestamp:", f"  from: [{inferred.timestamp_path}]"]
    if inferred.value_path:
        lines += [
            "",
            "value:",
            f"  from: [{inferred.value_path}]",
            f"  currency_from: [{inferred.currency_path}]"
            if inferred.currency_path
            else "  currency_default: USD",
        ]
    if inferred.property_paths:
        lines += ["", "properties:"]
        for entry in inferred.property_paths:
            lines.append(f"  {entry.label}: {entry.path}")
    return "\n".join(lines) + "\n"


class LLMMappingError(RuntimeError):
    pass
