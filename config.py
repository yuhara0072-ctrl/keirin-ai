"""競輪観測AI — 共通設定"""

import os
from pathlib import Path

# プロジェクトのルート（このファイルがあるフォルダ）
PROJECT_ROOT = Path(__file__).resolve().parent

# データ保存先（レポート・モデル等 — ローカル/ephemeral）
DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Render Persistent Disk の標準マウントパス（DATABASE_PATH 未設定時の参考値）
RENDER_DISK_MOUNT = Path("/var/data")


def resolve_db_path() -> Path:
    """SQLite DB パス。環境変数 DATABASE_PATH があれば優先、なければ data/keirin.db"""
    env_path = os.environ.get("DATABASE_PATH", "").strip()
    if env_path:
        path = Path(env_path)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return path
    return DATA_DIR / "keirin.db"


# SQLite（Render Disk では DATABASE_PATH=/var/data/keirin.db 等を指定）
DB_PATH = resolve_db_path()
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

# HTTP（サイトへの負荷を抑える）
REQUEST_TIMEOUT = 10  # 秒
REQUEST_INTERVAL = 1.0  # リクエスト間隔（秒）
USER_AGENT = (
    "Mozilla/5.0 (compatible; KeirinObserver/0.1; +https://example.local)"
)

# 取得元（後の fetch_*.py で使用）
BASE_URL = "https://keirin.jp"
RACE_API_URL = "https://keirin.netkeiba.com/api/race/"

# 毎日取得の既定件数
DAILY_FETCH_LIMIT = 5

# 100レース収集モードの目標
TARGET_RACES = 100
TARGET_RACES_MID = 300
TARGET_RACES_FULL = 1000

DATA_MILESTONES = (TARGET_RACES, TARGET_RACES_MID, TARGET_RACES_FULL)

# 資金管理の初期元手
DEFAULT_BANKROLL = 5000
