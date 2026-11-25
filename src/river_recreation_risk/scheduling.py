"""采样船、人员时段以及与已确认活动的冲突检测。"""

from __future__ import annotations

from typing import Any

from .timeutils import closed_interval_overlap, overlap_minutes, parse_dt

ACTIVE_ACTIVITY_STATUSES = {"sold", "confirmed", "in_progress"}
BLOCKING_TRIP_STATUSES = {"planned", "scheduled", "confirmed", "in_progress"}


def _interval(record: dict[str, Any], start_keys: tuple[str, ...], end_keys: tuple[str, ...]):
    start = None
    end = None
    for key in start_keys:
        if record.get(key):
            start = parse_dt(record[key], key)
            break
    for key in end_keys:
        if record.get(key):
            end = parse_dt(record[key], key)
            break
    if end is None and start is not None and record.get("duration_hours") is not None:
        from datetime import timedelta

        end = start + timedelta(hours=float(record["duration_hours"]))
    return start, end


def normalize_resources(resources: dict[str, Any] | list[dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    """支持 resources.json 为列表，或 ``{"boats": [...], "staff": [...]}``。"""
    boats: dict[str, dict[str, Any]] = {}
    staff: dict[str, dict[str, Any]] = {}
    if not resources:
        return {"boats": boats, "staff": staff}

    rows: list[dict[str, Any]] = []
    if isinstance(resources, dict):
        for key in ("boats", "vessels"):
            for row in resources.get(key, []) or []:
                rows.append({**row, "resource_type": row.get("resource_type", "boat")})
        for key in ("staff", "people", "personnel"):
            for row in resources.get(key, []) or []:
                rows.append({**row, "resource_type": row.get("resource_type", "staff")})
    else:
        rows = list(resources)

    for row in rows:
        rtype = (row.get("resource_type") or row.get("type") or "").lower()
        rid = row.get("resource_id") or row.get("boat_id") or row.get("staff_id") or row.get("id")
        if not rid:
            continue
        normalized = {**row, "resource_id": rid, "resource_type": rtype}
        if rtype in {"boat", "vessel"}:
            boats[rid] = normalized
        elif rtype in {"staff", "person", "personnel"}:
            staff[rid] = normalized
    return {"boats": boats, "staff": staff}


def resource_windows(resource: dict[str, Any]) -> list[tuple[Any, Any]]:
    raw_windows = resource.get("available_windows") or resource.get("availability") or []
    windows = []
    for raw in raw_windows:
        start, end = _interval(raw, ("start", "start_at", "starts_at", "available_from"), ("end", "end_at", "ends_at", "available_to"))
        if start and end:
            windows.append((start, end))
    if not windows:
        start, end = _interval(resource, ("available_from", "start_at", "starts_at"), ("available_to", "end_at", "ends_at"))
        if start and end:
            windows.append((start, end))
    return windows


def resource_available_at(resource: dict[str, Any], start, end) -> bool:
    windows = resource_windows(resource)
    if not windows:
        # 没有提供可用时段不等于全天可用；契约中记为未知，由调用方决定。
        return False
    return any(start >= ws and end <= we for ws, we in windows)


def trip_interval(trip: dict[str, Any]):
    return _interval(
        trip,
        ("start", "start_at", "starts_at", "planned_start", "window_start", "planned_for", "scheduled_for", "at"),
        ("end", "end_at", "ends_at", "planned_end", "window_end"),
    )


def activity_interval(activity: dict[str, Any], default_duration_hours: float = 2.0):
    row = dict(activity)
    row.setdefault("duration_hours", default_duration_hours)
    return _interval(row, ("starts_at", "start_at", "start"), ("ends_at", "end_at", "end"))


def _entrance_overlap(trip: dict[str, Any], activity: dict[str, Any]) -> bool:
    trip_entrances = set(trip.get("entrance_ids") or [])
    if trip.get("entrance_id"):
        trip_entrances.add(trip["entrance_id"])
    activity_entrance = activity.get("entrance_id")
    if activity_entrance and activity_entrance in trip_entrances:
        return True
    if trip.get("segment_id") and activity.get("segment_id") == trip.get("segment_id"):
        # 同河段但入口未显式包含时，作为潜在作业区冲突返回，spatial_relation 标明。
        return True
    # 如果补采请求没有空间信息，时间冲突仍需展示给使用者。
    return not trip_entrances and not trip.get("segment_id")


def evaluate_trip(
    trip: dict[str, Any],
    resources: dict[str, Any] | list[dict[str, Any]] | None,
    activities: list[dict[str, Any]] | None = None,
    existing_trips: list[dict[str, Any]] | None = None,
    *,
    activity_statuses: set[str] | None = None,
) -> dict[str, Any]:
    """检查一艘/多艘采样船、人员、其他采样任务和活动冲突。"""
    pool = normalize_resources(resources)
    start, end = trip_interval(trip)
    result: dict[str, Any] = {
        "trip_id": trip.get("trip_id") or trip.get("plan_id"),
        "segment_id": trip.get("segment_id"),
        "entrance_ids": trip.get("entrance_ids") or ([trip["entrance_id"]] if trip.get("entrance_id") else []),
        "start": start.isoformat(timespec="minutes") if start else None,
        "end": end.isoformat(timespec="minutes") if end else None,
        "boat_checks": [],
        "staff_checks": [],
        "resource_conflicts": [],
        "trip_conflicts": [],
        "activity_conflicts": [],
        "has_conflict": False,
        "resource_feasible": False,
        "can_confirm": False,
    }
    if start is None or end is None or end <= start:
        result["resource_conflicts"].append({"reason": "invalid_or_missing_trip_interval"})
        result["has_conflict"] = True
        return result

    requested_boat = trip.get("boat_id")
    boat_ids = [requested_boat] if requested_boat else list(pool["boats"])
    chosen_boat = None
    for bid in boat_ids:
        boat = pool["boats"].get(bid)
        check = {"resource_id": bid, "resource_type": "boat", "available": False}
        if boat is None:
            check["reason"] = "unknown_boat"
            result["resource_conflicts"].append(check)
        elif resource_available_at(boat, start, end):
            check["available"] = True
            chosen_boat = bid
        else:
            check["reason"] = "outside_available_windows"
            result["resource_conflicts"].append(check)
        result["boat_checks"].append(check)
        if chosen_boat:
            break

    requested_staff = list(trip.get("staff_ids") or [])
    chosen_staff: list[str] = []
    if requested_staff:
        for sid in requested_staff:
            person = pool["staff"].get(sid)
            check = {"resource_id": sid, "resource_type": "staff", "available": False}
            if person is None:
                check["reason"] = "unknown_staff"
                result["resource_conflicts"].append(check)
            elif resource_available_at(person, start, end):
                check["available"] = True
                chosen_staff.append(sid)
            else:
                check["reason"] = "outside_available_windows"
                result["resource_conflicts"].append(check)
            result["staff_checks"].append(check)
    else:
        # 未指定人员时，至少需要一名有可用时段的采样人员。
        for sid, person in pool["staff"].items():
            if resource_available_at(person, start, end):
                chosen_staff.append(sid)
                result["staff_checks"].append({"resource_id": sid, "resource_type": "staff", "available": True, "auto_selected": True})
                break
        if not chosen_staff:
            result["resource_conflicts"].append({"resource_type": "staff", "reason": "no_available_sampler"})

    # 与其他已排采样任务的资源时间重叠。
    for other in existing_trips or []:
        if other.get("trip_id") == trip.get("trip_id") or other.get("plan_id") == trip.get("plan_id"):
            continue
        if (other.get("status") or "scheduled").lower() not in BLOCKING_TRIP_STATUSES:
            continue
        o_start, o_end = trip_interval(other)
        if not closed_interval_overlap(start, end, o_start, o_end):
            continue
        shared_boat = chosen_boat and other.get("boat_id") == chosen_boat
        shared_staff = set(chosen_staff) & set(other.get("staff_ids") or [])
        if shared_boat or shared_staff:
            result["trip_conflicts"].append({
                "other_trip_id": other.get("trip_id") or other.get("plan_id"),
                "boat_id": other.get("boat_id"),
                "shared_staff_ids": sorted(shared_staff),
                "overlap_minutes": overlap_minutes(start, end, o_start, o_end),
            })

    statuses = activity_statuses or ACTIVE_ACTIVITY_STATUSES
    for activity in activities or []:
        if (activity.get("status") or "").lower() not in statuses:
            continue
        a_start, a_end = activity_interval(activity)
        if not closed_interval_overlap(start, end, a_start, a_end):
            continue
        same_area = _entrance_overlap(trip, activity)
        result["activity_conflicts"].append({
            "activity_id": activity.get("activity_id") or activity.get("id"),
            "entrance_id": activity.get("entrance_id"),
            "status": activity.get("status"),
            "starts_at": activity.get("starts_at"),
            "ends_at": activity.get("ends_at") or a_end.isoformat(timespec="minutes"),
            "overlap_minutes": overlap_minutes(start, end, a_start, a_end),
            "same_entrance_or_segment": same_area,
            "spatial_relation": "covered_area" if same_area else "time_overlap_only",
        })

    blocking_activity_conflicts = [c for c in result["activity_conflicts"] if c.get("same_entrance_or_segment")]
    result["blocking_activity_conflicts"] = blocking_activity_conflicts
    result["assigned_boat_id"] = chosen_boat
    result["assigned_staff_ids"] = chosen_staff
    result["resource_feasible"] = bool(chosen_boat and chosen_staff and not result["resource_conflicts"] and not result["trip_conflicts"])
    result["has_conflict"] = bool(result["resource_conflicts"] or result["trip_conflicts"] or result["activity_conflicts"])
    # 同入口/同作业河段的活动冲突需要人工处置；仅时间重叠但空间无关只作提示。
    result["can_confirm"] = result["resource_feasible"] and not blocking_activity_conflicts
    result["risk_impact"] = _risk_impact(result)
    return result


def _risk_impact(evaluation: dict[str, Any]) -> dict[str, Any]:
    blocking = evaluation.get("blocking_activity_conflicts") or []
    time_only = [c for c in evaluation.get("activity_conflicts", []) if not c.get("same_entrance_or_segment")]
    if not blocking and evaluation["can_confirm"]:
        extra = ""
        if time_only:
            ids = ", ".join(c["activity_id"] for c in time_only)
            extra = f"；与其他入口活动 {ids} 仅时间重叠，已保留提示但不阻断该入口补采"
        return {
            "changes_risk_conclusion": False,
            "blocks_upgrade_to_open": False,
            "message": "补采资源可按时落实；在新有效结果到达前，入口仍保持证据缺口而非安全。" + extra,
        }
    messages = []
    if blocking:
        ids = ", ".join(c["activity_id"] for c in blocking)
        messages.append(f"补采时段与同入口/同河段已售或已确认活动 {ids} 重叠；若不调整活动或船期，补采不能被当作已落实")
    if time_only:
        ids = ", ".join(c["activity_id"] for c in time_only)
        messages.append(f"与其他入口活动 {ids} 仅时间重叠，需日历协调但不直接改变该入口结论")
    if evaluation["resource_conflicts"] or evaluation["trip_conflicts"]:
        messages.append("采样船或人员在该窗口不可用")
    if blocking or evaluation["resource_conflicts"] or evaluation["trip_conflicts"]:
        messages.append("因此雨后证据缺口不能关闭，风险结论只能保持暂缓/限流或证据不足，不能升级为继续开放")
    return {
        "changes_risk_conclusion": bool(blocking or evaluation["resource_conflicts"] or evaluation["trip_conflicts"]),
        "blocks_upgrade_to_open": bool(blocking or evaluation["resource_conflicts"] or evaluation["trip_conflicts"]),
        "message": "；".join(messages),
    }


def suggest_slots(
    trip_request: dict[str, Any],
    resources: dict[str, Any] | list[dict[str, Any]] | None,
    activities: list[dict[str, Any]] | None,
    *,
    search_start: Any,
    search_end: Any,
    duration_hours: float = 2.0,
    slot_minutes: int = 60,
    limit: int = 3,
) -> list[dict[str, Any]]:
    """在搜索区间内给出资源可行且不与活动冲突的候选时段。"""
    from datetime import timedelta

    start_bound = parse_dt(search_start, "search_start")
    end_bound = parse_dt(search_end, "search_end")
    if start_bound is None or end_bound is None:
        return []
    candidates = []
    current = start_bound
    step = timedelta(minutes=slot_minutes)
    while current + timedelta(hours=duration_hours) <= end_bound and len(candidates) < limit:
        candidate = dict(trip_request)
        candidate.pop("trip_id", None)
        candidate["start"] = current.isoformat(timespec="minutes")
        candidate["end"] = (current + timedelta(hours=duration_hours)).isoformat(timespec="minutes")
        evaluation = evaluate_trip(candidate, resources, activities)
        if evaluation["can_confirm"]:
            candidates.append(evaluation)
        current += step
    return candidates
