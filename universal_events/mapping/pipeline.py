"""Strategy selection, delivery, and persistence.

Resolution order, designed so a live demo degrades instead of failing:

    mode="config"     -> named or detected config only
    mode="llm"        -> LLM inference, falls back to heuristic on any error
    mode="heuristic"  -> offline structural inference only
    mode="auto"       -> detect a config; if none, try LLM; if that fails,
                         heuristic. Always returns a MappingResult.

Nothing here raises during normal operation. `fallback_chain` records which
strategies were attempted so the UI can show what actually happened.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from .. import store
from ..klaviyo import DeliveryResult, KlaviyoClient
from .base import MappingResult
from .config_mapper import available_configs, load_config, map_with_config
from .heuristic import map_with_heuristics
from .paths import MISSING, resolve, resolve_first

Mode = Literal["auto", "config", "llm", "heuristic"]

# Below this, we treat the payload as unrecognised rather than force a bad fit.
DETECTION_THRESHOLD = 8.0


@dataclass
class IngestResult:
    """Everything the UI needs to render one end-to-end run."""

    raw_payload: dict[str, Any]
    mapping: MappingResult
    delivery: DeliveryResult | None = None
    source_key: str | None = None
    detected_config: str | None = None
    fallback_chain: list[str] = field(default_factory=list)
    stored_event_id: int | None = None


def score_config(payload: dict[str, Any], config_name: str) -> float:
    """How well does this config fit this payload? Higher is better."""
    try:
        config = load_config(config_name)
    except Exception:
        return 0.0

    score = 0.0
    for paths in (config.get("identity") or {}).values():
        value, _ = resolve_first(payload, paths)
        if value is not MISSING:
            score += 2.0

    metric_spec = config.get("metric") or {}
    raw, _ = resolve_first(payload, metric_spec.get("from"))
    if raw is not MISSING:
        score += 3.0
        # A discriminator value the config actually knows is the strongest signal.
        if str(raw) in (metric_spec.get("map") or {}):
            score += 5.0
    for rule in metric_spec.get("rules") or []:
        path = (rule.get("when") or {}).get("path")
        if path and resolve(payload, path) is not MISSING:
            score += 2.0

    for path in (config.get("properties") or {}).values():
        if resolve(payload, path) is not MISSING:
            score += 0.5
    return score


def detect_config(payload: dict[str, Any]) -> str | None:
    """Pick the best-fitting config, or None if nothing fits well enough."""
    best: tuple[float, str] | None = None
    for name in available_configs():
        score = score_config(payload, name)
        if best is None or score > best[0]:
            best = (score, name)
    if best and best[0] >= DETECTION_THRESHOLD:
        return best[1]
    return None


def map_payload(
    payload: dict[str, Any],
    *,
    mode: Mode = "auto",
    config_name: str | None = None,
) -> tuple[MappingResult, list[str], str | None]:
    """Map a payload. Returns (result, fallback_chain, detected_config)."""
    chain: list[str] = []
    detected: str | None = None

    if mode in ("auto", "config"):
        detected = config_name or detect_config(payload)
        if detected:
            chain.append(f"config:{detected}")
            try:
                return map_with_config(payload, detected), chain, detected
            except Exception as exc:
                chain.append(f"config failed ({type(exc).__name__})")
        elif mode == "config":
            chain.append("no matching config")

    if mode in ("auto", "llm"):
        chain.append("llm")
        try:
            from .llm_mapper import map_with_llm

            return map_with_llm(payload), chain, detected
        except Exception as exc:
            chain.append(f"llm failed ({type(exc).__name__})")

    chain.append("heuristic")
    return map_with_heuristics(payload), chain, detected


def ingest(
    payload: dict[str, Any],
    *,
    mode: Mode = "auto",
    config_name: str | None = None,
    source_key: str | None = None,
    send: bool = True,
    persist: bool = True,
) -> IngestResult:
    """Map a payload, deliver it to Klaviyo, and mirror it locally.

    The local mirror is what the agent later reads as customer context, so
    persistence is on by default even when delivery is skipped.
    """
    mapping, chain, detected = map_payload(payload, mode=mode, config_name=config_name)

    delivery: DeliveryResult | None = None
    if send:
        client = KlaviyoClient()
        delivery = client.create_event(mapping.to_klaviyo_payload())
        if delivery.ok and not delivery.dry_run and mapping.identity.email:
            profile = client.find_profile(
                email=mapping.identity.email, attempts=3
            )
            if profile:
                delivery.profile_id = profile.get("id")
                delivery.profile_url = (
                    f"https://www.klaviyo.com/profile/{profile.get('id')}"
                )

    stored_id: int | None = None
    if persist:
        try:
            stored_id = store.record_event(mapping, source_key=source_key)
        except Exception:
            # A mirror failure must never break the visible demo path.
            chain.append("local persist failed")

    return IngestResult(
        raw_payload=payload,
        mapping=mapping,
        delivery=delivery,
        source_key=source_key,
        detected_config=detected,
        fallback_chain=chain,
        stored_event_id=stored_id,
    )
