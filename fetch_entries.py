"""出走表取得（netkeirin）"""

import re
import time
from dataclasses import dataclass
from typing import Optional

import requests
from bs4 import BeautifulSoup

from config import REQUEST_INTERVAL, REQUEST_TIMEOUT, USER_AGENT
from db import db_session, init_db

ENTRY_URL = "https://keirin.netkeiba.com/race/entry/?race_id={race_id}"
STYLE_MARKERS = ("逃", "捲", "差", "追", "マ", "両")

TITLE_PATTERN = re.compile(
    r"^(.+?)競輪\s+(.+?)\s+(\S+)\s+(\d{4})年(\d{2})月(\d{2})日\s+(\d+)R"
)


@dataclass
class RaceMeta:
    race_id: str
    race_date: str
    venue_code: str
    venue_name: str
    race_no: int
    grade: str
    distance: Optional[int] = None


@dataclass
class Entry:
    bracket: int
    racer_id: str
    racer_name: str
    region: str
    racer_grade: str
    style: str


def _headers() -> dict[str, str]:
    return {"User-Agent": USER_AGENT}


def split_race_id(race_id: str) -> tuple[str, str, int]:
    """netkeirin形式: YYYYMMDD + 場コード2桁 + R番号2桁"""
    if len(race_id) < 12 or not race_id.isdigit():
        raise ValueError(f"race_id の形式が不正です: {race_id}")
    race_date = f"{race_id[0:4]}-{race_id[4:6]}-{race_id[6:8]}"
    venue_code = race_id[8:10]
    race_no = int(race_id[10:12])
    return race_date, venue_code, race_no


def fetch_html(race_id: str) -> str:
    url = ENTRY_URL.format(race_id=race_id)
    resp = requests.get(url, headers=_headers(), timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding or "utf-8"
    time.sleep(REQUEST_INTERVAL)
    return resp.text


def parse_race_meta(soup: BeautifulSoup, race_id: str) -> RaceMeta:
    race_date, venue_code, race_no = split_race_id(race_id)
    venue_name = ""
    grade = ""

    title = (soup.title.string or "") if soup.title else ""
    m = TITLE_PATTERN.search(title)
    if m:
        venue_name = m.group(1)
        grade = m.group(3)

    return RaceMeta(
        race_id=race_id,
        race_date=race_date,
        venue_code=venue_code,
        venue_name=venue_name,
        race_no=race_no,
        grade=grade,
    )


def _first_line_text(element) -> str:
    if element is None:
        return ""
    for part in element.stripped_strings:
        return part
    return ""


def parse_entries(soup: BeautifulSoup) -> list[Entry]:
    rows = soup.select("#RaceCard_Table_Static tr.PlayerList")
    entries: list[Entry] = []

    for tr in rows:
        tds = tr.find_all("td")
        if len(tds) < 7:
            continue

        bracket = int(tds[1].get_text(strip=True))

        anchor = tr.select_one("a.Player01")
        racer_id = ""
        if anchor and anchor.get("id"):
            id_match = re.search(r"_(\d+)_", anchor["id"])
            if id_match:
                racer_id = id_match.group(1)

        racer_name = _first_line_text(tr.select_one("dt.PlayerName"))

        region = ""
        region_el = tr.select_one("dd.PlayerFrom span")
        if region_el:
            region = region_el.get_text(strip=True)

        racer_grade = ""
        grade_el = tr.select_one("dd.PlayerClass")
        if grade_el:
            racer_grade = grade_el.get_text(strip=True)

        style = ""
        player_idx = next(
            (i for i, td in enumerate(tds) if "Player_Info" in (td.get("class") or [])),
            -1,
        )
        if player_idx >= 0 and player_idx + 2 < len(tds):
            raw_style = tds[player_idx + 2].get_text(strip=True)
            style = next((s for s in STYLE_MARKERS if s in raw_style), raw_style)

        entries.append(
            Entry(
                bracket=bracket,
                racer_id=racer_id,
                racer_name=racer_name,
                region=region,
                racer_grade=racer_grade,
                style=style,
            )
        )

    return entries


def save_race(meta: RaceMeta, entries: list[Entry]) -> None:
    with db_session() as conn:
        conn.execute(
            """
            INSERT INTO races (race_id, race_date, venue_code, venue_name, race_no, grade, distance)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(race_id) DO UPDATE SET
                race_date=excluded.race_date,
                venue_name=excluded.venue_name,
                grade=excluded.grade
            """,
            (
                meta.race_id,
                meta.race_date,
                meta.venue_code,
                meta.venue_name,
                meta.race_no,
                meta.grade,
                meta.distance,
            ),
        )
        for e in entries:
            conn.execute(
                """
                INSERT INTO entries (
                    race_id, bracket, racer_id, racer_name, region, racer_grade, style
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(race_id, bracket) DO UPDATE SET
                    racer_id=excluded.racer_id,
                    racer_name=excluded.racer_name,
                    region=excluded.region,
                    racer_grade=excluded.racer_grade,
                    style=excluded.style
                """,
                (
                    meta.race_id,
                    e.bracket,
                    e.racer_id,
                    e.racer_name,
                    e.region,
                    e.racer_grade,
                    e.style,
                ),
            )


def fetch_and_save(race_id: str) -> int:
    html = fetch_html(race_id)
    soup = BeautifulSoup(html, "lxml")
    meta = parse_race_meta(soup, race_id)
    entries = parse_entries(soup)
    if not entries:
        raise ValueError(f"出走表を取得できませんでした: {race_id}")
    save_race(meta, entries)
    return len(entries)


if __name__ == "__main__":
    import sys

    init_db()
    target_id = sys.argv[1] if len(sys.argv) > 1 else "202508115601"
    count = fetch_and_save(target_id)
    print(f"保存完了: {target_id} ({count}名)")
