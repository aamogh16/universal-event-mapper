"""Strategy selection and fallback.  [STUB -- not implemented]

Resolution order, designed so a live demo degrades instead of failing:

    mode="config"     -> named config only
    mode="llm"        -> Gemini, falls back to heuristic on any error
    mode="heuristic"  -> offline inference only
    mode="auto"       -> detect a config; if none, try LLM; if that fails,
                         heuristic. Always returns a MappingResult.

Nothing in here should be able to raise during a demo.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from ..klaviyo import DeliveryResult
from .base import MappingResult

Mode = Literal["auto", "config", "llm", "heuristic"]


@dataclass
class IngestResult:
    """Everything the UI needs to render one end-to-end run."""

    raw_payload: dict[str, Any]
    mapping: MappingResult
    delivery: DeliveryResult | None = None
    source_key: str | None = None
    detected_config: str | None = None
    fallback_chain: list[str] | None = None


def detect_config(payload: dict[str, Any]) -> str | None:
    """Guess which mappings/*.yaml fits this payload.

    TODO: implement. Score each config by how many of its identity + metric
    paths actually resolve against the payload; require a minimum score so an
    unknown vertical correctly returns None rather than a bad match.
    """
    raise NotImplementedError


def map_payload(
    payload: dict[str, Any],
    *,
    mode: Mode = "auto",
    config_name: str | None = None,
) -> MappingResult:
    """Map a payload using the requested strategy, with fallbacks. TODO."""
    raise NotImplementedError


def ingest(
    payload: dict[str, Any],
    *,
    mode: Mode = "auto",
    config_name: str | None = None,
    source_key: str | None = None,
    send: bool = True,
) -> IngestResult:
    """Map, deliver to Klaviyo, and record locally. TODO.

    Should also write to store.record_event() so Part 2's agent has history.
    """
    raise NotImplementedError
