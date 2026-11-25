"""3.1 로그인, 3.9 상태 확인."""

from fastapi import APIRouter, Request
from pydantic import BaseModel

from db import get_connection
from services import approvals, members, sessions

router = APIRouter(prefix="/api", tags=["auth"])


class LoginRequest(BaseModel):
    login_id: str
    password: str


@router.post("/auth/login")
def login(body: LoginRequest):
    with get_connection() as conn:
        token, user = sessions.login(conn, body.login_id.strip(), body.password)
        return {"token": token, "user": {"id": user["id"], "login_id": user["login_id"], "name": user["name"]}}


@router.post("/auth/logout")
def logout(request: Request):
    with get_connection() as conn:
        sessions.logout(conn, request)
        return {"ok": True}


@router.get("/me")
def me(request: Request):
    with get_connection() as conn:
        user = sessions.get_login_user(conn, request)
        projects = [
            {"id": p["id"], "name": p["name"], "roles": members.roles_of(conn, p["id"], user["id"])}
            for p in members.my_projects(conn, user["id"])
        ]
        return {
            "id": user["id"],
            "login_id": user["login_id"],
            "name": user["name"],
            "slack_user_id": user["slack_user_id"],
            "projects": projects,
            "pending_approvals": len(approvals.inbox_pending(conn, user["id"])),
        }


@router.get("/health")
def health():
    with get_connection() as conn:
        conn.execute("select 1")
    return {"status": "ok"}
