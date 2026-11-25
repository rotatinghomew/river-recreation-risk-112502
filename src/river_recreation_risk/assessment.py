"""入口当前状态、证据缺口与开放决策。"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from .evidence import evaluate_sample, post_rain_sample_satisfied, weather_context
from .observations import for_entrance, interpret_feedback, interpret_volunteer, recent_records
from .policies import select_policy
from .timeutils import parse_dt
from .topology import RiverIndex, active_zones_for_entrance, flow_relation

STATUS_LABELS_ZH = {
    "open": "可继续开放",
    "limited": "证据有限/建议限流",
    "resample_required": "需要补采",
    "hold": "暂缓或关闭",
    "unknown": "证据不足",
}

ACTION_LABELS_ZH = {
    "continue_open": "继续开放",
    "limit_capacity": "限流",
    "supplement_sample": "补采",
    "hold": "暂缓",
}


def assess_entrance(
    *,
    entrance_id: str,
    index: RiverIndex,
    samples: list[dict[str, Any]],
    as_of: Any,
    policies: list[dict[str, Any]] | dict[str, Any] | None = None,
    target_at: Any = None,
    weather: list[dict[str, Any]] | None = None,
    volunteer_observations: list[dict[str, Any]] | None = None,
    visitor_feedback: list[dict[str, Any]] | None = None,
        risk_zones: list[dict[str, Any]] | None = None,
        sampling_plans: list[dict[str, Any]] | None = None,
        supplement_evaluation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    when = parse_dt(as_of, "as_of")
    if when is None:
        raise ValueError("as_of 不能为空")
    target = parse_dt(target_at, "target_at") or when

    entrance = index.entrances_by_id.get(entrance_id)
    if entrance is None:
        raise KeyError(f"未知入口: {entrance_id}")
    segment_id = entrance.get("segment_id")
    segment = index.segment_snapshot(segment_id, as_of) if segment_id else None
    policy = select_policy(policies, as_of)
    active_zones = active_zones_for_entrance(risk_zones, entrance, as_of)
    zone_hold = any(
        str(z.get("risk_level", "")).lower() in {"high", "critical", "closed", "avoid"}
        or str(z.get("required_action", z.get("decision", ""))).lower() == "hold"
        for z in active_zones
    )

    applicable: list[dict[str, Any]] = []
    segment_audit_items: list[dict[str, Any]] = []
    for sample in samples:
        relation = flow_relation(sample, entrance, index, as_of)
        sample_assessment = evaluate_sample(sample, as_of, policy)
        target_assessment = evaluate_sample(sample, target, policy) if target != when else sample_assessment
        item = {
            **sample_assessment,
            "relation": relation,
            "applies_to_entrance": bool(relation.get("applies")),
            "effective_for_target": target_assessment.get("is_effective", False),
            "stale_at_target": target_assessment.get("stale", False),
        }
        if sample.get("segment_id") == segment_id:
            segment_audit_items.append(item)
        if relation.get("applies"):
            applicable.append(item)

    applicable.sort(key=lambda x: parse_dt(x.get("collected_at")) or when)
    direct_items = [
        x for x in applicable
        if x["relation"].get("relation") in {
            "direct_entrance_sample",
            "covered_entrance",
            "full_segment_coverage",
            "same_segment_scope_unspecified",
        }
    ]
    flow_items = [x for x in applicable if x["relation"].get("relation") == "adverse_downstream_flow"]

    effective_direct = [x for x in direct_items if x.get("is_effective")]
    open_effective = [x for x in direct_items if x.get("effective_for_target")]
    below_samples = [x for x in effective_direct if x.get("classification") == "below_threshold"]
    above_samples = [x for x in effective_direct if x.get("classification") == "above_threshold"]
    target_below = [x for x in open_effective if x.get("classification") == "below_threshold"]
    target_above = [x for x in open_effective if x.get("classification") == "above_threshold"]
    flow_above = [x for x in flow_items if x.get("classification") == "above_threshold"]

    latest_direct = effective_direct[-1] if effective_direct else None
    latest_classification = latest_direct.get("classification") if latest_direct else None
    active_above = bool(above_samples or flow_above)
    latest_below_after_above = False
    if above_samples and below_samples:
        latest_below_after_above = (below_samples[-1].get("collected_at") or "") > (above_samples[-1].get("collected_at") or "")

    # 降雨背景按分析时刻的已观测窗口汇总；是否满足“雨后样本”再对照目标时刻。
    weather_summary = weather_context(weather, when, policy)
    post_rain = post_rain_sample_satisfied(applicable, weather_summary, target_at=target, policy=policy)

    volunteer_raw = for_entrance(
        recent_records(volunteer_observations or [], when), entrance_id, segment_id
    )
    feedback_raw = for_entrance(
        recent_records(visitor_feedback or [], when, time_field="contact_at"), entrance_id, segment_id
    )
    volunteer_rows = [interpret_volunteer(x) for x in volunteer_raw]
    feedback_rows = [interpret_feedback(x) for x in feedback_raw]
    contextual_hazard = any(x.get("hazard_signal") for x in volunteer_rows)

    gaps: list[dict[str, str]] = []

    def add_gap(code: str, message_zh: str, severity: str = "warning") -> None:
        if not any(g["code"] == code for g in gaps):
            gaps.append({"code": code, "message_zh": message_zh, "severity": severity})

    if zone_hold:
        zone_ids = ", ".join(str(z.get("zone_id") or z.get("id")) for z in active_zones)
        add_gap(
            "risk_zone_restriction",
            f"入口位于生效中的高风险分区 {zone_ids}；分区管控范围优先于单点低值样本。",
            "critical",
        )

    segment_sample_records = [s for s in samples if s.get("segment_id") == segment_id]

    actual_sample_ids = {s.get("sample_id") for s in samples}
    actual_plan_ids = {s.get("plan_id") for s in samples if s.get("plan_id")}
    plan_rows: list[dict[str, Any]] = []
    for plan in sampling_plans or []:
        if plan.get("status") in {"cancelled", "canceled"}:
            continue
        plan_entrances = set(plan.get("entrance_ids") or [])
        applies = entrance_id in plan_entrances or plan.get("entrance_id") == entrance_id
        if not applies and plan.get("segment_id") == segment_id and not plan_entrances:
            applies = True
        if not applies:
            continue
        planned_time = parse_dt(
            plan.get("planned_for") or plan.get("scheduled_for") or plan.get("planned_start") or plan.get("start"),
            "sampling_plan.planned_for",
        )
        linked = (
            plan.get("plan_id") in actual_plan_ids
            or plan.get("sample_id") in actual_sample_ids
            or bool(plan.get("actual_sample_id") and plan.get("actual_sample_id") in actual_sample_ids)
        )
        timing_state = "time_unknown"
        if linked:
            timing_state = "actual_sample_recorded"
        elif planned_time is None:
            timing_state = "unscheduled"
        elif planned_time <= when:
            timing_state = "planned_without_actual_sample"
            add_gap(
                "planned_sample_missing",
                f"采样计划 {plan.get('plan_id') or plan.get('id')} 已过计划时间但没有关联实际采样记录；计划不能算作样本。",
                "critical",
            )
        else:
            timing_state = "upcoming_planned_sample"
        plan_rows.append({
            "plan_id": plan.get("plan_id") or plan.get("id"),
            "segment_id": plan.get("segment_id"),
            "entrance_ids": sorted(plan_entrances) or ([plan["entrance_id"]] if plan.get("entrance_id") else []),
            "planned_for": plan.get("planned_for") or plan.get("scheduled_for") or plan.get("planned_start"),
            "status": plan.get("status"),
            "linked_actual_sample": linked,
            "timing_state": timing_state,
        })
    if not segment_sample_records:
        add_gap("no_sample", "该入口所属河段没有任何采样记录；无样本不等于低于阈值或安全。", "critical")
    elif not applicable:
        add_gap("spatial_coverage_missing", "有河段样本但覆盖范围不能归属到该入口；不能套用整条河结论。", "critical")
    elif not effective_direct:
        classifications = {x.get("classification") for x in applicable}
        if "pending_or_processing" in classifications:
            add_gap("pending_result", "已有样本尚在采集、接收、化验或待质量确认阶段，不能按合格样本计数。", "critical")
        if "partial_indicators" in classifications:
            add_gap("partial_indicators", "样本只完成部分指标或空间覆盖不完整，不能与完整有效样本等同。", "warning")
        if "preservation_failed" in classifications:
            add_gap("preservation_failed", "样本超出保存条件，不能作为合格证据。", "critical")
        if any(x.get("stale") for x in applicable):
            add_gap("stale_sample", "样本已超过策略规定的新鲜度窗口。", "warning")
        if not gaps:
            add_gap("no_effective_sample", "没有完整、有效且在新鲜度窗口内的样本。", "critical")

    # 即使已有其他有效样本，也保留待处理/部分样本的提示，避免分析员忽略即将成熟的证据。
    applicable_classes = {x.get("classification") for x in applicable}
    if effective_direct and "pending_or_processing" in applicable_classes:
        add_gap("pending_result", "存在尚在采集、接收或化验流程中的样本；在可用前不能按合格样本计数。", "info")
    if effective_direct and "partial_indicators" in applicable_classes:
        add_gap("partial_indicators", "存在只完成部分指标的样本；它不能替代完整指标样本。", "info")

    min_samples = int(policy.get("min_valid_samples", 1))
    if len(target_below) < min_samples and not target_above:
        add_gap(
            "insufficient_valid_sample_count",
            f"可用于{target.strftime('%Y-%m-%d %H:%M')}开放判断的明确低于阈值样本为 {len(target_below)} 个，要求至少 {min_samples} 个。",
            "critical",
        )

    if post_rain.get("required") and not post_rain.get("satisfied"):
        add_gap(
            "missing_post_rain_sample",
            "监测到高风险降雨，但缺少雨后完整有效样本；无采样时段不能视为安全。",
            "critical",
        )

    if any(x.get("stale_at_target") and x.get("is_effective") for x in direct_items):
        add_gap("stale_at_target_time", "现有样本到活动/开放目标时刻将超出新鲜度要求。", "warning")

    if contextual_hazard and not active_above:
        add_gap("volunteer_hazard_signal", "志愿者观察到异常现场信号；它不是化验结论，但应触发核查或限流。", "warning")

    if feedback_rows:
        add_gap("visitor_feedback_not_evidence", "游客反馈仅证明入口接触/暴露，不能替代水质检测。", "info")

    evidence_gap_codes = {
        "no_sample",
        "spatial_coverage_missing",
        "pending_result",
        "preservation_failed",
        "no_effective_sample",
        "planned_sample_missing",
        "insufficient_valid_sample_count",
        "missing_post_rain_sample",
    }
    supplement_required = (
        not active_above
        and not target_above
        and (
            len(target_below) < min_samples
            or not post_rain.get("satisfied", True)
            or any(g["code"] in evidence_gap_codes for g in gaps)
        )
    )

    if supplement_evaluation is not None and supplement_evaluation.get("has_conflict"):
        blocking_conflicts = supplement_evaluation.get("blocking_activity_conflicts") or []
        if blocking_conflicts and supplement_required:
            add_gap(
                "supplement_activity_conflict",
                supplement_evaluation.get("risk_impact", {}).get("message", "补采时段与活动冲突。"),
                "critical",
            )
        elif blocking_conflicts:
            add_gap(
                "scheduled_trip_activity_conflict_but_evidence_sufficient",
                "排期上仍有船与活动冲突，但现有证据已足够；可取消/改派该补采任务，风险结论不再依赖该时段。",
                "warning",
            )
        elif supplement_evaluation.get("activity_conflicts"):
            add_gap(
                "supplement_time_overlap_other_area",
                supplement_evaluation.get("risk_impact", {}).get("message", "补采与其他活动时间重叠。"),
                "info",
            )
        if (supplement_evaluation.get("resource_conflicts") or supplement_evaluation.get("trip_conflicts")) and supplement_required:
            add_gap("supplement_resource_unavailable", "采样船或人员在目标窗口不可用。", "critical")

    evidence_state = _evidence_state(
        segment_has_records=bool(segment_sample_records),
        applicable=applicable,
        below=below_samples,
        above=above_samples + flow_above,
    )

    status, action, risk_level = _decide(
        zone_hold=zone_hold,
        active_above=active_above,
        latest_classification=latest_classification,
        latest_below_after_above=latest_below_after_above,
        target_below=target_below,
        target_above=target_above,
        min_samples=min_samples,
        post_rain=post_rain,
        gaps=gaps,
        contextual_hazard=contextual_hazard,
        supplement_evaluation=supplement_evaluation,
        policy=policy,
    )

    supporting = [
        {
            "sample_id": x.get("sample_id"),
            "collected_at": x.get("collected_at"),
            "available_at": x.get("available_at"),
            "classification": x.get("classification"),
            "maturity": x.get("maturity") or x.get("stage"),
            "stage": x.get("stage"),
            "value": x.get("value"),
            "threshold": x.get("threshold"),
            "is_effective": x.get("is_effective"),
            "effective_for_target": x.get("effective_for_target"),
            "reasons": x.get("reasons"),
            "relation": x.get("relation"),
        }
        for x in applicable
    ]

    return {
        "entrance_id": entrance_id,
        "entrance_name": entrance.get("name") or entrance_id,
        "segment_id": segment_id,
        "segment_version": {
            "version": segment.get("version") if segment else None,
            "fingerprint": segment.get("fingerprint") if segment else None,
            "version_source": segment.get("version_source") if segment else None,
        },
        "flow_direction": entrance.get("flow_direction") or (segment or {}).get("flow_direction"),
        "as_of": when.isoformat(timespec="minutes"),
        "target_at": target.isoformat(timespec="minutes"),
        "policy": {
            "version": policy.get("version"),
            "policy_id": policy.get("policy_id"),
            "threshold": policy.get("threshold"),
            "fingerprint": policy.get("fingerprint"),
            "source": policy.get("source"),
            "freshness_hours": policy.get("freshness_hours"),
            "min_valid_samples": min_samples,
        },
        "current_status": status,
        "status_zh": STATUS_LABELS_ZH[status],
        "recommended_action": action,
        "decision": action,
        "action_zh": ACTION_LABELS_ZH[action],
        "risk_level": risk_level,
        "evidence_state": evidence_state,
        "counts": {
            "applicable_samples": len(applicable),
            "effective_direct_samples": len(effective_direct),
            "explicit_below_threshold": len(below_samples),
            "explicit_above_threshold": len(above_samples) + len(flow_above),
            "below_for_target": len(target_below),
            "above_for_target": len(target_above),
            "volunteer_observations": len(volunteer_rows),
            "visitor_feedback": len(feedback_rows),
        },
        "latest_effective_sample_id": latest_direct.get("sample_id") if latest_direct else None,
        "supporting_samples": supporting,
        "segment_sample_audit": segment_audit_items,
        "sampling_plans": plan_rows,
        "gaps": gaps,
        "weather": weather_summary,
        "risk_zones": active_zones,
        "post_rain_check": post_rain,
        "supplement_required": supplement_required,
        "volunteer_observations": volunteer_rows,
        "visitor_feedback": feedback_rows,
        "supplement_evaluation": supplement_evaluation,
        "basis_fingerprints": {
            "policy": policy.get("fingerprint"),
            "segment": (segment or {}).get("fingerprint"),
        },
    }


def _evidence_state(*, segment_has_records: bool, applicable: list[dict[str, Any]], below, above) -> str:
    if above and below:
        return "mixed_threshold_signals"
    if above:
        return "explicit_above_threshold"
    if below:
        return "explicit_below_threshold"
    if not segment_has_records or not applicable:
        return "no_sample"
    return "only_unusable_or_pending_samples"


def _decide(
    *,
    zone_hold: bool,
    active_above: bool,
    latest_classification: str | None,
    latest_below_after_above: bool,
    target_below: list[dict[str, Any]],
    target_above: list[dict[str, Any]],
    min_samples: int,
    post_rain: dict[str, Any],
    gaps: list[dict[str, str]],
    contextual_hazard: bool,
    supplement_evaluation: dict[str, Any] | None,
    policy: dict[str, Any],
) -> tuple[str, str, str]:
    # 1) 分区管控或明确超标优先：暂缓，不能被已售活动/已发公告事实覆盖。
    if zone_hold or target_above or active_above:
        if not zone_hold and latest_below_after_above and not target_above:
            return "limited", "limit_capacity", "moderate"
        return "hold", "hold", "high"

    sufficient_count = len(target_below) >= min_samples
    post_rain_ok = post_rain.get("satisfied", True)
    hard_gap_codes = {
        "no_sample",
        "spatial_coverage_missing",
        "pending_result",
        "preservation_failed",
        "no_effective_sample",
        "planned_sample_missing",
        "insufficient_valid_sample_count",
        "missing_post_rain_sample",
    }
    has_hard_gap = any(g["code"] in hard_gap_codes for g in gaps)

    if sufficient_count and post_rain_ok and not has_hard_gap and not contextual_hazard:
        return "open", "continue_open", "low"

    # 有明确低值但数量/时效/部分指标不足：限流并补采，而不是宣称安全。
    has_some_below = bool(target_below)
    supplement_confirmed = bool(supplement_evaluation and supplement_evaluation.get("can_confirm"))
    supplement_blocked = bool(
        supplement_evaluation
        and (
            supplement_evaluation.get("blocking_activity_conflicts")
            or supplement_evaluation.get("resource_conflicts")
            or supplement_evaluation.get("trip_conflicts")
        )
    )
    has_alternative_slot = bool(supplement_evaluation and supplement_evaluation.get("alternative_slots"))

    if has_some_below and not contextual_hazard:
        # 已有低值但雨后强制复核仍缺，且冲突时段没有可替代窗口：不能安全支持周末开放，
        # 升级为暂缓；否则限流并安排/改期补采。
        if supplement_blocked and not has_alternative_slot and not post_rain_ok:
            return "hold", "hold", "high"
        return "limited", "limit_capacity", "moderate"

    # 没有任何明确低于阈值证据。
    if supplement_evaluation is None:
        return "resample_required", "supplement_sample", "unknown"
    if supplement_confirmed or has_alternative_slot or not supplement_blocked:
        return "resample_required", "supplement_sample", "unknown"
    return "hold", "hold", "high"


def evidence_calendar(
    *,
    entrance_id: str,
    index: RiverIndex,
    samples: list[dict[str, Any]],
    start_date: str,
    end_date: str,
    as_of: Any,
    policies: list[dict[str, Any]] | dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """按日期区分明确低于阈值、明确超标、不可用和无样本。"""
    start = parse_dt(start_date, "start_date")
    end = parse_dt(end_date, "end_date")
    if start is None or end is None:
        raise ValueError("start_date/end_date 必须是 YYYY-MM-DD 或 ISO 时间")
    # 允许 end_date 只给日期时包含当天。
    if len(end_date) == 10:
        end = end + timedelta(days=1)
    policy = select_policy(policies, as_of)
    entrance = index.entrances_by_id[entrance_id]
    days: list[dict[str, Any]] = []
    cursor = start
    while cursor < end:
        day_end = cursor + timedelta(days=1)
        day_samples = []
        for sample in samples:
            collected = parse_dt(sample.get("collected_at"), "collected_at")
            if not collected or not (cursor <= collected < day_end):
                continue
            relation = flow_relation(sample, entrance, index, as_of)
            if not relation.get("applies"):
                continue
            evaluated = evaluate_sample(sample, as_of, policy)
            day_samples.append({
                "sample_id": evaluated["sample_id"],
                "classification": evaluated["classification"],
                "is_effective": evaluated["is_effective"],
                "relation": relation["relation"],
            })
        if not day_samples:
            state = "no_sample"
            label = "无采样（不得视为安全）"
        elif any(x["classification"] == "above_threshold" and x["is_effective"] for x in day_samples):
            state = "explicit_above_threshold"
            label = "明确达到/超过阈值"
        elif any(x["classification"] == "below_threshold" and x["is_effective"] for x in day_samples):
            state = "explicit_below_threshold"
            label = "明确低于阈值"
        else:
            state = "sample_not_effective"
            label = "有采样但未形成有效结论"
        days.append({"date": cursor.date().isoformat(), "state": state, "label_zh": label, "samples": day_samples})
        cursor = day_end
    return days
