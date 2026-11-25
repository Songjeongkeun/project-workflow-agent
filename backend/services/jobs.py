"""에이전트 일을 요청 밖(백그라운드 스레드)에서 돌린다.

- 요청: 결정은 DB에 저장한 뒤 submit_*만 부르고 바로 응답한다.
- 같은 일은 동시에 하나만 한다. 도는 중에 또 요청이 오면 끝난 뒤 한 번 더 돈다
- 기준은 DB
"""

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import config
from db import fetch_all, get_connection

log = logging.getLogger(__name__)

_pool = ThreadPoolExecutor(max_workers=config.AGENT_WORKERS, thread_name_prefix="agent")
_lock = threading.Lock()
_busy: set = set()  # 지금 돌고 있는 작업
_again: dict = {}   # 도는 중에 다시 용청된 일 -> 다시 돌 때 사용

def _submit(key, fn, *args) -> None:
    if config.AGENT_INLINE:
        fn(*args)
        return
    with _lock:
        if key in _busy:
            _again[key] = args
            return
        _busy.add(key)
    _pool.submit(_loop, key, fn, args)


def _loop(key, fn, args) -> None:
    while True:
        try:
            fn(*args)
        except Exception:
            log.exception("백그라운드 작업 오류: %s", key)
        with _lock:
            if key in _again:
                args = _again.pop(key)
                continue
            _busy.discard(key)
            return

def submit_run(run_id: int) -> None:
    """실행을 다음 멈출 곳까지 진행한다."""
    from services import controller

    _submit(("run", run_id), controller.advance, run_id)

def submit_a01(project_id: int, change_request_id: int | None = None) -> None:
    """A01(기획)을 맡긴다. 같은 프로젝트의 A01은 한 번에 하나만 돈다."""

    from services import planning
    _submit(("a01", project_id), planning.run_a01, project_id, change_request_id)


def sweep() -> None:
    """DB를 보고 해야 하는데 아무도 안 하고 있는 작업을 다시 맡긴다. (서버 재시작, 놓친 요청)"""

    from services import planning
    with get_connection() as conn:
        runs = [r["id"] for r in fetch_all(conn, "select id from runs where status = 'running'")]
        projects = [p["id"] for p in fetch_all(conn, "select id from projects") if planning.processing(conn, p["id"])]

    for rid in runs:
        if ("run", rid) not in _busy:
            submit_run(rid)

    for pid in projects:
        if ("a01", pid) not in _busy:
            submit_a01(pid)

def wait_idle(timeout: float = 10) -> bool:
    "테스트용: 맡긴 일이 다 끝날 때까지 기다린다."
    end = time.time() + timeout
    while time.time() < end:
        with _lock:
            if not _busy:
                return True
        time.sleep(0.05)
    return False

def shutdown() -> None:
    _pool.shutdown(wait=False, cancel_futures=True)