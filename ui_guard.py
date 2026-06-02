"""Streamlit 描画の例外ガード（認証セッションは維持）"""

from __future__ import annotations

import traceback
from contextlib import contextmanager
from typing import Callable, Iterator, Optional

import plotly.graph_objects as go
import streamlit as st


@contextmanager
def safe_page(name: str) -> Iterator[None]:
    """タブ/セクション単位で例外を握り、ログアウトさせない"""
    try:
        yield
    except Exception as exc:
        print(f"[app] page error ({name}): {exc}", flush=True)
        traceback.print_exc()
        st.error(f"「{name}」の表示に失敗しました")
        st.caption("ログイン状態は維持されています。他のタブをお試しください。")
        with st.expander("エラー詳細", expanded=False):
            st.code(str(exc))


def safe_plotly_chart(
    fig,
    *,
    key: str,
    use_container_width: bool = True,
    label: str = "グラフ",
) -> None:
    try:
        st.plotly_chart(fig, use_container_width=use_container_width, key=key)
    except Exception as exc:
        print(f"[app] plotly error ({label}, {key}): {exc}", flush=True)
        st.warning(f"{label} の表示に失敗しました: {exc}")


def safe_call(label: str, fn: Callable, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception as exc:
        print(f"[app] call error ({label}): {exc}", flush=True)
        traceback.print_exc()
        return None


def empty_plotly_figure(title: str, note: str = "") -> go.Figure:
    fig = go.Figure()
    fig.update_layout(
        title=title,
        annotations=[
            dict(
                text=note or "データを表示できません",
                xref="paper",
                yref="paper",
                x=0.5,
                y=0.5,
                showarrow=False,
            )
        ],
    )
    return fig
