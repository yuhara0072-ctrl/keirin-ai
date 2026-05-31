"""DB内容を data/verify_report.txt に出力（確認用）"""

from pathlib import Path

from db import get_connection

RACE_ID = "202508115601"
OUT = Path("data/verify_report.txt")


def main() -> None:
    lines: list[str] = []
    conn = get_connection()

    lines.append("=" * 50)
    lines.append("競輪観測AI - DB内容確認")
    lines.append(f"race_id: {RACE_ID}")
    lines.append("=" * 50)

    r = conn.execute("SELECT * FROM races WHERE race_id=?", (RACE_ID,)).fetchone()
    lines.append("\n【races レース情報】")
    if r:
        lines.append(f"  日付: {r['race_date']}")
        lines.append(f"  場: {r['venue_name']} (コード{r['venue_code']})")
        lines.append(f"  R: {r['race_no']}")
        lines.append(f"  グレード: {r['grade']}")

    lines.append("\n【entries 出走表】")
    for e in conn.execute(
        """
        SELECT bracket, racer_name, region, racer_grade, style
        FROM entries WHERE race_id=? ORDER BY bracket
        """,
        (RACE_ID,),
    ):
        lines.append(
            f"  {e['bracket']}番 {e['racer_name']} ({e['region']}) "
            f"{e['racer_grade']} 脚質:{e['style']}"
        )

    lines.append("\n【results 結果】")
    res = conn.execute("SELECT * FROM results WHERE race_id=?", (RACE_ID,)).fetchone()
    if res:
        lines.append(f"  着順(車番): {res['finish_order']}")
        lines.append(f"  3連単払戻: {res['trifecta_pay']}円")
        lines.append(f"  2車単払戻: {res['exacta_pay']}円")

    snap = conn.execute(
        "SELECT MAX(captured_at) AS ts FROM odds WHERE race_id=?", (RACE_ID,)
    ).fetchone()
    ts = snap["ts"] if snap else ""
    lines.append(f"\n【odds 最新スナップショット】 {ts}")

    lines.append("\n【odds 3連単（安い順 TOP5）】")
    for o in conn.execute(
        """
        SELECT combination, odds FROM odds
        WHERE race_id=? AND bet_type='3連単' AND captured_at=?
        ORDER BY odds LIMIT 5
        """,
        (RACE_ID, ts),
    ):
        lines.append(f"  {o['combination']}: {o['odds']}倍")

    lines.append("\n【odds 件数サマリー（最新のみ）】")
    for row in conn.execute(
        """
        SELECT bet_type, COUNT(*) AS n FROM odds
        WHERE race_id=? AND captured_at=?
        GROUP BY bet_type
        """,
        (RACE_ID, ts),
    ):
        lines.append(f"  {row['bet_type']}: {row['n']}件")

    conn.close()
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"レポート出力: {OUT}")


if __name__ == "__main__":
    main()
