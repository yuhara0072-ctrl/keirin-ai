"""オッズ取得（netkeirin API）"""

import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import requests

from config import REQUEST_INTERVAL, REQUEST_TIMEOUT, USER_AGENT
from db import db_session, init_db

ODDS_API_URL = "https://keirin.netkeiba.com/api/race/"

# APIの list_N → 券種（db.odds.bet_type）
BET_TYPES = {
    "list_5": "2車複",
    "list_6": "2車単",
    "list_7": "ワイド",
    "list_8": "3連複",
    "list_9": "3連単",
}


@dataclass
class OddsRow:
    bet_type: str
    combination: str
    odds: float


def _headers() -> dict[str, str]:
    return {"User-Agent": USER_AGENT}


def parse_combination(code: str) -> str:
    """'010203' → '1-2-3'"""
    if len(code) % 2 != 0:
        return code
    nums = [str(int(code[i : i + 2])) for i in range(0, len(code), 2)]
    return "-".join(nums)


def fetch_odds_json(race_id: str) -> dict:
    params = {
        "class": "AplRaceOdds",
        "method": "get",
        "race_id": race_id,
        "output": "json",
    }
    resp = requests.get(
        ODDS_API_URL, params=params, headers=_headers(), timeout=REQUEST_TIMEOUT
    )
    resp.raise_for_status()
    time.sleep(REQUEST_INTERVAL)
    body = resp.json()
    if body.get("status") != "OK":
        raise ValueError(f"APIエラー: {body.get('reason', body)}")
    key = f"nkrace_odds::{race_id}"
    payload = body.get("data", {}).get(key)
    if not payload:
        raise ValueError(f"オッズデータがありません: {race_id}")
    return payload


def parse_odds(payload: dict) -> list[OddsRow]:
    rows: list[OddsRow] = []
    for list_key, bet_type in BET_TYPES.items():
        items = payload.get(list_key) or []
        for item in items:
            if len(item) < 2:
                continue
            code, odds_str = item[0], item[1]
            try:
                odds_val = float(odds_str)
            except (TypeError, ValueError):
                continue
            rows.append(
                OddsRow(
                    bet_type=bet_type,
                    combination=parse_combination(str(code)),
                    odds=odds_val,
                )
            )
    return rows


def save_odds(
    race_id: str,
    rows: list[OddsRow],
    captured_at: Optional[str] = None,
    *,
    new_snapshot: bool = False,
) -> int:
    """オッズ保存。new_snapshot=True で履歴用に新規スナップショットを追加"""
    ts = captured_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with db_session() as conn:
        exists = conn.execute(
            "SELECT 1 FROM races WHERE race_id = ?", (race_id,)
        ).fetchone()
        if not exists:
            raise ValueError(
                f"レース未登録です。先に fetch_entries.py を実行してください: {race_id}"
            )

        for row in rows:
            if new_snapshot:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO odds
                    (race_id, bet_type, combination, odds, captured_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (race_id, row.bet_type, row.combination, row.odds, ts),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO odds (race_id, bet_type, combination, odds, captured_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(race_id, bet_type, combination, captured_at)
                    DO UPDATE SET odds = excluded.odds
                    """,
                    (race_id, row.bet_type, row.combination, row.odds, ts),
                )
    return len(rows)


def fetch_and_save(race_id: str, *, new_snapshot: bool = False) -> int:
    payload = fetch_odds_json(race_id)
    rows = parse_odds(payload)
    if not rows:
        raise ValueError(f"オッズを解析できませんでした: {race_id}")
    return save_odds(race_id, rows, new_snapshot=new_snapshot)


def fetch_odds_snapshot(race_id: str) -> tuple[int, str]:
    """市場監視用: 新しい captured_at でスナップショット保存"""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    n = fetch_and_save(race_id, new_snapshot=True)
    return n, ts


def poll_odds_for_races(race_ids: list[str]) -> list[dict]:
    """複数レースのオッズを再取得（スナップショット追加）"""
    results: list[dict] = []
    for race_id in race_ids:
        try:
            n, ts = fetch_odds_snapshot(race_id)
            results.append({"race_id": race_id, "ok": True, "count": n, "captured_at": ts})
        except Exception as e:
            results.append({"race_id": race_id, "ok": False, "error": str(e)})
    return results


def list_race_ids_in_db(limit: int = 50) -> list[str]:
    from db import get_connection

    conn = get_connection()
    rows = conn.execute(
        "SELECT race_id FROM races ORDER BY race_date DESC, race_id LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


if __name__ == "__main__":
    import sys

    init_db()
    target_id = sys.argv[1] if len(sys.argv) > 1 else "202508115601"
    count = fetch_and_save(target_id)
    print(f"保存完了: {target_id} ({count}件)")
