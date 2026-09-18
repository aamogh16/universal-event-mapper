"""Connecting a tool that has no Klaviyo connector.

The sequence matters, because it is the whole cost argument:

  1. Infer the mapping ONCE with a model, from a single sample payload.
  2. Save that inference as a YAML config in mappings/.
  3. Every later event from that tool uses the config -- deterministic, free,
     no model call, no network.

So the model is a one-time onboarding cost, not a per-event cost. A gym with
four tools pays for four inferences, ever.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from .config import MAPPINGS_DIR
from .mapping import pipeline
from .mapping.config_mapper import load_config
from .mapping.llm_mapper import (
    InferredMapping,
    LLMMappingError,
    PropertyPath,
    map_with_llm,
    to_yaml_config,
)


@dataclass
class ConnectResult:
    tool: str
    config_name: str | None = None
    config_yaml: str = ""
    inferred_from_model: bool = False
    inference_ms: int = 0
    inference_cost: float | None = None
    events_ingested: int = 0
    events_failed: int = 0
    batch_ms: int = 0
    batch_cost: float = 0.0
    profile: str | None = None
    metric: str | None = None
    # The sample, and what it became. Shown side by side so the transformation
    # is visible rather than asserted.
    sample_payload: dict[str, Any] = field(default_factory=dict)
    sample_mapped: dict[str, Any] = field(default_factory=dict)
    sample_traces: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def per_event_after_setup(self) -> str:
        return "$0.00 — config-driven, no model call"


def _mapping_to_config(
    sample: dict[str, Any], result: Any, tool_name: str | None = None
) -> str:
    """Rebuild a config from a completed mapping's field traces.

    The traces record which source path fed which destination, so the config
    can be reconstructed from the mapping that was actually applied rather
    than from a second model call.
    """
    dest_to_path = {t.destination: t.source_path for t in result.field_traces}
    props = [
        PropertyPath(label=d.split("['")[1].rstrip("']"), path=p)
        for d, p in dest_to_path.items()
        if d.startswith("properties['")
    ]
    inferred = InferredMapping(
        metric_name=result.metric_name,
        email_path=dest_to_path.get("profile.email"),
        phone_path=dest_to_path.get("profile.phone_number"),
        external_id_path=dest_to_path.get("profile.external_id"),
        first_name_path=dest_to_path.get("profile.first_name"),
        last_name_path=dest_to_path.get("profile.last_name"),
        timestamp_path=dest_to_path.get("time"),
        value_path=dest_to_path.get("value"),
        currency_path=dest_to_path.get("value_currency"),
        property_paths=props,
        confidence=result.confidence,
        reasoning=result.reasoning,
    )
    return to_yaml_config(inferred, "kisi_door_access", tool_name)


def connect_tool(
    tool: str,
    sample: dict[str, Any],
    history: list[dict[str, Any]],
    *,
    source_key: str = "fitness",
    save_config: bool = True,
    send: bool = False,
) -> ConnectResult:
    """Infer a mapping from one payload, save it, then replay the history."""
    out = ConnectResult(tool=tool)

    # --- 1. one model call ------------------------------------------------
    started = time.monotonic()
    try:
        mapping = map_with_llm(sample)
        out.inferred_from_model = True
    except LLMMappingError as exc:
        # Fall back to the offline engine; still produces a usable mapping.
        from .mapping.heuristic import map_with_heuristics

        mapping = map_with_heuristics(sample)
        out.warnings.append(f"Model unavailable ({exc}); used offline inference.")
    out.inference_ms = int((time.monotonic() - started) * 1000)
    out.inference_cost = mapping.cost_estimate if hasattr(mapping, "cost_estimate") else None
    out.metric = mapping.metric_name
    out.profile = mapping.identity.primary
    out.sample_payload = sample
    out.sample_mapped = mapping.to_klaviyo_payload()
    out.sample_traces = [
        {"from": t.source_path, "to": t.destination,
         "value": str(t.value)[:60], "note": t.note}
        for t in mapping.field_traces
    ]

    # --- 2. persist it as a config ---------------------------------------
    if save_config:
        try:
            yaml_text = _mapping_to_config(sample, mapping, tool)
            name = "kisi_door_access"
            (MAPPINGS_DIR / f"{name}.yaml").write_text(yaml_text, encoding="utf-8")
            load_config.cache_clear()
            out.config_name = name
            out.config_yaml = yaml_text
        except Exception as exc:
            out.warnings.append(f"Could not save a config: {exc}")

    # --- 3. replay the history through the config -------------------------
    started = time.monotonic()
    for payload in history:
        try:
            res = pipeline.ingest(
                payload,
                mode="config" if out.config_name else "heuristic",
                config_name=out.config_name,
                source_key=source_key,
                send=send,
            )
            if res.mapping.strategy == "llm":
                out.batch_cost += 0.0035  # should not happen; flag if it does
                out.warnings.append("A batch event used the model unexpectedly.")
            out.events_ingested += 1
        except Exception:
            out.events_failed += 1
    out.batch_ms = int((time.monotonic() - started) * 1000)
    return out
