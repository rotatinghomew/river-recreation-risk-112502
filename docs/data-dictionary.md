# 资料说明与字段契约

`data/` 中所有业务资料均为合成资料；`source_info.json` 必须标明 `source_kind=synthetic`。加载层不会把缺失值静默填充为“安全”或“已完成”。

## 必需文件

| 文件 | 内容 | 关键标识/时间 |
| --- | --- | --- |
| `source_info.json` | 资料包来源、schema 版本 | `source_kind` |
| `segments.json` | 河段/分区及入口列表 | `segment_id`, `entrances[]`, `flow_direction` |
| `samples.json` | 实际采样、实验室接收/可用时间、质量和化验值 | `sample_id`, `segment_id`, `collected_at`, `received_at`, `available_at` |
| `weather.json` | 降雨与水位观测 | `observed_at`, `rain_mm`, `water_level` |
| `activities.json` | 已售/已确认亲水活动 | `activity_id`, `entrance_id`, `starts_at`, `status` |
| `notices.json` | 公告版本 | `notice_id`, `entrance_id`, `version`, `issued_at`, `status` |

## 可选扩展文件

缺失的可选集合按空列表/空对象处理：

- `entrances.json`：入口详细资料，可含 `position_km`、坐标或 `segment_id`。
- `sampling_plans.json`：采样计划；有明确 `planned_start`/`planned_end` 或 `planned_for` + `duration_hours` 时参与冲突检查。
- `trips.json`：采样船/人员实际排期，字段可用 `start`/`end`、`boat_id`、`staff_ids`、`entrance_ids`。
- `resources.json`：采样船和人员及 `available_windows`。
- `policies.json`：阈值、新鲜度、最低有效样本数、雨后规则的版本。
- `segment_versions.json`：河段边界/入口集合的历史版本。
- `risk_zones.json`：分区风险范围，可按 `segment_id` 或 `entrance_ids` 生效。
- `volunteer_observations.json`：志愿者观察；只能作为现场背景。
- `visitor_feedback.json`：游客反馈；只能证明接触/暴露，不能替代化验。
- `delivery_events.json`：公告送达事件。
- `activity_events.json`：活动状态变更、游客通知、退款/改期等处理事件。
- `arrangements.json`：开放安排及其 `draft`、`unconfirmed`、`confirmed` 等状态。
- `decisions.json`：冻结的历史决策快照。
- `later_observations.json` / `outcomes.json`：事后真实观测，用于回看。
- `decision_context.json`：CLI/看板默认 `as_of` 和 `target_at`。

## 样本状态语义

样本只有同时满足以下条件才是当前完整有效样本：

1. 已有 `collected_at`，且在分析时刻之前；
2. 已 `received_at`，保存/接收时延未超过策略；
3. 已到 `available_at`，或显式 `availability_status=available`；
4. `quality` 属于 `valid/accepted/ok/usable`；
5. `preservation_status` 未表明超出保存条件，且 `hold_hours`/接收时延未超限；
6. 策略要求的指标均有值，且没有 `complete_indicators=false`、`indicator_coverage=partial` 或部分指标质量标记；
7. 未超过 `freshness_hours`。

严格语义为：

- `risk_value < threshold`：明确低于阈值；
- `risk_value >= threshold`：达到/超过阈值；
- 无样本、未成熟、部分指标、保存失败、过期：证据缺口，而不是安全。

## 空间与水流

- 样本必须通过 `segment_id` 关联河段，绝不跨河段套用。
- `covered_entrances[]` 或 `entrance_id` 表示直接覆盖的入口。
- `coverage=full/complete/all/segment_full` 可覆盖当前河段版本中的全部入口。
- `coverage=partial` 且未列出的入口不会被当作直接证明。
- 有 `position_km`、`flow_path` 或保留的入口顺序时，上游超标样本可向下游传播保守风险；低值不自动证明下游安全，也不反推上游。

## 不可变事实

- 已 `sent/delivered/published/read` 的公告不能直接覆盖；应创建新 `notice_id`/`version` 并通过 `replaces_notice_id` 关联。
- `completed/cancelled/refunded` 活动是终态事实，不能删除或改写；风险变化应追加处理事件、退款/改期记录或新活动。
- 新化验结果可以刷新 `draft/proposed/pending/unconfirmed` 安排；对 `confirmed/scheduled/published` 安排只生成风险变更提案和活动/公告处理任务。
- 决策快照包含当时的策略版本、阈值指纹和河段版本指纹，后续策略或边界变化不会改写原快照。
