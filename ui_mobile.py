"""スマホ向け UI — CSS・カード・レイアウトヘルパー"""

import streamlit as st

from battle_judge import (
    VERDICT_BUY,
    VERDICT_CHECK,
    VERDICT_COLORS,
    VERDICT_SKIP,
    VERDICT_SMALL,
    VERDICT_BG,
)
from ai_recommend import DISCLAIMER

MOBILE_CSS = """
<style>
.block-container {
  padding-top: 0.75rem;
  padding-bottom: 2rem;
  padding-left: 0.85rem;
  padding-right: 0.85rem;
  max-width: 100%;
}
h1 { font-size: 1.55rem !important; line-height: 1.3 !important; }
h2 { font-size: 1.3rem !important; }
h3 { font-size: 1.15rem !important; }
h4, h5 { font-size: 1.05rem !important; }
p, li, label, .stMarkdown { font-size: 1rem; line-height: 1.55; }

.stButton > button {
  min-height: 3.1rem;
  font-size: 1.05rem !important;
  font-weight: 700 !important;
  border-radius: 12px !important;
  padding: 0.65rem 1rem !important;
}
.stDownloadButton > button {
  min-height: 3rem;
  font-size: 1rem !important;
  width: 100%;
}

[data-testid="stMetric"] {
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  padding: 0.5rem 0.75rem;
}
[data-testid="stMetricValue"] { font-size: 1.35rem !important; }
[data-testid="stMetricLabel"] { font-size: 0.85rem !important; }

.mobile-section {
  margin: 1rem 0 0.5rem;
  font-size: 1.2rem;
  font-weight: 700;
}
.mobile-card {
  border-radius: 14px;
  padding: 1rem 1.05rem;
  margin-bottom: 0.85rem;
  border: 1px solid #e2e8f0;
  word-wrap: break-word;
  overflow-wrap: anywhere;
}
.mobile-card-target {
  background: linear-gradient(135deg, #ecfdf5 0%, #f0fdf4 100%);
  border-left: 6px solid #059669;
}
.mobile-card-pick {
  background: linear-gradient(135deg, #eff6ff 0%, #ecfeff 100%);
  border-left: 6px solid #2563eb;
}
.mobile-card-danger {
  background: linear-gradient(135deg, #fef2f2 0%, #fff7ed 100%);
  border-left: 6px solid #dc2626;
}
.mobile-card-skip {
  background: #f8fafc;
  border-left: 6px solid #94a3b8;
}
.mobile-card-title {
  font-size: 1.2rem;
  font-weight: 800;
  line-height: 1.35;
  margin-bottom: 0.35rem;
}
.mobile-badge {
  display: inline-block;
  padding: 0.3rem 0.75rem;
  border-radius: 999px;
  font-size: 0.9rem;
  font-weight: 700;
  color: white;
  margin-top: 0.35rem;
}
.mobile-meta {
  font-size: 0.95rem;
  color: #475569;
  margin: 0.45rem 0;
}
.mobile-picks {
  font-size: 1.05rem;
  margin: 0.5rem 0 0;
  padding-left: 1.1rem;
}
.mobile-picks li { margin-bottom: 0.35rem; }
.mobile-hint {
  font-size: 0.92rem;
  color: #64748b;
  font-style: italic;
}

@media (max-width: 768px) {
  [data-testid="column"] {
    width: 100% !important;
    flex: 1 1 100% !important;
    min-width: 100% !important;
  }
  [data-testid="stHorizontalBlock"] {
    flex-wrap: wrap !important;
  }
  .stTabs [data-baseweb="tab-list"] {
    flex-wrap: wrap;
    gap: 0.25rem;
  }
  .stTabs [data-baseweb="tab"] {
    font-size: 0.82rem !important;
    padding: 0.45rem 0.55rem !important;
  }
  div[data-testid="stDataFrame"] { font-size: 0.85rem; }
}
</style>
"""


def inject_mobile_style() -> None:
    st.markdown(MOBILE_CSS, unsafe_allow_html=True)


def mobile_metrics(items: list[tuple[str, str | int | float]], per_row: int = 2) -> None:
    """スマホ向けメトリクス（2列ずつ縦積み）"""
    for i in range(0, len(items), per_row):
        chunk = items[i : i + per_row]
        cols = st.columns(len(chunk))
        for col, (label, val) in zip(cols, chunk):
            col.metric(label, val)



def render_battle_card(card: dict) -> None:
    """実戦判定カード"""
    verdict = card.get("battle_verdict", "")
    color = VERDICT_COLORS.get(verdict, "#64748b")
    bg = VERDICT_BG.get(verdict, "#f8fafc")
    venue = card.get("venue_name", "")
    race_no = card.get("race_no", "")
    composite = card.get("composite_score", 0)
    amount = card.get("recommended_yen", 0)
    hint = card.get("battle_hint", "")

    st.markdown(
        f"""
<div class="mobile-card" style="background:{bg}; border-left:6px solid {color};">
  <div class="mobile-card-title">{venue} {race_no}R</div>
  <span class="mobile-badge" style="background:{color};">{verdict}</span>
  <div class="mobile-meta">
    総合{composite:.0f}点 · AI{card.get('pre_race_score') or card.get('ai_total_score')} ·
    推奨<strong>{amount}円</strong>
  </div>
  <div class="mobile-hint">{hint}</div>
</div>
""",
        unsafe_allow_html=True,
    )
    reasons = card.get("battle_reasons") or []
    if reasons:
        st.caption(" / ".join(reasons[:4]))
    picks = card.get("picks") or []
    if picks:
        pick_lines = [
            f"{p.get('rank')}位 {p.get('combination')} ({p.get('odds')}倍)"
            for p in picks[:2]
        ]
        st.markdown("**買い目:** " + " · ".join(pick_lines))


def render_target_card(card: dict) -> None:
    verdict = card.get("verdict", "要確認")
    badge_color = VERDICT_COLORS.get(verdict, "#059669")
    card_class = "mobile-card-target"
    if verdict == VERDICT_SKIP:
        card_class = "mobile-card-skip"
    picks_html = ""
    for p in card.get("picks") or []:
        picks_html += (
            f"<li><b>{p['combination']}</b> "
            f"{p['odds']}倍 · {p['ninki']}番人気</li>"
        )
    pre_adj = float(card.get("pre_race_adjust") or 0)
    pre_score = card.get("pre_race_score", card["ai_total_score"])
    if pre_adj:
        sign = "+" if pre_adj > 0 else ""
        score_line = (
            f"AI <b>{card['ai_total_score']}</b> → 直前補正 <b>{pre_score}</b> "
            f"（{sign}{pre_adj:.0f}）"
        )
    else:
        score_line = f"AI <b>{card['ai_total_score']}</b>（{card['ev_rank']}）"
    reasons = (card.get("reasons") or [])[:4]
    reasons_html = "".join(f"<li>{r}</li>" for r in reasons)
    st.markdown(
        f"""
<div class="mobile-card {card_class}">
  <div class="mobile-card-title">{card['venue_name']} {card['race_no']}R</div>
  <span class="mobile-badge" style="background:{badge_color};">{verdict}</span>
  <div class="mobile-meta">
    {score_line}
    · 危険 {card['danger_level']}
    · 荒れ {card['are_forecast']}
  </div>
  <div class="mobile-hint">{card.get('verdict_hint', '')}</div>
  <div class="mobile-meta">ライン: {card.get('line_info') or '—'}</div>
  <div style="font-weight:700; margin-top:0.5rem;">おすすめ買い目</div>
  <ul class="mobile-picks">{picks_html or '<li>—</li>'}</ul>
  <div style="font-weight:700; margin-top:0.5rem;">理由</div>
  <ul class="mobile-picks" style="font-size:0.92rem;">{reasons_html or '<li>—</li>'}</ul>
</div>
        """,
        unsafe_allow_html=True,
    )


def render_pick_card(pick: dict) -> None:
    st.markdown(
        f"""
<div class="mobile-card mobile-card-pick">
  <div class="mobile-card-title">{pick['global_rank']}位 {pick['combination']}</div>
  <span class="mobile-badge" style="background:#2563eb;">スコア {pick['pick_score']}</span>
  <div class="mobile-meta">
    {pick['odds']}倍 · {pick['ninki_rank']}番人気<br>
    <b>{pick['race_label']}</b>
  </div>
</div>
        """,
        unsafe_allow_html=True,
    )


def render_danger_card(card: dict) -> None:
    st.markdown(
        f"""
<div class="mobile-card mobile-card-danger">
  <div class="mobile-card-title" style="color:#b91c1c;">⚠ {card['venue_name']} {card['race_no']}R</div>
  <div class="mobile-meta">{card.get('danger_reason', '人気・波乱リスク')}</div>
  <div class="mobile-meta">
    人気集中 {card.get('ninki_concentration')}%
    · 危険度 {card.get('danger_level', '—')}
  </div>
</div>
        """,
        unsafe_allow_html=True,
    )


def render_line_card(line: dict, *, kind: str = "default") -> None:
    cls = {
        "advantage": "mobile-card-target",
        "danger": "mobile-card-danger",
        "default": "mobile-card-pick",
    }.get(kind, "mobile-card-pick")
    members = " → ".join(
        f"{m['bracket']}{m['style']}" for m in (line.get("members_json") or [])
    ) if line.get("members_json") else line.get("line_label", "")
    st.markdown(
        f"""
<div class="mobile-card {cls}">
  <div class="mobile-card-title">{line.get('venue_name','')} {line.get('race_no','')}R
    ライン{line.get('line_no')}</div>
  <div class="mobile-meta"><code>{line.get('line_label')}</code> · AI {line.get('line_ai_score')}</div>
  <div class="mobile-meta">長{line.get('line_length')} · 自力{line.get('jiriki_count')}</div>
  <div class="mobile-meta">{members}</div>
</div>
        """,
        unsafe_allow_html=True,
    )


def render_ai_recommend_block(
    rec: dict,
    score_bundle: dict,
    bet_type: str,
    *,
    show_skip: bool = True,
    show_table: bool = False,
) -> None:
    """AIおすすめセクション（縦並びカード）"""
    st.caption(DISCLAIMER)

    if not rec["has_data"]:
        st.warning("データがありません。サイドバーから workflow を実行してください。")
        return

    st.info(
        f"対象日 **{rec['today']}** · {rec['race_count']}レース · {bet_type}"
    )
    mobile_metrics(
        [
            ("狙い目", len(rec["targets"])),
            ("見送り", len(rec["skip_races"])),
            ("危険人気", len(rec["dangerous_popular"])),
            ("期待値買い目", len(rec["global_picks"])),
        ]
    )

    st.markdown('<div class="mobile-section">🎯 今日の狙い目 TOP3</div>', unsafe_allow_html=True)
    if not rec["targets"]:
        st.warning("狙い目なし（見送り判定またはデータ不足）")
    else:
        for card in rec["targets"]:
            render_target_card(card)

    st.markdown('<div class="mobile-section">💰 期待値買い目 TOP3</div>', unsafe_allow_html=True)
    if not rec["global_picks"]:
        st.caption("買い目データなし")
    else:
        for pick in rec["global_picks"]:
            render_pick_card(pick)

    st.markdown('<div class="mobile-section">⚠ 危険な人気レース</div>', unsafe_allow_html=True)
    if not rec["dangerous_popular"]:
        st.success("該当なし")
    else:
        for card in rec["dangerous_popular"]:
            render_danger_card(card)

    if show_skip:
        st.markdown('<div class="mobile-section">🚫 見送りレース</div>', unsafe_allow_html=True)
        if not rec["skip_races"]:
            st.caption("見送り判定なし")
        else:
            for card in rec["skip_races"]:
                render_target_card(card)

    if show_table:
        with st.expander("全レース一覧（表）"):
            scores_df = score_bundle["scores"]
            if not scores_df.empty:
                show = scores_df.copy()
                show["verdict"] = show["race_id"].map(
                    {c["race_id"]: c["verdict"] for c in rec["all_cards"]}
                )
                st.dataframe(show, use_container_width=True, hide_index=True)
