"""无第三方依赖的 HTTP 展示与操作 API。

启动：
    PYTHONPATH=src python3 -m river_recreation_risk.web --data data --port 8000
"""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from typing import Any
from urllib.parse import parse_qs, urlparse

from .reporting import render_html
from .service import RiverRiskService


def create_server(data_dir: str | Path, host: str = "127.0.0.1", port: int = 8000) -> ThreadingHTTPServer:
    service = RiverRiskService.from_root(data_dir)
    lock = Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:  # 保持测试输出安静
            return

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, payload: Any, status: int = 200) -> None:
            self._send(status, json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"), "application/json; charset=utf-8")

        def _body_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or 0)
            if not length:
                return {}
            raw = self.rfile.read(length)
            return json.loads(raw.decode("utf-8"))

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            query = parse_qs(parsed.query)
            try:
                if path == "/":
                    with lock:
                        html = render_html(service.dashboard()).encode("utf-8")
                    self._send(200, html, "text/html; charset=utf-8")
                    return
                if path == "/api/dashboard":
                    as_of = query.get("as_of", [None])[0]
                    with lock:
                        self._json(service.dashboard(as_of))
                    return
                if path.startswith("/api/entrances/"):
                    eid = path.rsplit("/", 1)[-1]
                    with lock:
                        assessments = service.assess_all()
                        if eid not in assessments:
                            self._json({"error": f"unknown entrance {eid}"}, 404)
                        else:
                            self._json(assessments[eid])
                    return
                if path == "/api/calendar":
                    eid = query.get("entrance_id", [""])[0]
                    start = query.get("start", [""])[0]
                    end = query.get("end", [""])[0]
                    with lock:
                        self._json(service.calendar(eid, start, end))
                    return
                self._json({"error": "not_found", "path": path}, 404)
            except KeyError as exc:
                self._json({"error": "not_found", "detail": str(exc)}, 404)
            except Exception as exc:  # pragma: no cover - HTTP boundary
                self._json({"error": type(exc).__name__, "detail": str(exc)}, 400)

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/")
            try:
                payload = self._body_json()
                with lock:
                    if path == "/api/lab-results":
                        result = service.receive_lab_result(payload)
                        self._json({"ok": True, **result}, 201)
                        return
                    if path == "/api/refresh-arrangements":
                        result = service.refresh_arrangements(payload.get("as_of"))
                        self._json({"ok": True, "refresh": result})
                        return
                    if path.startswith("/api/notices/") and path.endswith("/events"):
                        nid = path.split("/")[3]
                        event = service.ledger.record_notice_event(
                            nid,
                            payload.get("event_type", "delivered"),
                            payload.get("at"),
                            payload.get("detail"),
                        )
                        self._json({"ok": True, "event": event}, 201)
                        return
                    if path.startswith("/api/activities/") and path.endswith("/transition"):
                        aid = path.split("/")[3]
                        event = service.ledger.transition_activity(
                            aid,
                            payload["status"],
                            payload.get("at"),
                            payload.get("note"),
                        )
                        self._json({"ok": True, "event": event}, 201)
                        return
                    if path.startswith("/api/activities/") and path.endswith("/handling"):
                        aid = path.split("/")[3]
                        event = service.ledger.add_activity_handling(
                            aid, payload["action"], payload.get("state", "pending"), payload.get("note")
                        )
                        self._json({"ok": True, "event": event}, 201)
                        return
                self._json({"error": "not_found", "path": path}, 404)
            except KeyError as exc:
                self._json({"ok": False, "error": "not_found", "detail": str(exc)}, 404)
            except Exception as exc:
                self._json({"ok": False, "error": type(exc).__name__, "detail": str(exc)}, 409)

    return ThreadingHTTPServer((host, port), Handler)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="河道风险看板 HTTP 服务")
    parser.add_argument("--data", default="data")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    server = create_server(args.data, args.host, args.port)
    print(f"river-risk dashboard on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
