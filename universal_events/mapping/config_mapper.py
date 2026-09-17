"""Config-driven mapping: the deterministic default.

Reads a YAML file per source and applies it. No network, no model, no
surprises -- this is what runs in production and what the LLM path falls back
to. Every decision it makes is recorded as a FieldTrace so the demo can show
the mapping rather than assert it.
"""

from __future__ import annotations

from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from ..config import MAPPINGS_DIR
from .base import FieldTrace, MappingResult, ProfileIdentity
from .paths import MISSING, resolve, resolve_first


class MappingConfigError(RuntimeError):
    pass


@lru_cache(maxsize=None)
def load_config(name: str, mappings_dir: Path | None = None) -> dict[str, Any]:
    directory = mappings_dir or MAPPINGS_DIR
    path = directory / f"{name}.yaml"
    if not path.exists():
        raise MappingConfigError(f"No mapping config at {path}")
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise MappingConfigError(f"{path} must contain a YAML mapping")
    return data


def available_configs(mappings_dir: Path | None = None) -> list[str]:
    directory = mappings_dir or MAPPINGS_DIR
    if not directory.exists():
        return []
    return sorted(p.stem for p in directory.glob("*.yaml"))


def _coerce_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        # Heuristic: anything past ~1e11 is milliseconds, not seconds.
        seconds = value / 1000 if value > 1e11 else value
        try:
            return datetime.fromtimestamp(seconds)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        text = value.strip().replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            return None
    return None


def _coerce_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip().replace(",", "").lstrip("$"))
        except ValueError:
            return None
    return None


def _resolve_metric(
    payload: Any, spec: dict[str, Any], traces: list[FieldTrace]
) -> tuple[str, list[str]]:
    """Resolve the metric name via ordered `rules`, then `map`, then `default`."""
    warnings: list[str] = []

    # 1. Conditional rules win -- needed when one event type covers several
    #    real-world outcomes (see fitness_mindbody.yaml).
    for rule in spec.get("rules") or []:
        condition = rule.get("when") or {}
        path = condition.get("path")
        if not path:
            continue
        actual = resolve(payload, path)
        if actual is MISSING:
            continue
        if "equals" in condition and actual == condition["equals"]:
            name = rule["name"]
            traces.append(
                FieldTrace(
                    source_path=path,
                    destination="metric.name",
                    value=actual,
                    note=f"matched rule -> {name!r}",
                )
            )
            return name, warnings

    # 2. Flat lookup on the discriminator field.
    raw, winning = resolve_first(payload, spec.get("from"))
    table = spec.get("map") or {}
    if raw is not MISSING:
        key = str(raw)
        if key in table:
            traces.append(
                FieldTrace(
                    source_path=winning or "",
                    destination="metric.name",
                    value=raw,
                    note=f"mapped -> {table[key]!r}",
                )
            )
            return table[key], warnings
        warnings.append(
            f"Event type {key!r} is not in this config's map; used the default metric."
        )

    default = spec.get("default")
    if not default:
        raise MappingConfigError("metric spec must provide `default` or a matching rule")
    traces.append(
        FieldTrace(
            source_path=winning or "<none>",
            destination="metric.name",
            value=default,
            note="fell back to config default",
        )
    )
    return default, warnings


def map_with_config(
    payload: dict[str, Any],
    config_name: str,
    *,
    mappings_dir: Path | None = None,
) -> MappingResult:
    config = load_config(config_name, mappings_dir)
    traces: list[FieldTrace] = []
    warnings: list[str] = []

    # --- identity -----------------------------------------------------------
    identity_spec = config.get("identity") or {}
    identity_kwargs: dict[str, Any] = {}
    for field in ("email", "phone_number", "external_id", "first_name", "last_name"):
        value, winning = resolve_first(payload, identity_spec.get(field))
        if value is MISSING:
            continue
        identity_kwargs[field] = str(value)
        traces.append(
            FieldTrace(
                source_path=winning or "",
                destination=f"profile.{field}",
                value=value,
            )
        )
    identity = ProfileIdentity(**identity_kwargs)
    if not identity.is_resolvable:
        warnings.append(
            "No email, phone, or external_id found — Klaviyo cannot attach this "
            "event to a profile."
        )

    # --- metric -------------------------------------------------------------
    metric_name, metric_warnings = _resolve_metric(
        payload, config.get("metric") or {}, traces
    )
    warnings.extend(metric_warnings)

    # --- timestamp ----------------------------------------------------------
    occurred_at = None
    ts_value, ts_path = resolve_first(payload, (config.get("timestamp") or {}).get("from"))
    if ts_value is not MISSING:
        occurred_at = _coerce_datetime(ts_value)
        if occurred_at is None:
            warnings.append(f"Could not parse a timestamp from {ts_path!r}.")
        else:
            traces.append(
                FieldTrace(source_path=ts_path or "", destination="time", value=ts_value)
            )

    # --- value / currency ---------------------------------------------------
    value_spec = config.get("value") or {}
    numeric = None
    raw_value, value_path = resolve_first(payload, value_spec.get("from"))
    currency = None
    if raw_value is not MISSING:
        numeric = _coerce_float(raw_value)
        if numeric is None:
            warnings.append(f"Non-numeric value at {value_path!r}; omitted.")
        else:
            traces.append(
                FieldTrace(source_path=value_path or "", destination="value", value=numeric)
            )
            cur, cur_path = resolve_first(payload, value_spec.get("currency_from"))
            currency = (
                str(cur).upper()
                if cur is not MISSING
                else value_spec.get("currency_default")
            )
            if currency:
                traces.append(
                    FieldTrace(
                        source_path=cur_path or "<config default>",
                        destination="value_currency",
                        value=currency,
                    )
                )

    # --- unique_id ----------------------------------------------------------
    unique_id = None
    uid, uid_path = resolve_first(payload, (config.get("unique_id") or {}).get("from"))
    if uid is not MISSING:
        unique_id = str(uid)
        traces.append(
            FieldTrace(source_path=uid_path or "", destination="unique_id", value=unique_id)
        )

    # --- properties ---------------------------------------------------------
    properties: dict[str, Any] = {}
    for label, path in (config.get("properties") or {}).items():
        found = resolve(payload, path)
        if found is MISSING or found is None:
            continue
        if isinstance(found, str) and not found.strip():
            continue
        properties[label] = found
        traces.append(
            FieldTrace(source_path=path, destination=f"properties['{label}']", value=found)
        )

    return MappingResult(
        metric_name=metric_name,
        identity=identity,
        properties=properties,
        value=numeric,
        value_currency=currency if numeric is not None else None,
        occurred_at=occurred_at,
        unique_id=unique_id,
        strategy="config",
        confidence=1.0,
        reasoning=(
            f"Applied config {config_name!r} "
            f"({config.get('tool', 'unknown tool')} -> Klaviyo). "
            "Fully deterministic: no model involved."
        ),
        field_traces=traces,
        warnings=warnings,
    )
