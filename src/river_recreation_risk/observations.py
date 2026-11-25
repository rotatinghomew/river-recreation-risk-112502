"""志愿者观察与游客反馈的证据边界。"""

from __future__ import annotations

from typing import Any

from .timeutils import parse_dt


def for_entrance(records: list[dict[str, Any]] | None, entrance_id: str, segment_id: str | None = None) -> list[dict[str, Any]]:
    result = []
    for raw in records or []:
        if raw.get("entrance_id") == entrance_id:
            result.append({**raw, "attribution": "entrance"})
        elif segment_id and raw.get("segment_id") == segment_id and not raw.get("entrance_id"):
            result.append({**raw, "attribution": "segment"})
    return result


def interpret_volunteer(raw: dict[str, Any]) -> dict[str, Any]:
    """志愿者观察可支持现场风险背景，但不能替代化验阈值判定。"""
    observed_at = raw.get("observed_at") or raw.get("reported_at")
    severity = raw.get("severity") or raw.get("risk_signal") or "not_scored"
    signal_types = set(raw.get("signals") or [])
    for key in ("observation_type", "type", "category"):
        if raw.get(key):
            signal_types.add(str(raw[key]))
    hazard_signals = signal_types & {
        "discoloration",
        "odor",
        "dead_fish",
        "algae",
        "sewage",
        "turbidity",
        " debris",
    }
    return {
        "observation_id": raw.get("observation_id") or raw.get("volunteer_observation_id") or raw.get("id"),
        "entrance_id": raw.get("entrance_id"),
        "segment_id": raw.get("segment_id"),
        "observed_at": observed_at,
        "severity": severity,
        "signals": sorted(signal_types),
        "hazard_signal": bool(hazard_signals) or str(severity).lower() in {"high", "critical"},
        "role": "contextual_observation_only",
        "can_replace_lab_test": False,
    }


def interpret_feedback(raw: dict[str, Any]) -> dict[str, Any]:
    """游客/游客反馈只证明某人接触过入口，不能证明水质合格或超标。"""
    return {
        "feedback_id": raw.get("feedback_id") or raw.get("id"),
        "entrance_id": raw.get("entrance_id"),
        "contact_at": raw.get("contact_at") or raw.get("visited_at") or raw.get("reported_at"),
        "reported_at": raw.get("reported_at"),
        "symptoms": list(raw.get("symptoms") or []),
        "role": "exposure_or_contact_claim_only",
        "can_replace_lab_test": False,
        "note": "只能说明接触过某个入口，不能替代相应水质检测",
    }


def recent_records(records: list[dict[str, Any]], as_of: Any, hours: float | None = None, time_field: str = "observed_at") -> list[dict[str, Any]]:
    when = parse_dt(as_of, "as_of")
    result = []
    for raw in records:
        dt = parse_dt(raw.get(time_field) or raw.get("reported_at") or raw.get("contact_at"), time_field)
        if dt is None or when is None or dt > when:
            continue
        if hours is not None and (when - dt).total_seconds() > hours * 3600:
            continue
        result.append(raw)
    return result
