"""結果・払戻取得（netkeirin）"""

import re
import time
from dataclasses import dataclass
from typing import Optional

import requests
from bs4 import BeautifulSoup

from config import REQUEST_INTERVAL, REQUEST_TIMEOUT, USER_AGENT
from db import db_session, init_db

RESULT_URL = "https://keirin.netkeiba.com/race/result/?race_id={race_id}"


@dataclass
class RaceResult:
    finish_order: str
    trifecta_pay: Optional[int]
    exacta_pay: Optional[int]


def _headers() -> dict[str, str]:
    return {"User-Agent": USER_AGENT}


def fetch_html(race_id: str) -> str:
    url = RESULT_URL.format(race_id=race_id)
    resp = requests.get(url, headers=_headers(), timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding or "utf-8"
    time.sleep(REQUEST_INTERVAL)
    return resp.text


def _parse_yen(text: str) -> int:
    return int(text.replace("円", "").replace(",", "").strip())


def parse_finish_order(soup: BeautifulSoup) -> str:
    brackets: list[str] = []
    for tr in soup.select("#All_Result_Table tr.PlayerList"):
        car_cell = None
        for td in tr.select("td.RaceCardCell01"):
            classes = " ".join(td.get("class", []))
            if re.search(r"Waku\d", classes) and "WakbanCell" not in classes:
                car_cell = td
                break
        if car_cell is None:
            cells = tr.select("td.RaceCardCell01")
            if len(cells) >= 2:
                car_cell = cells[1]
        if car_cell is None:
            continue
        brackets.append(car_cell.get_text(strip=True))

    if not brackets:
        raise ValueError("着順を取得できませんでした（レース未確定の可能性）")
    return ",".join(brackets)


def parse_payouts(soup: BeautifulSoup) -> tuple[Optional[int], Optional[int]]:
    trifecta_pay: Optional[int] = None
    exacta_pay: Optional[int] = None

    for tr in soup.select(".Payout_Detail_Table tr"):
        th = tr.select_one("th")
        payout_el = tr.select_one("td.Payout span")
        if not th or not payout_el:
            continue
        bet_name = th.get_text(strip=True)
        pay = _parse_yen(payout_el.get_text())
        if "３連単" in bet_name:
            trifecta_pay = pay
        elif "２車単" in bet_name:
            exacta_pay = pay

    return trifecta_pay, exacta_pay


def parse_result(soup: BeautifulSoup) -> RaceResult:
    finish_order = parse_finish_order(soup)
    trifecta_pay, exacta_pay = parse_payouts(soup)
    return RaceResult(
        finish_order=finish_order,
        trifecta_pay=trifecta_pay,
        exacta_pay=exacta_pay,
    )


def save_result(race_id: str, result: RaceResult) -> None:
    with db_session() as conn:
        exists = conn.execute(
            "SELECT 1 FROM races WHERE race_id = ?", (race_id,)
        ).fetchone()
        if not exists:
            raise ValueError(
                f"レース未登録です。先に fetch_entries.py を実行してください: {race_id}"
            )

        conn.execute(
            """
            INSERT INTO results (race_id, finish_order, trifecta_pay, exacta_pay)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(race_id) DO UPDATE SET
                finish_order = excluded.finish_order,
                trifecta_pay = excluded.trifecta_pay,
                exacta_pay = excluded.exacta_pay,
                updated_at = datetime('now', 'localtime')
            """,
            (
                race_id,
                result.finish_order,
                result.trifecta_pay,
                result.exacta_pay,
            ),
        )


def fetch_and_save(race_id: str) -> RaceResult:
    html = fetch_html(race_id)
    soup = BeautifulSoup(html, "lxml")
    result = parse_result(soup)
    save_result(race_id, result)
    return result


if __name__ == "__main__":
    import sys

    init_db()
    target_id = sys.argv[1] if len(sys.argv) > 1 else "202508115601"
    res = fetch_and_save(target_id)
    print(f"保存完了: {target_id}")
    print(f"  着順: {res.finish_order}")
    print(f"  3連単: {res.trifecta_pay}円  2車単: {res.exacta_pay}円")
