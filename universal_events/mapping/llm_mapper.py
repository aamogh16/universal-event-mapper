"""LLM-driven mapping via Gemini.  [STUB -- not implemented]

Verified working against the live API (Sept 2026):
    client = genai.Client(api_key=...)
    client.models.generate_content(model="gemini-3.8-flash", contents=...)
Note: `gemini-2.5-flash` returns 404 for new API keys. Use 3.5-flash-lite / 3.8-flash.

DESIGN DECISION TO PRESERVE:
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

from typing import Any

from pydantic import BaseModel, Field

from .base import MappingResult


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
    timestamp_path: str | None = None
    value_path: str | None = None
    currency: str | None = None
    property_paths: list[PropertyPath] = Field(default_factory=list)
    confidence: float = 0.0
    reasoning: str = ""


def build_prompt(payload: dict[str, Any]) -> str:
    """Render the inference prompt. TODO: implement."""
    raise NotImplementedError


def map_with_llm(payload: dict[str, Any], *, model: str | None = None) -> MappingResult:
    """Infer a mapping for an unfamiliar payload.

    TODO: implement.
      - genai.Client(api_key=settings.gemini_api_key)
      - generate_content with response_mime_type="application/json"
        and response_schema=InferredMapping
      - resolve the returned paths with paths.resolve()
      - return MappingResult(strategy="llm", model_used=..., tokens_used=...,
        latency_ms=...) so the UI can show model + cost
      - raise LLMMappingError on any failure; pipeline handles the fallback
    """
    raise NotImplementedError


def to_yaml_config(inferred: InferredMapping, source_name: str) -> str:
    """Serialise an inferred mapping into a mappings/*.yaml config.

    TODO: implement. This is what turns a one-off inference into a permanent,
    deterministic, zero-cost mapping.
    """
    raise NotImplementedError


class LLMMappingError(RuntimeError):
    pass
