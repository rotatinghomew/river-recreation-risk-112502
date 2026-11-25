"""河段、入口、边界版本与水流方向。"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from .policies import canonical_fingerprint
from .timeutils import parse_dt


@dataclass
class RiverIndex:
    segments_by_id: dict[str, dict[str, Any]] = field(default_factory=dict)
    entrances_by_id: dict[str, dict[str, Any]] = field(default_factory=dict)
    segment_versions: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_records(
        cls,
        segments: list[dict[str, Any]] | None = None,
        entrances: list[dict[str, Any]] | None = None,
        segment_versions: list[dict[str, Any]] | None = None,
    ) -> "RiverIndex":
        segments = list(segments or [])
        segment_versions = list(segment_versions or [])
        segments_by_id: dict[str, dict[str, Any]] = {}
        entrances_by_id: dict[str, dict[str, Any]] = {}

        for segment in segments:
            sid = segment.get("segment_id")
            if not sid:
                raise ValueError("河段记录缺少 segment_id")
            if sid in segments_by_id:
                raise ValueError(f"河段标识重复: {sid}")
            seg = dict(segment)
            seg["entrances"] = list(seg.get("entrances") or [])
            segments_by_id[sid] = seg
            for order, eid in enumerate(seg["entrances"]):
                if eid in entrances_by_id:
                    # 详细入口记录优先；仍记录它在河段数组中的顺序。
                    entrances_by_id[eid].setdefault("listed_order", order)
                    continue
                entrances_by_id[eid] = {
                    "entrance_id": eid,
                    "segment_id": sid,
                    "listed_order": order,
                    "flow_direction": seg.get("flow_direction", "unknown"),
                    "source": "segments.json#entrances",
                }

        for raw in entrances or []:
            eid = raw.get("entrance_id") or raw.get("id")
            if not eid:
                raise ValueError("入口记录缺少 entrance_id")
            sid = raw.get("segment_id")
            if sid and sid not in segments_by_id:
                # 不因为扩展资料中的入口而伪造河段；保留引用并交由契约检查提示。
                segments_by_id[sid] = {"segment_id": sid, "entrances": [], "incomplete": True}
            entrance = dict(raw)
            entrance["entrance_id"] = eid
            if sid:
                entrance["segment_id"] = sid
                entrance.setdefault("flow_direction", segments_by_id.get(sid, {}).get("flow_direction", "unknown"))
            if eid in entrances_by_id:
                merged = {**entrances_by_id[eid], **entrance}
                entrances_by_id[eid] = merged
            else:
                entrances_by_id[eid] = entrance

        return cls(segments_by_id, entrances_by_id, segment_versions)

    def segment_snapshot(self, segment_id: str, as_of: Any) -> dict[str, Any]:
        """返回 as_of 时有效的河段边界；没有历史版本时回退当前记录。"""
        when = parse_dt(as_of, "as_of")
        candidates = []
        for version in self.segment_versions:
            if version.get("segment_id") != segment_id:
                continue
            start = parse_dt(version.get("effective_from"), "segment_version.effective_from")
            end = parse_dt(version.get("effective_to"), "segment_version.effective_to")
            if when and start and when < start:
                continue
            if when and end and when >= end:
                continue
            candidates.append(version)

        if candidates:
            candidates.sort(key=lambda v: parse_dt(v.get("effective_from")) or parse_dt("2000-01-01"))
            snapshot = dict(candidates[-1])
            snapshot["version_source"] = "segment_version"
        else:
            current = self.segments_by_id.get(segment_id)
            if current is None:
                raise KeyError(f"未知河段: {segment_id}")
            snapshot = dict(current)
            snapshot["version"] = current.get("version", "current-import")
            snapshot["version_source"] = "current-segment-record"

        snapshot["fingerprint"] = canonical_fingerprint(
            {k: v for k, v in snapshot.items() if k != "fingerprint"}
        )
        return snapshot

    def entrance_ids(self) -> list[str]:
        return sorted(self.entrances_by_id)


def flow_relation(
    sample: dict[str, Any],
    entrance: dict[str, Any],
    index: RiverIndex,
    as_of: Any,
) -> dict[str, Any]:
    """判断样本与入口的空间/水流关系。

    只有显式 covered_entrances、完整覆盖、坐标/路径或资料中保留的入口顺序时
    才做归属；绝不跨河段套用。
    """
    sid = sample.get("segment_id")
    eid = entrance.get("entrance_id")
    entrance_sid = entrance.get("segment_id")
    coverage = (sample.get("coverage") or sample.get("coverage_scope") or "").lower()
    covered = sample.get("covered_entrances") or sample.get("entrance_ids")
    if covered is not None:
        covered = list(covered)

    base = {
        "segment_id": sid,
        "entrance_id": eid,
        "coverage": coverage or None,
        "covered_entrances": covered,
        "flow_direction": entrance.get("flow_direction", "unknown"),
        "applies": False,
        "relation": "unknown",
    }

    if sid != entrance_sid:
        base["relation"] = "other_segment"
        return base

    if sample.get("entrance_id") == eid:
        return {**base, "applies": True, "relation": "direct_entrance_sample"}

    if covered and eid in covered:
        return {**base, "applies": True, "relation": "covered_entrance"}

    if coverage in {"full", "complete", "all", "segment_full"}:
        return {**base, "applies": True, "relation": "full_segment_coverage"}

    # 尝试判断采样点是否位于入口上游；污染（高值）可顺流提示下游风险。
    source_id = (
        sample.get("station_entrance_id")
        or sample.get("upstream_entrance_id")
        or sample.get("entrance_id")
    )
    if source_id is None and covered:
        source_id = covered[0]
    if source_id and source_id in index.entrances_by_id:
        source = index.entrances_by_id[source_id]
        relation = _direction_between(source, entrance, index, sid, as_of)
        if relation == "downstream":
            reach = sample.get("downstream_reach_km")
            source_pos = source.get("position_km")
            target_pos = entrance.get("position_km")
            within_reach = True
            distance = None
            if reach is not None and source_pos is not None and target_pos is not None:
                distance = float(target_pos) - float(source_pos)
                within_reach = 0 <= distance <= float(reach)
            if within_reach:
                return {
                    **base,
                    "applies": True,
                    "relation": "adverse_downstream_flow",
                    "source_entrance_id": source_id,
                    "distance_km": distance,
                    "propagation_limit": "above_threshold_only",
                }

    if covered is not None:
        base["relation"] = "partial_coverage_other_entrance"
    elif coverage == "partial":
        base["relation"] = "partial_segment_scope_not_attributable"
    elif sid:
        # 同一分区但没有空间覆盖字段：只能作为河段线索，结论中保留警告。
        base.update(applies=True, relation="same_segment_scope_unspecified")
    else:
        base["relation"] = "missing_spatial_scope"
    return base


def active_zones_for_entrance(
    zones: list[dict[str, Any]] | None,
    entrance: dict[str, Any],
    as_of: Any,
) -> list[dict[str, Any]]:
    """返回 as_of 时覆盖入口的分区风险范围。"""
    when = parse_dt(as_of, "as_of")
    eid = entrance.get("entrance_id")
    sid = entrance.get("segment_id")
    result = []
    for raw in zones or []:
        start = parse_dt(raw.get("effective_from"), "risk_zone.effective_from")
        end = parse_dt(raw.get("effective_to"), "risk_zone.effective_to")
        if when and start and when < start:
            continue
        if when and end and when >= end:
            continue
        entrance_ids = set(raw.get("entrance_ids") or [])
        applies = eid in entrance_ids
        if not applies and raw.get("segment_id") == sid and raw.get("scope", "segment") in {"segment", "full", "all"}:
            applies = True
        if applies:
            result.append(copy.deepcopy(raw))
    return result


def _direction_between(
    source: dict[str, Any],
    target: dict[str, Any],
    index: RiverIndex,
    segment_id: str | None,
    as_of: Any,
) -> str | None:
    direction = source.get("flow_direction") or target.get("flow_direction")
    source_pos = source.get("position_km")
    target_pos = target.get("position_km")
    if source_pos is not None and target_pos is not None:
        if direction == "downstream" and float(target_pos) > float(source_pos):
            return "downstream"
        if direction == "upstream" and float(target_pos) < float(source_pos):
            return "downstream"
        return "other"

    # 显式 flow_path 优先；否则使用河段记录中的数组顺序。数组顺序是资料保留事实。
    path = None
    if segment_id:
        snapshot = index.segment_snapshot(segment_id, as_of)
        path = snapshot.get("flow_path") or snapshot.get("entrance_order") or snapshot.get("entrances")
    if path and source.get("entrance_id") in path and target.get("entrance_id") in path:
        si = path.index(source["entrance_id"])
        ti = path.index(target["entrance_id"])
        if direction == "downstream" and ti > si:
            return "downstream"
        if direction == "upstream" and ti < si:
            return "downstream"
    return None
