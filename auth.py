"""Streamlit ログイン認証（Render 環境変数 / Secrets 対応）"""

from __future__ import annotations

import hmac
import os
import time
from typing import Optional

import streamlit as st

SESSION_AUTHENTICATED = "authenticated"
SESSION_USERNAME = "auth_username"
SESSION_DB_BOOTSTRAPPED = "db_bootstrapped"
WORKFLOW_LAST_RESULT = "workflow_last_result"
DEFER_HEAVY_BUNDLES = "defer_heavy_bundles"
LOGIN_FLASH_UNTIL = "login_flash_until"
PENDING_DB_RESTORE = "pending_db_restore"
FULL_BUNDLES_LOADED = "full_bundles_loaded"


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


def reinforce_authenticated() -> None:
    """workflow 等の長処理後も認証フラグを維持"""
    init_auth_session()
    if st.session_state.get(SESSION_AUTHENTICATED) is True:
        st.session_state[SESSION_AUTHENTICATED] = True


def log_session_state(context: str = "") -> None:
    """Render Logs 用 — 認証状態を明示"""
    flag = is_authenticated()
    suffix = f" ctx={context}" if context else ""
    print(f"[session] authenticated {flag}{suffix}", flush=True)


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
    st.session_state.pop(LOGIN_FLASH_UNTIL, None)
    st.session_state.pop(FULL_BUNDLES_LOADED, None)
    st.session_state.pop(PENDING_DB_RESTORE, None)
    # ログアウトで DB は消さない（persist / GitHub が正）
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

    # DB にデータが入ったときだけ「準備完了」とする（空のまま再試行可能に）
    if outcome.get("race_count", 0) > 0 or (restore or {}).get("skipped"):
        st.session_state[SESSION_DB_BOOTSTRAPPED] = True
    return outcome


def ensure_db_schema_only() -> None:
    """テーブル作成のみ（ログイン直後用・GitHub復元は後回し）"""
    try:
        from db import init_db

        init_db()
    except Exception as exc:
        print(f"[auth] init_db error: {exc}", flush=True)


def ensure_db_ready(*, force_restore: bool = False) -> None:
    """DB 復元が必要なときだけ bootstrap（詳細タブ読み込み時など）"""
    if not force_restore and not st.session_state.get(PENDING_DB_RESTORE, True):
        ensure_db_schema_only()
        return

    count = _race_count_safe()
    if count == 0:
        st.session_state.pop(SESSION_DB_BOOTSTRAPPED, None)
    if not st.session_state.get(SESSION_DB_BOOTSTRAPPED):
        run_login_bootstrap()
        st.session_state[PENDING_DB_RESTORE] = False
    elif count == 0:
        print("[auth] db empty after bootstrapped — retry restore", flush=True)
        run_login_bootstrap()
        st.session_state[PENDING_DB_RESTORE] = False
    else:
        st.session_state[PENDING_DB_RESTORE] = False


def mark_pending_restore() -> None:
    st.session_state[PENDING_DB_RESTORE] = True
    st.session_state.pop(SESSION_DB_BOOTSTRAPPED, None)


def render_login_flash() -> None:
    """ログイン成功メッセージ（約3秒で消える）"""
    until = float(st.session_state.get(LOGIN_FLASH_UNTIL) or 0)
    if until <= 0:
        return
    if time.time() < until:
        st.success("ログインしました。", icon="✅")
    else:
        st.session_state.pop(LOGIN_FLASH_UNTIL, None)


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
        print("[auth] auth success (deferred restore)", flush=True)
        ensure_db_schema_only()
        mark_pending_restore()
        st.session_state[LOGIN_FLASH_UNTIL] = time.time() + 3
        st.session_state.pop(FULL_BUNDLES_LOADED, None)
        log_session_state("login")
        return True

    st.error("ユーザー名またはパスワードが正しくありません。")
    return False


def require_authentication() -> bool:
    init_auth_session()
    if is_authenticated():
        log_session_state("require-auth")
        return True
    if render_login_page():
        if is_authenticated():
            log_session_state("require-auth-after-login")
        return is_authenticated()
    return False


def render_logout_control() -> None:
    username = st.session_state.get(SESSION_USERNAME, "")
    if username:
        st.caption(f"👤 {username}")
    if st.button("ログアウト", use_container_width=True, key="logout_button"):
        logout()
        st.rerun()
