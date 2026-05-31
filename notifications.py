"""通知機能 — AI高スコア・危険人気・直前急変の通知ログ"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Optional

import pandas as pd

from db import db_session, get_connection
from market_monitor import SUDDEN_CHANGE_PCT

HIGH_SCORE_THRESHOLD = 80.0
NOTIFY_TYPES = ("high_score", "danger_popular", "odds_surge")

NOTIFY_TABLE = """
CREATE TABLE IF NOT EXISTS notifications (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    race_id      TEXT NOT NULL,
    notify_type  TEXT NOT NULL,
    bet_type     TEXT NOT NULL,
    notify_date  TEXT NOT NULL,
    title        TEXT NOT NULL,
    message      TEXT NOT NULL,
    severity     TEXT NOT NULL DEFAULT 'info',
    score_value  REAL,
    meta_json    TEXT,
    notified_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    UNIQUE (race_id, notify_type, bet_type, notify_date)
);
CREATE INDEX IF NOT EXISTS idx_notify_date ON notifications(notify_date DESC);
CREATE INDEX IF NOT EXISTS idx_notify_type ON notifications(notify_type);
"""


def migrate_notifications_table(conn) -> None:
    conn.executescript(NOTIFY_TABLE)


def _today_str() -> str:
    return date.today().strftime("%Y%m%d")


def _effective_score(row: pd.Series | dict) -> float:
    if isinstance(row, dict):
        return float(row.get("pre_race_score") or row.get("ai_total_score") or 0)
    return float(row.get("pre_race_score") or row.get("ai_total_score") or 0)


def _race_label(row: dict | pd.Series) -> str:
    if isinstance(row, pd.Series):
        row = row.to_dict()
    return f"{row.get('venue_name', '?')} {row.get('race_no', '?')}R"


def _filter_today_scores(scores: pd.DataFrame) -> pd.DataFrame:
    if scores.empty or "race_date" not in scores.columns:
        return scores
    today = _today_str()
    today_rows = scores[scores["race_date"].astype(str) == today]
    return today_rows if not today_rows.empty else scores


def build_high_score_candidates(
    scores: pd.DataFrame,
    bet_type: str = "3連単",
) -> list[dict]:
    if scores.empty:
        return []

    today = _today_str()
    rows: list[dict] = []
    for _, row in _filter_today_scores(scores).iterrows():
        sc = _effective_score(row)
        if sc < HIGH_SCORE_THRESHOLD:
            continue

        label = _race_label(row)
        rows.append(
            {
                "race_id": str(row["race_id"]),
                "notify_type": "high_score",
                "bet_type": bet_type,
                "notify_date": today,
                "title": f"高期待値 {label}",
                "message": (
                    f"AIスコア {sc:.0f}（{row.get('ev_rank', '—')}）"
                    f" · 危険{row.get('danger_level', '—')}"
                    f" · 人気集中{row.get('ninki_concentration', '—')}%"
                ),
                "severity": "info",
                "score_value": sc,
                "meta": {
                    "venue_name": row.get("venue_name"),
                    "race_no": row.get("race_no"),
                    "ev_rank": row.get("ev_rank"),
                    "verdict_hint": "",
                },
            }
        )
    return rows


def build_danger_candidates(
    recommend: dict,
    bet_type: str = "3連単",
) -> list[dict]:
    today = _today_str()
    rows: list[dict] = []
    for card in recommend.get("dangerous_popular") or []:
        rows.append(
            {
                "race_id": str(card["race_id"]),
                "notify_type": "danger_popular",
                "bet_type": bet_type,
                "notify_date": today,
                "title": f"危険人気 {_race_label(card)}",
                "message": card.get("danger_reason") or "人気・波乱リスクが高い組み合わせ",
                "severity": "warning",
                "score_value": _effective_score(card),
                "meta": {
                    "venue_name": card.get("venue_name"),
                    "race_no": card.get("race_no"),
                    "ninki_concentration": card.get("ninki_concentration"),
                    "danger_level": card.get("danger_level"),
                },
            }
        )
    return rows


def build_surge_candidates(
    pre_race: dict,
    market: dict,
    bet_type: str = "3連単",
    *,
    limit: int = 15,
) -> list[dict]:
    today = _today_str()
    rows: list[dict] = []
    seen: set[tuple[str, str]] = set()

    surge_df = pre_race.get("surge_ranking", pd.DataFrame())
    if surge_df.empty:
        surge_df = market.get("sudden_ranking", pd.DataFrame())

    if surge_df.empty:
        return rows

    for _, row in surge_df.head(limit).iterrows():
        race_id = str(row.get("race_id", ""))
        combo = str(row.get("combination", ""))
        key = (race_id, combo)
        if not race_id or key in seen:
            continue
        seen.add(key)

        change = float(row.get("change_pct") or 0)
        if change < SUDDEN_CHANGE_PCT:
            continue

        label = f"{row.get('venue_name', '?')} {row.get('race_no', '?')}R"
        rows.append(
            {
                "race_id": race_id,
                "notify_type": "odds_surge",
                "bet_type": bet_type,
                "notify_date": today,
                "title": f"急変アラート {label} {combo}",
                "message": (
                    f"オッズ急変 +{change:.0f}%"
                    f" ({row.get('odds_old', '—')}→{row.get('odds_new', '—')})"
                ),
                "severity": "alert",
                "score_value": change,
                "meta": {
                    "venue_name": row.get("venue_name"),
                    "race_no": row.get("race_no"),
                    "combination": combo,
                    "change_pct": change,
                    "rank_delta": row.get("rank_delta"),
                },
            }
        )
    return rows


def build_notification_candidates(
    bet_type: str = "3連単",
    scores: Optional[pd.DataFrame] = None,
    recommend: Optional[dict] = None,
    pre_race: Optional[dict] = None,
    market: Optional[dict] = None,
) -> list[dict]:
    """本日の通知候補を検出"""
    if scores is None:
        from ai_score import build_race_scores

        scores = build_race_scores(bet_type)
    if recommend is None:
        from ai_recommend import build_daily_recommendations

        recommend = build_daily_recommendations(bet_type, scores=scores)
    if pre_race is None:
        from pre_race import get_pre_race_bundle

        pre_race = get_pre_race_bundle(bet_type)
    if market is None:
        from market_monitor import get_market_monitor_bundle

        market = get_market_monitor_bundle(bet_type)

    candidates: list[dict] = []
    candidates.extend(build_high_score_candidates(scores, bet_type))
    candidates.extend(build_danger_candidates(recommend, bet_type))
    candidates.extend(build_surge_candidates(pre_race, market, bet_type))
    return candidates


def save_notifications(candidates: list[dict], bet_type: str = "3連単") -> int:
    """新規候補のみ DB に保存"""
    if not candidates:
        return 0

    saved = 0
    with db_session() as conn:
        migrate_notifications_table(conn)
        for c in candidates:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO notifications (
                    race_id, notify_type, bet_type, notify_date,
                    title, message, severity, score_value, meta_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    c["race_id"],
                    c["notify_type"],
                    bet_type,
                    c.get("notify_date", _today_str()),
                    c["title"],
                    c["message"],
                    c.get("severity", "info"),
                    c.get("score_value"),
                    json.dumps(c.get("meta") or {}, ensure_ascii=False),
                ),
            )
            if cur.rowcount:
                saved += 1
    return saved


def load_notification_history(
    limit: int = 100,
    notify_date: Optional[str] = None,
) -> pd.DataFrame:
    conn = get_connection()
    migrate_notifications_table(conn)
    if notify_date:
        df = pd.read_sql(
            """
            SELECT id, race_id, notify_type, bet_type, notify_date,
                   title, message, severity, score_value, notified_at
            FROM notifications
            WHERE notify_date = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            conn,
            params=(notify_date, limit),
        )
    else:
        df = pd.read_sql(
            """
            SELECT id, race_id, notify_type, bet_type, notify_date,
                   title, message, severity, score_value, notified_at
            FROM notifications
            ORDER BY id DESC
            LIMIT ?
            """,
            conn,
            params=(limit,),
        )
    conn.close()
    return df


def candidates_to_frame(candidates: list[dict]) -> pd.DataFrame:
    if not candidates:
        return pd.DataFrame()
    rows = []
    type_label = {
        "high_score": "高期待値",
        "danger_popular": "危険人気",
        "odds_surge": "急変",
    }
    for c in candidates:
        rows.append(
            {
                "種別": type_label.get(c["notify_type"], c["notify_type"]),
                "race_id": c["race_id"],
                "タイトル": c["title"],
                "内容": c["message"],
                "重要度": c.get("severity", "info"),
                "値": c.get("score_value"),
            }
        )
    return pd.DataFrame(rows)


def get_notification_bundle(
    bet_type: str = "3連単",
    scores: Optional[pd.DataFrame] = None,
    recommend: Optional[dict] = None,
    pre_race: Optional[dict] = None,
    market: Optional[dict] = None,
    *,
    persist: bool = True,
) -> dict:
    """Streamlit / CLI 用"""
    candidates = build_notification_candidates(
        bet_type, scores, recommend, pre_race, market
    )
    saved = save_notifications(candidates, bet_type) if persist and candidates else 0

    today = _today_str()
    high = [c for c in candidates if c["notify_type"] == "high_score"]
    danger = [c for c in candidates if c["notify_type"] == "danger_popular"]
    surge = [c for c in candidates if c["notify_type"] == "odds_surge"]
    history = load_notification_history(limit=100)
    today_history = history[history["notify_date"] == today] if not history.empty else history

    return {
        "has_data": bool(candidates) or not history.empty,
        "bet_type": bet_type,
        "today": today,
        "candidates": candidates,
        "high_score": high,
        "danger_popular": danger,
        "odds_surge": surge,
        "candidate_count": len(candidates),
        "saved_count": saved,
        "history": history,
        "today_history": today_history,
        "history_count": len(history),
    }


def build_notify_lines(bet_type: str = "3連単") -> list[str]:
    bundle = get_notification_bundle(bet_type, persist=False)
    lines = [f"【通知】券種={bet_type}", ""]
    lines.append(f"  本日候補: {bundle['candidate_count']} 件")
    lines.append(f"  履歴: {bundle['history_count']} 件")
    lines.append("")

    for label, key in [
        ("高期待値", "high_score"),
        ("危険人気", "danger_popular"),
        ("急変", "odds_surge"),
    ]:
        items = bundle[key]
        lines.append(f"--- {label} ({len(items)}) ---")
        if not items:
            lines.append("  （なし）")
        else:
            for c in items[:5]:
                lines.append(f"  {c['title']}: {c['message']}")
        lines.append("")
    return lines
