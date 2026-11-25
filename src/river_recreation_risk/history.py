"""建议快照、历史依据冻结与事后回看。"""

from __future__ import annotations

import copy
from datetime import datetime
from typing import Any
from uuid import uuid4

from .policies import canonical_fingerprint
from .timeutils import parse_dt


class DecisionLedger:
    """只追加的决策/建议台账。"""

    def __init__(self, records: list[dict[str, Any]] | None = None) -> None:
        self.records: dict[str, dict[str, Any]] = {}
        for record in records or []:
            did = record.get("decision_id")
            if not did:
                raise ValueError("decision record 缺少 decision_id")
            self.records[did] = copy.deepcopy(record)

    def record_from_assessment(
        self,
        assessment: dict[str, Any],
        *,
        decision_id: str | None = None,
        decided_at: Any = None,
        note: str | None = None,
        selected_action: str | None = None,
    ) -> dict[str, Any]:
        did = decision_id or f"dec-{uuid4().hex[:10]}"
        if did in self.records:
            raise KeyError(f"决策已存在且不可覆盖: {did}")
        record = {
            "decision_id": did,
            "decided_at": decided_at or assessment.get("as_of") or datetime.now().isoformat(timespec="minutes"),
            "entrance_id": assessment["entrance_id"],
            "segment_id": assessment.get("segment_id"),
            "selected_action": selected_action or assessment.get("recommended_action"),
            "recommended_action": assessment.get("recommended_action"),
            "current_status": assessment.get("current_status"),
            "risk_level": assessment.get("risk_level"),
            "note": note,
            "basis": copy.deepcopy(assessment),
        }
        record["record_fingerprint"] = canonical_fingerprint(record)
        self.records[did] = record
        return copy.deepcopy(record)

    def get(self, decision_id: str) -> dict[str, Any]:
        return copy.deepcopy(self.records[decision_id])

    def list(self) -> list[dict[str, Any]]:
        return [copy.deepcopy(r) for _, r in sorted(self.records.items())]


def review_decision(
    decision: dict[str, Any],
    later_observations: list[dict[str, Any]],
    *,
    reviewed_at: Any = None,
    outcome_window_hours: float = 72,
) -> dict[str, Any]:
    """用后续真实观测回看原判断。

    只匹配同一入口/河段且显式关联目标时间或落在目标窗口内的观测；找不到时
    标记 unverifiable，而不是强行判定原建议正确。
    """
    basis = decision.get("basis", decision)
    entrance_id = decision.get("entrance_id") or basis.get("entrance_id")
    segment_id = decision.get("segment_id") or basis.get("segment_id")
    target_at = parse_dt(basis.get("target_at") or decision.get("decided_at"), "target_at")
    reviewed = reviewed_at or datetime.now().isoformat(timespec="minutes")

    matches = []
    for raw in later_observations or []:
        if raw.get("decision_id") == decision.get("decision_id"):
            matches.append(raw)
            continue
        if raw.get("entrance_id") not in (None, entrance_id):
            continue
        if raw.get("entrance_id") is None and raw.get("segment_id") != segment_id:
            continue
        observed = parse_dt(raw.get("observed_at") or raw.get("outcome_at"), "observed_at")
        if raw.get("target_at") or raw.get("for_target_at"):
            linked = parse_dt(raw.get("target_at") or raw.get("for_target_at"), "target_at")
            if target_at and linked and abs((linked - target_at).total_seconds()) > 24 * 3600:
                continue
        elif observed and target_at:
            # 回看目标开放时段：允许目标前 24h 到后 outcome_window_hours。
            delta = (observed - target_at).total_seconds() / 3600
            if delta < -24 or delta > outcome_window_hours:
                continue
        matches.append(raw)

    truth = _actual_state(matches)
    original_action = decision.get("selected_action") or decision.get("recommended_action")
    verdict = _compare(original_action, truth["actual_state"])

    review = {
        "review_id": f"rev-{uuid4().hex[:10]}",
        "decision_id": decision.get("decision_id"),
        "reviewed_at": reviewed,
        "original_action": original_action,
        "original_status": decision.get("current_status"),
        "original_fingerprint": decision.get("record_fingerprint") or basis.get("policy", {}).get("fingerprint"),
        "actual_state": truth["actual_state"],
        "verdict": verdict,
        "matching_observation_ids": [m.get("observation_id") or m.get("id") for m in matches],
        "observations": copy.deepcopy(matches),
        "explanation_zh": _verdict_text(verdict, original_action, truth["actual_state"]),
    }
    return review


def _actual_state(observations: list[dict[str, Any]]) -> dict[str, Any]:
    if not observations:
        return {"actual_state": "unobserved"}
    states = []
    for obs in observations:
        explicit = obs.get("actual_state") or obs.get("outcome")
        if explicit:
            states.append(str(explicit).lower())
            continue
        value = obs.get("risk_value")
        threshold = obs.get("threshold", 0.5)
        if value is not None:
            states.append("hazard" if float(value) >= float(threshold) else "safe")
        elif obs.get("hazard_confirmed") is True:
            states.append("hazard")
        elif obs.get("safe_confirmed") is True:
            states.append("safe")
    if "hazard" in states:
        return {"actual_state": "hazard"}
    if "safe" in states:
        return {"actual_state": "safe"}
    return {"actual_state": "inconclusive"}


def _compare(action: str | None, actual: str) -> str:
    permissive = action == "continue_open"
    protective = action == "hold"
    cautious = action in {"limit_capacity", "supplement_sample"}
    if actual == "unobserved" or actual == "inconclusive":
        return "unverifiable"
    if actual == "hazard":
        if permissive:
            return "false_safe_or_missed_hazard"
        if protective:
            return "correct_protective"
        return "caution_warranted"
    if actual == "safe":
        if permissive:
            return "correct_open"
        if cautious:
            return "possibly_over_cautious"
        if protective:
            return "over_cautious_or_false_alarm"
    return "unverifiable"


def _verdict_text(verdict: str, action: str | None, actual: str) -> str:
    mapping = {
        "false_safe_or_missed_hazard": "原判断偏开放，后续真实观测确认风险；必须复盘阈值、覆盖范围和雨后样本时机。",
        "correct_protective": "后续观测确认风险，原先的暂缓决定与依据一致。",
        "caution_warranted": "后续出现风险，原先的限流/补采等谨慎处理是合理的。",
        "correct_open": "后续观测支持当时继续开放的判断。",
        "possibly_over_cautious": "后续观测安全；限流/补采可能偏保守，但仍需检查时间窗口是否完全对应。",
        "over_cautious_or_false_alarm": "后续观测安全；原先暂缓可能偏保守，应结合降雨和样本成熟度复盘。",
        "unverifiable": "缺少同一入口和目标时段的有效真实观测，不能把回看结果判定为正确或错误。",
    }
    return mapping.get(verdict, f"原动作 {action} 与实际状态 {actual} 的关系无法判定。")
