"""样本成熟度、质量、阈值分类和降雨上下文。"""

from __future__ import annotations

from typing import Any

from .policies import freeze_policy
from .timeutils import parse_dt

VALID_QUALITIES = {"valid", "accepted", "ok", "usable"}
PENDING_QUALITIES = {"pending", "processing", "preliminary"}
PARTIAL_QUALITIES = {"partial", "partial_indicators", "incomplete_indicators"}
INVALID_QUALITIES = {
    "invalid",
    "rejected",
    "failed",
    "preservation_exceeded",
    "expired",
    "qa_failed",
}


def indicator_value(sample: dict[str, Any], indicator: str) -> Any:
    if indicator in sample and sample[indicator] is not None:
        return sample[indicator]
    for key in ("indicators", "values", "measurements"):
        container = sample.get(key)
        if isinstance(container, dict) and container.get(indicator) is not None:
            value = container[indicator]
            if isinstance(value, dict):
                return value.get("value")
            return value
    return None


def evaluate_sample(
    sample: dict[str, Any],
    as_of: Any,
    policy: dict[str, Any],
) -> dict[str, Any]:
    """评估单个样本在 as_of 是否能作为完整有效证据。

    该函数只描述事实和质量状态，不把“无值/未成熟”解释为安全。
    """
    when = parse_dt(as_of, "as_of")
    p = freeze_policy(policy)
    collected = parse_dt(sample.get("collected_at"), "collected_at")
    received = parse_dt(sample.get("received_at"), "received_at")
    available = parse_dt(sample.get("available_at"), "available_at")

    reasons: list[str] = []
    stage = "unknown"
    if collected is None:
        stage = "planned"
        reasons.append("missing_collected_at")
    elif when and collected > when:
        stage = "planned"
        reasons.append("collected_in_future")
    availability_status = (
        sample.get("availability_status") or sample.get("available_status") or ""
    ).lower()
    if received is None:
        stage = "collected_awaiting_receipt"
        reasons.append("not_received")
    elif available is not None and (when is None or available <= when):
        stage = "available"
    elif availability_status in {"available", "ready", "released", "complete"}:
        stage = "available"
        if available is not None and when and available > when:
            reasons.append("availability_timestamp_in_future")
            stage = "received_processing"
    else:
        stage = "received_processing"
        reasons.append("not_available")

    quality = (sample.get("quality") or "").lower() or "unknown"
    quality_ok = quality in VALID_QUALITIES
    if quality in PENDING_QUALITIES:
        reasons.append("quality_pending")
    elif quality in PARTIAL_QUALITIES:
        reasons.append("partial_indicators")
    elif quality in INVALID_QUALITIES:
        reasons.append(f"quality_{quality}")
    elif not quality_ok:
        reasons.append(f"quality_{quality}")

    preservation_ok = True
    preservation_status = sample.get("preservation_status")
    if preservation_status and str(preservation_status).lower() not in {
        "within_conditions",
        "ok",
        "valid",
        "preserved",
    }:
        preservation_ok = False
        reasons.append("preservation_condition_exceeded")

    max_receipt_hours = p.get("max_receipt_hours")
    receipt_hours = None
    if collected and received:
        receipt_hours = (received - collected).total_seconds() / 3600
        if max_receipt_hours is not None and receipt_hours > float(max_receipt_hours):
            preservation_ok = False
            reasons.append("receipt_after_preservation_limit")

    hold_hours = sample.get("hold_hours")
    max_hold_hours = sample.get("max_hold_hours", p.get("max_hold_hours"))
    if hold_hours is not None and max_hold_hours is not None and float(hold_hours) > float(max_hold_hours):
        preservation_ok = False
        reasons.append("hold_time_exceeds_preservation_limit")

    required = list(p.get("required_indicators") or [p.get("indicator", "risk_value")])
    values = {name: indicator_value(sample, name) for name in required}
    missing_indicators = [name for name, value in values.items() if value is None]
    explicit_measured = sample.get("measured_indicators")
    if explicit_measured is not None:
        missing_measured = [name for name in required if name not in explicit_measured]
        for name in missing_measured:
            if name not in missing_indicators:
                missing_indicators.append(name)
                values.setdefault(name, None)
    if sample.get("complete_indicators") is False or sample.get("indicator_coverage") == "partial":
        if "partial_indicators" not in reasons:
            reasons.append("partial_indicators")

    indicators_complete = not missing_indicators and "partial_indicators" not in reasons
    value = values.get(p.get("indicator", "risk_value"))
    if value is None:
        reasons.append("missing_indicator_value")

    age_hours = None
    stale = False
    if collected and when:
        age_hours = (when - collected).total_seconds() / 3600
        freshness = p.get("freshness_hours")
        if freshness is not None and age_hours > float(freshness):
            stale = True
            reasons.append("stale_beyond_freshness_window")

    is_mature = stage == "available"
    is_complete = indicators_complete and quality_ok
    is_usable = is_mature and quality_ok and preservation_ok and is_complete and value is not None
    is_effective = is_usable and not stale

    threshold = p.get("threshold")
    threshold_state = "unknown"
    if value is not None and threshold is not None:
        try:
            threshold_state = "below_threshold" if float(value) < float(threshold) else "at_or_above_threshold"
        except (TypeError, ValueError):
            threshold_state = "non_numeric"
            reasons.append("indicator_not_numeric")
            is_usable = False
            is_effective = False

    if not is_effective:
        if stale and is_usable:
            classification = "stale"
        elif "partial_indicators" in reasons:
            classification = "partial_indicators"
        elif not preservation_ok:
            classification = "preservation_failed"
        elif quality in PENDING_QUALITIES or stage != "available":
            classification = "pending_or_processing"
        elif quality in INVALID_QUALITIES or not quality_ok:
            classification = "invalid_quality"
        elif missing_indicators:
            classification = "incomplete_indicators"
        else:
            classification = "not_effective"
    else:
        # 决策层使用 above_threshold 表示“达到或超过阈值”；
        # threshold_state 保留更精确的 at_or_above_threshold 语义。
        classification = "above_threshold" if threshold_state == "at_or_above_threshold" else threshold_state

    return {
        "sample_id": sample.get("sample_id"),
        "segment_id": sample.get("segment_id"),
        "collected_at": sample.get("collected_at"),
        "received_at": sample.get("received_at"),
        "available_at": sample.get("available_at"),
        "coverage": sample.get("coverage"),
        "quality": quality,
        "stage": stage,
        "maturity": stage,
        "maturity_status": stage,
        "is_mature": is_mature,
        "is_complete": is_complete,
        "preservation_ok": preservation_ok,
        "indicators": values,
        "missing_indicators": missing_indicators,
        "value": value,
        "threshold": threshold,
        "threshold_version": p.get("version"),
        "policy_fingerprint": p.get("fingerprint"),
        "age_hours": round(age_hours, 1) if age_hours is not None else None,
        "stale": stale,
        "is_usable": is_usable,
        "is_effective": is_effective,
        "threshold_state": threshold_state,
        "classification": classification,
        "reasons": reasons,
    }


def weather_context(
    weather: list[dict[str, Any]] | None,
    as_of: Any,
    policy: dict[str, Any],
    *,
    target_at: Any = None,
) -> dict[str, Any]:
    """汇总降雨/水位；降雨只构成风险背景和补采触发，不替代化验。"""
    when = parse_dt(as_of, "as_of")
    target = parse_dt(target_at, "target_at") if target_at else when
    rows = list(weather or [])
    window_hours = float(policy.get("rain_window_hours", 24))
    alert_mm = float(policy.get("rain_alert_mm", 20))

    recent: list[dict[str, Any]] = []
    rain_total = 0.0
    max_level = None
    latest_heavy_rain_at = None

    for row in rows:
        observed = parse_dt(row.get("observed_at") or row.get("forecast_for"), "weather.observed_at")
        if observed is None or when is None:
            continue
        # as_of 之后的预报可作为计划背景，但不称为已发生观测。
        if observed > when and not row.get("forecast"):
            continue
        reference = target or when
        if reference and observed < reference and (reference - observed).total_seconds() > window_hours * 3600:
            continue
        recent.append(row)
        rain = row.get("rain_mm")
        if rain is not None and observed <= when:
            rain_total += float(rain)
            if float(rain) >= alert_mm:
                if latest_heavy_rain_at is None or observed > latest_heavy_rain_at:
                    latest_heavy_rain_at = observed
        level = row.get("water_level")
        if level is not None:
            max_level = float(level) if max_level is None else max(max_level, float(level))

    rain_alert = rain_total >= alert_mm or latest_heavy_rain_at is not None
    return {
        "rain_total_mm": round(rain_total, 1),
        "rain_window_hours": window_hours,
        "rain_alert_mm": alert_mm,
        "rain_alert": rain_alert,
        "latest_heavy_rain_at": latest_heavy_rain_at.isoformat(timespec="minutes") if latest_heavy_rain_at else None,
        "max_water_level": max_level,
        "records": recent,
        "interpretation": "rainfall_is_context_not_water_quality_evidence",
    }


def post_rain_sample_satisfied(
    sample_assessments: list[dict[str, Any]],
    weather: dict[str, Any],
    *,
    target_at: Any,
    policy: dict[str, Any],
) -> dict[str, Any]:
    """检查高风险降雨后是否已有完整有效样本。"""
    if not policy.get("require_post_rain_sample", True):
        return {"required": False, "satisfied": True, "reason": "policy_disabled"}
    rain_at = parse_dt(weather.get("latest_heavy_rain_at"), "latest_heavy_rain_at")
    if not weather.get("rain_alert") or rain_at is None:
        return {"required": False, "satisfied": True, "reason": "no_heavy_rain_trigger"}

    target = parse_dt(target_at, "target_at")
    limit_hours = float(policy.get("post_rain_sample_hours", 48))
    candidates = []
    for assessment in sample_assessments:
        collected = parse_dt(assessment.get("collected_at"), "collected_at")
        if collected is None or collected < rain_at:
            continue
        if target and collected > target:
            continue
        age_after_rain = (collected - rain_at).total_seconds() / 3600
        if age_after_rain <= limit_hours and assessment.get("is_effective"):
            candidates.append(assessment["sample_id"])

    return {
        "required": True,
        "satisfied": bool(candidates),
        "rain_at": weather.get("latest_heavy_rain_at"),
        "max_delay_hours": limit_hours,
        "effective_sample_ids": candidates,
        "reason": "post_rain_effective_sample_present" if candidates else "missing_post_rain_effective_sample",
    }
