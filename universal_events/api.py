"""FastAPI backend for the demo dashboard.

Run:  ./serve      (or: uvicorn universal_events.api:app --reload)

Every endpoint returns plain JSON the single-page frontend renders. No build
step anywhere -- for a recorded demo, a missing toolchain is a bigger risk
than a nicer framework is a benefit.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import seed as seeder
from . import sources, store
from .config import PACKAGE_ROOT, settings
from .klaviyo import KlaviyoClient
from .mapping import pipeline

app = FastAPI(title="Composer proactive-agent demo", version="0.1.0")
WEB_DIR = PACKAGE_ROOT / "web"

_sweeper: Any = None


@app.on_event("startup")
def _start_sweeper() -> None:
    """Run the recurring job for real, on a background thread.

    Production would be daily; SWEEP_INTERVAL_SECONDS is 60 here so the
    behaviour is observable. Proposals dedupe, so a quiet sweep costs nothing
    and creates nothing -- but the run log fills up, which is what shows the
    job is genuinely recurring rather than button-driven.
    """
    global _sweeper
    if not settings.auto_sweep:
        return
    from composer.runners import Sweeper

    _sweeper = Sweeper(interval_seconds=settings.sweep_interval_seconds)
    _sweeper.start()


@app.on_event("shutdown")
def _stop_sweeper() -> None:
    if _sweeper is not None:
        _sweeper.stop()


@app.get("/api/sweeper")
def sweeper_state() -> dict[str, Any]:
    if _sweeper is None:
        return {"running": False, "interval": settings.sweep_interval_seconds}
    return {"running": _sweeper.running, "interval": _sweeper.interval,
            "next_in": _sweeper.seconds_until_next}


def _c():
    from composer import (audience, feedback, flow_store, patch, proposals,
                          runners, signals)
    return audience, feedback, flow_store, patch, proposals, runners, signals


# ------------------------------------------------------------------ requests


class TriggerReq(BaseModel):
    source: str
    sample: str


class MapReq(BaseModel):
    key: str | None = None
    payload: dict[str, Any] | None = None
    send: bool = True


class RejectReq(BaseModel):
    feedback: str


class ChatReq(BaseModel):
    prompt: str


class ArmReq(BaseModel):
    """Schedule an event to land after a delay.

    The point of a business notification is that it interrupts you. A button
    you press and then watch is not an interruption -- so this fires the event
    N seconds later, letting the notification arrive unprompted while the
    presenter is mid-sentence about something else.
    """

    source: str = "fitness"
    sample: str = "class_no_show"
    delay_seconds: float = 20.0


# -------------------------------------------------------------------- status


@app.get("/api/status")
def status() -> dict[str, Any]:
    *_, proposals, runners, _ = _c()
    _, _, flow_store, _, proposals, runners, _ = _c()
    spent = 0.0
    for p in proposals.all_proposals():
        for r in p.revisions:
            spent += r.cost_usd or 0.0
    klaviyo_ok, klaviyo_msg = KlaviyoClient().verify_credentials()
    return {
        "klaviyo": {"ok": klaviyo_ok, "detail": klaviyo_msg,
                    "revision": settings.klaviyo_api_revision},
        "provider": settings.active_provider,
        "model": settings.openai_audit_model,
        "events": store.count(),
        "flows": len(flow_store.all_flows()),
        "proposals": proposals.summary(),
        "spent_usd": round(spent, 6),
        "sweep_interval": settings.sweep_interval_seconds,
    }


@app.post("/api/reset")
def reset() -> dict[str, Any]:
    _, feedback, flow_store, _, proposals, runners, _ = _c()
    flow_store.reset(); proposals.reset(); feedback.reset(); runners.reset_log()
    return {"seeded": seeder.seed_demo_history()}


# ------------------------------------------------------------------- part one


@app.get("/api/sources")
def list_sources() -> dict[str, Any]:
    return {
        "sources": [
            {"key": s.key, "display_name": s.display_name, "tool": s.tool,
             "business_type": s.business_type,
             "klaviyo_connector": s.klaviyo_connector,
             "samples": [{"key": x.key, "label": x.label} for x in s.samples]}
            for s in sources.list_sources()
        ],
        "unknown": [
            {"key": k, "description": v["_description"], "payload": v["payload"],
             "tool": v.get("_tool"), "business": v.get("_business")}
            for k, v in sources.all_unintegrated().items()
        ],
    }


@app.post("/api/map")
def map_payload(req: MapReq) -> dict[str, Any]:
    if req.key:
        spec = sources.all_unintegrated().get(req.key)
        if not spec:
            raise HTTPException(404, f"no payload {req.key!r}")
        payload = spec["payload"]
    elif req.payload:
        payload = req.payload
    else:
        raise HTTPException(400, "provide key or payload")

    res = pipeline.ingest(payload, send=req.send, persist=False)
    m = res.mapping
    d = res.delivery
    return {
        "payload": payload,
        "mapping": {
            "strategy": m.strategy, "confidence": m.confidence,
            "chain": res.fallback_chain, "detected_config": res.detected_config,
            "metric_name": m.metric_name, "reasoning": m.reasoning,
            "identity": m.identity.model_dump(), "properties": m.properties,
            "value": m.value, "value_currency": m.value_currency,
            "traces": [t.model_dump() for t in m.field_traces],
            "warnings": m.warnings,
            "model_used": m.model_used,
            "tokens_used": m.tokens_used,
            "latency_ms": m.latency_ms,
        },
        "klaviyo": None if not d else {
            "ok": d.ok, "summary": d.summary, "status_code": d.status_code,
            "latency_ms": d.latency_ms, "profile_url": d.profile_url,
            "request_body": d.request_body,
        },
    }


# -------------------------------------------------------------------- agent


def _proposal_json(p: Any, full: bool = False) -> dict[str, Any]:
    r = p.current
    out = {
        "id": p.id, "kind": p.kind, "status": p.status, "origin": p.origin,
        "flow_id": p.flow_id, "flow_name": p.flow_name, "vertical": p.vertical,
        "signal_title": p.signal_title, "signal_kind": p.signal_kind,
        "signal_scope": p.signal_scope, "signal_severity": p.signal_severity,
        "revision": r.n,
        "revision_count": p.revision_count, "headline": r.headline,
        "created_at": p.created_at, "applied_version": p.applied_version,
        "sent_to": p.sent_to, "corrections_applied": len(p.corrections_applied),
        "supersedes": p.supersedes, "superseded_by": p.superseded_by,
        "source": r.source, "model": r.model_used, "cost_usd": r.cost_usd,
        "latency_ms": r.latency_ms, "confidence": r.confidence,
    }
    if not full:
        return out
    out.update({
        "context_lines": p.context_lines,
        "rejection_feedback": p.rejection_feedback,
        "revisions": [
            {"n": x.n, "headline": x.headline, "audit_summary": x.audit_summary,
             "observation": x.observation, "findings": x.findings,
             "predicted_impact": x.predicted_impact,
             "ops": [o.describe() for o in x.ops],
             "diff": [d.model_dump() for d in x.diff],
             "drafted_campaign": x.drafted_campaign,
             "prompted_by_feedback": x.prompted_by_feedback,
             "source": x.source, "model": x.model_used, "cost_usd": x.cost_usd,
             "latency_ms": x.latency_ms, "confidence": x.confidence,
             "fallback_reason": x.fallback_reason}
            for x in p.revisions
        ],
    })
    return out


@app.get("/api/signals")
def get_signals() -> dict[str, Any]:
    audience, _, _, _, _, _, signals = _c()
    out = []
    for s in signals.detect_aggregate():
        aud = audience.resolve(s)
        out.append({
            "kind": s.kind, "scope": s.scope, "title": s.title, "detail": s.detail,
            "severity": s.severity, "vertical": s.vertical, "origin": s.origin,
            "evidence": s.evidence,
            # Audience is attached here so the UI can put a counted number
            # next to whatever the chat path guessed.
            "audience_size": aud.size,
            "audience_description": aud.description,
            "audience_basis": aud.basis,
            "audience_sample": aud.sample_names,
        })
    return {"signals": out}


@app.get("/api/proposals")
def list_proposals() -> dict[str, Any]:
    *_, proposals, _, _ = _c()
    return {"proposals": [_proposal_json(p) for p in proposals.all_proposals()]}


@app.get("/api/proposals/{proposal_id}")
def get_proposal(proposal_id: str) -> dict[str, Any]:
    *_, proposals, _, _ = _c()
    p = proposals.get(proposal_id)
    if not p:
        raise HTTPException(404, "no such proposal")
    return _proposal_json(p, full=True)


@app.post("/api/proposals/{proposal_id}/approve")
def approve(proposal_id: str) -> dict[str, Any]:
    *_, proposals, _, _ = _c()
    p, result = proposals.approve(proposal_id)
    if not p:
        raise HTTPException(404, "no such proposal")
    return {"proposal": _proposal_json(p, full=True), "result": result}


@app.post("/api/proposals/{proposal_id}/reject")
def reject(proposal_id: str, req: RejectReq) -> dict[str, Any]:
    _, feedback, _, _, proposals, _, _ = _c()
    before = {c.id for c in feedback.all_corrections()}
    p = proposals.reject(proposal_id, req.feedback)
    if not p:
        raise HTTPException(404, "no such proposal")
    learned = [c.model_dump() for c in feedback.all_corrections() if c.id not in before]
    return {"proposal": _proposal_json(p, full=True), "learned": learned}


@app.post("/api/sweep")
def sweep(campaigns: bool = True) -> dict[str, Any]:
    _, _, _, _, proposals, runners, signals = _c()
    made = runners.sweep_once()
    if campaigns:
        for s in signals.detect_aggregate():
            if s.kind in ("lapsed_member", "lapsed_donor"):
                p = proposals.create_from_signal(s, want_campaign=True)
                if p:
                    made.append(p)
    return {"created": [_proposal_json(p) for p in made]}


@app.post("/api/trigger")
def trigger(req: TriggerReq) -> dict[str, Any]:
    _, _, _, _, _, runners, _ = _c()
    try:
        src = sources.get_source(req.source)
        payload = src.build(req.sample)
    except KeyError as exc:
        raise HTTPException(400, str(exc)) from exc

    res = pipeline.ingest(payload, source_key=req.source, send=True)
    m, d = res.mapping, res.delivery
    made = runners.on_event(m.identity.primary, m.metric_name, source_key=req.source)
    return {
        "tool": src.tool,
        "payload": payload,
        "mapping": {"metric_name": m.metric_name, "strategy": m.strategy,
                    "profile": m.identity.primary,
                    "detected_config": res.detected_config},
        "klaviyo": None if not d else {"ok": d.ok, "summary": d.summary,
                                       "profile_url": d.profile_url},
        "created": [_proposal_json(p) for p in made],
    }


@app.post("/api/chat")
def chat(req: ChatReq) -> dict[str, Any]:
    """The reactive path, for contrast: you ask, it builds."""
    from composer import chat as chat_mod

    return chat_mod.generate(req.prompt)


@app.get("/api/chat/suggestions")
def chat_suggestions() -> dict[str, Any]:
    from composer import chat as chat_mod

    return {"prompts": chat_mod.SUGGESTED_PROMPTS}


@app.post("/api/connect-tool")
def connect_tool_endpoint() -> dict[str, Any]:
    """Connect the door system: infer once, save a config, replay history."""
    from . import connect
    from composer import audience, signals

    def quiet() -> dict[str, Any]:
        sig = next((s for s in signals.detect_aggregate()
                    if s.kind == "lapsed_member"), None)
        if sig is None:
            return {"size": 0, "names": []}
        aud = audience.resolve(sig)
        return {"size": aud.size,
                "names": [p.display_name for p in aud.profiles]}

    before = quiet()
    res = connect.connect_tool(
        "Kisi", sources.GYM_TOOLS["door_access"]["payload"],
        sources.door_checkin_history(), send=False)
    after = quiet()
    removed = [n for n in before["names"] if n not in after["names"]]

    return {
        "tool": res.tool,
        "metric": res.metric,
        "inference_ms": res.inference_ms,
        "inferred_from_model": res.inferred_from_model,
        "config_name": res.config_name,
        "config_yaml": res.config_yaml,
        "events_ingested": res.events_ingested,
        "batch_ms": res.batch_ms,
        "per_event_after_setup": res.per_event_after_setup,
        "warnings": res.warnings,
        "audience_before": before["size"],
        "audience_after": after["size"],
        "removed": removed,
    }


@app.post("/api/demo/arm")
def arm(req: ArmReq) -> dict[str, Any]:
    """Fire an event after a delay, on a background thread."""

    def later() -> None:
        time.sleep(max(0.0, req.delay_seconds))
        try:
            trigger(TriggerReq(source=req.source, sample=req.sample))
        except Exception:
            # A demo aid must never take the server down with it.
            pass

    threading.Thread(target=later, daemon=True).start()
    return {"armed": True, "in_seconds": req.delay_seconds,
            "source": req.source, "sample": req.sample}


@app.get("/api/corrections")
def corrections() -> dict[str, Any]:
    _, feedback, *_ = _c()
    return {"corrections": [c.model_dump() for c in feedback.all_corrections()]}


@app.get("/api/flows")
def flows() -> dict[str, Any]:
    _, _, flow_store, patch, *_ = _c()
    return {
        "flows": [
            {"id": f["id"], "name": f["name"], "version": f["version"],
             "status": f["status"], "vertical": f.get("vertical"),
             "handles_signals": f.get("handles_signals") or [],
             "outline": patch.render_outline(f)}
            for f in flow_store.all_flows()
        ],
        "history": flow_store.history(),
    }


@app.get("/api/runs")
def runs() -> dict[str, Any]:
    _, _, _, _, _, runners, _ = _c()
    return {"runs": [
        {"origin": r.origin, "clock": r.clock, "at": r.at,
         "signals_found": r.signals_found, "proposals_created": r.proposals_created,
         "duration_ms": r.duration_ms, "detail": r.detail,
         "scope": "one profile" if r.origin == "trigger" else "all profiles + windows"}
        for r in runners.run_log(limit=40)
    ]}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")
