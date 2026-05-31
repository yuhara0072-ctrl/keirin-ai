"""レース直前モード — 発走前オッズ記録・市場変化分析・直前補正スコア"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

from db import db_session, get_connection
from fetch_odds import fetch_odds_json, parse_odds
from market_monitor import (
    ALERT_LEVEL_HIGH,
    ALERT_LEVEL_MID,
    DARK_HORSE_RANK_MIN,
    HOT_SELL_DROP_PCT,
    SUDDEN_CHANGE_PCT,
    _enrich_implied,
    build_dangerous_favorites,
    build_race_alerts,
    fig_market_heatmap,
)

PHASES = ("T-30", "T-10", "T-0")
PHASE_MINUTES = {"T-30": 30, "T-10": 10, "T-0": 0}
CAPTURE_TOLERANCE_MIN = 4
PRE_RACE_ADJ_MAX = 15.0

PRE_RACE_TABLE = """
CREATE TABLE IF NOT EXISTS race_snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    race_id     TEXT NOT NULL REFERENCES races(race_id),
    phase       TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    row_count   INTEGER NOT NULL DEFAULT 0,
    status      TEXT NOT NULL DEFAULT 'ok',
    created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    UNIQUE (race_id, phase)
);
CREATE INDEX IF NOT EXISTS idx_race_snapshots_race ON race_snapshots(race_id);
"""


def migrate_pre_race_table(conn) -> None:
    conn.executescript(PRE_RACE_TABLE)


def _parse_race_datetime(race_date: str, race_start: str) -> Optional[datetime]:
    if not race_date or not race_start:
        return None
    d = str(race_date).replace("-", "")[:8]
    t = str(race_start).strip()
    if len(d) == 8:
        ds = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
    else:
        ds = d
    try:
        return datetime.strptime(f"{ds} {t}", "%Y-%m-%d %H:%M")
    except ValueError:
        try:
            return datetime.strptime(f"{d} {t}", "%Y%m%d %H:%M")
        except ValueError:
            return None


def list_upcoming_races(within_hours: float = 3.0) -> pd.DataFrame:
    """発走前のレース一覧（race_start 必須）"""
    conn = get_connection()
    df = pd.read_sql(
        """
        SELECT race_id, race_date, venue_name, race_no, race_start
        FROM races
        WHERE race_start IS NOT NULL AND race_start != ''
        ORDER BY race_date, race_no
        """,
        conn,
    )
    conn.close()
    if df.empty:
        return df

    now = datetime.now()
    rows: list[dict] = []
    for _, row in df.iterrows():
        start_at = _parse_race_datetime(str(row["race_date"]), str(row["race_start"]))
        if not start_at:
            continue
        delta_min = (start_at - now).total_seconds() / 60
        if -10 <= delta_min <= within_hours * 60:
            rows.append(
                {
                    **row.to_dict(),
                    "start_at": start_at.strftime("%Y-%m-%d %H:%M:%S"),
                    "minutes_to_start": round(delta_min, 1),
                }
            )
    return pd.DataFrame(rows)


def load_race_snapshots() -> pd.DataFrame:
    conn = get_connection()
    migrate_pre_race_table(conn)
    df = pd.read_sql(
        """
        SELECT rs.race_id, rs.phase, rs.captured_at, rs.row_count, rs.status,
               r.race_date, r.venue_name, r.race_no, r.race_start
        FROM race_snapshots rs
        JOIN races r ON rs.race_id = r.race_id
        ORDER BY r.race_date DESC, r.race_no, rs.phase
        """,
        conn,
    )
    conn.close()
    return df


def phase_for_minutes(minutes_to_start: float) -> Optional[str]:
    """発走までの分数から記録すべきフェーズ"""
    for phase in PHASES:
        target = PHASE_MINUTES[phase]
        if abs(minutes_to_start - target) <= CAPTURE_TOLERANCE_MIN:
            return phase
    if minutes_to_start <= CAPTURE_TOLERANCE_MIN:
        return "T-0"
    return None


def capture_pre_race_snapshot(race_id: str, phase: str) -> dict:
    """指定フェーズでオッズを記録"""
    if phase not in PHASES:
        raise ValueError(f"不正なフェーズ: {phase}")

    from fetch_odds import save_odds

    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    payload = fetch_odds_json(race_id)
    rows = parse_odds(payload)
    if not rows:
        raise ValueError(f"オッズを解析できませんでした: {race_id}")
    count = save_odds(race_id, rows, captured_at=ts, new_snapshot=True)

    with db_session() as conn:
        migrate_pre_race_table(conn)
        conn.execute(
            """
            INSERT INTO race_snapshots (race_id, phase, captured_at, row_count, status)
            VALUES (?, ?, ?, ?, 'ok')
            ON CONFLICT(race_id, phase) DO UPDATE SET
                captured_at = excluded.captured_at,
                row_count = excluded.row_count,
                status = excluded.status,
                created_at = datetime('now', 'localtime')
            """,
            (race_id, phase, ts, count),
        )
    return {"race_id": race_id, "phase": phase, "ok": True, "count": count, "captured_at": ts}


def poll_pre_race_due(within_hours: float = 3.0) -> list[dict]:
    """発走前ウィンドウ内のレースを走査し、未記録フェーズを取得"""
    upcoming = list_upcoming_races(within_hours=within_hours)
    if upcoming.empty:
        return []

    existing = load_race_snapshots()
    done = set(
        zip(existing["race_id"].astype(str), existing["phase"].astype(str))
        if not existing.empty
        else []
    )

    results: list[dict] = []
    for _, row in upcoming.iterrows():
        race_id = str(row["race_id"])
        minutes = float(row["minutes_to_start"])
        phase = phase_for_minutes(minutes)
        if not phase or (race_id, phase) in done:
            continue
        try:
            results.append(capture_pre_race_snapshot(race_id, phase))
        except Exception as e:
            results.append(
                {"race_id": race_id, "phase": phase, "ok": False, "error": str(e)}
            )
    return results


def load_phase_odds(bet_type: str = "3連単") -> pd.DataFrame:
    """フェーズラベル付きオッズ履歴"""
    conn = get_connection()
    migrate_pre_race_table(conn)
    df = pd.read_sql(
        """
        SELECT
            rs.race_id, rs.phase, rs.captured_at,
            r.race_date, r.venue_name, r.race_no, r.race_start,
            o.bet_type, o.combination, o.odds
        FROM race_snapshots rs
        JOIN races r ON rs.race_id = r.race_id
        JOIN odds o ON o.race_id = rs.race_id AND o.captured_at = rs.captured_at
        WHERE o.bet_type = ?
        ORDER BY rs.race_id, rs.phase
        """,
        conn,
        params=(bet_type,),
    )
    conn.close()
    return df


def _phase_order(phase: str) -> int:
    return {"T-30": 0, "T-10": 1, "T-0": 2}.get(phase, 99)


def build_pre_race_change_frame(df: pd.DataFrame, bet_type: str = "3連単") -> pd.DataFrame:
    """T-30 → 直前（T-0 or 最新フェーズ）のオッズ変化"""
    sub = df[df["bet_type"] == bet_type].copy() if "bet_type" in df.columns else df.copy()
    if sub.empty:
        return pd.DataFrame()

    rows: list[dict] = []
    for race_id, grp in sub.groupby("race_id"):
        phases = sorted(grp["phase"].unique(), key=_phase_order)
        if len(phases) < 2:
            continue
        base_phase, latest_phase = phases[0], phases[-1]
        base_ts = grp[grp["phase"] == base_phase]["captured_at"].iloc[0]
        latest_ts = grp[grp["phase"] == latest_phase]["captured_at"].iloc[0]
        base = _enrich_implied(grp[(grp["phase"] == base_phase) & (grp["captured_at"] == base_ts)])
        latest = _enrich_implied(grp[(grp["phase"] == latest_phase) & (grp["captured_at"] == latest_ts)])
        meta = grp.iloc[0]

        lat = latest[
            ["combination", "odds", "prob_share", "ninki_rank", "distortion_ratio"]
        ].rename(
            columns={
                "odds": "odds_new",
                "prob_share": "prob_share_new",
                "ninki_rank": "ninki_rank_new",
            }
        )
        old = base[["combination", "odds", "prob_share", "ninki_rank"]].rename(
            columns={
                "odds": "odds_old",
                "prob_share": "prob_share_old",
                "ninki_rank": "ninki_rank_old",
            }
        )
        merged = lat.merge(old, on="combination", how="inner")
        for _, row in merged.iterrows():
            old_o = float(row["odds_old"])
            new_o = float(row["odds_new"])
            if old_o <= 0:
                continue
            change_pct = round((old_o - new_o) / old_o * 100, 1)
            share_delta = round(
                (float(row["prob_share_new"]) - float(row["prob_share_old"])) * 100, 2
            )
            rank_delta = int(row["ninki_rank_old"]) - int(row["ninki_rank_new"])
            rows.append(
                {
                    "race_id": race_id,
                    "venue_name": meta["venue_name"],
                    "race_no": meta["race_no"],
                    "race_start": meta.get("race_start"),
                    "combination": row["combination"],
                    "odds_old": old_o,
                    "odds_new": new_o,
                    "change_pct": change_pct,
                    "share_delta": share_delta,
                    "ninki_rank_new": int(row["ninki_rank_new"]),
                    "ninki_rank_old": int(row["ninki_rank_old"]),
                    "rank_delta": rank_delta,
                    "distortion_ratio": round(float(row["distortion_ratio"]), 2),
                    "base_phase": base_phase,
                    "latest_phase": latest_phase,
                    "hot_sell": change_pct >= HOT_SELL_DROP_PCT,
                    "sudden": abs(change_pct) >= SUDDEN_CHANGE_PCT,
                    "surge": change_pct >= SUDDEN_CHANGE_PCT,
                    "drop": change_pct <= -SUDDEN_CHANGE_PCT,
                }
            )

    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values("change_pct", ascending=False)


def build_pre_race_latest_frame(df: pd.DataFrame, bet_type: str = "3連単") -> pd.DataFrame:
    """最新フェーズ（T-0 優先）の市場状態"""
    sub = df.copy()
    if sub.empty:
        return pd.DataFrame()

    rows: list[dict] = []
    for race_id, grp in sub.groupby("race_id"):
        phases = sorted(grp["phase"].unique(), key=_phase_order, reverse=True)
        latest_phase = phases[0]
        latest_ts = grp[grp["phase"] == latest_phase]["captured_at"].iloc[0]
        latest = _enrich_implied(grp[(grp["phase"] == latest_phase) & (grp["captured_at"] == latest_ts)])
        meta = grp.iloc[0]
        fav = latest.loc[latest["odds"].idxmin()]
        fav_share = float(fav["prob_share"]) * 100

        base_share = fav_share
        base_phases = sorted(grp["phase"].unique(), key=_phase_order)
        if len(base_phases) >= 2:
            base_phase = base_phases[0]
            base_ts = grp[grp["phase"] == base_phase]["captured_at"].iloc[0]
            base = _enrich_implied(grp[(grp["phase"] == base_phase) & (grp["captured_at"] == base_ts)])
            base_fav = base.loc[base["odds"].idxmin()]
            base_share = float(base_fav["prob_share"]) * 100

        for _, row in latest.iterrows():
            rows.append(
                {
                    "race_id": race_id,
                    "venue_name": meta["venue_name"],
                    "race_no": meta["race_no"],
                    "race_start": meta.get("race_start"),
                    "phase": latest_phase,
                    "combination": row["combination"],
                    "odds": float(row["odds"]),
                    "prob_share": round(float(row["prob_share"]) * 100, 2),
                    "distortion_ratio": round(float(row["distortion_ratio"]), 2),
                    "ninki_rank": int(row["ninki_rank"]),
                    "ninki_concentration": round(fav_share, 1),
                    "ninki_concentration_delta": round(fav_share - base_share, 1),
                    "fav_combo": str(fav["combination"]),
                    "fav_odds": float(fav["odds"]),
                    "captured_at": latest_ts,
                }
            )
    return pd.DataFrame(rows)


def build_honmei_overheat(alerts: pd.DataFrame, latest: pd.DataFrame) -> pd.DataFrame:
    """本命過熱（人気集中率上昇 + 低オッズ）"""
    if alerts.empty or latest.empty:
        return pd.DataFrame()

    if "ninki_concentration_delta" in latest.columns:
        race_meta = (
            latest.groupby("race_id")
            .agg(
                ninki_concentration_delta=("ninki_concentration_delta", "first"),
                fav_odds=("fav_odds", "first"),
            )
            .reset_index()
        )
    else:
        race_meta = (
            latest.groupby("race_id")
            .agg(fav_odds=("fav_odds", "first"))
            .reset_index()
        )
        race_meta["ninki_concentration_delta"] = 0.0

    merged = alerts.merge(race_meta, on="race_id", how="left", suffixes=("", "_meta"))
    if "ninki_concentration_delta" not in merged.columns:
        merged["ninki_concentration_delta"] = 0.0
    if "fav_odds_meta" in merged.columns:
        merged["fav_odds"] = merged["fav_odds_meta"].fillna(merged.get("fav_odds"))
    overheat = merged[
        (merged["danger_favorite"])
        | (merged["ninki_concentration_delta"].fillna(0) >= 3)
        | (
            (merged["ninki_concentration"] >= 14)
            & (merged["fav_odds"].fillna(99) < 10)
        )
    ]
    return overheat.sort_values("ninki_concentration", ascending=False)


def build_hole_candidates(changes: pd.DataFrame, latest: pd.DataFrame) -> pd.DataFrame:
    """穴候補（人気薄・歪み・急上昇）"""
    if latest.empty:
        return pd.DataFrame()
    holes = latest[
        (latest["ninki_rank"] >= DARK_HORSE_RANK_MIN)
        & (latest["distortion_ratio"] >= 1.8)
    ].copy()
    if not changes.empty:
        ch_map = changes.set_index(["race_id", "combination"])["change_pct"].to_dict()
        holes["change_pct"] = holes.apply(
            lambda r: ch_map.get((r["race_id"], r["combination"]), 0.0),
            axis=1,
        )
        holes = holes[
            (holes["change_pct"] >= SUDDEN_CHANGE_PCT) | (holes["distortion_ratio"] >= 2.2)
        ]
        holes = holes.sort_values(["change_pct", "distortion_ratio"], ascending=[False, False])
    else:
        holes["change_pct"] = 0.0
        holes = holes.sort_values("distortion_ratio", ascending=False)
    return holes.head(25)


def compute_pre_race_adjust(
    row: pd.Series,
    race_alert: Optional[pd.Series],
    changes: pd.DataFrame,
) -> tuple[float, list[str]]:
    """直前補正スコア（-15〜+15）"""
    race_id = str(row["race_id"])
    adj = 0.0
    reasons: list[str] = []

    ch = changes[changes["race_id"] == race_id] if not changes.empty else pd.DataFrame()
    picks = [
        str(row.get(f"pick{i}_combo") or "")
        for i in (1, 2, 3)
    ]
    picks = [p for p in picks if p]

    if not ch.empty and picks:
        pick_changes = ch[ch["combination"].isin(picks)]
        for _, pc in pick_changes.iterrows():
            cp = float(pc["change_pct"])
            if cp >= HOT_SELL_DROP_PCT:
                adj += 5.0
                reasons.append(f"狙い目{pc['combination']} 急変+{cp:.0f}%")
            elif cp >= SUDDEN_CHANGE_PCT:
                adj += 3.0
                reasons.append(f"狙い目{pc['combination']} 上昇+{cp:.0f}%")

    if not ch.empty:
        hole_surge = ch[
            (ch["ninki_rank_new"] >= DARK_HORSE_RANK_MIN)
            & (ch["change_pct"] >= HOT_SELL_DROP_PCT)
        ]
        if not hole_surge.empty:
            top = hole_surge.iloc[0]
            adj += 4.0
            reasons.append(
                f"穴{top['combination']} 人気急上昇({top['change_pct']:+.0f}%)"
            )

    if race_alert is not None and not (isinstance(race_alert, pd.Series) and race_alert.empty):
        alert = race_alert if isinstance(race_alert, pd.Series) else pd.Series(race_alert)
        level = float(alert.get("market_alert_level") or 0)
        fav_share = float(alert.get("ninki_concentration") or 0)
        if alert.get("danger_favorite"):
            adj -= 8.0
            reasons.append(f"危険本命（集中{fav_share:.0f}%）")
        elif level >= ALERT_LEVEL_HIGH:
            adj -= 6.0
            reasons.append(f"市場警戒{level:.0f}（高）")
        elif level >= ALERT_LEVEL_MID:
            adj -= 3.0
            reasons.append(f"市場警戒{level:.0f}（中）")

        delta = float(alert.get("ninki_concentration_delta") or 0)
        if delta >= 4:
            adj -= 4.0
            reasons.append(f"人気集中+{delta:.1f}pt（過熱）")
        elif delta <= -3:
            adj += 2.0
            reasons.append(f"人気分散{delta:.1f}pt")

    if not ch.empty:
        max_drop = float(ch["change_pct"].min())
        if max_drop <= -15:
            adj -= 3.0
            reasons.append("大穴オッズ上昇（期待値低下）")

    adj = max(-PRE_RACE_ADJ_MAX, min(PRE_RACE_ADJ_MAX, adj))
    return round(adj, 1), reasons[:5]


def apply_pre_race_to_scores(
    scores: pd.DataFrame,
    bet_type: str = "3連単",
    bundle: Optional[dict] = None,
) -> pd.DataFrame:
    """AIスコアに直前補正を反映"""
    if scores.empty:
        return scores

    b = bundle or get_pre_race_bundle(bet_type)
    alerts = b.get("race_alerts", pd.DataFrame())
    changes = b.get("changes", pd.DataFrame())
    alert_map = (
        {str(r["race_id"]): r for _, r in alerts.iterrows()}
        if not alerts.empty
        else {}
    )

    out = scores.copy()
    adjusts: list[float] = []
    reasons_list: list[str] = []
    final_scores: list[float] = []

    for _, row in out.iterrows():
        alert = alert_map.get(str(row["race_id"]))
        adj, reasons = compute_pre_race_adjust(row, alert, changes)
        base = float(row.get("ai_total_score") or 0)
        adjusts.append(adj)
        reasons_list.append(" / ".join(reasons) if reasons else "")
        final_scores.append(round(max(0, min(100, base + adj)), 1))

    out["pre_race_adjust"] = adjusts
    out["pre_race_reasons"] = reasons_list
    out["pre_race_score"] = final_scores
    if adjusts and any(a != 0 for a in adjusts):
        out["ev_rank"] = out["pre_race_score"].apply(_ev_rank_from_score)
    return out


def _ev_rank_from_score(score: float) -> str:
    from ai_score import _ev_rank

    return _ev_rank(float(score))


def get_pre_race_bundle(bet_type: str = "3連単") -> dict:
    """Streamlit / CLI 用バンドル"""
    phase_df = load_phase_odds(bet_type)
    snapshots = load_race_snapshots()
    has_phase = not phase_df.empty

    if has_phase:
        latest = build_pre_race_latest_frame(phase_df, bet_type)
        changes = build_pre_race_change_frame(phase_df, bet_type)
    else:
        from market_monitor import build_latest_market_frame, build_odds_change_frame, load_odds_history

        history = load_odds_history(bet_type)
        latest = build_latest_market_frame(history, bet_type)
        changes = build_odds_change_frame(history, bet_type)

    race_alerts = build_race_alerts(latest, changes, bet_type)
    if not race_alerts.empty and not latest.empty:
        delta_map = (
            latest.groupby("race_id")["ninki_concentration_delta"]
            .first()
            .to_dict()
            if "ninki_concentration_delta" in latest.columns
            else {}
        )
        race_alerts = race_alerts.copy()
        race_alerts["ninki_concentration_delta"] = race_alerts["race_id"].map(delta_map).fillna(0)

    surge = (
        changes[changes["change_pct"] >= SUDDEN_CHANGE_PCT].sort_values(
            "change_pct", ascending=False
        )
        if not changes.empty
        else pd.DataFrame()
    )
    drop = (
        changes[changes["change_pct"] <= -SUDDEN_CHANGE_PCT].sort_values(
            "change_pct", ascending=True
        )
        if not changes.empty
        else pd.DataFrame()
    )

    phase_counts = (
        snapshots.groupby("phase").size().to_dict() if not snapshots.empty else {}
    )

    return {
        "has_data": has_phase or not changes.empty,
        "has_phase_snapshots": has_phase,
        "bet_type": bet_type,
        "snapshots": snapshots,
        "phase_counts": phase_counts,
        "latest": latest,
        "changes": changes,
        "race_alerts": race_alerts,
        "surge_ranking": surge.head(30),
        "drop_ranking": drop.head(30),
        "danger_favorites": build_dangerous_favorites(race_alerts),
        "honmei_overheat": build_honmei_overheat(race_alerts, latest),
        "hole_candidates": build_hole_candidates(changes, latest),
        "fig_heatmap": fig_market_heatmap(changes, latest),
        "needs_phase_hint": not has_phase,
        "upcoming_races": list_upcoming_races(within_hours=3),
    }


def build_pre_race_lines(bet_type: str = "3連単") -> list[str]:
    b = get_pre_race_bundle(bet_type)
    lines = [f"【直前分析】券種={bet_type}", ""]
    if b["needs_phase_hint"]:
        lines.append("  ※ T-30/T-10/T-0 記録は `python main.py pre-race --poll` を発走前に実行")
    lines.append(f"  フェーズ記録: {b['phase_counts']}")
    lines.append("")

    lines.append("--- 急上昇ランキング ---")
    if b["surge_ranking"].empty:
        lines.append("  （なし）")
    else:
        lines.append(
            b["surge_ranking"][
                ["venue_name", "race_no", "combination", "change_pct", "rank_delta"]
            ].head(10).to_string(index=False)
        )
    lines.append("")

    lines.append("--- 市場警戒レベル TOP ---")
    if b["race_alerts"].empty:
        lines.append("  （なし）")
    else:
        lines.append(
            b["race_alerts"][
                ["venue_name", "race_no", "market_alert_level", "ninki_concentration"]
            ].head(8).to_string(index=False)
        )
    lines.append("")
    return lines
