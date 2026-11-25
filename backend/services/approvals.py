"""승인 요청과 결정. 결정(approvals)은 한 번 쓰면 고치지 않는다."""

from fastapi import HTTPException

import config
from db import fetch_all, fetch_one, insert, update, update_if
from services import members
from workspace_server import guards


def create_request(
    conn, project_id: int, gate: str, target_hash: str,
    run_id: int | None = None, target_version_id: int | None = None,
    deploy_target: dict | None = None, detail: dict | None = None,
) -> dict:
    """승인 요청을 만든다. 같은 대상에 대기 중인 요청이 있으면 무효로 한다."""
    for old in pending(conn, project_id, gate, run_id):
        update(conn, "approval_requests", old["id"], status="void")
    request_id = insert(
        conn, "approval_requests",
        project_id=project_id, run_id=run_id, gate=gate, role=members.GATE_ROLE[gate],
        target_version_id=target_version_id, target_hash=target_hash,
        deploy_target=deploy_target, detail=detail, status="pending",
    )
    return get_request(conn, request_id)


def pending(conn, project_id: int, gate: str, run_id: int | None = None) -> list[dict]:
    """대기 중인 요청. G1은 프로젝트 단위, G2~G4는 실행 단위로 찾는다."""
    if gate == "G1":
        return fetch_all(
            conn,
            "select * from approval_requests where project_id = ? and gate = 'G1' and status = 'pending'",
            (project_id,),
        )
    return fetch_all(
        conn,
        "select * from approval_requests where project_id = ? and gate = ? and run_id = ? and status = 'pending'",
        (project_id, gate, run_id),
    )


def void_run_requests(conn, run_id: int) -> None:
    conn.execute("update approval_requests set status = 'void' where run_id = ? and status = 'pending'", (run_id,))


def get_request(conn, request_id: int) -> dict:
    req = fetch_one(conn, "select * from approval_requests where id = ?", (request_id,))
    if req is None:
        raise HTTPException(status_code=404, detail="없는 승인 요청입니다.")
    return req


def decide(conn, req: dict, user_id: int, decision: str, target_hash: str, comment: str | None) -> int:
    """승인(approve) 또는 반려(reject).

    담당 역할이 아니면 403, 대상이 바뀌었으면 409, 반려 의견이 없으면 400 (07 4.7).
    """
    if req["role"] not in members.require_member(conn, req["project_id"], user_id):
        raise HTTPException(status_code=403, detail=f"{members.role_label(req['role'])} 담당만 결정할 수 있습니다.")
    if req["status"] != "pending":
        raise HTTPException(status_code=409, detail="이미 처리했거나 무효가 된 요청입니다.")
    if req["target_hash"] != target_hash:
        raise HTTPException(status_code=409, detail="그사이에 승인 대상이 바뀌었습니다. 다시 불러오세요.")
    comment = (comment or "").strip() or None
    if decision == "reject" and not comment:
        raise HTTPException(status_code=400, detail="반려할 때는 의견을 적어 주세요.")
    if decision == "approve":
        problem = deploy_target_problem(conn, req)
        if problem:
            raise HTTPException(status_code=409, detail=f"배포 대상이 올바르지 않아 승인할 수 없습니다: {problem}")

    # 위의 검사는 읽어 둔 값으로 했다. 그사이 다른 요청이 먼저 결정했으면 여기서 0행이 바뀐다
    new_status = "approved" if decision == "approve" else "rejected"
    if not update_if(conn, "approval_requests", req["id"], {"status": "pending", "target_hash": target_hash}, status=new_status):
        raise HTTPException(status_code=409, detail="이미 처리했거나 무효가 된 요청입니다.")
    approval_id = insert(
        conn, "approvals",
        request_id=req["id"], approver_id=user_id, role=req["role"], decision=decision, comment=comment,
    )
    req["status"] = new_status
    return approval_id


def deploy_target_problem(conn, req: dict) -> str | None:
    """G4 승인 전에, 승인 뒤 A05의 push가 거부될 게시 대상인지 미리 본다. 문제 없으면 None.

    workspace를 쓰지 않으면(개발용 임시 에이전트) push를 하지 않으므로 검사하지 않는다.
    재배포도 push하지 않으므로 저장소가 필요 없다.
    """
    if req["gate"] != "G4" or not config.USE_WORKSPACE:
        return None
    run = fetch_one(conn, "select redeploy_of_run_id from runs where id = ?", (req["run_id"],))
    if run and run["redeploy_of_run_id"]:
        return None
    try:
        guards.check_deploy_target(req["deploy_target"] or {})
    except guards.Denied as e:
        return str(e)
    return None


def carry_over(conn, req: dict, change_request_id: int) -> None:
    """변경이 영향을 주지 않는 승인 지점은 이전 승인을 이어받는다 (C04)."""
    insert(
        conn, "approvals",
        request_id=req["id"], approver_id=None, role=req["role"], decision="carry_over", change_request_id=change_request_id,
    )
    update(conn, "approval_requests", req["id"], status="approved")


def decision_of(conn, request_id: int) -> dict | None:
    return fetch_one(conn, "select * from approvals where request_id = ? order by id desc limit 1", (request_id,))


def inbox_pending(conn, user_id: int) -> list[dict]:
    """내 역할로 온 대기 중 요청 (프로젝트마다 지금 가진 역할 기준)."""
    out = []
    for m in fetch_all(conn, "select * from project_members where user_id = ?", (user_id,)):
        roles = members.active_roles(conn, m["id"])
        if not roles:
            continue
        marks = ", ".join("?" for _ in roles)
        out += fetch_all(
            conn,
            f"select * from approval_requests where project_id = ? and status = 'pending' and role in ({marks})",
            (m["project_id"], *roles),
        )
    return sorted(out, key=lambda r: r["created_at"], reverse=True)


def inbox_done(conn, user_id: int) -> list[dict]:
    """내가 결정한 요청. 요청 열에 decision, comment, decided_at을 붙여 돌려준다."""
    return fetch_all(
        conn,
        """
        select r.*, a.decision, a.comment, a.created_at as decided_at
        from approval_requests r
        join approvals a on a.request_id = r.id
        where a.approver_id = ?
        order by a.id desc
        """,
        (user_id,),
    )
