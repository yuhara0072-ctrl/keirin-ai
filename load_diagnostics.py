"""初回ロード診断 — 処理位置・API/sleep/欠損取得を Render ログで追跡（挙動は変更しない）"""

from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager
from typing import Any, Iterator

_ENABLED = os.environ.get("KEIRIN_LOAD_DIAG", "1").strip().lower() not in (
    "0",
    "false",
    "no",
)
_SLOW_SPAN_SEC = float(os.environ.get("KEIRIN_LOAD_DIAG_SLOW_SEC", "10"))
_INSTALLED = False


class LoadDiagnostics:
    """スレッドセーフなロード診断カウンタ"""

    _lock = threading.Lock()
    _stack: list[tuple[str, float]] = []
    _session_start = time.perf_counter()

    api_calls = 0
    github_api_calls = 0
    keirin_api_calls = 0
    other_api_calls = 0
    sleep_count = 0
    sleep_total_sec = 0.0
    missing_fetch_count = 0
    missing_fetch_by_kind: dict[str, int] = {}
    missing_fetch_races: set[str] = set()
    loop_counts: dict[str, int] = {}
    api_errors = 0
    slow_api_count = 0

    @classmethod
    def enabled(cls) -> bool:
        return _ENABLED

    @classmethod
    def _elapsed(cls) -> float:
        return time.perf_counter() - cls._session_start

    @classmethod
    def _log(cls, msg: str) -> None:
        if not cls.enabled():
            return
        print(f"[load_diag +{cls._elapsed():7.1f}s] {msg}", flush=True)

    @classmethod
    def active_path(cls) -> str:
        with cls._lock:
            return " > ".join(name for name, _ in cls._stack) or "(idle)"

    @classmethod
    @contextmanager
    def span(cls, name: str, **meta: Any) -> Iterator[None]:
        if not cls.enabled():
            yield
            return
        t0 = time.perf_counter()
        with cls._lock:
            cls._stack.append((name, t0))
        meta_s = " ".join(f"{k}={v}" for k, v in meta.items())
        cls._log(f"ENTER {name}" + (f" {meta_s}" if meta_s else ""))
        try:
            yield
        finally:
            dt = time.perf_counter() - t0
            with cls._lock:
                if cls._stack and cls._stack[-1][0] == name:
                    cls._stack.pop()
            flag = " **SLOW**" if dt >= _SLOW_SPAN_SEC else ""
            cls._log(f"EXIT  {name} {dt:.3f}s{flag}")

    @classmethod
    def heartbeat(cls, label: str, **extra: Any) -> None:
        if not cls.enabled():
            return
        parts = [
            f"HEARTBEAT {label}",
            f"stuck_at={cls.active_path()}",
            f"api={cls.api_calls}(gh={cls.github_api_calls} keirin={cls.keirin_api_calls})",
            f"sleep={cls.sleep_count}x/{cls.sleep_total_sec:.1f}s",
            f"missing_fetch={cls.missing_fetch_count}",
        ]
        if cls.api_errors:
            parts.append(f"api_errors={cls.api_errors}")
        for k, v in extra.items():
            parts.append(f"{k}={v}")
        cls._log(" | ".join(parts))

    @classmethod
    def log_api_start(cls, kind: str, target: str, *, timeout: Any = None) -> int:
        cls.api_calls += 1
        n = cls.api_calls
        if kind == "github":
            cls.github_api_calls += 1
        elif kind == "keirin":
            cls.keirin_api_calls += 1
        else:
            cls.other_api_calls += 1
        to = f" timeout={timeout}s" if timeout is not None else ""
        cls._log(
            f"API #{n} START {kind} {target[:140]}{to} "
            f"active={cls.active_path()}"
        )
        return n

    @classmethod
    def log_api_end(
        cls,
        n: int,
        kind: str,
        target: str,
        sec: float,
        *,
        ok: bool = True,
        status: str | int | None = None,
    ) -> None:
        if not ok:
            cls.api_errors += 1
        if sec >= _SLOW_SPAN_SEC:
            cls.slow_api_count += 1
            slow = " **SLOW/TIMEOUT?**"
        elif sec >= 3.0:
            slow = " slow"
        else:
            slow = ""
        st = f" status={status}" if status is not None else ""
        ok_s = "ok" if ok else "FAIL"
        cls._log(
            f"API #{n} END   {kind} {sec:.3f}s {ok_s}{st}{slow} "
            f"active={cls.active_path()}"
        )

    @classmethod
    def log_sleep(cls, sec: float, *, reason: str = "") -> None:
        cls.sleep_count += 1
        cls.sleep_total_sec += float(sec)
        why = f" reason={reason}" if reason else ""
        cls._log(
            f"SLEEP #{cls.sleep_count} {sec:.1f}s{why} "
            f"active={cls.active_path()}"
        )

    @classmethod
    def log_missing_fetch(
        cls,
        kind: str,
        race_id: str,
        *,
        index: int | None = None,
        total: int | None = None,
    ) -> None:
        cls.missing_fetch_count += 1
        cls.missing_fetch_races.add(str(race_id))
        cls.missing_fetch_by_kind[kind] = cls.missing_fetch_by_kind.get(kind, 0) + 1
        pos = f" [{index}/{total}]" if index is not None and total is not None else ""
        est_remain = ""
        if index is not None and total is not None and total > index:
            est_remain = f" est_remain_sleep~{total - index}s"
        cls._log(
            f"MISSING_FETCH #{cls.missing_fetch_count}{pos} kind={kind} "
            f"race_id={race_id}{est_remain} active={cls.active_path()}"
        )

    @classmethod
    def log_loop(cls, name: str, index: int, total: int, **extra: Any) -> None:
        cls.loop_counts[name] = index
        if index == 1 or index % 10 == 0 or index == total:
            cls.heartbeat(f"loop:{name}", i=f"{index}/{total}", **extra)
        if total > 200 and index == 200:
            cls._log(
                f"WARN loop:{name} exceeded 200 iterations — "
                f"possible long run (not infinite) active={cls.active_path()}"
            )

    @classmethod
    def warn(cls, msg: str) -> None:
        cls._log(f"WARN {msg} active={cls.active_path()}")

    @classmethod
    def summary(cls, label: str = "session") -> None:
        if not cls.enabled():
            return
        total = cls._elapsed()
        kinds = ",".join(f"{k}:{v}" for k, v in sorted(cls.missing_fetch_by_kind.items()))
        cls._log(
            f"SUMMARY [{label}] total={total:.1f}s "
            f"api={cls.api_calls}(gh={cls.github_api_calls} keirin={cls.keirin_api_calls} "
            f"other={cls.other_api_calls} err={cls.api_errors} slow_api={cls.slow_api_count}) "
            f"sleep={cls.sleep_count}x/{cls.sleep_total_sec:.1f}s "
            f"missing_fetch={cls.missing_fetch_count} races={len(cls.missing_fetch_races)}"
            + (f" by_kind={{{kinds}}}" if kinds else "")
        )
        if cls.missing_fetch_count >= 30:
            cls._log(
                f"WARN missing_fetch={cls.missing_fetch_count} — "
                f"~{cls.missing_fetch_count}s sleep alone at 1s/req "
                f"(+ API latency). Check save_learned_patterns / build_race_metrics."
            )
        if cls.github_api_calls >= 5 and total >= 60:
            cls._log(
                "WARN GitHub restore may dominate — each file up to "
                f"{os.environ.get('GITHUB_REQUEST_TIMEOUT', '60')}s timeout."
            )
        if cls.active_path() != "(idle)":
            cls._log(f"WARN summary while still active: {cls.active_path()}")


def install_load_diagnostics() -> None:
    """time.sleep / requests.get をフック（1回だけ）"""
    global _INSTALLED
    if _INSTALLED or not LoadDiagnostics.enabled():
        return
    _INSTALLED = True
    LoadDiagnostics._log("install hooks (sleep + requests.get)")

    import time as _time

    _orig_sleep = _time.sleep

    def _patched_sleep(seconds: float) -> None:
        LoadDiagnostics.log_sleep(float(seconds))
        return _orig_sleep(seconds)

    _time.sleep = _patched_sleep  # type: ignore[assignment]

    try:
        import requests

        _orig_get = requests.get

        def _patched_get(url, *args, **kwargs):  # type: ignore[no-untyped-def]
            target = str(url)
            if "github.com" in target:
                kind = "github"
            elif "keirin" in target:
                kind = "keirin"
            else:
                kind = "http"
            n = LoadDiagnostics.log_api_start(
                kind, target, timeout=kwargs.get("timeout")
            )
            t0 = _time.perf_counter()
            try:
                resp = _orig_get(url, *args, **kwargs)
                LoadDiagnostics.log_api_end(
                    n,
                    kind,
                    target,
                    _time.perf_counter() - t0,
                    ok=resp.ok,
                    status=resp.status_code,
                )
                return resp
            except Exception:
                LoadDiagnostics.log_api_end(
                    n,
                    kind,
                    target,
                    _time.perf_counter() - t0,
                    ok=False,
                )
                raise

        requests.get = _patched_get  # type: ignore[assignment]
    except ImportError:
        LoadDiagnostics._log("requests not installed — API hook skipped")


diag = LoadDiagnostics
span = LoadDiagnostics.span
