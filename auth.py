"""Streamlit ログイン認証（Streamlit Cloud Secrets 対応）"""

from __future__ import annotations

import hmac
import os
from typing import Optional

import streamlit as st

SESSION_AUTHENTICATED = "authenticated"
SESSION_USERNAME = "auth_username"


def _load_credentials() -> Optional[tuple[str, str]]:
    """Secrets → 環境変数の順で認証情報を取得"""
    try:
        auth = st.secrets.get("auth")
        if auth:
            username = auth.get("username") or auth.get("user")
            password = auth.get("password")
            if username and password:
                return str(username), str(password)
    except (AttributeError, KeyError, FileNotFoundError, TypeError):
        pass

    username = os.environ.get("KEIRIN_AUTH_USERNAME") or os.environ.get(
        "STREAMLIT_AUTH_USERNAME"
    )
    password = os.environ.get("KEIRIN_AUTH_PASSWORD") or os.environ.get(
        "STREAMLIT_AUTH_PASSWORD"
    )
    if username and password:
        return str(username), str(password)
    return None


def credentials_configured() -> bool:
    return _load_credentials() is not None


def is_authenticated() -> bool:
    return st.session_state.get(SESSION_AUTHENTICATED) is True


def authenticate(username: str, password: str) -> bool:
    creds = _load_credentials()
    if not creds:
        return False

    expected_user, expected_password = creds
    user_ok = hmac.compare_digest(username.strip(), expected_user.strip())
    pwd_ok = hmac.compare_digest(password, expected_password)
    if user_ok and pwd_ok:
        st.session_state[SESSION_AUTHENTICATED] = True
        st.session_state[SESSION_USERNAME] = username.strip()
        return True
    return False


def logout() -> None:
    st.session_state[SESSION_AUTHENTICATED] = False
    st.session_state.pop(SESSION_USERNAME, None)


def render_login_page() -> None:
    st.title("🚴 競輪観測AI")
    st.subheader("ログイン")
    st.caption("認証に成功するとアプリを利用できます。")

    if not credentials_configured():
        st.error(
            "認証情報が未設定です。Streamlit Cloud の Secrets または環境変数を設定してください。"
        )
        with st.expander("Streamlit Cloud の設定例"):
            st.code(
                """[auth]
username = "your_username"
password = "your_password"
""",
                language="toml",
            )
        with st.expander("ローカル開発の設定例"):
            st.code(
                """# .streamlit/secrets.toml
[auth]
username = "your_username"
password = "your_password"
""",
                language="toml",
            )
        return

    with st.form("login_form", clear_on_submit=False):
        username = st.text_input("ユーザー名", autocomplete="username")
        password = st.text_input(
            "パスワード", type="password", autocomplete="current-password"
        )
        submitted = st.form_submit_button("ログイン", type="primary", use_container_width=True)

    if submitted:
        if not username.strip() or not password:
            st.error("ユーザー名とパスワードを入力してください。")
        elif authenticate(username, password):
            st.rerun()
        else:
            st.error("ユーザー名またはパスワードが正しくありません。")


def require_authentication() -> bool:
    if is_authenticated():
        return True
    render_login_page()
    return False


def render_logout_control() -> None:
    username = st.session_state.get(SESSION_USERNAME, "")
    if username:
        st.caption(f"👤 {username}")
    if st.button("ログアウト", use_container_width=True, key="logout_button"):
        logout()
        st.rerun()
