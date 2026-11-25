"""供脚本或外部测试使用的函数式快捷 API。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .evidence import evaluate_sample as _evaluate_sample
from .policies import select_policy
from .scheduling import evaluate_trip as _evaluate_trip
from .service import RiverRiskService, coerce_data


def build_dashboard(source: str | Path | dict[str, Any] = "data", as_of: str | None = None, target_at: str | None = None) -> dict[str, Any]:
    """加载资料目录或 bundle 字典并生成入口看板。"""
    return RiverRiskService(source).dashboard(as_of=as_of, target_at=target_at)


def analyze_bundle(bundle: dict[str, Any], as_of: str | None = None, target_at: str | None = None) -> dict[str, Any]:
    """分析 ``load_bundle()`` 风格或逻辑集合风格的字典。"""
    return build_dashboard(coerce_data(bundle), as_of=as_of, target_at=target_at)


def evaluate_entrance_status(
    data: dict[str, Any],
    entrance_id: str,
    as_of: str,
    target_at: str | None = None,
) -> dict[str, Any]:
    """评估单个入口的稳定快捷函数。"""
    return RiverRiskService(data).assess_all(as_of, target_at)[entrance_id]


def classify_sample(sample: dict[str, Any], as_of: str, policy: dict[str, Any] | None = None) -> dict[str, Any]:
    """按指定/默认策略分类单个样本。"""
    selected = policy or select_policy(None, as_of)
    return _evaluate_sample(sample, as_of, selected)


def check_conflicts(
    trip: dict[str, Any],
    resources: list[dict[str, Any]] | dict[str, Any] | None = None,
    activities: list[dict[str, Any]] | None = None,
    existing_trips: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """检查补采行程与资源/活动的冲突。"""
    return _evaluate_trip(trip, resources, activities, existing_trips)
