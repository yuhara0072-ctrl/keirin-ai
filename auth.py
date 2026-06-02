"""Streamlit ログイン認証（Render 環境変数 / Secrets 対応）"""

from __future__ import annotations

import hmac
import os
from typing import Optional

import streamlit as st

SESSION_AUTHENTICATED = "authenticated"
SESSION_USERNAME = "auth_username"
SESSION_DB_BOOTSTRAPPED = "db_bootstrapped"


def init_auth_session() -> None:
    """セッションキーを初期化（未設定時に False 扱いでログアウトしない）"""
    st.session_state.setdefault(SESSION_AUTHENTICATED, False)
    st.session_state.setdefault(SESSION_USERNAME, "")


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
    """明示的ログアウト時のみ認証を解除"""
    st.session_state[SESSION_AUTHENTICATED] = False
    st.session_state[SESSION_USERNAME] = ""
    st.session_state[SESSION_DB_BOOTSTRAPPED] = False
    for key in list(st.session_state.keys()):
        if str(key).startswith("bundles_"):
            st.session_state.pop(key, None)


def _race_count_safe() -> int:
    try:
        from db import get_connection, safe_table_count

        conn = get_connection()
        try:
            return safe_table_count(conn, "races")
        finally:
            conn.close()
    except Exception:
        return 0


def run_login_bootstrap() -> dict:
    """ログイン直後の DB 初期化・GitHub 復元（例外でも認証は維持）"""
    if st.session_state.get(SESSION_DB_BOOTSTRAPPED):
        count = _race_count_safe()
        print("[auth] restore start (already bootstrapped)", flush=True)
        print("[auth] restore result: skipped", flush=True)
        print(f"[auth] db race count: {count}", flush=True)
        return {"ok": True, "restore": None, "race_count": count, "skipped": True}

    print("[auth] restore start", flush=True)
    outcome: dict = {"ok": True, "restore": None, "race_count": 0, "error": None}
    try:
        from db import bootstrap_database

        outcome = bootstrap_database()
    except Exception as exc:
        outcome = {"ok": False, "restore": None, "race_count": 0, "error": str(exc)}
        print(f"[auth] bootstrap error: {exc}", flush=True)

    restore = outcome.get("restore")
    print(f"[auth] restore result: {restore if restore is not None else 'none'}", flush=True)
    print(f"[auth] db race count: {outcome.get('race_count', 0)}", flush=True)
    st.session_state[SESSION_DB_BOOTSTRAPPED] = True
    return outcome


def ensure_db_ready() -> None:
    """認証済みセッションで DB を一度だけ準備（ログイン以外の再実行用）"""
    if st.session_state.get(SESSION_DB_BOOTSTRAPPED):
        return
    run_login_bootstrap()


def render_login_page() -> bool:
    """ログイン UI。成功したら True（同一実行で require_authentication が通過可能）"""
    st.title("🚴 競輪観測AI")
    st.subheader("ログイン")
    st.caption("認証に成功するとアプリを利用できます。")

    if not credentials_configured():
        st.error(
            "認証情報が未設定です。Render の Environment または環境変数を設定してください。"
        )
        with st.expander("Render の設定例"):
            st.code(
                """KEIRIN_AUTH_USERNAME=your_username
KEIRIN_AUTH_PASSWORD=your_password
""",
                language="bash",
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
        return False

    with st.form("login_form", clear_on_submit=False):
        username = st.text_input("ユーザー名", autocomplete="username")
        password = st.text_input(
            "パスワード", type="password", autocomplete="current-password"
        )
        submitted = st.form_submit_button("ログイン", type="primary", use_container_width=True)

    if not submitted:
        return False

    if not username.strip() or not password:
        st.error("ユーザー名とパスワードを入力してください。")
        return False

    if authenticate(username, password):
        print("[auth] auth success", flush=True)
        run_login_bootstrap()
        print(f"[auth] session authenticated: {is_authenticated()}", flush=True)
        st.success("ログインしました。アプリを読み込んでいます…")
        return True

    st.error("ユーザー名またはパスワードが正しくありません。")
    return False


def require_authentication() -> bool:
    init_auth_session()
    if is_authenticated():
        return True
    if render_login_page():
        return is_authenticated()
    return False


def render_logout_control() -> None:
    username = st.session_state.get(SESSION_USERNAME, "")
    if username:
        st.caption(f"👤 {username}")
    if st.button("ログアウト", use_container_width=True, key="logout_button"):
        logout()
        st.rerun()
