"""グラフ用データ集計と Plotly チャート生成"""

import logging
from typing import Callable, Optional

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from ai_score import build_race_scores
from analyze import analyze_by_venue, load_bet_frame
from db import get_connection

logger = logging.getLogger(__name__)

HIGH_SCORE_DEFAULT = 65
PLOTLY_TEMPLATE = "plotly_white"
COLOR_RECOVERY = "#2563eb"
COLOR_HIT = "#16a34a"
COLOR_SCORE = "#7c3aed"


def _ensure_columns(df: pd.DataFrame, defaults: dict[str, object]) -> pd.DataFrame:
    """欠損カラムを補完（Plotly hover_data / 軸参照の落ち込み防止）"""
    out = df.copy()
    for col, default in defaults.items():
        if col not in out.columns:
            if out.empty:
                out[col] = pd.Series(dtype="object")
            else:
                out[col] = default
    return out


def _coalesce_column(df: pd.DataFrame, base: str) -> pd.DataFrame:
    """merge 後の hit_rate_x / hit_rate_y 等を base に統合"""
    if base in df.columns:
        return df
    out = df.copy()
    x, y = f"{base}_x", f"{base}_y"
    if x in out.columns and y in out.columns:
        out[base] = out[x].combine_first(out[y])
        out = out.drop(columns=[x, y], errors="ignore")
    elif x in out.columns:
        out[base] = out[x]
        out = out.drop(columns=[x], errors="ignore")
    elif y in out.columns:
        out[base] = out[y]
        out = out.drop(columns=[y], errors="ignore")
    return out


def _hover_data_existing(df: pd.DataFrame, columns: list[str]) -> list[str]:
    """DataFrame に実在する列だけを hover_data に渡す"""
    seen: set[str] = set()
    out: list[str] = []
    for col in columns:
        if col in df.columns and col not in seen:
            seen.add(col)
            out.append(col)
    return out


def _safe_chart(build: Callable[[], go.Figure], title: str) -> go.Figure:
    try:
        return build()
    except Exception as exc:
        logger.warning("chart build failed (%s): %s", title, exc)
        fig = go.Figure()
        fig.update_layout(title=f"{title}（生成エラー）", template=PLOTLY_TEMPLATE)
        return fig


def _race_summary(bet_type: str = "3連単") -> pd.DataFrame:
    """レース単位の回収率・的中率"""
    df = load_bet_frame(bet_type=bet_type)
    if df.empty:
        return pd.DataFrame()

    conn = get_connection()
    race_meta = pd.read_sql(
        "SELECT race_id, race_no FROM races",
        conn,
    )
    conn.close()
    df = df.merge(race_meta, on="race_id", how="left")

    agg = (
        df.groupby(["race_date", "race_id", "venue_name", "race_no"], dropna=False)
        .agg(
            total_bet=("bet_yen", "sum"),
            total_return=("return_yen", "sum"),
            hits=("hit", "sum"),
            bets=("bet_yen", "count"),
        )
        .reset_index()
    )
    agg["recovery_rate"] = (agg["total_return"] / agg["total_bet"] * 100).round(1)
    agg["hit_rate"] = (agg["hits"] / agg["bets"].replace(0, pd.NA) * 100).round(1)
    agg["hit_rate"] = agg["hit_rate"].fillna(0.0)
    agg["race_label"] = (
        agg["race_date"].astype(str)
        + " "
        + agg["venue_name"].astype(str)
        + " "
        + agg["race_no"].astype(str)
        + "R"
    )
    agg = agg.sort_values(["race_date", "race_id"]).reset_index(drop=True)
    agg["race_order"] = range(1, len(agg) + 1)
    agg["cum_bet"] = agg["total_bet"].cumsum()
    agg["cum_return"] = agg["total_return"].cumsum()
    agg["cum_recovery_rate"] = (agg["cum_return"] / agg["cum_bet"] * 100).round(1)
    return agg


def recovery_trend_by_date(bet_type: str = "3連単") -> pd.DataFrame:
    """開催日別回収率"""
    summary = _race_summary(bet_type)
    if summary.empty:
        return pd.DataFrame()

    by_date = (
        summary.groupby("race_date", dropna=False)
        .agg(
            races=("race_id", "count"),
            total_bet=("total_bet", "sum"),
            total_return=("total_return", "sum"),
            hits=("hits", "sum"),
            bets=("bets", "sum"),
        )
        .reset_index()
    )
    by_date["recovery_rate"] = (by_date["total_return"] / by_date["total_bet"] * 100).round(1)
    by_date["hit_rate"] = (by_date["hits"] / by_date["bets"] * 100).round(1)
    return by_date.sort_values("race_date")


def hit_rate_trend_by_date(bet_type: str = "3連単") -> pd.DataFrame:
    return recovery_trend_by_date(bet_type)


def venue_recovery_ranking(bet_type: str = "3連単") -> pd.DataFrame:
    df = analyze_by_venue(bet_type)
    if df.empty:
        return df
    return df.sort_values("recovery_rate", ascending=True)


def high_score_races(
    bet_type: str = "3連単",
    min_score: float = HIGH_SCORE_DEFAULT,
) -> pd.DataFrame:
    scores = build_race_scores(bet_type)
    if scores.empty:
        return scores
    cols = [
        "race_id",
        "race_date",
        "venue_name",
        "race_no",
        "ai_total_score",
        "ev_rank",
        "danger_level",
        "are_forecast",
        "honmei_trust",
        "pick1_combo",
        "pick1_odds",
        "top3_picks",
    ]
    out = scores[scores["ai_total_score"] >= min_score].sort_values(
        "ai_total_score", ascending=False
    )
    return out[[c for c in cols if c in out.columns]]


def fig_recovery_trend(summary: pd.DataFrame, by_date: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    if summary.empty:
        fig.update_layout(title="回収率推移（データなし）", template=PLOTLY_TEMPLATE)
        return fig

    fig.add_trace(
        go.Scatter(
            x=summary["race_order"],
            y=summary["recovery_rate"],
            mode="markers+lines",
            name="レース別回収率",
            line=dict(color=COLOR_RECOVERY, width=1, dash="dot"),
            marker=dict(size=10),
            text=summary["race_label"],
            hovertemplate="%{text}<br>回収率: %{y}%<extra></extra>",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=summary["race_order"],
            y=summary["cum_recovery_rate"],
            mode="lines",
            name="累積回収率",
            line=dict(color="#dc2626", width=3),
            hovertemplate="累積回収率: %{y}%<extra></extra>",
        )
    )
    fig.add_hline(y=100, line_dash="dash", line_color="gray", annotation_text="100%")
    fig.update_layout(
        title="回収率推移（全組100円ずつ購入シミュレーション）",
        xaxis_title="レース順（開催日時系列）",
        yaxis_title="回収率 (%)",
        template=PLOTLY_TEMPLATE,
        hovermode="x unified",
        height=420,
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
    )
    return fig


def fig_recovery_by_date(by_date: pd.DataFrame) -> go.Figure:
    if by_date.empty:
        fig = go.Figure()
        fig.update_layout(title="開催日別回収率（データなし）", template=PLOTLY_TEMPLATE)
        return fig

    fig = px.bar(
        by_date,
        x="race_date",
        y="recovery_rate",
        text="recovery_rate",
        labels={"race_date": "開催日", "recovery_rate": "回収率 (%)"},
        color_discrete_sequence=[COLOR_RECOVERY],
    )
    fig.update_traces(texttemplate="%{text}%", textposition="outside")
    fig.add_hline(y=100, line_dash="dash", line_color="gray")
    fig.update_layout(
        title="開催日別回収率",
        template=PLOTLY_TEMPLATE,
        height=380,
        showlegend=False,
    )
    return fig


def fig_venue_ranking(venue_df: pd.DataFrame) -> go.Figure:
    if venue_df.empty:
        fig = go.Figure()
        fig.update_layout(title="競輪場別回収率（データなし）", template=PLOTLY_TEMPLATE)
        return fig

    fig = px.bar(
        venue_df,
        x="recovery_rate",
        y="venue_name",
        orientation="h",
        text="recovery_rate",
        color="recovery_rate",
        color_continuous_scale="Blues",
        labels={"recovery_rate": "回収率 (%)", "venue_name": "競輪場"},
    )
    fig.update_traces(texttemplate="%{text}%", textposition="outside")
    fig.add_vline(x=100, line_dash="dash", line_color="gray")
    fig.update_layout(
        title="競輪場別回収率ランキング",
        template=PLOTLY_TEMPLATE,
        height=max(320, len(venue_df) * 48),
        coloraxis_showscale=False,
    )
    return fig


def fig_ai_score_distribution(scores: pd.DataFrame) -> go.Figure:
    if scores.empty:
        fig = go.Figure()
        fig.update_layout(title="AIスコア分布（データなし）", template=PLOTLY_TEMPLATE)
        return fig

    fig = px.histogram(
        scores,
        x="ai_total_score",
        nbins=min(20, max(5, len(scores))),
        color_discrete_sequence=[COLOR_SCORE],
        labels={"ai_total_score": "AI総合スコア"},
    )
    for threshold, label, color in [
        (80, "S", "#059669"),
        (65, "A", "#2563eb"),
        (50, "B", "#ca8a04"),
        (35, "C", "#ea580c"),
    ]:
        fig.add_vline(
            x=threshold,
            line_dash="dash",
            line_color=color,
            annotation_text=label,
            annotation_position="top",
        )
    fig.update_layout(
        title="AIスコア分布",
        yaxis_title="レース数",
        template=PLOTLY_TEMPLATE,
        height=400,
        showlegend=False,
    )
    return fig


def fig_hit_rate_trend(summary: pd.DataFrame, by_date: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    if summary.empty:
        fig.update_layout(title="的中率推移（データなし）", template=PLOTLY_TEMPLATE)
        return fig

    summary = _ensure_columns(summary, {"hit_rate": 0.0, "race_order": 0, "race_label": ""})
    by_date = _ensure_columns(by_date, {"hit_rate": 0.0})

    fig.add_trace(
        go.Scatter(
            x=summary["race_order"],
            y=summary["hit_rate"],
            mode="markers+lines",
            name="レース別的中率",
            line=dict(color=COLOR_HIT, width=2),
            marker=dict(size=10),
            text=summary["race_label"],
            hovertemplate="%{text}<br>的中率: %{y}%<extra></extra>",
        )
    )
    if not by_date.empty:
        date_map = by_date.set_index("race_date")["hit_rate"].to_dict()
        summary_dates = summary["race_date"].map(date_map)
        fig.add_trace(
            go.Scatter(
                x=summary["race_order"],
                y=summary_dates,
                mode="lines",
                name="開催日平均",
                line=dict(color="#f59e0b", width=2, dash="dash"),
                hovertemplate="開催日平均: %{y}%<extra></extra>",
            )
        )

    fig.update_layout(
        title="的中率推移",
        xaxis_title="レース順（開催日時系列）",
        yaxis_title="的中率 (%)",
        template=PLOTLY_TEMPLATE,
        height=420,
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
    )
    return fig


def fig_ai_score_scatter(scores: pd.DataFrame, bet_type: str = "3連単") -> go.Figure:
    """スコアと回収率の関係（結果ありレース）"""
    if scores.empty:
        fig = go.Figure()
        fig.update_layout(title="スコア×回収率（データなし）", template=PLOTLY_TEMPLATE)
        return fig

    try:
        summary = _race_summary(bet_type)
        merge_cols = ["race_id", "recovery_rate", "hit_rate"]
        if summary.empty:
            merged = scores.copy()
        else:
            available = [c for c in merge_cols if c in summary.columns]
            merged = scores.merge(summary[available], on="race_id", how="left")

        for col in ("recovery_rate", "hit_rate"):
            merged = _coalesce_column(merged, col)

        merged = _ensure_columns(
            merged,
            {
                "recovery_rate": None,
                "hit_rate": None,
                "venue_name": "",
                "race_no": None,
                "ev_rank": "",
                "honmei_trust": 1.0,
                "ai_total_score": 0.0,
            },
        )

        hover_cols = _hover_data_existing(
            merged, ["venue_name", "race_no", "ev_rank", "hit_rate"]
        )

        scatter_kwargs: dict = {
            "x": "ai_total_score",
            "y": "recovery_rate",
            "color": "ev_rank",
            "labels": {
                "ai_total_score": "AI総合スコア",
                "recovery_rate": "回収率 (%)",
                "ev_rank": "ランク",
            },
            "color_discrete_map": {
                "S": "#059669",
                "A": "#2563eb",
                "B": "#ca8a04",
                "C": "#ea580c",
                "D": "#94a3b8",
            },
        }
        if hover_cols:
            scatter_kwargs["hover_data"] = hover_cols
        if "honmei_trust" in merged.columns and merged["honmei_trust"].notna().any():
            scatter_kwargs["size"] = "honmei_trust"

        fig = px.scatter(merged, **scatter_kwargs)
        fig.update_layout(
            title="AIスコアと回収率（バブル＝本命信頼度）",
            template=PLOTLY_TEMPLATE,
            height=400,
        )
        return fig
    except Exception as exc:
        logger.warning("fig_ai_score_scatter failed: %s", exc)
        fig = go.Figure()
        fig.update_layout(title="スコア×回収率（表示エラー）", template=PLOTLY_TEMPLATE)
        return fig


def get_charts_bundle(
    bet_type: str = "3連単",
    min_score: float = HIGH_SCORE_DEFAULT,
) -> dict:
    """Streamlit 用: データと Plotly 図をまとめて返す"""
    summary = _race_summary(bet_type)
    by_date = recovery_trend_by_date(bet_type)
    venue = venue_recovery_ranking(bet_type)
    scores = build_race_scores(bet_type)
    high = high_score_races(bet_type, min_score)

    has_data = not summary.empty or not scores.empty

    return {
        "has_data": has_data,
        "race_summary": summary,
        "by_date": by_date,
        "venue_ranking": venue,
        "scores": scores,
        "high_score_races": high,
        "min_score": min_score,
        "fig_recovery_trend": _safe_chart(
            lambda: fig_recovery_trend(summary, by_date), "回収率推移"
        ),
        "fig_recovery_by_date": _safe_chart(
            lambda: fig_recovery_by_date(by_date), "開催日別回収率"
        ),
        "fig_venue_ranking": _safe_chart(
            lambda: fig_venue_ranking(venue), "競輪場別回収率"
        ),
        "fig_ai_distribution": _safe_chart(
            lambda: fig_ai_score_distribution(scores), "AIスコア分布"
        ),
        "fig_hit_rate": _safe_chart(
            lambda: fig_hit_rate_trend(summary, by_date), "的中率推移"
        ),
        "fig_score_scatter": _safe_chart(
            lambda: fig_ai_score_scatter(scores, bet_type), "スコア×回収率"
        ),
    }
