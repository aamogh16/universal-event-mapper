"""Flow loading and versioning.

Baseline flows live in composer/flows/*.json and are git-tracked and never
mutated. Approved patches are written to a separate runtime state file, so
`reset()` restores a pristine v1 and every demo starts identically.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from typing import Any

from universal_events.config import PROJECT_ROOT

FLOWS_DIR = Path(__file__).resolve().parent / "flows"
STATE_PATH = PROJECT_ROOT / "flow_state.json"


def _baseline() -> dict[str, dict]:
    flows: dict[str, dict] = {}
    for path in sorted(FLOWS_DIR.glob("*.json")):
        flow = json.loads(path.read_text(encoding="utf-8"))
        flows[flow["id"]] = flow
    return flows


def _load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {"flows": {}, "history": []}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {"flows": {}, "history": []}


def _save_state(state: dict[str, Any]) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


def reset() -> None:
    """Discard all applied versions; flows return to baseline v1."""
    if STATE_PATH.exists():
        STATE_PATH.unlink()


def all_flows() -> list[dict]:
    """Current live flows -- the applied version where one exists."""
    flows = _baseline()
    flows.update(_load_state().get("flows", {}))
    return list(flows.values())


def get_flow(flow_id: str) -> dict | None:
    flows = _baseline()
    flows.update(_load_state().get("flows", {}))
    return flows.get(flow_id)


def flows_for(vertical: str | None = None, trigger_metric: str | None = None) -> list[dict]:
    """Flows matching a vertical and/or trigger metric."""
    result = []
    for flow in all_flows():
        if vertical and flow.get("vertical") != vertical:
            continue
        if trigger_metric and (flow.get("trigger") or {}).get("metric") != trigger_metric:
            continue
        result.append(flow)
    return result


def pick_flow_for_signal(signal: Any) -> dict | None:
    """Best flow for a signal: exact trigger match first, then same vertical.

    Returning None is meaningful -- it means no flow covers this signal, which
    is itself a finding worth surfacing rather than an error.
    """
    if signal.trigger_metric:
        exact = flows_for(vertical=signal.vertical, trigger_metric=signal.trigger_metric)
        if exact:
            return exact[0]

    by_vertical = flows_for(vertical=signal.vertical)
    if not by_vertical:
        return None

    # Prefer a flow whose purpose matches the signal's shape.
    wants = "lapse" if signal.kind.startswith("lapsed") else "no-show"
    for flow in by_vertical:
        name = flow.get("name", "").lower()
        if wants == "lapse" and any(w in name for w in ("lapse", "win-back", "recall")):
            return flow
        if wants == "no-show" and any(w in name for w in ("no-show", "win-back")):
            return flow
    return by_vertical[0]


def find_covering_flow(signal: Any) -> dict | None:
    """The flow RESPONSIBLE for this signal, or None if nothing is.

    Strict on purpose. "Same vertical" is not the same as "handles this".
    A gym's no-show win-back does not cover a member who simply stopped
    booking -- that member never no-showed, they just went quiet, and no
    event fires for an absence. Returning None is the interesting answer:
    it means the business has no automation for this at all, which is a
    stronger thing to surface than a delay that is 48 hours too long.
    """
    for flow in all_flows():
        if signal.kind in (flow.get("handles_signals") or []):
            return flow
    if signal.trigger_metric:
        exact = flows_for(vertical=signal.vertical, trigger_metric=signal.trigger_metric)
        if exact:
            return exact[0]
    return None


def add_flow(flow: dict, *, proposal_id: str | None = None) -> dict:
    """Persist a newly drafted flow as a draft, mirroring Klaviyo's own
    behaviour: flows created via the API land in Draft, not live."""
    state = _load_state()
    created = dict(flow)
    created.setdefault("id", f"flow_drafted_{uuid4().hex[:8]}")
    created["version"] = 1
    created["status"] = "draft"
    state.setdefault("flows", {})[created["id"]] = created
    state.setdefault("history", []).append(
        {
            "flow_id": created["id"],
            "version": 1,
            "note": f"created from proposal: {created.get('name')}",
            "proposal_id": proposal_id,
            "at": datetime.now(timezone.utc).isoformat(),
        }
    )
    _save_state(state)
    return created


def commit_version(
    flow_id: str, new_flow: dict, *, note: str = "", proposal_id: str | None = None
) -> dict:
    """Persist an approved patch as the next version of the flow."""
    state = _load_state()
    current = get_flow(flow_id) or {}
    updated = dict(new_flow)
    updated["version"] = int(current.get("version", 1)) + 1
    updated["id"] = flow_id
    state.setdefault("flows", {})[flow_id] = updated
    state.setdefault("history", []).append(
        {
            "flow_id": flow_id,
            "version": updated["version"],
            "note": note,
            "proposal_id": proposal_id,
            "at": datetime.now(timezone.utc).isoformat(),
        }
    )
    _save_state(state)
    return updated


def history(flow_id: str | None = None) -> list[dict]:
    entries = _load_state().get("history", [])
    if flow_id:
        return [e for e in entries if e.get("flow_id") == flow_id]
    return entries
