"""领域中可预期的错误类型。"""

from __future__ import annotations


class RiverRiskError(Exception):
    """所有本项目业务错误的基类。"""


class ContractError(RiverRiskError, ValueError):
    """输入资料缺少必要字段或字段契约不合法。"""


class NotFoundError(RiverRiskError, KeyError):
    """引用了不存在的稳定标识。"""


class ImmutableRecordError(RiverRiskError):
    """试图修改已经生效、送达、完成或归档的事实。"""


class ConflictError(RiverRiskError):
    """资源、活动或事实版本发生冲突。"""
