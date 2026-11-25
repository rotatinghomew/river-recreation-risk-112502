"""时间解析与区间运算。

合成资料使用 naive ISO 本地时间。若输入带时区，统一换算为 UTC 后去掉时区，
保证同一资料包内的比较口径确定。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def parse_dt(value: Any, field: str = "timestamp") -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        text = value.strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValueError(f"{field} 不是合法 ISO 时间: {value!r}") from exc
    else:
        raise TypeError(f"{field} 必须是 ISO 字符串、datetime 或 null，得到 {type(value)!r}")
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def require_dt(value: Any, field: str = "timestamp") -> datetime:
    dt = parse_dt(value, field)
    if dt is None:
        raise ValueError(f"{field} 不能为空")
    return dt


def iso(dt: datetime | None) -> str | None:
    return None if dt is None else dt.isoformat(timespec="minutes")


def closed_interval_overlap(
    start_a: datetime | None,
    end_a: datetime | None,
    start_b: datetime | None,
    end_b: datetime | None,
) -> bool:
    """两个半开/闭区间是否重叠；端点相接不算冲突。"""
    if None in (start_a, end_a, start_b, end_b):
        return False
    assert start_a is not None and end_a is not None
    assert start_b is not None and end_b is not None
    return start_a < end_b and start_b < end_a


def overlap_minutes(
    start_a: datetime,
    end_a: datetime,
    start_b: datetime,
    end_b: datetime,
) -> int:
    start = max(start_a, start_b)
    end = min(end_a, end_b)
    if end <= start:
        return 0
    return int((end - start).total_seconds() // 60)


def within_window(dt: datetime, start: datetime | None, end: datetime | None) -> bool:
    if start is not None and dt < start:
        return False
    if end is not None and dt >= end:
        return False
    return True
