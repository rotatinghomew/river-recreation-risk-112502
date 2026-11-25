"""面向终端和静态 HTML 看板的展示层。"""

from __future__ import annotations

import html
import json
from typing import Any


def render_text(dashboard: dict[str, Any]) -> str:
    lines: list[str] = []
    source = dashboard.get("source_info") or {}
    lines.append(f"河道开放风险看板｜资料: {source.get('project', 'river-recreation-risk')}")
    lines.append(f"分析依据时间: {dashboard.get('as_of')}；目标时间: {dashboard.get('target_at') or '未指定'}")
    summary = dashboard.get("summary", {})
    lines.append(
        "汇总: "
        f"继续开放 {summary.get('continue_open', 0)}，"
        f"限流 {summary.get('limit_capacity', 0)}，"
        f"补采 {summary.get('supplement_sample', 0)}，"
        f"暂缓 {summary.get('hold', 0)}，"
        f"排期冲突 {summary.get('trip_conflict_count', 0)}"
    )
    lines.append("")

    for entrance in dashboard.get("entrances", []):
        lines.append("=" * 78)
        lines.append(
            f"入口 {entrance['entrance_id']}（河段 {entrance.get('segment_id')}，"
            f"水流 {entrance.get('flow_direction')}）"
        )
        lines.append(
            f"当前状态: {entrance['status_zh']} [{entrance['current_status']}]；"
            f"建议: {entrance['action_zh']} [{entrance['recommended_action']}]；"
            f"风险: {entrance['risk_level']}"
        )
        policy = entrance.get("policy", {})
        lines.append(
            f"依据策略: {policy.get('version')}，阈值 {policy.get('threshold')}，"
            f"指纹 {policy.get('fingerprint')}"
        )
        lines.append(f"证据状态: {entrance.get('evidence_state')}")

        if entrance.get("supporting_samples"):
            lines.append("支持/相关样本:")
            for sample in entrance["supporting_samples"]:
                relation = sample.get("relation", {})
                value = sample.get("value")
                threshold = sample.get("threshold")
                value_text = "无值" if value is None else f"{value}（阈值 {threshold}）"
                lines.append(
                    f"  - {sample['sample_id']}: {sample['classification']}，成熟度={sample.get('maturity') or sample.get('stage')}，"
                    f"{value_text}，关系={relation.get('relation')}，有效={sample.get('is_effective')}"
                )
                if sample.get("reasons"):
                    lines.append(f"      原因: {', '.join(sample['reasons'])}")
        else:
            lines.append("支持样本: 无")

        excluded = [s for s in entrance.get("segment_sample_audit", []) if not s.get("applies_to_entrance")]
        if excluded:
            lines.append("同河段但未计入该入口的样本:")
            for sample in excluded:
                lines.append(
                    f"  - {sample.get('sample_id')}: {sample.get('classification')}，"
                    f"成熟度={sample.get('maturity') or sample.get('stage')}，"
                    f"空间关系={sample.get('relation', {}).get('relation')}"
                )

        if entrance.get("active_activities"):
            lines.append("活动容量建议:")
            for activity in entrance["active_activities"]:
                lines.append(
                    f"  - {activity['activity_id']} ({activity.get('status')}): "
                    f"原容量 {activity.get('capacity')}，建议允许 {activity.get('allowed_capacity_under_advice')}，"
                    f"处理={activity.get('handling')}"
                )

        if entrance.get("gaps"):
            lines.append("缺口/限制:")
            for gap in entrance["gaps"]:
                lines.append(f"  ! [{gap['severity']}] {gap['message_zh']} ({gap['code']})")
        else:
            lines.append("缺口: 无")

        weather = entrance.get("weather") or {}
        if weather:
            lines.append(
                f"降雨背景: 窗口内 {weather.get('rain_total_mm')} mm，"
                f"警报={weather.get('rain_alert')}；降雨不替代检测。"
            )
        lines.append("")

    if dashboard.get("trip_evaluations"):
        lines.append("=" * 78)
        lines.append("采样船/人员/活动排期")
        for trip in dashboard["trip_evaluations"]:
            lines.append(
                f"- {trip.get('trip_id')}: {trip.get('start')} → {trip.get('end')}，"
                f"可确认={trip.get('can_confirm')}，资源可行={trip.get('resource_feasible')}"
            )
            for conflict in trip.get("activity_conflicts", []):
                lines.append(
                    f"    活动冲突 {conflict['activity_id']} ({conflict['status']}, "
                    f"{conflict['entrance_id']})，重叠 {conflict['overlap_minutes']} 分钟"
                )
            impact = trip.get("risk_impact") or {}
            if impact.get("message"):
                lines.append(f"    风险影响: {impact['message']}")
            for slot in trip.get("alternative_slots", [])[:3]:
                lines.append(f"    可改期窗口: {slot.get('start')} → {slot.get('end')} (可确认={slot.get('can_confirm')})")
        lines.append("")

    if dashboard.get("notices"):
        lines.append("公告送达追踪")
        for notice in dashboard["notices"]:
            lines.append(
                f"- {notice['notice_id']} {notice.get('version')} @ {notice.get('entrance_id')}: "
                f"状态={notice.get('status')}，送达={notice.get('current_delivery_state')}，"
                f"事件 {len(notice.get('events', []))} 条"
            )
        lines.append("")

    if dashboard.get("activities"):
        lines.append("活动处理追踪")
        for activity in dashboard["activities"]:
            lines.append(
                f"- {activity['activity_id']} @ {activity.get('entrance_id')} "
                f"{activity.get('starts_at')}: {activity.get('status')}，"
                f"事件 {len(activity.get('events', []))} 条"
            )
    return "\n".join(lines)


def render_json(dashboard: dict[str, Any], *, indent: int = 2) -> str:
    return json.dumps(dashboard, ensure_ascii=False, indent=indent, default=str)


def render_html(dashboard: dict[str, Any]) -> str:
    """生成无外部依赖的单页看板；原始 JSON 嵌入页面供脚本/测试读取。"""
    payload = html.escape(render_json(dashboard, indent=None).replace("</", "<\\/"))
    cards = []
    for e in dashboard.get("entrances", []):
        gap_items = "".join(f"<li class='{html.escape(g['severity'])}'>{html.escape(g['message_zh'])}</li>" for g in e.get("gaps", []))
        sample_items = "".join(
            "<li data-applies='{}'><code>{}</code> {}；成熟度={}；值={}；关系={}；计入={}</li>".format(
                html.escape(str(s.get("applies_to_entrance", True))),
                html.escape(str(s.get("sample_id"))),
                html.escape(str(s.get("classification"))),
                html.escape(str(s.get("maturity") or s.get("stage"))),
                html.escape(str(s.get("value"))),
                html.escape(str(s.get("relation", {}).get("relation"))),
                html.escape(str(s.get("applies_to_entrance", True))),
            )
            for s in (e.get("segment_sample_audit") or e.get("supporting_samples", []))
        )
        cards.append(f"""
        <section class="card {html.escape(e['current_status'])}">
          <h2>{html.escape(e['entrance_id'])} <span>{html.escape(e['status_zh'])}</span></h2>
          <p><b>建议：</b>{html.escape(e['action_zh'])}（{html.escape(e['recommended_action'])}） ·
             <b>风险：</b>{html.escape(str(e['risk_level']))} ·
             <b>河段：</b>{html.escape(str(e.get('segment_id')))} ·
             <b>水流：</b>{html.escape(str(e.get('flow_direction')))}</p>
          <p><b>证据状态：</b>{html.escape(e.get('evidence_state'))}；
             <b>策略：</b>{html.escape(e.get('policy', {}).get('version', ''))} /
             阈值 {html.escape(str(e.get('policy', {}).get('threshold')))}</p>
          <h3>支持样本</h3><ul>{sample_items or '<li>无</li>'}</ul>
          <h3>缺口</h3><ul class="gaps">{gap_items or '<li class="ok">无</li>'}</ul>
        </section>
        """)

    conflicts = []
    for t in dashboard.get("trip_evaluations", []):
        for c in t.get("activity_conflicts", []):
            conflicts.append(
                f"<li><b>{html.escape(str(t.get('trip_id')))}</b> 与活动 "
                f"{html.escape(str(c.get('activity_id')))}（{html.escape(str(c.get('status')))}）"
                f"在 {html.escape(str(c.get('entrance_id')))} 重叠 "
                f"{c.get('overlap_minutes')} 分钟。"
                f"{html.escape((t.get('risk_impact') or {}).get('message', ''))}</li>"
            )

    notice_rows = []
    for n in dashboard.get("notices", []):
        basis = n.get("historical_basis_at_issue") or {}
        notice_rows.append(f"""
        <tr>
          <td><code>{html.escape(str(n.get('notice_id')))}</code><br>{html.escape(str(n.get('version', '')))}</td>
          <td>{html.escape(str(n.get('entrance_id')))}</td>
          <td>{html.escape(str(n.get('status')))} / {html.escape(str(n.get('current_delivery_state')))}</td>
          <td>{len(n.get('events', []))} 条事件<br><small>当时策略 {html.escape(str(basis.get('policy_version', '—')))} / 阈值 {html.escape(str(basis.get('threshold', '—')))}</small></td>
          <td><button data-action="deliver-notice" data-id="{html.escape(str(n.get('notice_id')))}">记录送达</button></td>
        </tr>""")

    activity_rows = []
    for a in dashboard.get("activities", []):
        activity_rows.append(f"""
        <tr>
          <td><code>{html.escape(str(a.get('activity_id')))}</code></td>
          <td>{html.escape(str(a.get('entrance_id')))}</td>
          <td>{html.escape(str(a.get('starts_at')))}</td>
          <td data-activity-status="{html.escape(str(a.get('status')))}">{html.escape(str(a.get('status')))}</td>
          <td>{len(a.get('events', []))} 条事件</td>
          <td class="actions">
            <button data-action="notify-activity" data-id="{html.escape(str(a.get('activity_id')))}">记录游客通知</button>
            <button data-action="refund-activity" data-id="{html.escape(str(a.get('activity_id')))}">取消/退款</button>
            <button data-action="complete-activity" data-id="{html.escape(str(a.get('activity_id')))}">完成</button>
          </td>
        </tr>""")

    arrangement_rows = []
    for a in dashboard.get("arrangements", []):
        arrangement_rows.append(
            "<tr><td><code>{}</code></td><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
                html.escape(str(a.get("arrangement_id"))),
                html.escape(str(a.get("entrance_id"))),
                html.escape(str(a.get("status"))),
                html.escape(str(a.get("recommended_action"))),
                len(a.get("revisions", [])) + len(a.get("risk_change_proposals", [])),
            )
        )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>河道开放风险看板</title>
<style>
body {{ font-family: system-ui, -apple-system, "Segoe UI", sans-serif; margin: 0; background: #f5f7fa; color: #17202a; }}
header {{ background: #12355b; color: white; padding: 20px 28px; }}
main {{ padding: 24px 28px; max-width: 1180px; margin: auto; }}
.summary {{ display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 20px; }}
.pill {{ background: white; border-radius: 999px; padding: 8px 14px; box-shadow: 0 1px 3px #0002; }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(360px, 1fr)); gap: 18px; }}
.card {{ background: white; border-radius: 14px; padding: 18px; box-shadow: 0 2px 10px #0001; border-top: 6px solid #7f8c8d; }}
.card.open {{ border-top-color: #1e8449; }} .card.limited {{ border-top-color: #d4ac0d; }}
.card.resample_required {{ border-top-color: #ca6f1e; }} .card.hold {{ border-top-color: #c0392b; }}
.critical {{ color: #922b21; font-weight: 600; }} .warning {{ color: #9a7d0a; }} .info {{ color: #1f618d; }} .ok {{ color: #1e8449; }}
pre {{ background: #0b1f33; color: #d6eaf8; padding: 16px; border-radius: 10px; overflow: auto; }}
section.wide {{ background: white; border-radius: 14px; padding: 18px; margin-top: 20px; }}
table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
th, td {{ border-bottom: 1px solid #e5e8ec; padding: 9px 8px; text-align: left; vertical-align: top; }}
button {{ border: 0; border-radius: 8px; background: #12355b; color: white; padding: 6px 9px; margin: 2px; cursor: pointer; }}
button:hover {{ background: #1d4e80; }}
.actions {{ white-space: nowrap; }}
</style>
</head>
<body>
<header><h1>河道开放风险证据与公告排期</h1>
<div>依据时间 {html.escape(str(dashboard.get('as_of')))}；目标 {html.escape(str(dashboard.get('target_at') or '按活动时间'))}</div></header>
<main>
<div class="summary">
  <span class="pill">入口 {dashboard.get('summary', {}).get('entrance_count')}</span>
  <span class="pill">继续开放 {dashboard.get('summary', {}).get('continue_open')}</span>
  <span class="pill">限流 {dashboard.get('summary', {}).get('limit_capacity')}</span>
  <span class="pill">补采 {dashboard.get('summary', {}).get('supplement_sample')}</span>
  <span class="pill">暂缓 {dashboard.get('summary', {}).get('hold')}</span>
  <span class="pill">排期冲突 {dashboard.get('summary', {}).get('trip_conflict_count')}</span>
</div>
<div class="grid">{''.join(cards)}</div>
<section class="wide"><h2>排期冲突及风险影响</h2><ul>{''.join(conflicts) or '<li>无活动时间冲突。</li>'}</ul></section>
<section class="wide"><h2>公告版本与送达追踪</h2><table><thead><tr><th>公告/版本</th><th>入口</th><th>状态/送达</th><th>事件与当时依据</th><th>操作</th></tr></thead><tbody>{''.join(notice_rows) or '<tr><td colspan="5">无公告</td></tr>'}</tbody></table></section>
<section class="wide"><h2>已售/已确认活动处理</h2><table><thead><tr><th>活动</th><th>入口</th><th>开始</th><th>状态</th><th>追踪</th><th>处理</th></tr></thead><tbody>{''.join(activity_rows) or '<tr><td colspan="6">无活动</td></tr>'}</tbody></table></section>
<section class="wide"><h2>开放安排版本</h2><table><thead><tr><th>安排</th><th>入口</th><th>确认状态</th><th>建议动作</th><th>修订/风险提案</th></tr></thead><tbody>{''.join(arrangement_rows) or '<tr><td colspan="5">无安排</td></tr>'}</tbody></table></section>
<section class="wide"><h2>机器可读数据</h2><script id="dashboard-json" type="application/json">{payload}</script><pre id="json-view">{html.escape(render_json(dashboard, indent=2))}</pre></section>
</main>
<script>
async function postJSON(url, body) {{
  const response = await fetch(url, {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify(body || {{}})}});
  if (!response.ok) {{ const e = await response.json().catch(() => ({{}})); alert('操作失败: ' + (e.detail || response.status)); return; }}
  window.location.reload();
}}
document.body.addEventListener('click', event => {{
  const button = event.target.closest('button[data-action]');
  if (!button) return;
  const id = button.dataset.id;
  const action = button.dataset.action;
  if (action === 'deliver-notice') postJSON(`/api/notices/${{id}}/events`, {{event_type:'delivered'}});
  if (action === 'notify-activity') postJSON(`/api/activities/${{id}}/handling`, {{action:'notify_participants', state:'done'}});
  if (action === 'refund-activity') postJSON(`/api/activities/${{id}}/transition`, {{status:'refunded', note:'风险建议触发退款/改期'}});
  if (action === 'complete-activity') postJSON(`/api/activities/${{id}}/transition`, {{status:'completed'}});
}});
</script>
</body></html>"""
