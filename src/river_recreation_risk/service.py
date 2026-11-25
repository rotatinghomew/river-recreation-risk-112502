"""应用服务：装配资料、评估入口、处理新结果并追踪运营状态。"""

from __future__ import annotations

import copy
import math
from pathlib import Path
from typing import Any

from .assessment import assess_entrance, evidence_calendar
from .history import DecisionLedger, review_decision
from .ledger import OperationsLedger
from .policies import select_policy
from .repository import load_workspace
from .scheduling import evaluate_trip, suggest_slots, trip_interval
from .topology import RiverIndex


def coerce_data(source: str | Path | dict[str, Any]) -> dict[str, Any]:
    if isinstance(source, (str, Path)):
        return load_workspace(source)
    data = dict(source)
    # 兼容 input_contract.load_bundle() 返回的文件名为键的结构。
    if "segments" not in data and "segments.json" in data:
        data.update({
            "source_info": data.get("source_info.json", {}),
            "segments": data.get("segments.json", []),
            "samples": data.get("samples.json", []),
            "weather": data.get("weather.json", []),
            "activities": data.get("activities.json", []),
            "notices": data.get("notices.json", []),
        })
    data.setdefault("resources", [])
    data.setdefault("trips", [])
    data.setdefault("sampling_plans", [])
    data.setdefault("policies", [])
    data.setdefault("segment_versions", [])
    data.setdefault("risk_zones", [])
    data.setdefault("volunteer_observations", [])
    data.setdefault("visitor_feedback", [])
    data.setdefault("outcomes", [])
    data.setdefault("later_observations", [])
    data.setdefault("decision_context", {})
    data.setdefault("arrangements", [])
    data.setdefault("delivery_events", [])
    data.setdefault("activity_events", [])
    data.setdefault("decisions", [])
    return data


class RiverRiskService:
    def __init__(self, source: str | Path | dict[str, Any]):
        self.data = coerce_data(source)
        self.index = RiverIndex.from_records(
            segments=self.data.get("segments"),
            entrances=self.data.get("entrances"),
            segment_versions=self.data.get("segment_versions"),
        )
        self.samples = copy.deepcopy(self.data.get("samples") or [])
        self.ledger = OperationsLedger(
            notices=self.data.get("notices"),
            activities=self.data.get("activities"),
            delivery_events=self.data.get("delivery_events"),
            activity_events=self.data.get("activity_events"),
            arrangements=self.data.get("arrangements"),
        )
        self.decisions = DecisionLedger(self.data.get("decisions"))
        self.lab_events: list[dict[str, Any]] = []

    @classmethod
    def from_root(cls, root: str | Path) -> "RiverRiskService":
        return cls(load_workspace(root))

    def _default_as_of(self, as_of: Any) -> str:
        if as_of:
            return as_of if isinstance(as_of, str) else as_of.isoformat(timespec="minutes")
        context = self.data.get("decision_context") or {}
        if context.get("as_of"):
            return context["as_of"]
        # 确定性的资料包默认值：取最后一个样本可用时间/采集时间，而不是真实今天，
        # 以免历史合成资料在未来运行时全部因“当前时间”而失去演示意义。
        candidates = []
        for sample in self.samples:
            candidates.extend(x for x in (sample.get("available_at"), sample.get("collected_at")) if x)
        if candidates:
            return max(candidates)
        return "2026-01-01T00:00"

    def _target_for_entrance(self, entrance_id: str, target_at: Any, as_of: str) -> str | None:
        if target_at:
            return target_at if isinstance(target_at, str) else target_at.isoformat(timespec="minutes")
        context = self.data.get("decision_context") or {}
        if context.get("target_at"):
            return context["target_at"]
        starts = [
            a.get("starts_at") for a in self.ledger.activities.values()
            if a.get("entrance_id") == entrance_id and a.get("starts_at")
        ]
        return min(starts) if starts else as_of

    def evaluate_trips(self, as_of: Any = None) -> list[dict[str, Any]]:
        when = self._default_as_of(as_of)
        trips = list(self.data.get("trips") or [])
        plans = list(self.data.get("sampling_plans") or [])
        # 计划若有明确时间，也按同一冲突规则评估。
        all_trip_like = trips + plans
        context = self.data.get("decision_context") or {}
        search_end = context.get("target_at") or "2026-08-16T23:59"
        evaluations = []
        for trip in all_trip_like:
            evaluation = evaluate_trip(
                trip,
                self.data.get("resources"),
                list(self.ledger.activities.values()),
                existing_trips=all_trip_like,
            )
            start, end = trip_interval(trip)
            if not evaluation.get("can_confirm") and start and end:
                duration = (end - start).total_seconds() / 3600
                try:
                    evaluation["alternative_slots"] = suggest_slots(
                        trip,
                        self.data.get("resources"),
                        list(self.ledger.activities.values()),
                        search_start=when,
                        search_end=search_end,
                        duration_hours=duration or 2.0,
                        limit=3,
                    )
                except Exception:
                    evaluation["alternative_slots"] = []
            else:
                evaluation["alternative_slots"] = []
            evaluations.append(evaluation)
        return evaluations

    def _trip_for_entrance(self, entrance_id: str, trip_evaluations: list[dict[str, Any]]) -> dict[str, Any] | None:
        for evaluation in trip_evaluations:
            if entrance_id in (evaluation.get("entrance_ids") or []):
                return evaluation
        # 若任务只给 segment_id，且该入口属于该河段，也附加，但保留空间范围信息。
        entrance = self.index.entrances_by_id.get(entrance_id, {})
        for evaluation in trip_evaluations:
            if evaluation.get("segment_id") == entrance.get("segment_id") and not evaluation.get("entrance_ids"):
                return evaluation
        return None

    def assess_all(self, as_of: Any = None, target_at: Any = None) -> dict[str, dict[str, Any]]:
        when = self._default_as_of(as_of)
        trip_evaluations = self.evaluate_trips(when)
        assessments: dict[str, dict[str, Any]] = {}
        for entrance_id in self.index.entrance_ids():
            entrance_target = self._target_for_entrance(entrance_id, target_at, when)
            trip_eval = self._trip_for_entrance(entrance_id, trip_evaluations)
            assessments[entrance_id] = assess_entrance(
                entrance_id=entrance_id,
                index=self.index,
                samples=self.samples,
                as_of=when,
                target_at=entrance_target,
                policies=self.data.get("policies"),
                risk_zones=self.data.get("risk_zones"),
                sampling_plans=self.data.get("sampling_plans"),
                weather=self.data.get("weather"),
                volunteer_observations=self.data.get("volunteer_observations"),
                visitor_feedback=self.data.get("visitor_feedback"),
                supplement_evaluation=trip_eval,
            )
        return assessments

    def dashboard(self, as_of: Any = None, target_at: Any = None) -> dict[str, Any]:
        when = self._default_as_of(as_of)
        assessments = self.assess_all(when, target_at)
        trip_evaluations = self.evaluate_trips(when)
        notice_rows = self.ledger.notice_tracking()
        for notice in notice_rows:
            issued_at = notice.get("issued_at") or notice.get("sent_at")
            if issued_at:
                historical_policy = select_policy(self.data.get("policies"), issued_at)
                entrance = self.index.entrances_by_id.get(notice.get("entrance_id"), {})
                sid = entrance.get("segment_id")
                historical_segment = None
                if sid:
                    snapshot = self.index.segment_snapshot(sid, issued_at)
                    historical_segment = {
                        "segment_id": sid,
                        "version": snapshot.get("version"),
                        "fingerprint": snapshot.get("fingerprint"),
                        "entrances": snapshot.get("entrances"),
                        "flow_direction": snapshot.get("flow_direction"),
                    }
                notice["historical_basis_at_issue"] = {
                    "issued_at": issued_at,
                    "policy_version": historical_policy.get("version"),
                    "threshold": historical_policy.get("threshold"),
                    "policy_fingerprint": historical_policy.get("fingerprint"),
                    "segment": historical_segment,
                }
        conflict_count = sum(
            1 for t in trip_evaluations
            if t.get("activity_conflicts") or t.get("resource_conflicts") or t.get("trip_conflicts")
        )
        entrance_rows = [assessments[eid] for eid in sorted(assessments)]
        self._enrich_operational_context(entrance_rows, notice_rows)
        return {
            "source_info": self.data.get("source_info"),
            "as_of": when,
            "target_at": target_at or (self.data.get("decision_context") or {}).get("target_at"),
            "entrances": entrance_rows,
            "trip_evaluations": trip_evaluations,
            "notices": notice_rows,
            "activities": self.ledger.activity_tracking(),
            "arrangements": list(self.ledger.arrangements.values()),
            "summary": {
                "entrance_count": len(assessments),
                "hold": sum(a["recommended_action"] == "hold" for a in assessments.values()),
                "limit_capacity": sum(a["recommended_action"] == "limit_capacity" for a in assessments.values()),
                "supplement_sample": sum(a["recommended_action"] == "supplement_sample" for a in assessments.values()),
                "continue_open": sum(a["recommended_action"] == "continue_open" for a in assessments.values()),
                "trip_conflict_count": conflict_count,
                "open_activity_count": sum(
                    a.get("status") in {"sold", "confirmed"} for a in self.ledger.activities.values()
                ),
            },
        }

    def _enrich_operational_context(self, entrance_rows: list[dict[str, Any]], notice_rows: list[dict[str, Any]]) -> None:
        notices_by_entrance: dict[str, list[dict[str, Any]]] = {}
        for notice in notice_rows:
            notices_by_entrance.setdefault(notice.get("entrance_id"), []).append(notice)
        activities_by_entrance: dict[str, list[dict[str, Any]]] = {}
        for activity in self.ledger.activities.values():
            if activity.get("status") in {"sold", "confirmed", "in_progress"}:
                activities_by_entrance.setdefault(activity.get("entrance_id"), []).append(copy.deepcopy(activity))

        for entrance in entrance_rows:
            eid = entrance["entrance_id"]
            policy = select_policy(self.data.get("policies"), entrance.get("as_of") or self._default_as_of(None))
            capacity_fraction = float(policy.get("limited_capacity_fraction", 0.5))
            entrance_notices = notices_by_entrance.get(eid, [])
            executed = [
                n for n in entrance_notices
                if n.get("status") in {"sent", "delivered", "published", "read"}
                or n.get("current_delivery_state") in {"delivered", "read"}
            ]
            executed.sort(key=lambda n: n.get("issued_at") or "")
            latest_notice = executed[-1] if executed else None
            active_activities = activities_by_entrance.get(eid, [])
            entrance["published_notices"] = [
                {
                    "notice_id": n.get("notice_id"),
                    "version": n.get("version"),
                    "status": n.get("status"),
                    "delivery": n.get("current_delivery_state"),
                    "recommended_action": n.get("recommended_action") or n.get("notice_action"),
                }
                for n in entrance_notices
            ]
            activity_guidance = []
            for activity in active_activities:
                capacity = activity.get("capacity")
                if entrance.get("recommended_action") == "hold":
                    allowed_capacity = 0
                elif entrance.get("recommended_action") == "limit_capacity" and capacity is not None:
                    allowed_capacity = max(1, math.ceil(float(capacity) * capacity_fraction))
                elif capacity is not None:
                    allowed_capacity = capacity
                else:
                    allowed_capacity = None
                activity_guidance.append({
                    "activity_id": activity.get("activity_id"),
                    "status": activity.get("status"),
                    "starts_at": activity.get("starts_at"),
                    "capacity": capacity,
                    "sold_count": activity.get("sold_count"),
                    "allowed_capacity_under_advice": allowed_capacity,
                    "handling": (
                        "suspend_and_notify_refund_or_reschedule"
                        if allowed_capacity == 0
                        else "reduce_occupancy_and_notify"
                        if allowed_capacity != capacity
                        else "continue_with_standard_controls"
                    ),
                })
            entrance["active_activities"] = activity_guidance

            if entrance.get("recommended_action") == "hold" and not latest_notice:
                entrance["gaps"].append({
                    "code": "missing_hold_notice",
                    "severity": "critical",
                    "message_zh": "当前建议暂缓，但没有已发布/送达公告；需要立即发布替代或警示版本。",
                })
            if latest_notice:
                notice_action = latest_notice.get("recommended_action") or latest_notice.get("notice_action")
                if notice_action and notice_action != entrance.get("recommended_action"):
                    entrance["gaps"].append({
                        "code": "published_notice_needs_revision",
                        "severity": "critical",
                        "message_zh": (
                            f"已发布公告动作 {notice_action} 与当前建议 "
                            f"{entrance.get('recommended_action')} 不一致；旧公告和送达记录保留，应发布新版本。"
                        ),
                    })
            if entrance.get("recommended_action") in {"hold", "limit_capacity"} and active_activities:
                entrance["gaps"].append({
                    "code": "sold_activities_require_handling",
                    "severity": "critical",
                    "message_zh": (
                        f"有 {len(active_activities)} 个已售/已确认活动不能被删除；"
                        "必须通知参与者并执行限流、改期或退款。"
                    ),
                })

    def calendar(self, entrance_id: str, start_date: str, end_date: str, as_of: Any = None):
        when = self._default_as_of(as_of)
        return evidence_calendar(
            entrance_id=entrance_id,
            index=self.index,
            samples=self.samples,
            start_date=start_date,
            end_date=end_date,
            as_of=when,
            policies=self.data.get("policies"),
        )

    def receive_lab_result(self, result: dict[str, Any], *, refresh_arrangements: bool = True) -> dict[str, Any]:
        """接收新化验结果。

        对 pending 样本就地推进其读模型；原始接收事件和历史决策仍保留。
        已最终有效的值不允许被无名覆盖，必须以 correction=true 追加更正。
        """
        sample_id = result.get("sample_id")
        sample = next((s for s in self.samples if s.get("sample_id") == sample_id), None)
        if sample is None:
            raise KeyError(f"未知样本: {sample_id}")
        already_final = sample.get("quality") == "valid" and sample.get("risk_value") is not None
        if already_final and not result.get("correction"):
            raise ValueError("已有最终化验值；更正必须显式设置 correction=true 并保留原因")

        event = {"event_type": "lab_result_received", **copy.deepcopy(result)}
        self.lab_events.append(event)

        for key in ("received_at", "available_at", "quality", "risk_value", "coverage", "preservation_status", "measured_indicators"):
            if key in result and result[key] is not None:
                sample[key] = result[key]
        if result.get("indicators"):
            sample.setdefault("indicators", {}).update(copy.deepcopy(result["indicators"]))
        if result.get("correction"):
            sample.setdefault("corrections", []).append(copy.deepcopy(result))

        refresh_as_of = result.get("as_of") or result.get("available_at") or self._default_as_of(None)
        dashboard = self.dashboard(refresh_as_of)
        refresh = None
        if refresh_arrangements:
            assessments = {a["entrance_id"]: a for a in dashboard["entrances"]}
            refresh = self.ledger.refresh_unconfirmed_arrangements(assessments, at=result.get("available_at") or refresh_as_of)
        dashboard["arrangement_refresh"] = refresh
        dashboard["refreshed_as_of"] = refresh_as_of
        return {"event": event, "dashboard": dashboard, "arrangement_refresh": refresh}

    def refresh_arrangements(self, as_of: Any = None) -> dict[str, Any]:
        when = self._default_as_of(as_of)
        assessments = self.assess_all(when)
        return self.ledger.refresh_unconfirmed_arrangements(assessments, at=when)

    def record_decision(self, entrance_id: str, *, decision_id: str | None = None, note: str | None = None, selected_action: str | None = None, as_of: Any = None):
        assessment = self.assess_all(as_of)[entrance_id]
        return self.decisions.record_from_assessment(
            assessment,
            decision_id=decision_id,
            note=note,
            selected_action=selected_action,
        )

    def review(self, decision_id: str, later_observations: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        decision = self.decisions.get(decision_id)
        observations = later_observations if later_observations is not None else self.data.get("later_observations") or self.data.get("outcomes") or []
        return review_decision(decision, observations)
