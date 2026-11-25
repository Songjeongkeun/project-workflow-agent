"""3.5 실행: 상태, 범위 확인, 다시 시도, 목록, 기록, 재배포."""

from fastapi import APIRouter, HTTPException, Request

from db import fetch_all, fetch_one, get_connection
from services import artifacts, controller, jobs, members, sessions, views

router = APIRouter(prefix="/api", tags=["runs"])


def _run_for_member(conn, run_id: int, user: dict) -> dict:
    run = controller.get_run(conn, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="없는 실행입니다.")
    members.require_member(conn, run["project_id"], user["id"])
    return run


@router.get("/projects/{pid}/run")
def run_status(pid: int, request: Request):
    with get_connection() as conn:
        user = sessions.get_login_user(conn, request)
        project, _ = members.project_access(conn, pid, user)
        return views.run_view(conn, project, user["id"])


@router.get("/projects/{pid}/runs")
def list_runs(pid: int, request: Request):
    with get_connection() as conn:
        user = sessions.get_login_user(conn, request)
        _, roles = members.project_access(conn, pid, user)
        rows = fetch_all(
            conn,
            """
            select r.*, p.version as plan_version
            from runs r
            join run_plans p on p.id = r.plan_id
            where r.project_id = ?
            order by r.id desc
            """,
            (pid,),
        )
        return {
            "runs": [
                {
                    "id": r["id"], "plan_version": r["plan_version"], "status": r["status"],
                    "started_at": r["created_at"], "finished_at": r["finished_at"],
                    "change_request_id": r["change_request_id"], "redeploy_of_run_id": r["redeploy_of_run_id"],
                    "can_redeploy": r["status"] == "done" and bool(r["tested_sha"]) and "deployer" in roles,
                }
                for r in rows
            ],
            "has_active_run": controller.active_run(conn, pid) is not None,
        }


@router.get("/runs/{rid}")
def run_detail(rid: int, request: Request):
    """추적 경로, 검사 결과, 승인 이력, 토큰 사용량 (07 4.8)."""
    with get_connection() as conn:
        user = sessions.get_login_user(conn, request)
        run = _run_for_member(conn, rid, user)
        plan = controller.get_plan(conn, run["plan_id"])
        pv = artifacts.get_version(conn, plan["input_versions"]["planning"]["id"])

        # 이 실행의 근거가 된 원문 메시지
        sources = sorted({e["message_id"] for e in artifacts.planning_bundle(pv).get("evidence", [])})
        if run["change_request_id"]:
            cr = fetch_one(conn, "select source_message_id from change_requests where id = ?", (run["change_request_id"],))
            if cr and cr["source_message_id"]:
                sources.append(cr["source_message_id"])

        tests = fetch_all(conn, "select * from test_results where run_id = ? order by id", (rid,))
        history = fetch_all(
            conn,
            """
            select r.gate, a.role, a.decision, a.comment, a.created_at, u.name as approver
            from approval_requests r
            join approvals a on a.request_id = r.id
            left join users u on u.id = a.approver_id
            where r.run_id = ? or (r.gate = 'G1' and r.target_version_id = ?)
            order by a.id
            """,
            (rid, pv["id"]),
        )
        usage = fetch_all(
            conn,
            """
            select agent, sum(input_tokens) as input_tokens, sum(output_tokens) as output_tokens
            from run_steps where run_id = ? group by agent order by agent
            """,
            (rid,),
        )
        return {
            "id": run["id"],
            "project_id": run["project_id"],
            "status": run["status"],
            "trace": {
                "deployment_id": run["deployment_id"], "deployment_url": run["deployment_url"],
                "sha": run["pushed_sha"] or run["tested_sha"],
                "plan_version": plan["version"], "planning_version": pv["version"],
                "change_request_id": run["change_request_id"], "redeploy_of_run_id": run["redeploy_of_run_id"],
                "source_message_ids": sources, "target": plan["target"],
            },
            "test_results": [
                {"ac_id": t["ac_id"], "result": t["result"], "phase": t["phase"], "sha": t["sha"], "evidence_path": t["evidence_path"]}
                for t in tests
            ],
            "approvals": [{**h, "approver": h["approver"] or "(승계)"} for h in history],
            "usage": usage,
        }


@router.post("/runs/{rid}/scope-confirm")
def scope_confirm(rid: int, request: Request):
    with get_connection() as conn:
        user = sessions.get_login_user(conn, request)
        run = _run_for_member(conn, rid, user)
        controller.scope_confirm(conn, run, user["id"])
    jobs.submit_run(rid)
    return {"status": "running"}


@router.post("/runs/{rid}/retry")
def retry(rid: int, request: Request):
    with get_connection() as conn:
        user = sessions.get_login_user(conn, request)
        run = _run_for_member(conn, rid, user)
        controller.retry(conn, run, user["id"])
    jobs.submit_run(rid)
    return {"status": "running"}


@router.post("/runs/{rid}/redeploy", status_code=201)
def redeploy(rid: int, request: Request):
    with get_connection() as conn:
        user = sessions.get_login_user(conn, request)
        run = _run_for_member(conn, rid, user)
        new_run_id, approval_id = controller.redeploy(conn, run, user["id"])
        return {"run_id": new_run_id, "approval_id": approval_id}
