"""Thin Klaviyo Events API client.

Verified against the live docs (September 2026):
  POST https://a.klaviyo.com/api/events
    Authorization: Klaviyo-API-Key pk_...
    revision: 2026-07-15
    Content-Type: application/vnd.api+json
  -> 202 Accepted on success (fire-and-forget: 202 means *queued*, not stored)

  GET  https://a.klaviyo.com/api/events?filter=equals(profile_id,'...')
  GET  https://a.klaviyo.com/api/profiles?filter=equals(email,'...')
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import httpx

from .config import settings


@dataclass
class DeliveryResult:
    """What happened when we tried to hand the event to Klaviyo."""

    ok: bool
    status_code: int | None
    request_url: str
    request_headers: dict[str, str]
    request_body: dict[str, Any]
    response_body: Any = None
    error: str | None = None
    dry_run: bool = False
    latency_ms: int | None = None
    profile_id: str | None = None
    profile_url: str | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        if self.dry_run:
            return "DRY RUN — request built, not sent"
        if self.ok:
            return f"{self.status_code} Accepted — queued by Klaviyo"
        return f"{self.status_code or 'ERR'} — {self.error or 'failed'}"

    def curl(self) -> str:
        """The equivalent curl, for when someone asks 'what did you actually send?'"""
        parts = [f"curl -X POST '{self.request_url}' \\"]
        for key, value in self.request_headers.items():
            shown = "pk_***REDACTED***" if key.lower() == "authorization" else value
            if key.lower() == "authorization":
                shown = "Klaviyo-API-Key pk_***REDACTED***"
            parts.append(f"  -H '{key}: {shown}' \\")
        body = json.dumps(self.request_body, indent=2)
        parts.append(f"  -d '{body}'")
        return "\n".join(parts)


class KlaviyoError(RuntimeError):
    pass


class KlaviyoClient:
    def __init__(
        self,
        api_key: str | None = None,
        *,
        dry_run: bool | None = None,
        revision: str | None = None,
        base_url: str | None = None,
        timeout: float = 15.0,
    ) -> None:
        self.api_key = api_key if api_key is not None else settings.klaviyo_private_api_key
        self.revision = revision or settings.klaviyo_api_revision
        self.base_url = (base_url or settings.klaviyo_base_url).rstrip("/")
        self.timeout = timeout
        # Dry run if explicitly asked, or implicitly when there's no usable key
        # so the demo degrades to "show the request" instead of crashing.
        explicit = settings.klaviyo_dry_run if dry_run is None else dry_run
        self.dry_run = bool(explicit) or not self._key_usable()

    def _key_usable(self) -> bool:
        return bool(self.api_key) and not self.api_key.startswith("pk_replace")

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Klaviyo-API-Key {self.api_key}",
            "revision": self.revision,
            "Content-Type": "application/vnd.api+json",
            "Accept": "application/vnd.api+json",
        }

    # ---------------------------------------------------------------- write

    def create_event(self, payload: dict[str, Any]) -> DeliveryResult:
        """POST one event. Never raises -- failures come back as DeliveryResult.

        A live demo must not die on a network blip, so every error path is
        turned into a displayable result instead of an exception.
        """
        url = f"{self.base_url}/api/events"
        headers = self._headers()

        if self.dry_run:
            reason = (
                "KLAVIYO_DRY_RUN=true"
                if self._key_usable()
                else "no KLAVIYO_PRIVATE_API_KEY set"
            )
            return DeliveryResult(
                ok=True,
                status_code=None,
                request_url=url,
                request_headers=headers,
                request_body=payload,
                dry_run=True,
                warnings=[f"Not sent ({reason})."],
            )

        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(url, headers=headers, json=payload)
            latency = int(response.elapsed.total_seconds() * 1000)
        except httpx.HTTPError as exc:
            return DeliveryResult(
                ok=False,
                status_code=None,
                request_url=url,
                request_headers=headers,
                request_body=payload,
                error=f"{type(exc).__name__}: {exc}",
            )

        body: Any
        try:
            body = response.json() if response.content else None
        except ValueError:
            body = (response.text or "")[:500]

        ok = response.status_code in (200, 201, 202)
        return DeliveryResult(
            ok=ok,
            status_code=response.status_code,
            request_url=url,
            request_headers=headers,
            request_body=payload,
            response_body=body,
            error=None if ok else _describe_error(response.status_code, body),
            latency_ms=latency,
        )

    # ----------------------------------------------------------------- read

    def find_profile(self, *, email: str | None = None, phone: str | None = None) -> dict | None:
        """Look up a profile so the demo can deep-link straight to it.

        Returns None on any failure -- this is a convenience, never a blocker.
        """
        if self.dry_run or not (email or phone):
            return None
        if email:
            flt = f"equals(email,'{email}')"
        else:
            flt = f"equals(phone_number,'{phone}')"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.get(
                    f"{self.base_url}/api/profiles",
                    headers=self._headers(),
                    params={"filter": flt},
                )
            if response.status_code != 200:
                return None
            data = response.json().get("data") or []
            return data[0] if data else None
        except (httpx.HTTPError, ValueError, KeyError, IndexError):
            return None

    def profile_events(self, profile_id: str, limit: int = 50) -> list[dict]:
        """Recent events for a profile, newest first. Empty list on failure."""
        if self.dry_run:
            return []
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.get(
                    f"{self.base_url}/api/events",
                    headers=self._headers(),
                    params={
                        "filter": f"equals(profile_id,'{profile_id}')",
                        "include": "metric",
                        "sort": "-datetime",
                        "page[size]": min(limit, 200),
                    },
                )
            if response.status_code != 200:
                return []
            return response.json().get("data") or []
        except (httpx.HTTPError, ValueError):
            return []

    def verify_credentials(self) -> tuple[bool, str]:
        """Cheap preflight so the demo fails loudly now, not on stage."""
        if not self._key_usable():
            return False, "No KLAVIYO_PRIVATE_API_KEY set (running in dry-run mode)."
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.get(
                    f"{self.base_url}/api/accounts", headers=self._headers()
                )
        except httpx.HTTPError as exc:
            return False, f"Could not reach Klaviyo: {exc}"

        if response.status_code == 200:
            try:
                data = response.json().get("data") or []
                name = data[0]["attributes"].get("contact_information", {}).get(
                    "organization_name"
                )
                company_id = data[0].get("id")
                label = name or company_id or "account"
                return True, f"Connected to Klaviyo account: {label}"
            except (ValueError, KeyError, IndexError):
                return True, "Connected to Klaviyo."
        if response.status_code in (401, 403):
            return False, (
                f"Klaviyo rejected the key ({response.status_code}). Check the key and "
                "that it has events:write / profiles:write scopes."
            )
        return False, f"Unexpected status from Klaviyo: {response.status_code}"


def _describe_error(status: int, body: Any) -> str:
    detail = ""
    if isinstance(body, dict):
        errors = body.get("errors")
        if isinstance(errors, list) and errors:
            first = errors[0]
            detail = first.get("detail") or first.get("title") or ""
    hints = {
        400: "Malformed payload — check required fields.",
        401: "Bad or missing API key.",
        403: "Key lacks the events:write scope.",
        409: "Conflict — likely a duplicate unique_id.",
        429: "Rate limited (burst 350/s, steady 3500/m).",
    }
    hint = hints.get(status, "")
    return " ".join(part for part in (detail, hint) if part) or f"HTTP {status}"
