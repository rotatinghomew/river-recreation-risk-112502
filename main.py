"""命令行入口的根目录兼容包装：python main.py。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from river_recreation_risk.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
