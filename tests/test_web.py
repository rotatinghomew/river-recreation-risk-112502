"""HTTP 看板的标准库冒烟测试。"""

from __future__ import annotations

import json
import sys
import threading
import unittest
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from river_recreation_risk.web import create_server

DATA = Path(__file__).parents[1] / "data"


class WebSmokeTest(unittest.TestCase):
    def setUp(self):
        self.server = create_server(DATA, "127.0.0.1", 0)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def get(self, path):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=5) as response:
            return response.status, response.read()

    def test_dashboard_html_and_json(self):
        status, body = self.get("/api/dashboard")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["summary"]["entrance_count"], 3)

        status, html = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn("河道开放风险".encode(), html)
        self.assertIn("公告版本".encode(), html)

    def test_notice_event_post(self):
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/notices/n-001/events",
            data=json.dumps({"event_type": "read", "at": "2026-08-15T10:00"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            self.assertEqual(response.status, 201)
            payload = json.loads(response.read())
        self.assertTrue(payload["ok"])


if __name__ == "__main__":
    unittest.main()
