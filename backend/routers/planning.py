"""3.4 기획서: 조회, 근거, 확정(G1)."""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from db import get_connection
from services import artifacts, controller, jobs, members, planning, sessions

router = APIRouter(prefix="/api/projects/{pid}/planning", tags=["planning"])


class ConfirmRequest(BaseModel):
    version: int
    hash: str


@router.get("")
def get_planning(pid: int, request: Request):
    with get_connection() as conn:
        user = sessions.get_login_user(conn, request)
        _, roles = members.project_access(conn, pid, user)
        version = artifacts.latest(conn, pid, "planning")
        if version is None:
            raise HTTPException(status_code=404, detail="아직 기획서 초안이 없습니다. 대화에서 만들 서비스를 설명해 주세요.")

        bundle = artifacts.planning_bundle(version)
        scope = bundle["planning"].get("release_scope", {})
        names = {f["id"]: f["name"] for f in scope.get("features", [])}
        blockers = planning.confirm_blockers(conn, pid)
        return {
            "version": version["version"],
            "hash": version["content_hash"],
            "status": "confirmed" if version["status"] == "confirmed" else "draft",
            "brief": bundle["brief"],
            "project_type": scope.get("project_type"),
            "tech_stack": scope.get("tech_stack", []),
            "features": [{"id": f["id"], "name": f["name"], "included": f.get("included_in_release")} for f in scope.get("features", [])],
            "acceptance_criteria": [{**c, "feature_name": names.get(c["feature_id"])} for c in bundle["criteria"]],
            "required_open_questions": len(planning.open_questions(conn, pid, required_only=True)),
            "can_confirm": not blockers and "planner" in roles,
            "is_planner": "planner" in roles,
            "blocked_reason": " ".join(blockers) if blockers and version["status"] != "confirmed" else None,
            "notion_url": version["external_url"],
        }


@router.get("/evidence")
def get_evidence(pid: int, request: Request, field_path: str | None = None):
    """항목의 근거가 된 원문 인용. field_path를 주면 그 경로(와 하위 경로)만 돌려준다."""
    with get_connection() as conn:
        user = sessions.get_login_user(conn, request)
        members.project_access(conn, pid, user)
        version = artifacts.latest(conn, pid, "planning")
        if version is None:
            raise HTTPException(status_code=404, detail="아직 기획서 초안이 없습니다.")
        items = artifacts.planning_bundle(version).get("evidence", [])

    if field_path:
        items = [
            e for e in items
            if e["field_path"] == field_path or e["field_path"].startswith((field_path + ".", field_path + "["))
        ]
    return {"evidence": items}


@router.post("/confirm")
def confirm(pid: int, body: ConfirmRequest, request: Request):
    with get_connection() as conn:
        user = sessions.get_login_user(conn, request)
        project, _ = members.project_access(conn, pid, user)
        result = planning.confirm(conn, project, user, body.version, body.hash)

    # 확정(커밋) 뒤 실행을 다음 멈출 곳까지 진행한다
    if result["run_id"] is not None:
        jobs.submit_run(result["run_id"])
    return result
