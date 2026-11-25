"""계정과 로그인 세션. 토큰은 원문 대신 해시로 저장하고, 로그아웃하면 거둔다."""

from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request

import config
from db import fetch_one, insert, now, update
from security import hash_password, new_token, token_hash, verify_password


def create_user(conn, login_id: str, password: str, name: str, slack_user_id: str | None = None) -> int:
    return insert(
        conn, "users",
        login_id=login_id, password_hash=hash_password(password), name=name, slack_user_id=slack_user_id,
    )


def find_user_by_login_id(conn, login_id: str) -> dict | None:
    return fetch_one(conn, "select * from users where login_id = ?", (login_id,))


def login(conn, login_id: str, password: str) -> tuple[str, dict]:
    user = find_user_by_login_id(conn, login_id)
    if user is None or not verify_password(password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="아이디 또는 비밀번호가 맞지 않습니다.")

    token = new_token()
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=config.TOKEN_HOURS)).isoformat(timespec="milliseconds")
    insert(conn, "auth_sessions", user_id=user["id"], token_hash=token_hash(token), expires_at=expires_at)
    return token, user


def _token_from(request: Request) -> str:
    """Authorization: Bearer <토큰> 헤더에서 토큰을 꺼낸다."""
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
    return header.removeprefix("Bearer ").strip()


def _valid_session(conn, token: str) -> dict | None:
    sess = fetch_one(conn, "select * from auth_sessions where token_hash = ?", (token_hash(token),))
    if sess is None or sess["revoked_at"] is not None or sess["expires_at"] <= now():
        return None
    return sess


def get_login_user(conn, request: Request) -> dict:
    """요청을 보낸 사용자. 로그인하지 않았거나 만료됐으면 401."""
    sess = _valid_session(conn, _token_from(request))
    if sess is None:
        raise HTTPException(status_code=401, detail="로그인이 만료됐습니다. 다시 로그인해 주세요.")
    return fetch_one(conn, "select * from users where id = ?", (sess["user_id"],))


def logout(conn, request: Request) -> None:
    sess = _valid_session(conn, _token_from(request))
    if sess is not None:
        update(conn, "auth_sessions", sess["id"], revoked_at=now())
