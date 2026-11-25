"""资料包加载与可选实体契约。

基线资料只要求六个文件；任务描述中的志愿者、反馈、资源、策略版本等可以作为
可选 JSON 文件提供。缺失集合按空集合处理，而不是伪造记录。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .input_contract import REQUIRED_FILES, load_bundle

OPTIONAL_FILES: dict[str, str] = {
    "entrances": "entrances.json",
    "sampling_plans": "sampling_plans.json",
    "trips": "trips.json",
    "policies": "policies.json",
    "segment_versions": "segment_versions.json",
    "risk_zones": "risk_zones.json",
    "resources": "resources.json",
    "volunteer_observations": "volunteer_observations.json",
    "visitor_feedback": "visitor_feedback.json",
    "outcomes": "outcomes.json",
    "later_observations": "later_observations.json",
    "decision_context": "decision_context.json",
    "arrangements": "arrangements.json",
    "delivery_events": "delivery_events.json",
    "activity_events": "activity_events.json",
    "decisions": "decisions.json",
}


def load_workspace(root: str | Path) -> dict[str, Any]:
    """加载资料包并把文件名映射为逻辑集合。"""
    base = Path(root)
    raw = load_bundle(base)
    data: dict[str, Any] = {
        "root": str(base),
        "source_info": raw["source_info.json"],
        "segments": raw["segments.json"],
        "samples": raw["samples.json"],
        "weather": raw["weather.json"],
        "activities": raw["activities.json"],
        "notices": raw["notices.json"],
        "_raw_files": raw,
    }

    for logical, filename in OPTIONAL_FILES.items():
        path = base / filename
        if path.is_file():
            with path.open(encoding="utf-8") as handle:
                data[logical] = json.load(handle)
        else:
            data[logical] = [] if logical != "decision_context" else {}

    validate_contract(data)
    return data


def validate_contract(data: dict[str, Any]) -> None:
    """执行不会静默补值的最小引用契约检查。"""
    if not isinstance(data.get("segments"), list):
        raise ValueError("segments 必须是列表")
    if not isinstance(data.get("samples"), list):
        raise ValueError("samples 必须是列表")

    segment_ids = {row.get("segment_id") for row in data["segments"] if isinstance(row, dict)}
    entrance_ids: set[str] = set()
    for segment in data["segments"]:
        for eid in segment.get("entrances") or []:
            if eid in entrance_ids:
                raise ValueError(f"入口在河段边界中重复出现: {eid}")
            entrance_ids.add(eid)

    for entrance in data.get("entrances") or []:
        eid = entrance.get("entrance_id") or entrance.get("id")
        if not eid:
            raise ValueError("entrances 记录缺少 entrance_id")
        if eid in entrance_ids:
            # 允许 segments 派生入口与 entrances.json 详细资料合并。
            continue
        entrance_ids.add(eid)
        sid = entrance.get("segment_id")
        if sid and sid not in segment_ids:
            raise ValueError(f"入口 {eid} 引用了不存在的 segment_id {sid}")

    sample_ids: set[str] = set()
    for sample in data["samples"]:
        sid = sample.get("sample_id")
        if not sid:
            raise ValueError("sample 缺少 sample_id")
        if sid in sample_ids:
            raise ValueError(f"sample_id 重复: {sid}")
        sample_ids.add(sid)
        if sample.get("segment_id") not in segment_ids:
            raise ValueError(f"样本 {sid} 引用了不存在的 segment_id")
        for eid in sample.get("covered_entrances") or []:
            if entrance_ids and eid not in entrance_ids:
                raise ValueError(f"样本 {sid} 覆盖了不存在的入口 {eid}")

    for activity in data.get("activities") or []:
        aid = activity.get("activity_id") or activity.get("id")
        if not aid:
            raise ValueError("activity 缺少 activity_id/id")
        eid = activity.get("entrance_id")
        if entrance_ids and eid not in entrance_ids:
            raise ValueError(f"活动 {aid} 引用了不存在的入口 {eid}")

    for notice in data.get("notices") or []:
        nid = notice.get("notice_id") or notice.get("id")
        if not nid:
            raise ValueError("notice 缺少 notice_id/id")
        eid = notice.get("entrance_id")
        if entrance_ids and eid not in entrance_ids:
            raise ValueError(f"公告 {nid} 引用了不存在的入口 {eid}")
