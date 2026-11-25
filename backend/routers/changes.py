"""3.7 변경 요청."""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from db import fetch_all, fetch_one, get_connection
from services import artifacts, jobs, members, planning, sessions

router = APIRouter(prefix="/api", tags=["change-requests"])


class ChangeRequestBody(BaseModel):
    text: str


@router.get("/projects/{pid}/change-requests")
def list_changes(pid: int, request: Request):
    with get_connection() as conn:
        user = sessions.get_login_user(conn, request)
        members.project_access(conn, pid, user)
        rows = fetch_all(
            conn,
            """
            select c.id, c.status, c.request_text as text, u.name as "by", c.created_at
            from change_requests c
            join users u on u.id = c.requested_by
            where c.project_id = ?
            order by c.id desc
            """,
            (pid,),
        )
        return {"change_requests": rows}


@router.post("/projects/{pid}/change-requests", status_code=201)
def create_change(pid: int, body: ChangeRequestBody, request: Request):
    with get_connection() as conn:
        user = sessions.get_login_user(conn, request)
        project, _ = members.project_access(conn, pid, user)
        cr_id = planning.register_change(conn, project, user, body.text)

    # 변경 요청을 저장(커밋)한 뒤 A01이 새 초안을 만들고 영향을 분석한다
    jobs.submit_a01(pid, cr_id)
    return {"id": cr_id}


@router.get("/change-requests/{cid}")
def change_detail(cid: int, request: Request):
    with get_connection() as conn:
        user = sessions.get_login_user(conn, request)
        cr = fetch_one(conn, "select * from change_requests where id = ?", (cid,))
        if cr is None:
            raise HTTPException(status_code=404, detail="없는 변경 요청입니다.")
        members.require_member(conn, cr["project_id"], user["id"])
        by = fetch_one(conn, "select name from users where id = ?", (cr["requested_by"],))
        run = fetch_one(conn, "select id from runs where change_request_id = ? order by id desc limit 1", (cid,))

        # 다시 승인할 지점마다 지금 상태
        reapproval = []
        for gate in cr["reapproval_gates"] or []:
            if gate == "G1":
                req = fetch_one(
                    conn,
                    "select * from approval_requests where gate = 'G1' and target_version_id = ? order by id desc limit 1",
                    (cr["to_version_id"],),
                )
            else:
                req = fetch_one(
                    conn,
                    "select * from approval_requests where gate = ? and run_id = ? order by id desc limit 1",
                    (gate, run["id"] if run else -1),
                )
            reapproval.append({
                "gate": gate, "role": members.GATE_ROLE[gate],
                "status": req["status"] if req else "waiting", "approval_id": req["id"] if req else None,
            })

        impact = dict(cr["impact"] or {})
        carried = impact.pop("carried_over", [])
        impact.pop("rerun_steps", None)
        from_v = artifacts.get_version(conn, cr["from_version_id"])
        to_v = artifacts.get_version(conn, cr["to_version_id"])
        return {
            "id": cr["id"],
            "project_id": cr["project_id"],
            "status": cr["status"],
            "request": {"by": by["name"], "text": cr["request_text"], "created_at": cr["created_at"]},
            "from_version": from_v["version"],
            "to_version": to_v["version"] if to_v else None,
            "changed": cr["changed_ids"] or {"added": [], "modified": [], "removed": []},
            "impact": impact,
            "reapproval": reapproval,
            "carried_over": carried,
            "run_id": run["id"] if run else None,
        }
