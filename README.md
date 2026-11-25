# 河道开放风险证据与公告排期

一个仅依赖 Python 标准库的可复现领域原型：把固定站采样、志愿者观察、游客反馈、降雨水位、采样资源、公告版本和已售活动放在同一条可追溯证据链上，输出每个入口的当前状态、支持样本、缺口、排期冲突和运营处理事项。

## 核心原则

- **无样本不等于安全**：`explicit_below_threshold` 与 `no_sample`、`pending_or_processing`、`partial_indicators`、`preservation_failed`、`stale` 分开。
- **完整样本才可计数**：采集、接收、可用时间、保存条件、指标完整性和新鲜度共同决定样本是否有效。
- **不把整条河套给每个入口**：保留河段、入口、覆盖入口、覆盖范围和水流方向；只在完整覆盖、显式覆盖或有方向依据时归属/传播。
- **游客反馈和志愿者观察不替代化验**：它们分别表示接触暴露或现场背景。
- **历史可复查**：策略阈值和河段边界均按时间选择版本；决策快照冻结策略/河段指纹与完整依据。
- **已执行事实不可抹掉**：已发送公告、已完成/已退款活动只能追加事件或发布新版本；新化验结果只能自动改写未确认安排。
- **冲突可见**：补采船/人员窗口与已售、已确认活动重叠时，返回重叠时长、空间关系以及“为何不能升级为开放”的风险影响。
- **事后回看**：后续观测到达后，可判定 `correct_open`、`false_safe_or_missed_hazard`、`correct_protective`、`possibly_over_cautious` 或 `unverifiable`。

## 快速运行

无需安装第三方依赖，Python 3.11+：

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q src tests app.py

# 终端中文看板（默认读取 data/decision_context.json 的 as_of/target_at）
PYTHONPATH=src python3 -m river_recreation_risk --data data --format text

# JSON
PYTHONPATH=src python3 -m river_recreation_risk --data data --format json

# 静态 HTML 文件
PYTHONPATH=src python3 -m river_recreation_risk --data data --format html --output dashboard.html

# 交互式 HTTP 看板和 JSON API
PYTHONPATH=src python3 -m river_recreation_risk.web --data data --host 127.0.0.1 --port 8000
# 或 python app.py --port 8000
```

打开 <http://127.0.0.1:8000/>。页面展示入口卡片、样本/缺口、冲突、公告送达、活动处理和开放安排，并提供记录送达、游客通知、退款/取消和完成活动的操作按钮。

证据日历：

```bash
PYTHONPATH=src python3 -m river_recreation_risk \
  --calendar-entrance E-03 \
  --calendar-start 2026-08-14 \
  --calendar-end 2026-08-17
```

事后回看已冻结决策：

```bash
PYTHONPATH=src python3 -m river_recreation_risk \
  --review-decision dec-2026-e01-weekend
```

## 主要 API

```python
from river_recreation_risk import RiverRiskService

service = RiverRiskService("data")
dashboard = service.dashboard()                 # 可传 as_of="2026-08-15T12:00"

for entrance in dashboard["entrances"]:
    print(
        entrance["entrance_id"],
        entrance["current_status"],
        entrance["recommended_action"],
        [s["sample_id"] for s in entrance["supporting_samples"]],
        [g["code"] for g in entrance["gaps"]],
    )

# 新化验结果：刷新未确认安排；已确认安排只产生 risk_change_proposals
result = service.receive_lab_result({
    "sample_id": "w-002",
    "received_at": "2026-08-15T13:10",
    "available_at": "2026-08-15T14:00",
    "quality": "valid",
    "risk_value": 0.31,
})

# 公告送达和活动处理均为追加事件
service.ledger.record_notice_event("n-001", "delivered", at="2026-08-15T10:00")
service.ledger.add_activity_handling("a-002", "notify_participants", state="done")
service.ledger.transition_activity("a-002", "refunded", note="风险建议触发改期/退款")

# 冻结决策，之后用后续观测回看
decision = service.record_decision("E-03", decision_id="dec-e03")
review = service.review("dec-e03")
```

HTTP 端点：

- `GET /api/dashboard?as_of=...`
- `GET /api/entrances/E-03`
- `GET /api/calendar?entrance_id=E-03&start=2026-08-14&end=2026-08-17`
- `POST /api/lab-results`
- `POST /api/refresh-arrangements`
- `POST /api/notices/{notice_id}/events`
- `POST /api/activities/{activity_id}/transition`
- `POST /api/activities/{activity_id}/handling`

## 决策口径

稳定动作码为：

- `continue_open`：有足够数量、在目标时刻仍新鲜且明确低于阈值的完整样本；雨后强制复核已满足；无高风险分区管控。
- `limit_capacity`：有部分低值证据，但样本数、新鲜度、指标完整性、雨后复核或现场观察不足。
- `supplement_sample`：缺少明确低于阈值证据，但补采船/人员可在目标前确认落实。
- `hold`：明确达到/超过阈值、位于生效高风险分区，或关键证据缺口无法及时补采/存在必须处置的冲突。

中文标签见 `assessment.STATUS_LABELS_ZH` 和 `assessment.ACTION_LABELS_ZH`。阈值、样本新鲜度、最低样本数、降雨警报和雨后时限均在 `policies.json` 中版本化；未提供策略时使用显式标注为 `builtin-default` 的基线。

## 代码结构

- `input_contract.py` / `repository.py`：原始资料读取、可选集合加载和最小引用契约。
- `policies.py`：策略/阈值版本选择与指纹。
- `topology.py`：河段、入口、边界版本、风险分区和水流关系。
- `evidence.py`：样本成熟度、保存/指标/新鲜度、阈值分类、降雨上下文。
- `observations.py`：志愿者观察和游客反馈的证据边界。
- `assessment.py`：入口状态、证据日历和开放/限流/补采/暂缓建议。
- `scheduling.py`：船、人员、采样任务、活动时间冲突和候选时段。
- `ledger.py`：公告、活动、开放安排的追加式状态台账。
- `history.py`：决策冻结与事后回看判定。
- `service.py`：应用服务和用例编排。
- `reporting.py` / `cli.py` / `web.py`：文本、JSON、HTML 看板和 HTTP API。

字段和可选文件说明见 [`docs/data-dictionary.md`](docs/data-dictionary.md)。
