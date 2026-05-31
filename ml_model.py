"""XGBoost 予測モデル — 過去データから回収率・期待値を学習"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

from analyze import load_bet_frame, load_entries_frame
from config import DATA_DIR
from learning import (
    _dominant_first_style,
    _line_bucket,
    _ninki_bucket,
    _race_popularity_label,
    _race_style_tag,
)
from race_features import build_race_metrics

MODEL_DIR = DATA_DIR / "models"
MODEL_DIR.mkdir(parents=True, exist_ok=True)

MIN_TRAIN_RACES = 8
RECOVERY_RANK = [(120, "S"), (100, "A"), (85, "B"), (70, "C"), (0, "D")]

CATEGORICAL_FEATURES = [
    "venue_name",
    "race_style",
    "dominant_style",
    "line_bucket",
    "ninki_bucket",
    "popularity_label",
]
NUMERIC_FEATURES = [
    "line_count",
    "nige_count",
    "senko_count",
    "ninki_concentration",
    "are_index",
    "fav_odds",
    "avg_odds",
    "median_odds",
    "ai_total_score",
    "danger_level",
    "honmei_trust",
    "are_forecast",
]


def _bet_type_slug(bet_type: str) -> str:
    mapping = {
        "3連単": "3rentan",
        "3連複": "3renpuku",
        "2車単": "2shatan",
        "2車複": "2shafuku",
        "ワイド": "wide",
    }
    return mapping.get(bet_type, bet_type.replace(" ", "_"))


def _legacy_slug(bet_type: str) -> str:
    return bet_type.replace("連", "ren").replace("車", "sha")


def _resolve_model_file(prefix: str, bet_type: str) -> Path:
    for slug in (_bet_type_slug(bet_type), _legacy_slug(bet_type)):
        path = MODEL_DIR / f"{prefix}_{slug}.json"
        if path.exists():
            return path
    return MODEL_DIR / f"{prefix}_{_bet_type_slug(bet_type)}.json"


def model_path(bet_type: str) -> Path:
    return _resolve_model_file("xgb_recovery", bet_type)


def meta_path(bet_type: str) -> Path:
    return _resolve_model_file("xgb_meta", bet_type)


def recovery_to_rank(recovery: float) -> str:
    for threshold, label in RECOVERY_RANK:
        if recovery >= threshold:
            return label
    return "D"


def _race_recovery(bet_df: pd.DataFrame) -> pd.DataFrame:
    if bet_df.empty:
        return pd.DataFrame()
    agg = (
        bet_df.groupby("race_id")
        .agg(
            total_bet=("bet_yen", "sum"),
            total_return=("return_yen", "sum"),
            hits=("hit", "sum"),
            bets=("bet_yen", "count"),
            avg_odds=("odds", "mean"),
            median_odds=("odds", "median"),
            min_odds=("odds", "min"),
        )
        .reset_index()
    )
    agg["recovery_rate"] = (
        agg["total_return"] / agg["total_bet"].replace(0, np.nan) * 100
    ).round(1)
    agg["hit_rate"] = (agg["hits"] / agg["bets"].replace(0, np.nan) * 100).round(1)
    return agg


def build_feature_frame(
    scores: pd.DataFrame,
    entries: Optional[pd.DataFrame] = None,
    bet_df: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """レース単位の学習/推論用特徴量"""
    if scores.empty:
        return pd.DataFrame()

    ent = entries if entries is not None else load_entries_frame()
    odds_stats = _race_recovery(bet_df) if bet_df is not None else pd.DataFrame()

    rows: list[dict] = []
    for _, row in scores.iterrows():
        race_id = row["race_id"]
        feat = {
            "race_id": race_id,
            "race_date": row.get("race_date"),
            "venue_name": str(row.get("venue_name") or "不明"),
            "race_no": row.get("race_no"),
            "line_count": int(row.get("line_count") or 0),
            "nige_count": int(row.get("nige_count") or 0),
            "senko_count": int(row.get("senko_count") or 0),
            "ninki_concentration": float(row.get("ninki_concentration") or 0),
            "are_index": float(row.get("are_index") or 0),
            "fav_odds": float(row.get("fav_odds") or 0),
            "ai_total_score": float(row.get("pre_race_score") or row.get("ai_total_score") or 0),
            "danger_level": float(row.get("danger_level") or 0),
            "honmei_trust": float(row.get("honmei_trust") or 0),
            "are_forecast": float(row.get("are_forecast") or 0),
            "race_style": _race_style_tag(row, ent),
            "dominant_style": _dominant_first_style(row, ent),
            "line_bucket": _line_bucket(int(row.get("line_count") or 0)),
            "ninki_bucket": _ninki_bucket(float(row.get("ninki_concentration") or 0)),
            "popularity_label": _race_popularity_label(row),
        }
        if not odds_stats.empty and race_id in odds_stats["race_id"].values:
            st = odds_stats[odds_stats["race_id"] == race_id].iloc[0]
            feat["avg_odds"] = float(st.get("avg_odds") or 0)
            feat["median_odds"] = float(st.get("median_odds") or 0)
        else:
            feat["avg_odds"] = float(row.get("fav_odds") or 0)
            feat["median_odds"] = float(row.get("fav_odds") or 0)
        rows.append(feat)

    return pd.DataFrame(rows)


def _encode_features(
    df: pd.DataFrame,
    encoders: Optional[dict[str, dict[str, int]]] = None,
    *,
    fit: bool = False,
) -> tuple[pd.DataFrame, dict[str, dict[str, int]]]:
    enc = {k: dict(v) for k, v in (encoders or {}).items()}
    out = df.copy()

    for col in CATEGORICAL_FEATURES:
        if col not in out.columns:
            out[col] = "不明"
        vals = out[col].astype(str).fillna("不明")
        if fit or col not in enc:
            uniq = sorted(vals.unique())
            enc[col] = {v: i for i, v in enumerate(uniq)}
        mapping = enc[col]
        out[f"{col}_enc"] = vals.map(lambda x: mapping.get(x, -1)).astype(int)

    feature_cols = NUMERIC_FEATURES + [f"{c}_enc" for c in CATEGORICAL_FEATURES]
    for col in NUMERIC_FEATURES:
        if col not in out.columns:
            out[col] = 0.0
        out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0.0)

    return out[feature_cols], enc


def _compute_confidence(pred_recovery: float, meta: dict) -> float:
    r2 = float(meta.get("cv_r2") or 0.5)
    n_train = int(meta.get("n_train") or 0)
    data_factor = min(1.0, n_train / 40)
    center_factor = 1.0 - min(abs(pred_recovery - 100) / 200, 1) * 0.25
    return round(max(5.0, min(99.0, r2 * 100 * data_factor * center_factor)), 1)


def train_ml_model(
    bet_type: str = "3連単",
    scores: Optional[pd.DataFrame] = None,
) -> dict:
    """XGBoost で回収率モデルを学習"""
    try:
        import xgboost as xgb
        from sklearn.model_selection import cross_val_score
    except ImportError as e:
        raise ImportError(
            "XGBoost / scikit-learn が必要です: pip install xgboost scikit-learn"
        ) from e

    if scores is None:
        from ai_score import build_race_scores

        scores = build_race_scores(bet_type)

    bet_df = load_bet_frame(bet_type=bet_type)
    if bet_df.empty:
        return {"ok": False, "error": "結果付きデータがありません"}

    recovery = _race_recovery(bet_df)
    features = build_feature_frame(scores, bet_df=bet_df)
    train_df = features.merge(
        recovery[["race_id", "recovery_rate", "hit_rate"]],
        on="race_id",
        how="inner",
    )

    if len(train_df) < MIN_TRAIN_RACES:
        return {
            "ok": False,
            "error": f"学習には{MIN_TRAIN_RACES}レース以上必要です（現在{len(train_df)}）",
            "n_train": len(train_df),
        }

    X, encoders = _encode_features(train_df, fit=True)
    y = train_df["recovery_rate"].astype(float)

    model = xgb.XGBRegressor(
        n_estimators=80,
        max_depth=4,
        learning_rate=0.08,
        subsample=0.85,
        colsample_bytree=0.85,
        random_state=42,
        objective="reg:squarederror",
    )

    cv_scores = cross_val_score(
        model, X, y, cv=min(5, len(train_df)), scoring="r2"
    )
    cv_r2 = float(np.mean(cv_scores))
    rmse_scores = cross_val_score(
        model, X, y, cv=min(5, len(train_df)), scoring="neg_root_mean_squared_error"
    )
    cv_rmse = float(-np.mean(rmse_scores))

    model.fit(X, y)

    importance = model.feature_importances_
    feat_names = list(X.columns)
    imp_sorted = sorted(
        zip(feat_names, importance), key=lambda x: x[1], reverse=True
    )

    model.save_model(str(model_path(bet_type)))
    meta = {
        "bet_type": bet_type,
        "trained_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "n_train": len(train_df),
        "cv_r2": round(cv_r2, 3),
        "cv_rmse": round(cv_rmse, 2),
        "encoders": encoders,
        "feature_columns": feat_names,
        "feature_importance": [
            {"feature": f, "importance": round(float(i), 4)} for f, i in imp_sorted
        ],
        "target": "recovery_rate",
    }
    meta_path(bet_type).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    return {
        "ok": True,
        "n_train": len(train_df),
        "cv_r2": cv_r2,
        "cv_rmse": cv_rmse,
        "model_path": str(model_path(bet_type)),
        "feature_importance": pd.DataFrame(meta["feature_importance"]),
    }


def load_model_meta(bet_type: str = "3連単") -> Optional[dict]:
    path = meta_path(bet_type)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def model_exists(bet_type: str = "3連単") -> bool:
    return model_path(bet_type).exists() and meta_path(bet_type).exists()


def predict_races(
    bet_type: str = "3連単",
    scores: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """学習済みモデルで回収率・期待値・信頼度を予測"""
    if not model_exists(bet_type):
        return pd.DataFrame()

    try:
        import xgboost as xgb
    except ImportError:
        return pd.DataFrame()

    meta = load_model_meta(bet_type)
    if not meta:
        return pd.DataFrame()

    if scores is None:
        from ai_score import build_race_scores

        scores = build_race_scores(bet_type)

    bet_df = load_bet_frame(bet_type=bet_type)
    features = build_feature_frame(scores, bet_df=bet_df if not bet_df.empty else None)
    if features.empty:
        return pd.DataFrame()

    X, _ = _encode_features(features, encoders=meta.get("encoders", {}), fit=False)
    for col in meta.get("feature_columns", []):
        if col not in X.columns:
            X[col] = 0.0
    X = X[meta["feature_columns"]]

    model = xgb.XGBRegressor()
    model.load_model(str(model_path(bet_type)))
    preds = model.predict(X)

    out = features[
        ["race_id", "race_date", "venue_name", "race_no", "ai_total_score"]
    ].copy()
    out["pred_recovery"] = np.clip(preds, 0, 300).round(1)
    out["pred_confidence"] = [
        _compute_confidence(float(p), meta) for p in out["pred_recovery"]
    ]
    out["pred_ev"] = (
        out["pred_recovery"] * out["pred_confidence"] / 100
    ).round(1)
    out["pred_ev_rank"] = out["pred_recovery"].map(recovery_to_rank)

    if not bet_df.empty:
        actual = _race_recovery(bet_df)[["race_id", "recovery_rate"]].rename(
            columns={"recovery_rate": "actual_recovery"}
        )
        out = out.merge(actual, on="race_id", how="left")

    return out.sort_values("pred_ev", ascending=False)


def get_ml_bundle(
    bet_type: str = "3連単",
    scores: Optional[pd.DataFrame] = None,
    *,
    retrain: bool = False,
) -> dict:
    """Streamlit / CLI 用"""
    meta = load_model_meta(bet_type)
    train_result: dict[str, Any] = {}

    if retrain or not model_exists(bet_type):
        train_result = train_ml_model(bet_type, scores=scores)
        meta = load_model_meta(bet_type)

    predictions = predict_races(bet_type, scores=scores)
    importance = pd.DataFrame(meta.get("feature_importance", [])) if meta else pd.DataFrame()

    bet_df = load_bet_frame(bet_type=bet_type)
    n_labeled = len(_race_recovery(bet_df)) if not bet_df.empty else 0

    return {
        "has_model": model_exists(bet_type),
        "can_train": n_labeled >= MIN_TRAIN_RACES,
        "n_labeled_races": n_labeled,
        "min_train_races": MIN_TRAIN_RACES,
        "meta": meta or {},
        "train_result": train_result,
        "predictions": predictions,
        "feature_importance": importance,
        "trained_at": (meta or {}).get("trained_at", ""),
        "cv_r2": (meta or {}).get("cv_r2"),
        "cv_rmse": (meta or {}).get("cv_rmse"),
    }


def build_ml_lines(bet_type: str = "3連単") -> list[str]:
    bundle = get_ml_bundle(bet_type, retrain=False)
    lines = [f"【予測AI (XGBoost)】券種={bet_type}", ""]
    if not bundle["has_model"]:
        lines.append("  モデル未学習。`python main.py ml --train` を実行してください。")
        lines.append(f"  結果ありレース: {bundle['n_labeled_races']}")
        lines.append("")
        return lines

    meta = bundle["meta"]
    lines.append(f"  学習日時: {meta.get('trained_at', '—')}")
    lines.append(f"  学習件数: {meta.get('n_train', 0)}  CV R²={meta.get('cv_r2', '—')}")
    lines.append("")

    pred = bundle["predictions"]
    if pred.empty:
        lines.append("  予測対象レースがありません。")
    else:
        lines.append("--- 予測 TOP10 ---")
        cols = [
            "venue_name",
            "race_no",
            "pred_recovery",
            "pred_confidence",
            "pred_ev_rank",
            "pred_ev",
        ]
        lines.append(pred[cols].head(10).to_string(index=False))
    lines.append("")
    return lines
