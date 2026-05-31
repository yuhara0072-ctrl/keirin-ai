"""異常検知（市場の歪み・オッズ歪み）"""

from dataclasses import dataclass

import pandas as pd

from analyze import analyze_by_odds_bucket, winning_combinations
from db import get_connection

RECOVERY_ALERT = 100.0
PROB_SHARE_ALERT = 0.08
ODDS_Z_ALERT = 2.5
EXTREME_LOW_ODDS = 1.5
HIGH_ODDS_PERCENTILE = 0.95
HIGH_ODDS_MIN = 100.0

# オッズ歪み: 均等配分比の何倍以上で「偏り」とするか
DISTORTION_RATIO_ALERT = 3.0
FAVORITE_SHARE_ALERT = 0.25  # 1番人気が暗黙確率の25%超（60点なら均等約1.7%）


@dataclass
class Anomaly:
    race_id: str
    anomaly_type: str
    bet_type: str
    combination: str
    message: str
    score: float


def load_latest_odds_frame() -> pd.DataFrame:
    conn = get_connection()
    query = """
        WITH latest AS (
            SELECT race_id, MAX(captured_at) AS ts
            FROM odds
            GROUP BY race_id
        )
        SELECT
            r.race_id,
            r.race_date,
            r.venue_name,
            res.finish_order,
            o.bet_type,
            o.combination,
            o.odds,
            o.captured_at
        FROM odds o
        JOIN latest l
          ON o.race_id = l.race_id AND o.captured_at = l.ts
        JOIN races r ON o.race_id = r.race_id
        LEFT JOIN results res ON r.race_id = res.race_id
    """
    df = pd.read_sql(query, conn)
    conn.close()
    return df


def _to_frame(anomalies: list[Anomaly]) -> pd.DataFrame:
    if not anomalies:
        return pd.DataFrame(
            columns=["race_id", "anomaly_type", "bet_type", "combination", "message", "score"]
        )
    return pd.DataFrame([a.__dict__ for a in anomalies]).sort_values(
        "score", ascending=False
    )


def detect_recovery_spike(bet_type: str = "3連単") -> list[Anomaly]:
    summary = analyze_by_odds_bucket(bet_type)
    if summary.empty:
        return []

    found: list[Anomaly] = []
    for row in summary.itertuples():
        if row.recovery_rate < RECOVERY_ALERT:
            continue
        found.append(
            Anomaly(
                race_id="(集計)",
                anomaly_type="回収率スパイク",
                bet_type=row.bet_type,
                combination=row.odds_bucket,
                message=f"回収率 {row.recovery_rate}%（的中率 {row.hit_rate}%）",
                score=float(row.recovery_rate),
            )
        )
    return found


def detect_odds_distortion(df: pd.DataFrame, bet_type: str = "3連単") -> list[Anomaly]:
    """
    オッズ歪み検知:
    - 暗黙確率シェアが均等配分の数倍（特定組への過集中）
    - 1番人気への過剰投票
    - 結果あり: 的中組が市場予想より割高（割安的中）
    """
    sub = df[df["bet_type"] == bet_type].copy()
    if sub.empty:
        return []

    found: list[Anomaly] = []
    for race_id, group in sub.groupby("race_id"):
        n = len(group)
        if n == 0:
            continue
        fair_share = 1.0 / n

        group = group.copy()
        group["implied_prob"] = 1.0 / group["odds"]
        total_prob = group["implied_prob"].sum()
        group["prob_share"] = group["implied_prob"] / total_prob
        group["distortion_ratio"] = group["prob_share"] / fair_share

        # 1番人気の過集中
        fav = group.loc[group["odds"].idxmin()]
        fav_share = float(fav["prob_share"])
        if fav_share >= FAVORITE_SHARE_ALERT:
            found.append(
                Anomaly(
                    race_id=race_id,
                    anomaly_type="オッズ歪み",
                    bet_type=bet_type,
                    combination=str(fav["combination"]),
                    message=(
                        f"1番人気への資金集中 シェア{fav_share*100:.1f}% "
                        f"（オッズ{fav['odds']}倍・均等比{fav['distortion_ratio']:.1f}倍）"
                    ),
                    score=fav_share * 100,
                )
            )

        # 個別組み合わせの過集中
        hot = group[group["distortion_ratio"] >= DISTORTION_RATIO_ALERT]
        for row in hot.itertuples():
            if row.combination == fav["combination"] and fav_share >= FAVORITE_SHARE_ALERT:
                continue
            found.append(
                Anomaly(
                    race_id=race_id,
                    anomaly_type="オッズ歪み",
                    bet_type=bet_type,
                    combination=row.combination,
                    message=(
                        f"投票偏り 均等比{row.distortion_ratio:.1f}倍 "
                        f"（シェア{row.prob_share*100:.1f}%・{row.odds}倍）"
                    ),
                    score=float(row.distortion_ratio),
                )
            )

        # 的中組が人気薄（市場が過小評価）
        fo = group["finish_order"].iloc[0] if "finish_order" in group.columns else None
        if pd.notna(fo) and str(fo).strip():
            wins = winning_combinations(bet_type, str(fo))
            win_rows = group[group["combination"].isin(wins)]
            ranks = group["odds"].rank(method="first", ascending=True)
            for row in win_rows.itertuples():
                rank = int(ranks.loc[row.Index])
                if rank >= 5:
                    found.append(
                        Anomaly(
                            race_id=race_id,
                            anomaly_type="オッズ歪み",
                            bet_type=bet_type,
                            combination=row.combination,
                            message=(
                                f"的中が{rank}番人気相当（{row.odds}倍）"
                                "→ 市場は過小評価の可能性"
                            ),
                            score=float(row.odds),
                        )
                    )

    return found


def detect_prob_concentration(df: pd.DataFrame, bet_type: str = "3連単") -> list[Anomaly]:
    sub = df[df["bet_type"] == bet_type].copy()
    if sub.empty:
        return []

    sub["implied_prob"] = 1.0 / sub["odds"]
    sub["prob_share"] = sub.groupby("race_id")["implied_prob"].transform(
        lambda x: x / x.sum()
    )

    found: list[Anomaly] = []
    for row in sub[sub["prob_share"] >= PROB_SHARE_ALERT].itertuples():
        share_pct = round(row.prob_share * 100, 1)
        found.append(
            Anomaly(
                race_id=row.race_id,
                anomaly_type="確率集中",
                bet_type=bet_type,
                combination=row.combination,
                message=f"暗黙確率シェア {share_pct}%（オッズ {row.odds}倍）",
                score=share_pct,
            )
        )
    return found


def detect_odds_outliers(df: pd.DataFrame, bet_type: str = "3連単") -> list[Anomaly]:
    sub = df[df["bet_type"] == bet_type].copy()
    if sub.empty:
        return []

    def zscore(group: pd.Series) -> pd.Series:
        std = group.std()
        if std == 0 or pd.isna(std):
            return pd.Series(0.0, index=group.index)
        return (group - group.mean()) / std

    sub["odds_z"] = sub.groupby("race_id")["odds"].transform(zscore)
    found: list[Anomaly] = []

    for row in sub[sub["odds_z"].abs() >= ODDS_Z_ALERT].itertuples():
        direction = "高め" if row.odds_z > 0 else "低め"
        found.append(
            Anomaly(
                race_id=row.race_id,
                anomaly_type="オッズ乖離",
                bet_type=bet_type,
                combination=row.combination,
                message=f"レース内でオッズが{direction}（z={row.odds_z:.1f}、{row.odds}倍）",
                score=abs(float(row.odds_z)),
            )
        )
    return found


def detect_extreme_odds(df: pd.DataFrame, bet_type: str = "3連単") -> list[Anomaly]:
    sub = df[df["bet_type"] == bet_type]
    found: list[Anomaly] = []

    for _, group in sub.groupby("race_id"):
        high_bar = max(group["odds"].quantile(HIGH_ODDS_PERCENTILE), HIGH_ODDS_MIN)
        for row in group.itertuples():
            if row.odds >= high_bar:
                found.append(
                    Anomaly(
                        race_id=row.race_id,
                        anomaly_type="極端高配当",
                        bet_type=bet_type,
                        combination=row.combination,
                        message=f"レース上位オッズ {row.odds}倍（閾値 {high_bar:.0f}倍）",
                        score=float(row.odds),
                    )
                )
            elif row.odds <= EXTREME_LOW_ODDS:
                found.append(
                    Anomaly(
                        race_id=row.race_id,
                        anomaly_type="極端人気",
                        bet_type=bet_type,
                        combination=row.combination,
                        message=f"超低オッズ {row.odds}倍",
                        score=100.0 / row.odds,
                    )
                )
    return found


def detect_winning_value(df: pd.DataFrame, bet_type: str = "3連単") -> list[Anomaly]:
    sub = df[df["bet_type"] == bet_type].dropna(subset=["finish_order"])
    if sub.empty:
        return []

    found: list[Anomaly] = []
    for race_id, group in sub.groupby("race_id"):
        fo = group["finish_order"].iloc[0]
        wins = winning_combinations(bet_type, fo)
        if not wins:
            continue
        median = group["odds"].median()
        for combo in wins:
            row = group[group["combination"] == combo]
            if row.empty:
                continue
            odds = float(row["odds"].iloc[0])
            if odds >= median * 1.5:
                found.append(
                    Anomaly(
                        race_id=race_id,
                        anomaly_type="的中高配当",
                        bet_type=bet_type,
                        combination=combo,
                        message=f"的中オッズ {odds}倍はレース中央値 {median:.1f}倍の1.5倍超",
                        score=odds / median,
                    )
                )
    return found


def detect_all(bet_type: str = "3連単") -> pd.DataFrame:
    odds_df = load_latest_odds_frame()
    if odds_df.empty:
        return _to_frame([])

    anomalies: list[Anomaly] = []
    anomalies.extend(detect_odds_distortion(odds_df, bet_type))
    anomalies.extend(detect_recovery_spike(bet_type))
    anomalies.extend(detect_prob_concentration(odds_df, bet_type))
    anomalies.extend(detect_odds_outliers(odds_df, bet_type))
    anomalies.extend(detect_extreme_odds(odds_df, bet_type))
    anomalies.extend(detect_winning_value(odds_df, bet_type))

    df = _to_frame(anomalies)
    if df.empty:
        return df
    return df.drop_duplicates(
        subset=["race_id", "anomaly_type", "combination", "message"],
        keep="first",
    )


def print_report(bet_type: str = "3連単") -> None:
    from report import build_detect_lines

    for line in build_detect_lines(bet_type):
        print(line)


if __name__ == "__main__":
    import sys

    bt = sys.argv[1] if len(sys.argv) > 1 else "3連単"
    print_report(bt)
