"""公告、活动和开放安排的追加式状态追踪。"""

from __future__ import annotations

import copy
from datetime import datetime
from typing import Any
from uuid import uuid4

from .errors import ImmutableRecordError, NotFoundError
from .timeutils import parse_dt

NOTICE_EXECUTED_STATUSES = {"sent", "delivered", "published", "read"}
ACTIVITY_TERMINAL_STATUSES = {"completed", "cancelled", "refunded"}
UNCONFIRMED_ARRANGEMENT_STATUSES = {"draft", "proposed", "pending", "unconfirmed"}
CONFIRMED_ARRANGEMENT_STATUSES = {"confirmed", "scheduled", "published"}

ALLOWED_ACTIVITY_TRANSITIONS = {
    "sold": {"confirmed", "cancelled", "refunded", "rescheduled", "completed", "handling"},
    "confirmed": {"in_progress", "completed", "cancelled", "refunded", "rescheduled", "handling"},
    "rescheduled": {"confirmed", "cancelled", "refunded", "completed", "handling"},
    "in_progress": {"completed", "handling"},
    "handling": {"confirmed", "cancelled", "refunded", "rescheduled", "completed", "handling"},
    "completed": set(),
    "cancelled": set(),
    "refunded": set(),
}


def _id_of(row: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        if row.get(key):
            return str(row[key])
    return None


class OperationsLedger:
    """内存追加式台账；可通过 ``snapshot()`` 序列化。"""

    def __init__(
        self,
        notices: list[dict[str, Any]] | None = None,
        activities: list[dict[str, Any]] | None = None,
        delivery_events: list[dict[str, Any]] | None = None,
        activity_events: list[dict[str, Any]] | None = None,
        arrangements: list[dict[str, Any]] | dict[str, Any] | None = None,
    ) -> None:
        self.notices: dict[str, dict[str, Any]] = {}
        self.notice_events: list[dict[str, Any]] = []
        self.activities: dict[str, dict[str, Any]] = {}
        self.activity_events: list[dict[str, Any]] = []
        self.arrangements: dict[str, dict[str, Any]] = {}
        self.audit_events: list[dict[str, Any]] = []

        for notice in notices or []:
            self._ingest_notice(notice)
        for activity in activities or []:
            self._ingest_activity(activity)
        for event in delivery_events or []:
            # 初始事件直接追加，避免重复改变原始状态。
            self.notice_events.append(copy.deepcopy(event))
        for event in activity_events or []:
            self.activity_events.append(copy.deepcopy(event))
        if isinstance(arrangements, dict):
            arrangements = list(arrangements.values())
        for arrangement in arrangements or []:
            aid = _id_of(arrangement, "arrangement_id", "id")
            if aid:
                record = copy.deepcopy(arrangement)
                record["arrangement_id"] = aid
                self.arrangements[aid] = record

    def _ingest_notice(self, notice: dict[str, Any]) -> None:
        nid = _id_of(notice, "notice_id", "id")
        if not nid:
            raise ValueError("公告缺少 notice_id")
        record = copy.deepcopy(notice)
        record["notice_id"] = nid
        self.notices[nid] = record
        if record.get("status") in NOTICE_EXECUTED_STATUSES:
            self.notice_events.append({
                "event_id": f"evt-{uuid4().hex[:10]}",
                "notice_id": nid,
                "event_type": record["status"],
                "at": record.get("issued_at") or record.get("sent_at"),
                "source": "initial_record",
            })

    def _ingest_activity(self, activity: dict[str, Any]) -> None:
        aid = _id_of(activity, "activity_id", "id")
        if not aid:
            raise ValueError("活动缺少 activity_id/id")
        record = copy.deepcopy(activity)
        record["activity_id"] = aid
        self.activities[aid] = record

    def _audit(self, action: str, target_type: str, target_id: str, detail: dict[str, Any] | None = None) -> None:
        self.audit_events.append({
            "event_id": f"audit-{uuid4().hex[:10]}",
            "at": datetime.now().isoformat(timespec="seconds"),
            "action": action,
            "target_type": target_type,
            "target_id": target_id,
            "detail": detail or {},
        })

    # ---- 公告 ---------------------------------------------------------
    def add_notice(self, notice: dict[str, Any]) -> dict[str, Any]:
        nid = _id_of(notice, "notice_id", "id") or f"n-{uuid4().hex[:8]}"
        if nid in self.notices:
            raise ImmutableRecordError(f"公告 {nid} 已存在；已发布公告只能追加新版本")
        record = copy.deepcopy(notice)
        record["notice_id"] = nid
        self.notices[nid] = record
        self._audit("notice_added", "notice", nid, {"version": record.get("version")})
        return copy.deepcopy(record)

    def issue_revision(self, previous_notice_id: str, new_notice: dict[str, Any]) -> dict[str, Any]:
        previous = self.notices.get(previous_notice_id)
        if previous is None:
            raise NotFoundError(previous_notice_id)
        nid = _id_of(new_notice, "notice_id", "id") or f"n-{uuid4().hex[:8]}"
        if nid in self.notices:
            raise ImmutableRecordError(f"公告 {nid} 已存在，不能覆盖")
        record = copy.deepcopy(new_notice)
        record["notice_id"] = nid
        record["replaces_notice_id"] = previous_notice_id
        record.setdefault("entrance_id", previous.get("entrance_id"))
        self.notices[nid] = record
        self._audit("notice_revision_issued", "notice", nid, {"replaces": previous_notice_id})
        return copy.deepcopy(record)

    def record_notice_event(self, notice_id: str, event_type: str, at: Any = None, detail: dict[str, Any] | None = None) -> dict[str, Any]:
        if notice_id not in self.notices:
            raise NotFoundError(notice_id)
        event = {
            "event_id": f"nevt-{uuid4().hex[:10]}",
            "notice_id": notice_id,
            "event_type": event_type,
            "at": at or datetime.now().isoformat(timespec="minutes"),
            "detail": detail or {},
        }
        parse_dt(event["at"], "event.at")
        self.notice_events.append(event)
        # 读模型更新；原始公告字段和事件均保留。
        if event_type in {"delivered", "failed", "read", "bounced"}:
            self.notices[notice_id]["delivery_status"] = event_type
        elif event_type in NOTICE_EXECUTED_STATUSES:
            self.notices[notice_id]["status"] = event_type
        self._audit("notice_event", "notice", notice_id, {"event_type": event_type})
        return copy.deepcopy(event)

    def update_notice_draft(self, notice_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        notice = self.notices.get(notice_id)
        if notice is None:
            raise NotFoundError(notice_id)
        if notice.get("status") in NOTICE_EXECUTED_STATUSES:
            raise ImmutableRecordError("已发送/送达公告不能被改写；请发布替代版本")
        notice.update(copy.deepcopy(changes))
        self._audit("notice_draft_updated", "notice", notice_id, {"fields": sorted(changes)})
        return copy.deepcopy(notice)

    def notice_tracking(self) -> list[dict[str, Any]]:
        events_by_notice: dict[str, list[dict[str, Any]]] = {}
        for event in self.notice_events:
            events_by_notice.setdefault(event["notice_id"], []).append(copy.deepcopy(event))
        result = []
        for nid, notice in sorted(self.notices.items()):
            events = sorted(events_by_notice.get(nid, []), key=lambda e: e.get("at") or "")
            latest = events[-1]["event_type"] if events else notice.get("status")
            result.append({
                **copy.deepcopy(notice),
                "current_delivery_state": notice.get("delivery_status") or latest or "unknown",
                "events": events,
            })
        return result

    # ---- 活动 ---------------------------------------------------------
    def transition_activity(self, activity_id: str, new_status: str, at: Any = None, note: str | None = None) -> dict[str, Any]:
        activity = self.activities.get(activity_id)
        if activity is None:
            raise NotFoundError(activity_id)
        old_status = activity.get("status")
        if old_status in ACTIVITY_TERMINAL_STATUSES and new_status != old_status:
            raise ImmutableRecordError(f"活动 {activity_id} 已为终态 {old_status}，不能抹掉或改写")
        allowed = ALLOWED_ACTIVITY_TRANSITIONS.get(old_status or "", set())
        if new_status not in allowed and new_status != old_status:
            raise ImmutableRecordError(f"活动状态不能从 {old_status} 变为 {new_status}")
        activity["status"] = new_status
        event = {
            "event_id": f"aevt-{uuid4().hex[:10]}",
            "activity_id": activity_id,
            "from_status": old_status,
            "to_status": new_status,
            "at": at or datetime.now().isoformat(timespec="minutes"),
            "note": note,
        }
        self.activity_events.append(event)
        self._audit("activity_transition", "activity", activity_id, {"from": old_status, "to": new_status})
        return copy.deepcopy(event)

    def add_activity_handling(self, activity_id: str, action: str, state: str = "pending", note: str | None = None) -> dict[str, Any]:
        if activity_id not in self.activities:
            raise NotFoundError(activity_id)
        event = {
            "event_id": f"ahandle-{uuid4().hex[:10]}",
            "activity_id": activity_id,
            "action": action,
            "state": state,
            "note": note,
            "at": datetime.now().isoformat(timespec="minutes"),
        }
        self.activity_events.append(event)
        self._audit("activity_handling", "activity", activity_id, {"action": action, "state": state})
        return copy.deepcopy(event)

    def activity_tracking(self) -> list[dict[str, Any]]:
        events_by_activity: dict[str, list[dict[str, Any]]] = {}
        for event in self.activity_events:
            events_by_activity.setdefault(event["activity_id"], []).append(copy.deepcopy(event))
        result = []
        for aid, activity in sorted(self.activities.items()):
            result.append({
                **copy.deepcopy(activity),
                "immutable_completed_fact": activity.get("status") == "completed",
                "events": sorted(events_by_activity.get(aid, []), key=lambda e: e.get("at") or ""),
            })
        return result

    # ---- 开放安排 -----------------------------------------------------
    def upsert_arrangement(self, arrangement: dict[str, Any]) -> dict[str, Any]:
        aid = _id_of(arrangement, "arrangement_id", "id") or f"arr-{uuid4().hex[:8]}"
        record = copy.deepcopy(arrangement)
        record["arrangement_id"] = aid
        existing = self.arrangements.get(aid)
        if existing and existing.get("status") in CONFIRMED_ARRANGEMENT_STATUSES:
            raise ImmutableRecordError("已确认开放安排不能被新结果静默覆盖；应生成风险变更/活动处置")
        if existing:
            record.setdefault("revisions", existing.get("revisions", []) + [{
                "at": datetime.now().isoformat(timespec="minutes"),
                "previous_recommendation": existing.get("recommended_action"),
                "new_recommendation": record.get("recommended_action"),
                "reason": record.get("change_reason", "evidence_refresh"),
            }])
        self.arrangements[aid] = record
        self._audit("arrangement_upserted", "arrangement", aid, {"status": record.get("status")})
        return copy.deepcopy(record)

    def refresh_unconfirmed_arrangements(self, entrance_assessments: dict[str, dict[str, Any]], at: Any = None) -> dict[str, Any]:
        changed, blocked = [], []
        for aid, arrangement in list(self.arrangements.items()):
            eid = arrangement.get("entrance_id")
            assessment = entrance_assessments.get(eid)
            if assessment is None:
                continue
            new_action = assessment.get("recommended_action")
            if arrangement.get("status") in UNCONFIRMED_ARRANGEMENT_STATUSES:
                old_action = arrangement.get("recommended_action")
                arrangement["recommended_action"] = new_action
                arrangement["current_status"] = assessment.get("current_status")
                arrangement["basis_fingerprints"] = assessment.get("basis_fingerprints")
                arrangement.setdefault("revisions", []).append({
                    "at": at or datetime.now().isoformat(timespec="minutes"),
                    "previous_recommendation": old_action,
                    "new_recommendation": new_action,
                    "reason": "new_lab_result_or_evidence_refresh",
                })
                changed.append(aid)
            else:
                # 已确认安排/已执行公告不能被抹掉；只产生必须处理的变更建议。
                arrangement.setdefault("risk_change_proposals", []).append({
                    "at": at or datetime.now().isoformat(timespec="minutes"),
                    "proposed_action": new_action,
                    "current_status": assessment.get("current_status"),
                    "basis_fingerprints": assessment.get("basis_fingerprints"),
                    "requires_activity_and_notice_handling": True,
                })
                blocked.append(aid)
        return {"updated_unconfirmed": changed, "confirmed_requiring_handling": blocked}

    def snapshot(self) -> dict[str, Any]:
        return {
            "notices": self.notice_tracking(),
            "activities": self.activity_tracking(),
            "arrangements": copy.deepcopy(self.arrangements),
            "audit_events": copy.deepcopy(self.audit_events),
        }
