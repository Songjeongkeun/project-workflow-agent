"""3.6 승인: 승인함, 상세, 승인, 반려."""

from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from db import fetch_one, get_connection
from services import approvals, artifacts, controller, jobs, members, planning, sessions

router = APIRouter(prefix="/api/approvals", tags=["approvals"])


class DecisionRequest(BaseModel):
    target_hash: str
    comment: str | None = None


def _summary(conn, req: dict) -> str:
    detail = req["detail"] or {}
    if req["gate"] == "G1":
        version = artifacts.get_version(conn, req["target_version_id"])
        return f"기획서 v{version['version'] if version else '?'}와 완료 조건"
    if req["gate"] == "G2":
        return f"화면 {len(detail.get('screens', []))}개" if "screens" in detail else "화면 계획"
    if req["gate"] == "G3":
        return f"계획 밖 변경 {len(detail.get('changes', []))}건"
    return f"배포할 커밋 {(req['target_hash'] or '')[:7]}"


def _item(conn, req: dict) -> dict:
    project = fetch_one(conn, "select name from projects where id = ?", (req["project_id"],))
    return {
        "id": req["id"],
        "project_id": req["project_id"],
        "project_name": project["name"],
        "gate": req["gate"],
        "role": req["role"],
        "run_id": req["run_id"],
        "summary": _summary(conn, req),
        "requested_at": req["created_at"],
        "status": req["status"],
    }


@router.get("")
def inbox(request: Request, view: Literal["pending", "done"] = "pending"):
    with get_connection() as conn:
        user = sessions.get_login_user(conn, request)
        if view == "pending":
            return {"items": [_item(conn, r) for r in approvals.inbox_pending(conn, user["id"])]}
        return {
            "items": [
                {**_item(conn, r), "decision": r["decision"], "comment": r["comment"], "decided_at": r["decided_at"]}
                for r in approvals.inbox_done(conn, user["id"])
            ]
        }


@router.get("/{aid}")
def detail(aid: int, request: Request):
    with get_connection() as conn:
        user = sessions.get_login_user(conn, request)
        req = approvals.get_request(conn, aid)
        roles = members.require_member(conn, req["project_id"], user["id"])
        version = artifacts.get_version(conn, req["target_version_id"])

        decision = None
        a = approvals.decision_of(conn, req["id"])
        if a is not None:
            approver = fetch_one(conn, "select name from users where id = ?", (a["approver_id"],)) if a["approver_id"] else None
            decision = {
                "approver": approver["name"] if approver else "(승계)", "role": a["role"],
                "decision": a["decision"], "comment": a["comment"], "created_at": a["created_at"],
            }

        out = {
            **_item(conn, req),
            "target_hash": req["target_hash"],
            "target_version": version["version"] if version else None,
            "deploy_target": req["deploy_target"],
            "can_decide": req["status"] == "pending" and req["role"] in roles,
            "decision": decision,
            "g1": None, "g2": None, "g3": None, "g4": None,
        }
        d = req["detail"] or {}
        if req["gate"] == "G1":
            out["g1"] = {"planning_url": f"/projects/{req['project_id']}/planning"}
        elif req["gate"] == "G2":
            out["g2"] = {"screens": d.get("screens", []), "check": d.get("check", {"passed": True, "missing": []}), "carried_over": d.get("carried_over")}
        elif req["gate"] == "G3":
            out["g3"] = {"changes": d.get("changes", []), "candidate_sha": d.get("candidate_sha")}
        else:
            out["g4"] = {
                "sha": d.get("sha"), "target": d.get("target"), "results": d.get("results", []),
                # 승인해도 push가 거부될 대상이면 그 이유 (화면에서 승인 버튼을 막는다)
                "target_problem": approvals.deploy_target_problem(conn, req) if req["status"] == "pending" else None,
            }
        return out


def _decide(aid: int, decision: str, body: DecisionRequest, request: Request) -> dict:
    run_id, rerun_a01_for = None, None
    with get_connection() as conn:
        user = sessions.get_login_user(conn, request)
        req = approvals.get_request(conn, aid)

        if req["gate"] == "G1":
            # G1 승인은 기획서 화면의 [확정]으로 한다. 반려는 수정 요청과 같다
            if decision == "approve":
                raise HTTPException(status_code=400, detail="G1은 기획서 검토 화면에서 확정합니다.")
            if req["target_hash"] != body.target_hash:
                raise HTTPException(status_code=409, detail="승인 대상이 바뀌었습니다. 다시 불러오세요.")
            planning.reject_g1(conn, req, user, body.comment or "")
            rerun_a01_for = req["project_id"]
        else:
            approvals.decide(conn, req, user["id"], decision, body.target_hash, body.comment)
            run_id = controller.on_decision(conn, req, decision)
        status = req["status"]

    # 결정을 저장(커밋)한 뒤 에이전트를 진행한다
    if rerun_a01_for is not None:
        jobs.submit_a01(rerun_a01_for)
    if run_id is not None:
        jobs.submit_run(run_id)
    return {"status": status}


@router.post("/{aid}/approve")
def approve(aid: int, body: DecisionRequest, request: Request):
    return _decide(aid, "approve", body, request)


@router.post("/{aid}/reject")
def reject(aid: int, body: DecisionRequest, request: Request):
    return _decide(aid, "reject", body, request)
