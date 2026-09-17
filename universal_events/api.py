"""FastAPI app + minimal dashboard.  [STUB -- routes not implemented]

The app boots and serves /health and the static page so the shell is testable.
Every functional route returns 501 until pipeline.py lands.

Run:  uvicorn universal_events.api:app --reload
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from .config import PACKAGE_ROOT, settings
from .klaviyo import KlaviyoClient
from .sources import SOURCES, UNKNOWN_PAYLOADS

app = FastAPI(
    title="Universal Event Mapper",
    description=(
        "Absorb events from a tool nobody has built an integration for. "
        "Klaviyo has hundreds of hand-built connectors; this is a generic path "
        "that needs no per-tool engineering."
    ),
    version="0.1.0",
)

WEB_DIR = PACKAGE_ROOT / "web"


class TriggerRequest(BaseModel):
    source_key: str
    sample_key: str
    mode: str = "auto"
    send: bool = True


class RawPayloadRequest(BaseModel):
    payload: dict[str, Any]
    mode: str = "auto"
    send: bool = True


@app.get("/health")
def health() -> dict[str, Any]:
    """Preflight. Surfaces whether the demo is wired up before you present."""
    klaviyo_ok, klaviyo_msg = KlaviyoClient().verify_credentials()
    return {
        "status": "ok",
        "klaviyo": {"configured": settings.klaviyo_configured, "reachable": klaviyo_ok,
                    "detail": klaviyo_msg, "revision": settings.klaviyo_api_revision},
        "gemini": {"configured": settings.gemini_configured,
                   "mapping_model": settings.gemini_mapping_model,
                   "agent_model": settings.gemini_agent_model},
    }


@app.get("/sources")
def list_sources_endpoint() -> dict[str, Any]:
    """The demo menu: known sources plus the unknown-vertical payloads."""
    return {
        "sources": [
            {
                "key": s.key,
                "display_name": s.display_name,
                "tool": s.tool,
                "business_type": s.business_type,
                "has_config": s.config_name is not None,
                "samples": [{"key": x.key, "label": x.label} for x in s.samples],
            }
            for s in SOURCES.values()
        ],
        "unknown_payloads": [
            {"key": k, "description": v["_description"]} for k, v in UNKNOWN_PAYLOADS.items()
        ],
    }


@app.post("/trigger")
def trigger(_: TriggerRequest) -> JSONResponse:
    """Fire one mock source event through the mapper into Klaviyo. TODO."""
    raise HTTPException(status_code=501, detail="Not implemented: needs pipeline.ingest()")


@app.post("/ingest")
def ingest_raw(_: RawPayloadRequest) -> JSONResponse:
    """Accept an arbitrary payload -- the 'paste something new' demo. TODO."""
    raise HTTPException(status_code=501, detail="Not implemented: needs pipeline.ingest()")


@app.get("/notifications")
def notifications() -> JSONResponse:
    """Part 2: proactive business notifications. TODO."""
    raise HTTPException(status_code=501, detail="Not implemented: needs agent.sweep()")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")
