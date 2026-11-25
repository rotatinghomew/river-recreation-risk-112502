"""命令行入口：生成文本、JSON 或 HTML 看板，并支持回看。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .reporting import render_html, render_json, render_text
from .service import RiverRiskService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="river-recreation-risk", description="河道开放风险证据与公告排期")
    parser.add_argument("--data", default="data", help="资料包目录")
    parser.add_argument("--as-of", dest="as_of", default=None, help="分析依据时间，ISO 格式")
    parser.add_argument("--target-at", dest="target_at", default=None, help="开放/活动目标时间")
    parser.add_argument("--format", choices=("text", "json", "html"), default="text")
    parser.add_argument("--output", "-o", default=None, help="将输出写入文件")
    parser.add_argument("--calendar-entrance", default=None, help="输出指定入口的证据日历")
    parser.add_argument("--calendar-start", default=None)
    parser.add_argument("--calendar-end", default=None)
    parser.add_argument("--review-decision", default=None, help="回看已冻结的 decision_id")
    parser.add_argument("--observations", default=None, help="回看使用的后续观测 JSON 文件")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    service = RiverRiskService.from_root(Path(args.data))

    if args.review_decision:
        observations = None
        if args.observations:
            observations = json.loads(Path(args.observations).read_text(encoding="utf-8"))
        payload = service.review(args.review_decision, observations)
        output = render_json(payload) if args.format == "json" else (
            f"{payload['decision_id']} -> {payload['verdict']}\n{payload['explanation_zh']}"
        )
    elif args.calendar_entrance:
        if not (args.calendar_start and args.calendar_end):
            raise SystemExit("--calendar-entrance 需要同时提供 --calendar-start 和 --calendar-end")
        payload = service.calendar(args.calendar_entrance, args.calendar_start, args.calendar_end, args.as_of)
        output = render_json(payload) if args.format == "json" else "\n".join(
            f"{day['date']} {day['state']}: {day['label_zh']}" for day in payload
        )
    else:
        dashboard = service.dashboard(args.as_of, args.target_at)
        if args.format == "json":
            output = render_json(dashboard)
        elif args.format == "html":
            output = render_html(dashboard)
        else:
            output = render_text(dashboard)

    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
    else:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
