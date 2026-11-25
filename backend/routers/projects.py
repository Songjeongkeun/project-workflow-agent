"""3.2 프로젝트, 3.3 대화, 3.8 멤버와 역할."""

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from db import fetch_all, fetch_one, get_connection
from services import artifacts, jobs, members, planning, sessions, views

router = APIRouter(prefix="/api/projects", tags=["projects"])


# ── 프로젝트 ──────────────────────────────────────────────


class ProjectRequest(BaseModel):
    name: str


def _updated_at(conn, project: dict) -> str:
    """마지막 메시지나 에이전트 작업 시각. 없으면 만든 시각."""
    msg = fetch_one(conn, "select created_at from messages where project_id = ? order by id desc limit 1", (project["id"],))
    step = fetch_one(conn, "select created_at from run_steps where project_id = ? order by id desc limit 1", (project["id"],))
    times = [row["created_at"] for row in (msg, step) if row is not None]
    return max(times) if times else project["created_at"]


@router.get("")
def list_projects(request: Request):
    with get_connection() as conn:
        user = sessions.get_login_user(conn, request)
        items = []
        for p in members.my_projects(conn, user["id"]):
            state = views.project_state(conn, p)
            items.append({
                "id": p["id"],
                "name": p["name"],
                "roles": members.roles_of(conn, p["id"], user["id"]),
                "current_step": state["current_step"],
                "status": state["status"],
                "updated_at": _updated_at(conn, p),
                "my_pending_approvals": views.pending_for(conn, p["id"], user["id"]),
            })
        return {"projects": items}


@router.post("", status_code=201)
def create_project(body: ProjectRequest, request: Request):
    with get_connection() as conn:
        user = sessions.get_login_user(conn, request)
        return {"id": members.create_project(conn, body.name, user)}


@router.get("/{pid}")
def get_project(pid: int, request: Request):
    with get_connection() as conn:
        user = sessions.get_login_user(conn, request)
        project, roles = members.project_access(conn, pid, user)
        return {
            "id": project["id"],
            "name": project["name"],
            "created_by": project["created_by"],
            "is_owner": project["created_by"] == user["id"],
            "has_ui": project["has_ui"],
            "roles": roles,
            **views.project_state(conn, project),
        }


# ── 대화 ──────────────────────────────────────────────────


class Answer(BaseModel):
    question_id: int
    body: str | None = None


class MessageRequest(BaseModel):
    kind: str  # description | answer | revision
    body: str = ""
    answers: list[Answer] = Field(default_factory=list)


@router.get("/{pid}/messages")
def get_messages(pid: int, request: Request):
    with get_connection() as conn:
        user = sessions.get_login_user(conn, request)
        members.project_access(conn, pid, user)

        latest = artifacts.latest(conn, pid, "planning")
        features, feature_items, unknown = [], [], 0
        if latest is not None:
            bundle = artifacts.planning_bundle(latest)
            scope = bundle["planning"].get("release_scope", {}).get("features", [])
            features = [f["name"] for f in scope]
            # 화면의 "정리한 기능": FEAT-001 점검 시간 안내
            feature_items = [{"id": f.get("id"), "name": f["name"]} for f in scope]
            unknown = sum(c.get("origin") == "미정" for c in bundle["criteria"])
        questions = [q for q in planning.list_questions(conn, pid) if q["status"] != "dropped"]
        # 보낸 사람 이름 (피그마: 말풍선 위 "김메이커")
        names = {u["id"]: u["name"] for u in fetch_all(conn, "select id, name from users")}

        return {
            "messages": [
                {"id": m["id"], "sender": m["sender"], "kind": m["kind"], "body": m["body"], "created_at": m["created_at"],
                 "author": names.get(m["user_id"]) if m["sender"] == "user" else None}
                for m in planning.list_messages(conn, pid)
            ],
            "questions": [
                {"id": q["id"], "body": q["body"], "status": q["status"], "required": q["required"], "field_path": q["field_path"]}
                for q in questions
            ],
            "summary": {
                "features": features,
                "feature_items": feature_items,
                "open_questions": sum(q["status"] == "open" for q in questions),
                "unknown_count": unknown,
                "planning_version": latest["version"] if latest else None,
            },
            "processing": planning.processing(conn, pid),
            "locked": latest is not None and latest["status"] == "confirmed",
            "error": planning.last_a01_error(conn, pid),
        }


@router.post("/{pid}/messages", status_code=202)
def post_message(pid: int, body: MessageRequest, request: Request):
    with get_connection() as conn:
        user = sessions.get_login_user(conn, request)
        project, _ = members.project_access(conn, pid, user)
        answers = [a.model_dump() for a in body.answers]
        message_id = planning.receive_message(conn, project, user, body.kind, body.body, answers)

    # 메시지를 저장(커밋)한 뒤 A01이 기획서 초안을 새로 만든다
    jobs.submit_a01(pid)
    return {"message_id": message_id}


# ── 멤버와 역할 ───────────────────────────────────────────


class InviteRequest(BaseModel):
    login_id: str
    roles: list[str] = Field(default_factory=list)
    slack_user_id: str | None = None


class MemberUpdateRequest(BaseModel):
    roles: list[str] | None = None
    slack_user_id: str | None = None


@router.get("/{pid}/members")
def get_members(pid: int, request: Request):
    with get_connection() as conn:
        user = sessions.get_login_user(conn, request)
        project, _ = members.project_access(conn, pid, user)
        rows = members.list_members(conn, pid)
        names = {m["user_id"]: m["name"] for m in rows}
        return {
            "members": [
                {
                    "user_id": m["user_id"], "login_id": m["login_id"], "name": m["name"], "roles": m["roles"],
                    "slack_user_id": m["slack_user_id"],
                    "invited_by": names.get(m["invited_by"]) if m["invited_by"] else None,
                    "is_owner": m["user_id"] == project["created_by"],
                }
                for m in rows
            ],
            "gates": [
                {"gate": gate, "role": role, "owners": [m["name"] for m in rows if role in m["roles"]]}
                for gate, role in members.GATE_ROLE.items()
            ],
            "gates_without_owner": members.gates_without_owner(conn, pid),
            "can_edit": project["created_by"] == user["id"],
        }


@router.post("/{pid}/members", status_code=201)
def invite_member(pid: int, body: InviteRequest, request: Request):
    with get_connection() as conn:
        user = sessions.get_login_user(conn, request)
        project, _ = members.project_access(conn, pid, user)
        return {"user_id": members.invite(conn, project, user, body.login_id, body.roles, body.slack_user_id)}


@router.patch("/{pid}/members/{uid}")
def update_member(pid: int, uid: int, body: MemberUpdateRequest, request: Request):
    with get_connection() as conn:
        user = sessions.get_login_user(conn, request)
        project, _ = members.project_access(conn, pid, user)
        members.update_member(conn, project, user, uid, body.roles, body.slack_user_id)
        return {"ok": True}
