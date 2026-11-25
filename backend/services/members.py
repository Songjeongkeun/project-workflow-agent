"""프로젝트, 멤버, 역할. 권한은 서버가 요청마다 확인한다 (화면에서 버튼을 숨기는 것만으로 막지 않는다)."""

from fastapi import HTTPException

from db import fetch_all, fetch_one, insert, now, update

ROLES = ["planner", "designer", "developer", "deployer"]
ROLE_LABEL = {"planner": "기획", "designer": "디자인", "developer": "개발", "deployer": "배포"}
GATE_ROLE = {"G1": "planner", "G2": "designer", "G3": "developer", "G4": "deployer"}


def role_label(role: str) -> str:
    return ROLE_LABEL.get(role, role)


# ── 프로젝트 ──────────────────────────────────────────────


def get_project(conn, project_id: int) -> dict:
    project = fetch_one(conn, "select * from projects where id = ?", (project_id,))
    if project is None:
        raise HTTPException(status_code=404, detail="없는 프로젝트입니다.")
    return project


def create_project(conn, name: str, user: dict) -> int:
    name = name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="프로젝트 이름을 입력해 주세요.")
    project_id = insert(conn, "projects", name=name, created_by=user["id"])
    # 만든 사람은 멤버가 되고 기획 담당을 맡는다 (06 2장 예시 1)
    member_id = insert(conn, "project_members", project_id=project_id, user_id=user["id"], invited_by=user["id"])
    insert(conn, "member_roles", member_id=member_id, role="planner", assigned_by=user["id"])
    return project_id


def my_projects(conn, user_id: int) -> list[dict]:
    return fetch_all(
        conn,
        """
        select p.* from projects p
        join project_members m on m.project_id = p.id
        where m.user_id = ?
        order by p.id desc
        """,
        (user_id,),
    )


# ── 멤버와 역할 ───────────────────────────────────────────


def get_member(conn, project_id: int, user_id: int) -> dict | None:
    return fetch_one(conn, "select * from project_members where project_id = ? and user_id = ?", (project_id, user_id))


def active_roles(conn, member_id: int) -> list[str]:
    rows = fetch_all(conn, "select role from member_roles where member_id = ? and revoked_at is null", (member_id,))
    held = {row["role"] for row in rows}
    return [r for r in ROLES if r in held]  # 기획 → 디자인 → 개발 → 배포 순서


def roles_of(conn, project_id: int, user_id: int) -> list[str]:
    member = get_member(conn, project_id, user_id)
    return active_roles(conn, member["id"]) if member else []


def require_member(conn, project_id: int, user_id: int) -> list[str]:
    """멤버가 아니면 403. 멤버면 지금 가진 역할 목록을 돌려준다."""
    member = get_member(conn, project_id, user_id)
    if member is None:
        raise HTTPException(status_code=403, detail="이 프로젝트의 멤버가 아닙니다.")
    return active_roles(conn, member["id"])


def require_role(conn, project_id: int, user_id: int, role: str) -> None:
    if role not in require_member(conn, project_id, user_id):
        raise HTTPException(status_code=403, detail=f"{role_label(role)} 담당만 할 수 있습니다.")


def project_access(conn, project_id: int, user: dict) -> tuple[dict, list[str]]:
    """프로젝트 API 공통 확인: 없는 프로젝트면 404, 멤버가 아니면 403. (프로젝트, 내 역할)을 돌려준다."""
    project = get_project(conn, project_id)
    roles = require_member(conn, project_id, user["id"])
    return project, roles


def list_members(conn, project_id: int) -> list[dict]:
    rows = fetch_all(
        conn,
        """
        select m.id as member_id, m.invited_by, u.id as user_id, u.login_id, u.name, u.slack_user_id
        from project_members m
        join users u on u.id = m.user_id
        where m.project_id = ?
        order by m.id
        """,
        (project_id,),
    )
    for row in rows:
        row["roles"] = active_roles(conn, row["member_id"])
    return rows


def _check_roles(roles: list[str]) -> list[str]:
    bad = [r for r in roles if r not in ROLES]
    if bad:
        raise HTTPException(status_code=400, detail=f"없는 역할: {', '.join(bad)}")
    return [r for r in ROLES if r in roles]


def _require_owner(project: dict, user: dict, action: str) -> None:
    if project["created_by"] != user["id"]:
        raise HTTPException(status_code=403, detail=f"프로젝트를 만든 사람만 {action} 수 있습니다.")


def invite(conn, project: dict, by: dict, login_id: str, roles: list[str], slack_user_id: str | None) -> int:
    _require_owner(project, by, "멤버를 초대할")
    user = fetch_one(conn, "select * from users where login_id = ?", (login_id.strip(),))
    if user is None:
        raise HTTPException(status_code=404, detail="없는 계정입니다. 계정은 운영 담당자가 서버에서 만듭니다.")
    if get_member(conn, project["id"], user["id"]) is not None:
        raise HTTPException(status_code=400, detail="이미 멤버입니다.")

    member_id = insert(conn, "project_members", project_id=project["id"], user_id=user["id"], invited_by=by["id"])
    for role in _check_roles(roles):
        insert(conn, "member_roles", member_id=member_id, role=role, assigned_by=by["id"])
    if slack_user_id is not None:
        update(conn, "users", user["id"], slack_user_id=slack_user_id.strip() or None)
    return user["id"]


def update_member(conn, project: dict, by: dict, user_id: int, roles: list[str] | None, slack_user_id: str | None) -> None:
    _require_owner(project, by, "역할을 바꿀")
    member = get_member(conn, project["id"], user_id)
    if member is None:
        raise HTTPException(status_code=404, detail="이 프로젝트의 멤버가 아닙니다.")

    if roles is not None:
        wanted = set(_check_roles(roles))
        current = fetch_all(conn, "select * from member_roles where member_id = ? and revoked_at is null", (member["id"],))
        for row in current:
            if row["role"] not in wanted:
                update(conn, "member_roles", row["id"], revoked_at=now())  # 지우지 않고 거둔 시각을 남긴다
        for role in wanted - {row["role"] for row in current}:
            insert(conn, "member_roles", member_id=member["id"], role=role, assigned_by=by["id"])
    if slack_user_id is not None:
        update(conn, "users", user_id, slack_user_id=slack_user_id.strip() or None)


def gates_without_owner(conn, project_id: int) -> list[str]:
    """담당자가 없는 승인 지점 (멤버 화면 경고)."""
    held = {role for m in list_members(conn, project_id) for role in m["roles"]}
    return [gate for gate, role in GATE_ROLE.items() if role not in held]
