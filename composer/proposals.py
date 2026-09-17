"""Proposal inbox and lifecycle.

A proposal is the unit the human acts on: what was noticed, what was audited,
the concrete diff, and approve/reject. Rejections carry feedback and produce a
new revision rather than discarding the work -- the revision history is kept
so the demo can show the agent changing its mind correctly.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

from universal_events.config import PROJECT_ROOT

from . import auditor, context, feedback, flow_store
from .auditor import AuditOutput
from .patch import DiffLine, EditOp, apply_patch, diff_lines, render_diff

STORE_PATH = PROJECT_ROOT / "proposals.json"

Status = Literal["pending", "approved", "rejected", "revised"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Revision(BaseModel):
    n: int
    headline: str
    observation: str
    audit_summary: str
    findings: list[str] = Field(default_factory=list)
    predicted_impact: str = ""
    ops: list[EditOp] = Field(default_factory=list)
    drafted_flow: dict[str, Any] | None = None
    diff_text: str = ""
    diff: list[DiffLine] = Field(default_factory=list)
    confidence: float = 0.0
    source: str = "rules"
    model_used: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    latency_ms: int | None = None
    fallback_reason: str | None = None
    created_at: str = Field(default_factory=_now)
    # The feedback that CAUSED this revision (empty on revision 1).
    prompted_by_feedback: str = ""

    @property
    def cost_label(self) -> str:
        if self.source == "rules":
            return "$0.00 (deterministic)"
        return f"${self.cost_usd:.6f}" if self.cost_usd is not None else "n/a"


class Proposal(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    # "patch"  -> edit an existing flow
    # "create" -> nothing covers this signal, draft a new automation
    kind: str = "patch"
    flow_id: str
    flow_name: str
    vertical: str | None = None
    origin: str = "sweep"
    signal_kind: str = ""
    signal_title: str = ""
    signal_scope: str = ""
    signal_detail: str = ""
    # Persisted so a revision reasons from the SAME evidence as revision 1.
    # Re-detecting later could legitimately yield different numbers, which
    # would make the revision look like it changed its mind about the facts.
    signal_evidence: dict[str, Any] = Field(default_factory=dict)
    signal_profile_key: str | None = None
    signal_profile_name: str | None = None
    signal_trigger_metric: str | None = None
    context_lines: list[str] = Field(default_factory=list)
    corrections_applied: list[str] = Field(default_factory=list)
    revisions: list[Revision] = Field(default_factory=list)
    status: Status = "pending"
    created_at: str = Field(default_factory=_now)
    resolved_at: str | None = None
    applied_version: int | None = None
    rejection_feedback: list[str] = Field(default_factory=list)

    @property
    def current(self) -> Revision:
        return self.revisions[-1]

    @property
    def revision_count(self) -> int:
        return len(self.revisions)


# ------------------------------------------------------------------ storage


def _load() -> list[Proposal]:
    if not STORE_PATH.exists():
        return []
    try:
        return [Proposal(**item) for item in json.loads(STORE_PATH.read_text("utf-8"))]
    except (ValueError, OSError, TypeError):
        return []


def _save(items: list[Proposal]) -> None:
    STORE_PATH.write_text(
        json.dumps([p.model_dump() for p in items], indent=2, default=str), "utf-8"
    )


def reset() -> None:
    if STORE_PATH.exists():
        STORE_PATH.unlink()


def all_proposals(status: Status | None = None) -> list[Proposal]:
    items = _load()
    if status:
        items = [p for p in items if p.status == status]
    return sorted(items, key=lambda p: p.created_at, reverse=True)


def get(proposal_id: str) -> Proposal | None:
    for item in _load():
        if item.id == proposal_id:
            return item
    return None


def _upsert(proposal: Proposal) -> None:
    items = _load()
    for index, existing in enumerate(items):
        if existing.id == proposal.id:
            items[index] = proposal
            _save(items)
            return
    items.append(proposal)
    _save(items)


# ------------------------------------------------------------------ creation


EMPTY_FLOW: dict[str, Any] = {"name": "(no automation exists)", "version": 0,
                              "status": "none", "trigger": {}, "steps": []}


def _revision_from_audit(
    n: int, audit: AuditOutput, before: dict | None, prompted_by: str = ""
) -> Revision:
    if audit.drafted_flow is not None:
        # A created flow diffs against nothing, so every line reads as an
        # addition -- which is exactly what "I drafted this for you" looks like.
        before = EMPTY_FLOW
        after = audit.drafted_flow
    else:
        before = before or EMPTY_FLOW
        result = apply_patch(before, audit.ops)
        after = dict(result.flow)
        after["version"] = int(before.get("version", 1)) + 1
    return Revision(
        n=n,
        headline=audit.headline,
        observation=audit.observation,
        audit_summary=audit.audit_summary,
        findings=audit.findings,
        predicted_impact=audit.predicted_impact,
        ops=audit.ops,
        drafted_flow=audit.drafted_flow,
        diff_text=render_diff(before, after),
        diff=diff_lines(before, after),
        confidence=audit.confidence,
        source=audit.source,
        model_used=audit.model_used,
        input_tokens=audit.input_tokens,
        output_tokens=audit.output_tokens,
        cost_usd=audit.cost_usd,
        latency_ms=audit.latency_ms,
        fallback_reason=audit.fallback_reason,
        prompted_by_feedback=prompted_by,
    )


def create_from_signal(signal: Any, *, origin: str | None = None) -> Proposal | None:
    """Audit the flow for a signal and open a proposal. None if nothing to say."""
    # Strict coverage: is any flow actually RESPONSIBLE for this signal?
    # None is the interesting answer -- it means draft something new rather
    # than bend an unrelated flow to cover it.
    flow = flow_store.find_covering_flow(signal)
    siblings = flow_store.flows_for(vertical=signal.vertical)

    # One open proposal per flow. Several signals can point at the same flow
    # (two lapsed donors, one donor-lapse flow), and stacking near-identical
    # proposals is noise the reviewer has to dismiss.
    if flow is not None:
        for existing in _load():
            if existing.flow_id == flow["id"] and existing.status in ("pending", "revised"):
                return None
    else:
        for existing in _load():
            if (
                existing.kind == "create"
                and existing.signal_kind == signal.kind
                and existing.status in ("pending", "revised")
            ):
                return None

    bundle = context.build(signal, flow, sibling_flows=siblings)
    audit = auditor.propose(bundle) if flow else auditor.propose_new_flow(bundle)
    if not audit.ops and not audit.drafted_flow:
        return None

    proposal = Proposal(
        kind=audit.kind,
        flow_id=flow["id"] if flow else "",
        flow_name=flow["name"] if flow else (audit.drafted_flow or {}).get("name", "new flow"),
        vertical=(flow or {}).get("vertical") or signal.vertical,
        origin=origin or signal.origin,
        signal_kind=signal.kind,
        signal_title=signal.title,
        signal_scope=signal.scope,
        signal_detail=signal.detail,
        signal_evidence=dict(signal.evidence or {}),
        signal_profile_key=signal.profile_key,
        signal_profile_name=signal.profile_name,
        signal_trigger_metric=signal.trigger_metric,
        context_lines=bundle.summary_lines,
        corrections_applied=[c.id for c in bundle.corrections],
    )
    proposal.revisions.append(_revision_from_audit(1, audit, flow))
    _upsert(proposal)
    if bundle.corrections:
        feedback.mark_applied([c.id for c in bundle.corrections])
    return proposal


# ----------------------------------------------------------------- lifecycle


def approve(proposal_id: str) -> tuple[Proposal | None, dict | None]:
    """Apply the current revision's patch and version the flow."""
    proposal = get(proposal_id)
    if proposal is None or proposal.status in ("approved",):
        return proposal, None

    if proposal.kind == "create":
        drafted = proposal.current.drafted_flow
        if not drafted:
            return proposal, None
        created = flow_store.add_flow(drafted, proposal_id=proposal.id)
        proposal.status = "approved"
        proposal.resolved_at = _now()
        proposal.applied_version = created["version"]
        proposal.flow_id = created["id"]
        _upsert(proposal)
        return proposal, created

    flow = flow_store.get_flow(proposal.flow_id)
    if flow is None:
        return proposal, None

    result = apply_patch(flow, proposal.current.ops)
    if not result.applied:
        return proposal, None

    updated = flow_store.commit_version(
        proposal.flow_id,
        result.flow,
        note=proposal.current.headline,
        proposal_id=proposal.id,
    )
    proposal.status = "approved"
    proposal.resolved_at = _now()
    proposal.applied_version = updated["version"]
    _upsert(proposal)
    return proposal, updated


def reject(proposal_id: str, user_feedback: str) -> Proposal | None:
    """Reject with feedback, then revise rather than discard.

    Also persists the lesson so later proposals honour it without being told
    again -- that is the point of the whole loop.
    """
    proposal = get(proposal_id)
    if proposal is None:
        return None

    flow = flow_store.get_flow(proposal.flow_id)
    if flow is None:
        return proposal

    proposal.rejection_feedback.append(user_feedback)

    # Rebuild context so the revision sees any corrections learned since.
    signal = _rehydrate_signal(proposal)
    bundle = context.build(signal, flow)
    audit = auditor.revise(bundle, proposal.current.ops, user_feedback)

    for rule in audit.learned_rules or ([audit.learned_rule] if audit.learned_rule else []):
        feedback.add(
            rule,
            raw_feedback=user_feedback,
            vertical=None if audit.learned_rule_is_global else proposal.vertical,
            source_proposal_id=proposal.id,
        )

    proposal.revisions.append(
        _revision_from_audit(
            proposal.revision_count + 1, audit, flow, prompted_by=user_feedback
        )
    )
    proposal.status = "revised"
    _upsert(proposal)
    return proposal


class _SignalShim:
    """Stand-in that replays the original signal exactly as first detected."""

    def __init__(self, proposal: Proposal):
        self.kind = proposal.signal_kind
        self.scope = proposal.signal_scope
        self.title = proposal.signal_title
        self.detail = proposal.signal_detail or proposal.current.observation
        self.evidence = dict(proposal.signal_evidence)
        self.severity = "medium"
        self.vertical = proposal.vertical
        self.profile_key = proposal.signal_profile_key
        self.profile_name = proposal.signal_profile_name
        self.trigger_metric = proposal.signal_trigger_metric
        self.origin = proposal.origin


def _rehydrate_signal(proposal: Proposal) -> Any:
    return _SignalShim(proposal)


def summary() -> dict[str, int]:
    items = _load()
    return {
        "total": len(items),
        "pending": sum(1 for p in items if p.status == "pending"),
        "approved": sum(1 for p in items if p.status == "approved"),
        "revised": sum(1 for p in items if p.status == "revised"),
    }
