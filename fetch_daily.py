"""指定日の複数レースをまとめて取得"""

import re
import time
from datetime import date, datetime
from typing import Optional

import requests

from config import DAILY_FETCH_LIMIT, RACE_API_URL, REQUEST_INTERVAL, REQUEST_TIMEOUT, USER_AGENT
from db import db_session, init_db
from fetch_entries import fetch_and_save as fetch_entries
from fetch_odds import fetch_and_save as fetch_odds
from fetch_results import fetch_and_save as fetch_results
from race_features import update_line_from_api


def _headers() -> dict[str, str]:
    return {"User-Agent": USER_AGENT}


def normalize_date(value: Optional[str] = None) -> str:
    """YYYYMMDD 形式に変換（未指定なら今日）"""
    if value:
        cleaned = value.replace("-", "").replace("/", "")
        if len(cleaned) != 8 or not cleaned.isdigit():
            raise ValueError(f"日付形式が不正です: {value} （例: 20250811）")
        return cleaned
    return date.today().strftime("%Y%m%d")


def time_slot_from_start(start: str) -> str:
    """発走時刻から時間帯ラベル"""
    if not start or ":" not in start:
        return "不明"
    try:
        hour = int(start.split(":")[0])
    except ValueError:
        return "不明"
    if hour < 12:
        return "モーニング"
    if hour < 15:
        return "サマータイム"
    if hour < 20:
        return "ナイター"
    return "ミッドナイト"


def list_races_for_date(kaisai_date: str) -> list[dict]:
    """netkeirin API から開催日の全レース一覧"""
    resp = requests.get(
        RACE_API_URL,
        params={
            "class": "AplRace",
            "method": "get",
            "kaisai_date": kaisai_date,
            "output": "json",
        },
        headers=_headers(),
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    time.sleep(REQUEST_INTERVAL)
    body = resp.json()
    if body.get("status") != "OK":
        raise ValueError(f"レース一覧APIエラー: {body.get('reason', body)}")

    races: list[dict] = []
    for venues in body.get("data", {}).values():
        if not isinstance(venues, list):
            continue
        for venue in venues:
            jyo_cd = str(venue.get("jyo_cd", ""))
            jyo_name = venue.get("jyo", "")
            for race in venue.get("list", []):
                race_id = race.get("race_id")
                if not race_id:
                    continue
                start = race.get("start", "")
                races.append(
                    {
                        "race_id": race_id,
                        "kaisai_date": kaisai_date,
                        "venue_code": jyo_cd,
                        "venue_name": jyo_name,
                        "race_no": int(race_id[-2:]),
                        "race_start": start,
                        "time_slot": time_slot_from_start(start),
                        "race_status": str(race.get("race_status", "")),
                        "race_name": race.get("race_name", ""),
                    }
                )

    races.sort(key=lambda r: (r["race_start"] or "99:99", r["race_id"]))
    return races


def save_race_schedule(info: dict) -> None:
    race_date = f"{info['kaisai_date'][0:4]}-{info['kaisai_date'][4:6]}-{info['kaisai_date'][6:8]}"
    with db_session() as conn:
        conn.execute(
            """
            INSERT INTO races (
                race_id, race_date, venue_code, venue_name, race_no,
                race_start, time_slot
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(race_id) DO UPDATE SET
                venue_name = excluded.venue_name,
                race_start = excluded.race_start,
                time_slot = excluded.time_slot
            """,
            (
                info["race_id"],
                race_date,
                info["venue_code"],
                info["venue_name"],
                info["race_no"],
                info["race_start"],
                info["time_slot"],
            ),
        )


def select_races(
    races: list[dict],
    limit: int,
    venue_code: Optional[str] = None,
) -> list[dict]:
    """取得対象を絞り込み（件数上限・場コード）"""
    filtered = races
    if venue_code:
        filtered = [r for r in filtered if r["venue_code"] == venue_code]
    return filtered[:limit]


def fetch_one_race(race_info: dict, with_result: bool) -> dict:
    race_id = race_info["race_id"]
    result = {"race_id": race_id, "entries": 0, "odds": 0, "result": None, "error": None}
    try:
        save_race_schedule(race_info)
        try:
            line_info, _ = update_line_from_api(race_id)
            result["line"] = line_info
        except Exception:
            result["line"] = None
        result["entries"] = fetch_entries(race_id)
        result["odds"] = fetch_odds(race_id)
        if with_result:
            res = fetch_results(race_id)
            result["result"] = res.finish_order
    except Exception as e:
        result["error"] = str(e)
    return result


def fetch_daily(
    kaisai_date: Optional[str] = None,
    limit: int = DAILY_FETCH_LIMIT,
    with_result: bool = False,
    venue_code: Optional[str] = None,
) -> list[dict]:
    """
    指定日のレースを最大 limit 件取得してDB保存。
    with_result=True なら結果も取得（終了レース向け）。
    """
    init_db()
    day = normalize_date(kaisai_date)
    all_races = list_races_for_date(day)
    if not all_races:
        raise ValueError(f"開催レースがありません: {day}")

    targets = select_races(all_races, limit, venue_code)
    print(f"開催日 {day}: 全{len(all_races)}レース → {len(targets)}件を取得")
    results: list[dict] = []

    for i, info in enumerate(targets, 1):
        print(
            f"\n[{i}/{len(targets)}] {info['venue_name']} "
            f"{info['race_no']}R 発走{info['race_start']} ({info['time_slot']})"
        )
        print(f"  race_id: {info['race_id']}")
        row = fetch_one_race(info, with_result)
        if row["error"]:
            print(f"  エラー: {row['error']}")
        else:
            print(f"  出走表 {row['entries']}名 / オッズ {row['odds']}件", end="")
            if row["result"]:
                print(f" / 着順 {row['result']}")
            else:
                print()
        results.append(row)

    ok = sum(1 for r in results if not r["error"])
    print(f"\n完了: {ok}/{len(targets)} 件成功")
    return results


if __name__ == "__main__":
    import sys

    day_arg = sys.argv[1] if len(sys.argv) > 1 else None
    limit_arg = int(sys.argv[2]) if len(sys.argv) > 2 else DAILY_FETCH_LIMIT
    with_res = "--with-result" in sys.argv
    fetch_daily(day_arg, limit=limit_arg, with_result=with_res)
