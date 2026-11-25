"""本地一键启动入口：python app.py。"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent / "src"))

from river_recreation_risk.web import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
