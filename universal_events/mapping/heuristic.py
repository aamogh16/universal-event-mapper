"""Zero-config inference: understand a payload with no config and no model.

This is the demo's safety net and, arguably, its most convincing trick. Hand it
a vertical it has never seen -- veterinary, tutoring, physical therapy -- and it
still finds the person, names the event, and keeps the useful properties. No
network call, so it cannot fail on stage.

It works by recognising *shapes* rather than specific field names:
  - an email is an email wherever it appears, however the key is spelled
  - the human is usually the deepest object holding contact details
  - an event name is an entity noun plus a status/outcome word
"""

from __future__ import annotations

import re
from typing import Any

from .base import EMAIL_RE, FieldTrace, MappingResult, ProfileIdentity, humanize
from .config_mapper import _coerce_datetime, _coerce_float
from .paths import walk

PHONE_RE = re.compile(r"^\+?[0-9][0-9\-\s().]{6,20}$")

EMAIL_KEY_HINTS = ("email", "e_mail", "mail")
PHONE_KEY_HINTS = ("phone", "mobile", "sms", "cell", "telephone", "tel")
FIRST_NAME_HINTS = ("firstname", "first_name", "first", "given", "givenname", "forename")
LAST_NAME_HINTS = ("lastname", "last_name", "last", "family", "familyname", "surname")
FULL_NAME_HINTS = ("displayname", "display_name", "fullname", "full_name", "name")

EXTERNAL_ID_HINTS = (
    "clientid", "client_id", "customerid", "customer_id", "guestguid", "guest_guid",
    "memberid", "member_id", "patientid", "patient_id", "contactid", "contact_id",
    "constituentid", "userid", "user_id", "donorid", "donor_id",
)

# Entity nouns, most specific first -- "appointment" should beat "booking".
ENTITY_WORDS = (
    "appointment", "reservation", "encounter", "membership", "subscription",
    "donation", "pledge", "session", "enrollment", "registration", "booking",
    "invitee", "visit", "class", "order", "invoice", "payment", "claim", "lead",
)

STATUS_KEY_HINTS = (
    "status", "state", "outcome", "disposition", "dispositioncode", "bookingstatus",
    "eventtype", "event_type", "event", "action", "recordtype", "record_type",
    "topic", "entitytype", "entity_type", "type", "verb",
)

# Values that are categories, not things that happened.
NON_ACTION_VALUES = {
    "individual", "organization", "home", "work", "mobile", "email", "phone",
    "usd", "canine", "feline", "active", "reservation", "donation", "true", "false",
    "in_person", "remote", "web", "credit_card", "creditcard", "ach", "general",
}

# Never promote these into event properties.
NOISE_KEY_HINTS = (
    "guid", "uuid", "uri", "url", "npi", "messageid", "message_id", "schemaversion",
    "accountid", "account_id", "restaurantguid", "siteid", "site_id", "token",
    "password", "secret", "apikey", "api_key", "signature",
)


def _last_segment(path: str) -> str:
    return path.rsplit(".", 1)[-1]


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _key_matches(path: str, hints: tuple[str, ...]) -> bool:
    key = _norm(_last_segment(path))
    return any(hint.replace("_", "") in key for hint in hints)


def _find_email(leaves: list[tuple[str, Any]]) -> tuple[str | None, str | None]:
    """Prefer a well-named key; fall back to any value that looks like an email."""
    fallback: tuple[str, str] | None = None
    for path, value in leaves:
        if not isinstance(value, str) or not EMAIL_RE.fullmatch(value.strip()):
            continue
        if _key_matches(path, EMAIL_KEY_HINTS):
            return value.strip(), path
        if fallback is None:
            fallback = (value.strip(), path)
    return fallback if fallback else (None, None)


def _find_phone(leaves: list[tuple[str, Any]]) -> tuple[str | None, str | None]:
    fallback: tuple[str, str] | None = None
    for path, value in leaves:
        text = str(value).strip() if value is not None else ""
        if not text or not PHONE_RE.match(text):
            continue
        digits = re.sub(r"\D", "", text)
        if not 7 <= len(digits) <= 15:
            continue
        if _key_matches(path, PHONE_KEY_HINTS):
            return text, path
        if fallback is None:
            fallback = (text, path)
    return fallback if fallback else (None, None)


def _find_by_hints(
    leaves: list[tuple[str, Any]], hints: tuple[str, ...], *, near: str | None = None
) -> tuple[Any, str | None]:
    """Find a value by key hint, preferring keys that sit near the identity block.

    `near` is the path of the email/phone we already found. Fields sharing that
    parent are far more likely to be the right person's name than a clinician's.
    """
    parent = near.rsplit(".", 1)[0] if near and "." in near else None
    best: tuple[Any, str] | None = None
    for path, value in leaves:
        if value is None or not _key_matches(path, hints):
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if parent and path.startswith(f"{parent}."):
            return value, path
        if best is None:
            best = (value, path)
    return best if best else (None, None)


def _infer_metric(
    payload: Any, leaves: list[tuple[str, Any]], traces: list[FieldTrace]
) -> tuple[str, float, str]:
    """Derive an event name. Returns (metric_name, confidence, explanation)."""

    # Collect candidate action words from status-ish keys.
    actions: list[tuple[str, str]] = []  # (value, path)
    for path, value in leaves:
        if not isinstance(value, str) or not value.strip():
            continue
        if not _key_matches(path, STATUS_KEY_HINTS):
            continue
        if _norm(value) in NON_ACTION_VALUES:
            continue
        actions.append((value.strip(), path))

    # A dotted/underscored event type is already a full event name.
    for value, path in actions:
        if re.search(r"[._]", value) and not value.startswith("http"):
            name = humanize(value)
            traces.append(
                FieldTrace(
                    source_path=path,
                    destination="metric.name",
                    value=value,
                    note="event-type key recognised",
                )
            )
            return name, 0.85, f"Used the event-type value at `{path}`."

    # Otherwise: entity noun + action word.
    entity: str | None = None
    entity_path: str | None = None
    haystack = " ".join(path.lower() for path, _ in leaves)
    for word in ENTITY_WORDS:
        if word in haystack:
            entity = word
            for path, _ in leaves:
                if word in path.lower():
                    entity_path = path.split(".")[0] if "." in path else path
                    break
            break

    if actions:
        action, action_path = actions[0]
        if entity and _norm(entity) not in _norm(action):
            name = humanize(f"{entity} {action}")
            explanation = (
                f"Combined the entity `{entity}` (seen in the payload structure) "
                f"with the status value at `{action_path}`."
            )
            confidence = 0.75
        else:
            name = humanize(action)
            explanation = f"Used the status value at `{action_path}`."
            confidence = 0.65
        traces.append(
            FieldTrace(
                source_path=action_path,
                destination="metric.name",
                value=action,
                note=f"inferred -> {name!r}",
            )
        )
        return name, confidence, explanation

    if entity:
        name = humanize(f"{entity} activity")
        traces.append(
            FieldTrace(
                source_path=entity_path or "<structure>",
                destination="metric.name",
                value=name,
                note="entity noun only; no status field found",
            )
        )
        return name, 0.45, f"Found the entity `{entity}` but no status field."

    traces.append(
        FieldTrace(
            source_path="<none>",
            destination="metric.name",
            value="Incoming Event",
            note="no event-type or status field found",
        )
    )
    return "Incoming Event", 0.2, "Could not identify an event type or status field."


def map_with_heuristics(payload: dict[str, Any], *, source_hint: str = "") -> MappingResult:
    leaves = [(path, value) for path, value in walk(payload)]
    traces: list[FieldTrace] = []
    warnings: list[str] = []

    # --- identity -----------------------------------------------------------
    email, email_path = _find_email(leaves)
    phone, phone_path = _find_phone(leaves)
    anchor = email_path or phone_path

    if email:
        traces.append(
            FieldTrace(source_path=email_path or "", destination="profile.email", value=email)
        )
    if phone:
        traces.append(
            FieldTrace(
                source_path=phone_path or "", destination="profile.phone_number", value=phone
            )
        )

    first, first_path = _find_by_hints(leaves, FIRST_NAME_HINTS, near=anchor)
    last, last_path = _find_by_hints(leaves, LAST_NAME_HINTS, near=anchor)

    if not first:
        full, full_path = _find_by_hints(leaves, FULL_NAME_HINTS, near=anchor)
        if isinstance(full, str) and " " in full.strip():
            parts = full.strip().split()
            first, last = parts[0], " ".join(parts[1:])
            first_path = last_path = full_path
            traces.append(
                FieldTrace(
                    source_path=full_path or "",
                    destination="profile.first_name + last_name",
                    value=full,
                    note="split a combined name field",
                )
            )
    else:
        if first:
            traces.append(
                FieldTrace(
                    source_path=first_path or "",
                    destination="profile.first_name",
                    value=first,
                )
            )
        if last:
            traces.append(
                FieldTrace(
                    source_path=last_path or "", destination="profile.last_name", value=last
                )
            )

    external_id = None
    if not email and not phone:
        ext, ext_path = _find_by_hints(leaves, EXTERNAL_ID_HINTS)
        if ext is not None:
            external_id = str(ext)
            traces.append(
                FieldTrace(
                    source_path=ext_path or "",
                    destination="profile.external_id",
                    value=external_id,
                    note="no email or phone; used an ID so the event still lands",
                )
            )

    identity = ProfileIdentity(
        email=email,
        phone_number=phone,
        external_id=external_id,
        first_name=str(first) if first else None,
        last_name=str(last) if last else None,
    )
    if not identity.is_resolvable:
        warnings.append(
            "No email, phone, or usable ID found — Klaviyo cannot attach this event "
            "to a profile."
        )

    # --- metric -------------------------------------------------------------
    metric_name, confidence, explanation = _infer_metric(payload, leaves, traces)

    # --- timestamp ----------------------------------------------------------
    occurred_at = None
    ts_path = None
    for path, value in leaves:
        key = _norm(_last_segment(path))
        if not any(token in key for token in ("time", "date", "at", "when", "start")):
            continue
        parsed = _coerce_datetime(value)
        if parsed is not None:
            occurred_at, ts_path = parsed, path
            break
    if occurred_at is not None:
        traces.append(FieldTrace(source_path=ts_path or "", destination="time", value=str(occurred_at)))

    # --- value --------------------------------------------------------------
    numeric = None
    currency = None
    for path, value in leaves:
        key = _norm(_last_segment(path))
        if not any(
            token in key
            for token in ("amount", "total", "price", "cost", "paid", "charged", "revenue")
        ):
            continue
        candidate = _coerce_float(value)
        if candidate is not None and candidate > 0:
            numeric = candidate
            traces.append(FieldTrace(source_path=path, destination="value", value=numeric))
            break
    if numeric is not None:
        for path, value in leaves:
            if "currency" in _norm(_last_segment(path)) and isinstance(value, str):
                currency = value.strip().upper()
                traces.append(
                    FieldTrace(source_path=path, destination="value_currency", value=currency)
                )
                break
        if currency is None:
            currency = "USD"
            warnings.append("No currency field found; assumed USD.")

    # --- properties ---------------------------------------------------------
    consumed = {
        p for p in (email_path, phone_path, first_path, last_path, ts_path) if p
    }
    properties: dict[str, Any] = {}
    for path, value in leaves:
        if path in consumed or value is None:
            continue
        if _key_matches(path, NOISE_KEY_HINTS):
            continue
        if isinstance(value, str) and (not value.strip() or value.startswith("http")):
            continue
        if isinstance(value, str) and EMAIL_RE.fullmatch(value.strip()):
            continue
        label = humanize(_last_segment(path))
        if not label or label in properties:
            continue
        properties[label] = value
        if len(properties) >= 25:  # Klaviyo allows 400; keep the demo readable.
            warnings.append("Truncated to the first 25 inferred properties.")
            break

    return MappingResult(
        metric_name=metric_name,
        identity=identity,
        properties=properties,
        value=numeric,
        value_currency=currency,
        occurred_at=occurred_at,
        unique_id=None,
        strategy="heuristic",
        confidence=confidence,
        reasoning=(
            f"No config for this payload, so it was inferred from structure. {explanation} "
            "Deterministic and offline — no model call."
        ),
        field_traces=traces,
        warnings=warnings,
    )
