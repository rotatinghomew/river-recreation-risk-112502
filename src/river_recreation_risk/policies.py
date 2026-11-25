"""阈值、样本充分性和雨后规则的版本化策略。"""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Iterable

from .timeutils import parse_dt

# 没有显式策略文件时使用的内置基线。它会在结论中标注 source=builtin-default，
# 避免分析员误以为资料包本身已经给定了法定阈值。
DEFAULT_POLICY: dict[str, Any] = {
    "policy_id": "builtin",
    "version": "risk-threshold-default-v1",
    "effective_from": "2000-01-01T00:00",
    "effective_to": None,
    "indicator": "risk_value",
    # 严格“低于阈值”才算明确安全；等于阈值按达到阈值处理。
    "safe_when": "value < threshold",
    "threshold": 0.5,
    "freshness_hours": 72,
    "min_valid_samples": 1,
    "limited_capacity_fraction": 0.5,
    "required_indicators": ["risk_value"],
    "max_receipt_hours": 24,
    "rain_alert_mm": 20,
    "rain_window_hours": 24,
    "require_post_rain_sample": True,
    # 高风险降雨后，距活动/决策目标多久内的样本才算有效复核。
    "post_rain_sample_hours": 48,
    "exceedance_memory_hours": 168,
}


def canonical_fingerprint(record: dict[str, Any]) -> str:
    """生成稳定指纹，供历史建议冻结当时依据。"""
    raw = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def normalize_policy(policy: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(DEFAULT_POLICY)
    merged.update(policy)
    merged.setdefault("fingerprint", canonical_fingerprint({
        k: v for k, v in merged.items() if k != "fingerprint"
    }))
    return merged


def select_policy(
    policies: dict[str, Any] | list[dict[str, Any]] | None,
    as_of: Any,
    *,
    indicator: str | None = None,
) -> dict[str, Any]:
    """选择 as_of 当日有效的阈值版本。

    支持单个策略对象、策略列表，或 ``{"versions": [...]}`` 包装。
    """
    if policies is None:
        selected = normalize_policy({})
        selected["source"] = "builtin-default"
        return selected

    if isinstance(policies, dict) and "versions" in policies:
        rows = list(policies["versions"])
    elif isinstance(policies, dict):
        rows = [policies]
    else:
        rows = list(policies)

    when = parse_dt(as_of, "as_of")
    if when is None:
        raise ValueError("as_of 不能为空")

    candidates: list[dict[str, Any]] = []
    for raw in rows:
        policy = normalize_policy(raw)
        if indicator and policy.get("indicator") not in (None, indicator):
            continue
        start = parse_dt(policy.get("effective_from"), "policy.effective_from")
        end = parse_dt(policy.get("effective_to"), "policy.effective_to")
        if start is not None and when < start:
            continue
        if end is not None and when >= end:
            continue
        candidates.append(policy)

    if not candidates:
        selected = normalize_policy({})
        selected["source"] = "builtin-default-no-effective-policy"
        return selected

    candidates.sort(key=lambda p: parse_dt(p.get("effective_from")) or parse_dt("2000-01-01"))
    selected = copy.deepcopy(candidates[-1])
    selected["source"] = selected.get("source", "policy-record")
    return selected


def freeze_policy(policy: dict[str, Any]) -> dict[str, Any]:
    frozen = copy.deepcopy(policy)
    frozen["fingerprint"] = policy.get("fingerprint") or canonical_fingerprint(
        {k: v for k, v in frozen.items() if k != "fingerprint"}
    )
    return frozen
