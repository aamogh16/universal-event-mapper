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
import time
from datetime import datetime, timedelta, timezone
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

    def find_profile(
        self,
        *,
        email: str | None = None,
        phone: str | None = None,
        attempts: int = 1,
        delay: float = 0.8,
    ) -> dict | None:
        """Look up a profile so the demo can deep-link straight to it.

        Returns None on any failure -- this is a convenience, never a blocker.

        `attempts` > 1 retries with a short delay. Create Event returns 202
        (queued, not stored), so a brand-new profile is briefly unfindable; on
        a fresh account the first send for a person needs a beat before the
        lookup succeeds.
        """
        if self.dry_run or not (email or phone):
            return None
        flt = (
            f"equals(email,'{email}')" if email else f"equals(phone_number,'{phone}')"
        )
        for attempt in range(max(1, attempts)):
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    response = client.get(
                        f"{self.base_url}/api/profiles",
                        headers=self._headers(),
                        params={"filter": flt},
                    )
                if response.status_code == 200:
                    data = response.json().get("data") or []
                    if data:
                        return data[0]
            except (httpx.HTTPError, ValueError, KeyError, IndexError):
                return None
            if attempt < attempts - 1:
                time.sleep(delay)
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


# ---------------------------------------------------------------------------
# Real campaign creation.
#
# Deliberately stops one step short of sending. `POST /api/campaigns/{id}/
# send-jobs` is the only endpoint that transmits email, and on a free plan
# email #501 silently auto-upgrades the account to paid with no grace period.
# It is not implemented here, and it should not be added. Klaviyo creates
# campaigns in Draft by default, which is exactly the human-in-the-loop
# behaviour we want: the agent drafts, a person presses send.
#
# Three shapes below contradict Klaviyo's published docs, all verified by hand
# against a live account:
#   editor_type   docs say "html"; the API requires "CODE"
#   send_strategy docs nest datetime under static_options; it is top-level
#   assign template  docs give /api/campaign-messages/{id}/assign-template;
#                    the real path is /api/campaign-message-assign-template
# ---------------------------------------------------------------------------

CAMPAIGN_AUDIENCE_CAP = 50


@dataclass
class CampaignResult:
    ok: bool
    campaign_id: str | None = None
    campaign_url: str | None = None
    list_id: str | None = None
    template_id: str | None = None
    profiles_added: int = 0
    error: str | None = None
    warnings: list[str] = field(default_factory=list)


def _wrap_html(body: str, subject: str) -> str:
    """Minimal HTML for a plain-text body. Klaviyo requires HTML on templates."""
    import html as _html

    paragraphs = "\n".join(
        f'      <p style="margin:0 0 16px">{_html.escape(line)}</p>'
        for line in body.split("\n")
        if line.strip()
    )
    return (
        '<!doctype html><html><body style="margin:0;padding:0;'
        'background:#f6f7f9">\n'
        '  <table width="100%" cellpadding="0" cellspacing="0"><tr><td align="center">\n'
        '    <table width="600" cellpadding="24" cellspacing="0" '
        'style="background:#fff;font:16px/1.6 -apple-system,Helvetica,Arial,sans-serif;'
        'color:#1a1a1a">\n      <tr><td>\n'
        f"{paragraphs}\n"
        "      </td></tr>\n    </table>\n  </td></tr></table>\n</body></html>"
    )


class CampaignBuilder:
    """Creates a real Klaviyo campaign in Draft. Never sends."""

    def __init__(self, client: KlaviyoClient) -> None:
        self.c = client

    def _post(self, path: str, body: dict) -> tuple[int, Any]:
        try:
            with httpx.Client(timeout=self.c.timeout) as http:
                r = http.post(
                    f"{self.c.base_url}{path}", headers=self.c._headers(), json=body
                )
            payload = r.json() if r.content else None
        except (httpx.HTTPError, ValueError) as exc:
            return 0, {"errors": [{"detail": str(exc)}]}
        return r.status_code, payload

    def _detail(self, payload: Any) -> str:
        if isinstance(payload, dict):
            for e in payload.get("errors") or []:
                if e.get("detail"):
                    return str(e["detail"])
        return "unknown error"

    # ------------------------------------------------------------- profiles

    def upsert_profile(self, email: str, first: str | None, last: str | None) -> str | None:
        """Create a profile, or recover the id if it already exists.

        A duplicate returns 409 with `duplicate_profile_id` in the error meta,
        so no second lookup is needed.
        """
        attrs: dict[str, Any] = {"email": email}
        if first:
            attrs["first_name"] = first
        if last:
            attrs["last_name"] = last

        status, payload = self._post(
            "/api/profiles", {"data": {"type": "profile", "attributes": attrs}}
        )
        if status in (200, 201) and isinstance(payload, dict):
            return payload["data"]["id"]
        if status == 409 and isinstance(payload, dict):
            for e in payload.get("errors") or []:
                dup = (e.get("meta") or {}).get("duplicate_profile_id")
                if dup:
                    return str(dup)
        return None

    # ----------------------------------------------------------------- list

    def find_or_create_list(self, name: str) -> tuple[str | None, bool]:
        """Reuse a list by name. Returns (id, created). Capped at 150/day."""
        try:
            with httpx.Client(timeout=self.c.timeout) as http:
                r = http.get(
                    f"{self.c.base_url}/api/lists",
                    headers=self.c._headers(),
                    params={"filter": f"equals(name,'{name}')"},
                )
            if r.status_code == 200:
                data = r.json().get("data") or []
                if data:
                    return data[0]["id"], False
        except (httpx.HTTPError, ValueError):
            pass

        status, payload = self._post(
            "/api/lists", {"data": {"type": "list", "attributes": {"name": name}}}
        )
        if status in (200, 201) and isinstance(payload, dict):
            return payload["data"]["id"], True
        return None, False

    def add_to_list(self, list_id: str, profile_ids: list[str]) -> bool:
        status, _ = self._post(
            f"/api/lists/{list_id}/relationships/profiles",
            {"data": [{"type": "profile", "id": pid} for pid in profile_ids]},
        )
        return status in (200, 202, 204)

    # ------------------------------------------------------------- campaign

    def create(
        self,
        *,
        name: str,
        subject: str,
        body: str,
        audience: list[dict[str, Any]],
        list_name: str,
        from_email: str,
        from_label: str,
        preview_text: str = "",
    ) -> CampaignResult:
        """Build a Draft campaign end to end. Never raises."""
        if self.c.dry_run:
            return CampaignResult(ok=False, error="Klaviyo client is in dry-run mode.")
        if not audience:
            return CampaignResult(ok=False, error="No audience to target.")

        warnings: list[str] = []
        capped = audience[:CAMPAIGN_AUDIENCE_CAP]
        if len(audience) > CAMPAIGN_AUDIENCE_CAP:
            warnings.append(
                f"Audience capped at {CAMPAIGN_AUDIENCE_CAP} of {len(audience)} "
                "to stay well inside the 250-profile free-plan ceiling."
            )

        # 1. profiles must exist in Klaviyo before they can join a list
        ids: list[str] = []
        for person in capped:
            pid = self.upsert_profile(
                person.get("email", ""), person.get("first_name"), person.get("last_name")
            )
            if pid:
                ids.append(pid)
        if not ids:
            return CampaignResult(ok=False, error="Could not resolve any profiles.")
        if len(ids) < len(capped):
            warnings.append(f"{len(capped) - len(ids)} profile(s) could not be resolved.")

        # 2. a list, because audiences take list/segment ids, not emails
        list_id, created = self.find_or_create_list(list_name)
        if not list_id:
            return CampaignResult(ok=False, error="Could not create the audience list.")
        if not created:
            warnings.append("Reused the existing list of the same name.")
        if not self.add_to_list(list_id, ids):
            warnings.append("Some profiles may not have been added to the list.")

        # 3. a template -- editor_type must be CODE, not the documented "html"
        status, payload = self._post(
            "/api/templates",
            {"data": {"type": "template", "attributes": {
                "name": f"{name} — drafted by agent",
                "editor_type": "CODE",
                "html": _wrap_html(body, subject),
                "text": body,
            }}},
        )
        if status not in (200, 201):
            return CampaignResult(ok=False, list_id=list_id,
                                  error=f"Template failed: {self._detail(payload)}")
        template_id = payload["data"]["id"]

        # 4. the campaign. send_strategy.datetime is top-level, not nested in
        #    static_options. The datetime is required by the schema but is
        #    inert: a campaign is only scheduled when send-jobs is called, and
        #    it never is. Klaviyo caps how far ahead you may schedule, so this
        #    is a year out rather than something absurd -- 2099 is rejected
        #    with "Send time is past the allowed scheduling date".
        placeholder_send = (
            datetime.now(timezone.utc) + timedelta(days=365)
        ).replace(microsecond=0).isoformat()
        status, payload = self._post(
            "/api/campaigns",
            {"data": {"type": "campaign", "attributes": {
                "name": name,
                "audiences": {"included": [list_id]},
                "send_strategy": {"method": "static",
                                  "datetime": placeholder_send},
                "campaign-messages": {"data": [{
                    "type": "campaign-message",
                    "attributes": {"definition": {
                        "channel": "email",
                        "label": name,
                        "content": {"subject": subject,
                                    "preview_text": preview_text or subject,
                                    "from_email": from_email,
                                    "from_label": from_label},
                    }},
                }]},
            }}},
        )
        if status not in (200, 201):
            return CampaignResult(ok=False, list_id=list_id, template_id=template_id,
                                  error=f"Campaign failed: {self._detail(payload)}")

        data = payload["data"]
        campaign_id = data["id"]
        if str(data["attributes"].get("status", "")).lower() != "draft":
            warnings.append(
                f"Campaign status is {data['attributes'].get('status')!r}, expected Draft."
            )

        messages = (data.get("relationships", {}).get("campaign-messages", {})
                    .get("data") or [])
        message_id = messages[0]["id"] if messages else None

        # 5. attach the template. Real path differs from the documented one.
        if message_id:
            status, payload = self._post(
                "/api/campaign-message-assign-template",
                {"data": {"type": "campaign-message", "id": message_id,
                          "relationships": {"template": {
                              "data": {"type": "template", "id": template_id}}}}},
            )
            if status not in (200, 201):
                warnings.append(f"Template not attached: {self._detail(payload)}")

        # 6. There is no step 6. send-jobs is intentionally never called.
        return CampaignResult(
            ok=True,
            campaign_id=campaign_id,
            campaign_url=f"https://www.klaviyo.com/campaign/{campaign_id}/web-view",
            list_id=list_id,
            template_id=template_id,
            profiles_added=len(ids),
            warnings=warnings,
        )
