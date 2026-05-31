"""リアルタイム市場監視 — オッズ急変・人気集中・歪み"""

from typing import Optional

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from db import get_connection
from detect_anomaly import DISTORTION_RATIO_ALERT, FAVORITE_SHARE_ALERT

# オッズがこの％下落 = 売れ（人気急上昇）
HOT_SELL_DROP_PCT = 12.0
SUDDEN_CHANGE_PCT = 8.0
DARK_HORSE_RANK_MIN = 6
ALERT_LEVEL_HIGH = 70
ALERT_LEVEL_MID = 45


def load_odds_history(bet_type: str = "3連単") -> pd.DataFrame:
    conn = get_connection()
    df = pd.read_sql(
        """
        SELECT
            r.race_id, r.race_date, r.venue_name, r.race_no, r.race_start,
            o.bet_type, o.combination, o.odds, o.captured_at
        FROM odds o
        JOIN races r ON o.race_id = r.race_id
        WHERE o.bet_type = ?
        ORDER BY r.race_id, o.captured_at DESC
        """,
        conn,
        params=(bet_type,),
    )
    conn.close()
    return df


def _enrich_implied(group: pd.DataFrame) -> pd.DataFrame:
    g = group.copy()
    g["implied"] = 1.0 / g["odds"]
    total = g["implied"].sum()
    n = len(g)
    fair = 1.0 / n if n else 0
    g["prob_share"] = g["implied"] / total if total else 0
    g["distortion_ratio"] = g["prob_share"] / fair if fair else 1
    g["ninki_rank"] = g["odds"].rank(method="first", ascending=True).astype(int)
    return g


def _race_snapshot_times(df: pd.DataFrame) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for race_id, grp in df.groupby("race_id"):
        times = sorted(grp["captured_at"].unique(), reverse=True)
        out[race_id] = list(times)
    return out


def build_odds_change_frame(df: pd.DataFrame, bet_type: str = "3連単") -> pd.DataFrame:
    """直前スナップショット比較（急変・異常売れ）"""
    sub = df[df["bet_type"] == bet_type].copy()
    if sub.empty:
        return pd.DataFrame()

    rows: list[dict] = []
    for race_id, grp in sub.groupby("race_id"):
        times = sorted(grp["captured_at"].unique(), reverse=True)
        if len(times) < 2:
            continue
        latest_ts, prev_ts = times[0], times[1]
        latest = _enrich_implied(grp[grp["captured_at"] == latest_ts])
        prev = _enrich_implied(grp[grp["captured_at"] == prev_ts])
        lat = latest[
            ["combination", "odds", "prob_share", "ninki_rank", "distortion_ratio"]
        ].rename(
            columns={
                "odds": "odds_new",
                "prob_share": "prob_share_new",
                "ninki_rank": "ninki_rank_new",
            }
        )
        old = prev[["combination", "odds", "prob_share", "ninki_rank"]].rename(
            columns={
                "odds": "odds_old",
                "prob_share": "prob_share_old",
                "ninki_rank": "ninki_rank_old",
            }
        )
        merged = lat.merge(old, on="combination", how="inner")
        meta = grp.iloc[0]
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
                    "latest_ts": latest_ts,
                    "prev_ts": prev_ts,
                    "hot_sell": change_pct >= HOT_SELL_DROP_PCT,
                    "sudden": abs(change_pct) >= SUDDEN_CHANGE_PCT,
                }
            )

    if not rows:
        return pd.DataFrame()
    out = pd.DataFrame(rows)
    return out.sort_values("change_pct", ascending=False)


def build_latest_market_frame(df: pd.DataFrame, bet_type: str = "3連単") -> pd.DataFrame:
    """最新スナップショットの市場状態"""
    sub = df[df["bet_type"] == bet_type].copy()
    if sub.empty:
        return pd.DataFrame()

    rows: list[dict] = []
    for race_id, grp in sub.groupby("race_id"):
        latest_ts = grp["captured_at"].max()
        latest = _enrich_implied(grp[grp["captured_at"] == latest_ts])
        meta = grp.iloc[0]
        fav = latest.loc[latest["odds"].idxmin()]
        fav_share = float(fav["prob_share"]) * 100

        for _, row in latest.iterrows():
            rows.append(
                {
                    "race_id": race_id,
                    "venue_name": meta["venue_name"],
                    "race_no": meta["race_no"],
                    "race_start": meta.get("race_start"),
                    "combination": row["combination"],
                    "odds": float(row["odds"]),
                    "prob_share": round(float(row["prob_share"]) * 100, 2),
                    "distortion_ratio": round(float(row["distortion_ratio"]), 2),
                    "ninki_rank": int(row["ninki_rank"]),
                    "ninki_concentration": round(fav_share, 1),
                    "fav_combo": str(fav["combination"]),
                    "fav_odds": float(fav["odds"]),
                    "captured_at": latest_ts,
                }
            )

    return pd.DataFrame(rows)


def build_race_alerts(
    latest: pd.DataFrame,
    changes: pd.DataFrame,
    bet_type: str = "3連単",
) -> pd.DataFrame:
    """レース別 市場警戒レベル"""
    if latest.empty:
        return pd.DataFrame()

    alerts: list[dict] = []
    for race_id, grp in latest.groupby("race_id"):
        meta = grp.iloc[0]
        fav_share = float(meta["ninki_concentration"])
        max_dist = float(grp["distortion_ratio"].max())
        ch = changes[changes["race_id"] == race_id] if not changes.empty else pd.DataFrame()

        sudden_n = int(ch["sudden"].sum()) if not ch.empty else 0
        hot_n = int(ch["hot_sell"].sum()) if not ch.empty else 0
        max_change = float(ch["change_pct"].max()) if not ch.empty else 0

        level = 0.0
        level += min(25, sudden_n * 5)
        level += min(20, hot_n * 4)
        level += min(20, max_change * 0.5)
        level += min(15, max(0, fav_share - 10) * 1.2)
        level += min(20, max(0, max_dist - 3) * 3)

        danger_fav = fav_share >= FAVORITE_SHARE_ALERT * 100 * 0.6 and float(meta["fav_odds"]) < 12

        alerts.append(
            {
                "race_id": race_id,
                "venue_name": meta["venue_name"],
                "race_no": meta["race_no"],
                "race_start": meta.get("race_start"),
                "market_alert_level": round(min(100, level), 1),
                "ninki_concentration": fav_share,
                "fav_combo": meta["fav_combo"],
                "fav_odds": meta["fav_odds"],
                "danger_favorite": danger_fav,
                "sudden_count": sudden_n,
                "hot_sell_count": hot_n,
                "max_distortion": max_dist,
                "snapshot_count": grp["captured_at"].nunique(),
                "latest_ts": meta["captured_at"],
            }
        )

    df = pd.DataFrame(alerts)
    return df.sort_values("market_alert_level", ascending=False)


def build_distortion_ranking(latest: pd.DataFrame, top_n: int = 30) -> pd.DataFrame:
    if latest.empty:
        return pd.DataFrame()
    return (
        latest[latest["distortion_ratio"] >= DISTORTION_RATIO_ALERT]
        .sort_values("distortion_ratio", ascending=False)
        .head(top_n)
    )


def build_dangerous_favorites(alerts: pd.DataFrame) -> pd.DataFrame:
    if alerts.empty:
        return pd.DataFrame()
    return alerts[alerts["danger_favorite"]].sort_values(
        "ninki_concentration", ascending=False
    )


def build_undervalued_holes(latest: pd.DataFrame, changes: pd.DataFrame) -> pd.DataFrame:
    """過小評価の穴（高歪み・人気薄・オッズ急落）"""
    if latest.empty:
        return pd.DataFrame()

    holes = latest[
        (latest["ninki_rank"] >= DARK_HORSE_RANK_MIN)
        & (latest["distortion_ratio"] >= 2.0)
    ].copy()

    if not changes.empty:
        ch_map = changes.set_index(["race_id", "combination"])["change_pct"].to_dict()
        holes["change_pct"] = holes.apply(
            lambda r: ch_map.get((r["race_id"], r["combination"]), 0.0),
            axis=1,
        )
        holes = holes.sort_values(
            ["change_pct", "distortion_ratio"], ascending=[False, False]
        )
    else:
        holes["change_pct"] = 0.0
        holes = holes.sort_values("distortion_ratio", ascending=False)

    return holes.head(25)


def build_dark_horse_surge(changes: pd.DataFrame) -> pd.DataFrame:
    """穴人気急上昇（人気順位の改善＋オッズ下落）"""
    if changes.empty:
        return pd.DataFrame()
    sub = changes[
        (changes["ninki_rank_new"] >= DARK_HORSE_RANK_MIN)
        & (
            (changes["rank_delta"] >= 2)
            | (changes["change_pct"] >= HOT_SELL_DROP_PCT)
        )
    ]
    return sub.sort_values(["rank_delta", "change_pct"], ascending=[False, False]).head(25)


def fig_market_heatmap(changes: pd.DataFrame, latest: pd.DataFrame) -> go.Figure:
    """市場ヒートマップ（レース×変化率）"""
    fig = go.Figure()
    if changes.empty and latest.empty:
        fig.update_layout(title="市場ヒートマップ（データなし）")
        return fig

    src = changes if not changes.empty else latest.copy()
    if changes.empty:
        src["change_pct"] = src.get("distortion_ratio", 0)

    src = src.copy()
    src["race_label"] = (
        src["venue_name"].astype(str) + " " + src["race_no"].astype(str) + "R"
    )
    top_races = (
        src.groupby("race_label")["change_pct"]
        .max()
        .sort_values(ascending=False)
        .head(12)
        .index.tolist()
    )
    sub = src[src["race_label"].isin(top_races)]
    top_combos = (
        sub.groupby("combination")["change_pct"]
        .max()
        .sort_values(ascending=False)
        .head(15)
        .index.tolist()
    )
    sub = sub[sub["combination"].isin(top_combos)]

    if sub.empty:
        fig.update_layout(title="市場ヒートマップ（データ不足）")
        return fig

    pivot = sub.pivot_table(
        index="race_label",
        columns="combination",
        values="change_pct",
        aggfunc="max",
    ).fillna(0)

    fig = px.imshow(
        pivot,
        labels=dict(x="組み合わせ", y="レース", color="変化率(%)"),
        color_continuous_scale="RdYlGn_r",
        aspect="auto",
    )
    fig.update_layout(
        title="市場ヒートマップ（赤=オッズ下落・売れ）",
        height=max(400, len(pivot) * 36),
    )
    return fig


def build_last_minute_summary(changes: pd.DataFrame) -> pd.DataFrame:
    """直前変化サマリー"""
    if changes.empty:
        return pd.DataFrame()
    return (
        changes[changes["sudden"]]
        .sort_values("change_pct", ascending=False)
        .head(40)
    )


def get_market_monitor_bundle(bet_type: str = "3連単") -> dict:
    history = load_odds_history(bet_type)
    has_data = not history.empty

    latest = build_latest_market_frame(history, bet_type)
    changes = build_odds_change_frame(history, bet_type)
    race_alerts = build_race_alerts(latest, changes, bet_type)

    snapshot_counts = (
        history.groupby("race_id")["captured_at"].nunique().to_dict()
        if not history.empty
        else {}
    )
    multi_snapshot = sum(1 for v in snapshot_counts.values() if v >= 2)

    return {
        "has_data": has_data,
        "bet_type": bet_type,
        "snapshot_races": len(snapshot_counts),
        "multi_snapshot_races": multi_snapshot,
        "needs_poll_hint": multi_snapshot == 0 and has_data,
        "latest": latest,
        "changes": changes,
        "race_alerts": race_alerts,
        "sudden_ranking": build_last_minute_summary(changes),
        "hot_sell": (
            changes[changes["hot_sell"]].sort_values("change_pct", ascending=False)
            if not changes.empty
            else pd.DataFrame()
        ),
        "danger_favorites": build_dangerous_favorites(race_alerts),
        "undervalued_holes": build_undervalued_holes(latest, changes),
        "dark_horse_surge": build_dark_horse_surge(changes),
        "distortion_ranking": build_distortion_ranking(latest),
        "fig_heatmap": fig_market_heatmap(changes, latest),
        "ninki_by_race": (
            race_alerts[["venue_name", "race_no", "ninki_concentration", "fav_combo", "market_alert_level"]]
            if not race_alerts.empty
            else pd.DataFrame()
        ),
    }


def build_monitor_lines(bet_type: str = "3連単") -> list[str]:
    b = get_market_monitor_bundle(bet_type)
    lines = [f"【市場監視】券種={bet_type}", ""]
    if not b["has_data"]:
        lines.append("オッズデータがありません。")
        return lines

    lines.append(
        f"  スナップショット2回以上: {b['multi_snapshot_races']}レース / "
        f"全{b['snapshot_races']}レース"
    )
    if b["needs_poll_hint"]:
        lines.append("  ※ 急変検知には「オッズ再取得」を2回以上実行してください")
    lines.append("")

    if not b["race_alerts"].empty:
        lines.append("--- 市場警戒レベル TOP ---")
        lines.append(
            b["race_alerts"].head(10).to_string(index=False)
        )
        lines.append("")

    if not b["sudden_ranking"].empty:
        lines.append("--- 急変ランキング TOP10 ---")
        cols = ["venue_name", "race_no", "combination", "odds_old", "odds_new", "change_pct"]
        lines.append(b["sudden_ranking"][cols].head(10).to_string(index=False))
        lines.append("")

    return lines
