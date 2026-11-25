"""모든 도구 호출을 tool_calls 테이블에 남긴다

허용한 호출도, 거부한 호출도 모두 남긴다.
나중에 "에이전트가 무엇을 하려 했고 무엇이 막혔는가"를 실행 기록에서 볼 수 있다.
tool_calls 테이블은 db.py의 SCHEMA에 이미 있어서 새로 만들 필요가 없다.
"""

import logging

from db import fetch_one, get_connection, insert

# 이 파일 이름으로 로그 기록기를 생성
log = logging.getLogger(__name__)

def record(run_id: int, agent: str, tool: str, target: str, allowed: bool, reason: str | None = None) -> None:
    """도구 호출 한 건을 기록한다.
    
    target: 대상 파일 경로나 명령 (예: "src/app.py", "make test")
    allowed: 검사를 통과했는지, DB에는 True/False가 1/0으로 저장된다.
    reaseon: 막았거나 실행 중 실패했으면 그 이유를 저장한다.
    """

    with get_connection() as conn:
        if fetch_one(conn, "select id from runs where id = ?", (run_id,)) is None:
            log.warning("없는 실행의 도구 호출: run=%s agent=%s, tool=%s target=%s", run_id, agent, tool, target)
            return

        insert(
            conn, "tool_calls",
            run_id=run_id, agent=agent, tool=tool,
            target=target[:500], allowed=allowed,
            reason=reason,
        )