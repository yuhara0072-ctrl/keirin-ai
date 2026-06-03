"""競輪観測AI — 共通設定"""

import os
from pathlib import Path

# プロジェクトのルート（このファイルがあるフォルダ）
PROJECT_ROOT = Path(__file__).resolve().parent

# データ保存先（レポート・モデル等 — Render 上は ephemeral）
DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# GitHub JSON 永続化（Render 無料プラン — レース数・学習データ）
PERSIST_DIR = PROJECT_ROOT / "persist"
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()
GITHUB_REPO = os.environ.get("GITHUB_REPO", "").strip()
# 永続化は data ブランチのみ（main へ push すると Render Auto Deploy が走るため）
GITHUB_PERSIST_BRANCH = os.environ.get("GITHUB_PERSIST_BRANCH", "data").strip() or "data"
GITHUB_REQUEST_TIMEOUT = int(os.environ.get("GITHUB_REQUEST_TIMEOUT", "60"))


def resolve_db_path() -> Path:
    """実行用 SQLite（Render 上は ephemeral キャッシュ。永続化は persist/ + GitHub）"""
    env_path = os.environ.get("DATABASE_PATH", "").strip()
    if env_path:
        path = Path(env_path)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return path
    return DATA_DIR / "keirin.db"
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

# 安定化優先: 0=ホームは軽量UIのみ / 1=月目標・推奨購入額・攻め守りを表示
ENABLE_HOME_GOALS = os.environ.get("KEIRIN_ENABLE_HOME_GOALS", "0").strip() in (
    "1",
    "true",
    "True",
    "yes",
)
