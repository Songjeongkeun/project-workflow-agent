"""SQLite 연결과 테이블 만들기.

06 데이터 모델의 16개 테이블 + 로그인 세션(auth_sessions).
JSON 값(실행 계획의 steps, 변경 영향 impact 등)은 TEXT 열에 JSON 문자열로 저장한다.
"""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

import config

# JSON 문자열로 저장하는 열. 꺼낼 때 Python 값으로 바꾼다.
JSON_COLUMNS = {
    "deploy_target", "detail", "changed_ids", "impact", "reapproval_gates",
    "steps", "input_versions", "gates", "target", "limits", "failure", "waiting_input", "links",
    "payload",
}
# 0/1로 저장하는 참·거짓 열
BOOL_COLUMNS = {"required", "has_ui", "allowed"}


def now() -> str:
    """지금 시각 (UTC, ISO 문자열). 문자열끼리 비교해도 시간 순서가 맞는다."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@contextmanager
def get_connection():
    """with get_connection() as conn: 으로 쓴다. 블록이 끝나면 커밋하고, 오류가 나면 되돌린다."""
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── 조회와 저장 도우미 ─────────────────────────────────────


def _to_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    item = dict(row)
    for key, value in item.items():
        if key in JSON_COLUMNS and value is not None:
            item[key] = json.loads(value)
        elif key in BOOL_COLUMNS and value is not None:
            item[key] = bool(value)
    return item


def fetch_one(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> dict | None:
    return _to_dict(conn.execute(sql, params).fetchone())


def fetch_all(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict]:
    return [_to_dict(row) for row in conn.execute(sql, params).fetchall()]


def _to_db(key: str, value):
    if key in JSON_COLUMNS and value is not None:
        return json.dumps(value, ensure_ascii=False)
    return value


def insert(conn: sqlite3.Connection, table: str, **values) -> int:
    """insert into table (...) values (?, ...) 후 새 id를 돌려준다. created_at은 자동으로 채운다."""
    values.setdefault("created_at", now())
    columns = ", ".join(values)
    marks = ", ".join("?" for _ in values)
    cursor = conn.execute(
        f"insert into {table} ({columns}) values ({marks})",
        tuple(_to_db(k, v) for k, v in values.items()),
    )
    return int(cursor.lastrowid)


def update(conn: sqlite3.Connection, table: str, row_id: int, **values) -> None:
    """update table set a = ?, b = ? where id = ?"""
    sets = ", ".join(f"{k} = ?" for k in values)
    conn.execute(
        f"update {table} set {sets} where id = ?",
        (*[_to_db(k, v) for k, v in values.items()], row_id),
    )


def update_if(conn: sqlite3.Connection, table: str, row_id: int, where: dict, **values) -> bool:
    """조건이 맞을 때만 바꾼다. 바꿨으면 True.

    update table set a = ? where id = ? and status = ?
    읽고 나서 바꾸기 전에 다른 요청이 먼저 바꿨으면(승인 두 번, 버튼 연타) 0행이 바뀌어 False가 된다.
    """
    sets = ", ".join(f"{k} = ?" for k in values)
    conds = "".join(f" and {k} = ?" for k in where)
    cursor = conn.execute(
        f"update {table} set {sets} where id = ?{conds}",
        (*[_to_db(k, v) for k, v in values.items()], row_id, *[_to_db(k, v) for k, v in where.items()]),
    )
    return cursor.rowcount == 1


def write_lock(conn: sqlite3.Connection) -> None:
    """지금부터 이 연결이 끝날 때까지 다른 쓰기를 막는다 (begin immediate).

    '확인하고 → 쓰기'를 한 덩어리로 만들 때 쓴다. 확인보다 먼저 불러야 한다.
    다른 연결이 쓰는 중이면 끝날 때까지 기다린다 (sqlite3 기본 5초).
    """
    if not conn.in_transaction:
        conn.execute("begin immediate")


# ── 테이블 ────────────────────────────────────────────────

SCHEMA = """
create table if not exists users (
    id integer primary key autoincrement,
    login_id varchar(64) not null unique,
    password_hash varchar(255) not null,
    name varchar(100) not null,
    slack_user_id varchar(64),
    created_at varchar(40) not null
);

-- 06에 없는 테이블: 07 7장의 토큰 로그인과 로그아웃. 토큰 원문은 저장하지 않고 해시만 둔다.
create table if not exists auth_sessions (
    id integer primary key autoincrement,
    user_id integer not null references users(id),
    token_hash varchar(64) not null unique,
    expires_at varchar(40) not null,
    revoked_at varchar(40),
    created_at varchar(40) not null
);

create table if not exists projects (
    id integer primary key autoincrement,
    name varchar(200) not null,
    created_by integer not null references users(id),
    has_ui integer,
    created_at varchar(40) not null
);

create table if not exists project_members (
    id integer primary key autoincrement,
    project_id integer not null references projects(id),
    user_id integer not null references users(id),
    invited_by integer references users(id),
    created_at varchar(40) not null,
    unique (project_id, user_id)
);

-- 역할을 거둘 때는 행을 지우지 않고 revoked_at을 채운다.
create table if not exists member_roles (
    id integer primary key autoincrement,
    member_id integer not null references project_members(id),
    role varchar(16) not null check (role in ('planner', 'designer', 'developer', 'deployer')),
    assigned_by integer references users(id),
    revoked_at varchar(40),
    created_at varchar(40) not null
);

create table if not exists messages (
    id integer primary key autoincrement,
    project_id integer not null references projects(id),
    sender varchar(8) not null,
    user_id integer references users(id),
    kind varchar(16) not null check (kind in ('description', 'answer', 'revision', 'agent')),
    body text not null,
    created_at varchar(40) not null,
    check ((sender = 'agent' and kind = 'agent' and user_id is null)
        or (sender = 'user' and kind != 'agent' and user_id is not null))
);

-- required, 상태 dropped는 06에 더한 것: 필수 질문만 G1을 막는다. 다시 정리했더니 더 묻지 않게 된 질문은 닫는다.
create table if not exists questions (
    id integer primary key autoincrement,
    project_id integer not null references projects(id),
    field_path varchar(255),
    body text not null,
    required integer not null default 0,
    status varchar(16) not null default 'open' check (status in ('open', 'answered', 'dropped')),
    answer_message_id integer references messages(id),
    created_at varchar(40) not null
);

-- 산출물 본문은 data/ 아래 파일로 두고 DB에는 위치와 해시만 둔다.
create table if not exists artifact_versions (
    id integer primary key autoincrement,
    project_id integer not null references projects(id),
    kind varchar(16) not null check (kind in ('planning', 'screen_plan', 'tech_spec')),
    version integer not null,
    content_hash varchar(64) not null,
    content_path varchar(500) not null,
    prev_version_id integer references artifact_versions(id),
    created_by_agent varchar(4) not null,
    status varchar(16) not null default 'draft' check (status in ('draft', 'confirmed', 'void')),
    external_url varchar(500),
    created_at varchar(40) not null,
    unique (project_id, kind, version)
);

-- detail은 06에 더한 것: 승인 상세 화면에 보일 G2·G3·G4 내용
create table if not exists approval_requests (
    id integer primary key autoincrement,
    project_id integer not null references projects(id),
    run_id integer references runs(id),
    gate varchar(2) not null check (gate in ('G1', 'G2', 'G3', 'G4')),
    role varchar(16) not null,
    target_version_id integer references artifact_versions(id),
    target_hash varchar(64) not null,
    deploy_target text,
    detail text,
    status varchar(16) not null default 'pending' check (status in ('pending', 'approved', 'rejected', 'void')),
    created_at varchar(40) not null
);

-- 결정은 한 번 쓰면 고치지 않는다. 반려에는 의견이 꼭 있어야 한다.
create table if not exists approvals (
    id integer primary key autoincrement,
    request_id integer not null references approval_requests(id),
    approver_id integer references users(id),
    role varchar(16) not null,
    decision varchar(16) not null check (decision in ('approve', 'reject', 'carry_over')),
    comment text,
    change_request_id integer references change_requests(id),
    created_at varchar(40) not null,
    check (decision != 'reject' or (comment is not null and comment != ''))
);

-- source_message_id는 06에 더한 것: 변경 요청의 원문 메시지 (추적 경로 5)
create table if not exists change_requests (
    id integer primary key autoincrement,
    project_id integer not null references projects(id),
    requested_by integer not null references users(id),
    request_text text not null,
    from_version_id integer not null references artifact_versions(id),
    to_version_id integer references artifact_versions(id),
    changed_ids text,
    impact text,
    reapproval_gates text,
    source_message_id integer references messages(id),
    status varchar(20) not null default 'analyzing'
        check (status in ('analyzing', 'awaiting_approval', 'rerunning', 'done', 'rejected')),
    created_at varchar(40) not null
);

create table if not exists run_plans (
    id integer primary key autoincrement,
    project_id integer not null references projects(id),
    version integer not null,
    steps text not null,
    input_versions text not null,
    gates text not null,
    target text not null,
    limits text not null,
    status varchar(8) not null default 'valid' check (status in ('valid', 'void')),
    created_at varchar(40) not null
);

-- candidate_sha, failure, waiting_input은 06에 더한 것: 후보 커밋, 실패 이유, 범위 확인 대기 내용
create table if not exists runs (
    id integer primary key autoincrement,
    project_id integer not null references projects(id),
    plan_id integer not null references run_plans(id),
    change_request_id integer references change_requests(id),
    redeploy_of_run_id integer references runs(id),
    status varchar(20) not null default 'running'
        check (status in ('running', 'waiting_input', 'waiting_approval', 'failed', 'void', 'done')),
    current_step varchar(16),
    retry_count integer not null default 0,
    candidate_sha varchar(64),
    tested_sha varchar(64),
    pushed_sha varchar(64),
    deployment_id varchar(64),
    deployment_url varchar(500),
    finished_at varchar(40),
    failure text,
    waiting_input text,
    created_at varchar(40) not null,
    check (status != 'done' or tested_sha = pushed_sha)
);

-- links는 06에 더한 것: 실행 상태 화면의 단계별 산출물 링크
create table if not exists run_steps (
    id integer primary key autoincrement,
    project_id integer not null references projects(id),
    run_id integer references runs(id),
    agent varchar(4) not null check (agent in ('A01', 'A02', 'A03', 'A04', 'A05')),
    status varchar(16) not null check (status in ('completed', 'needs_input', 'failed')),
    summary text,
    error text,
    input_tokens integer not null default 0,
    output_tokens integer not null default 0,
    finished_at varchar(40),
    links text,
    created_at varchar(40) not null
);

create table if not exists test_results (
    id integer primary key autoincrement,
    run_id integer not null references runs(id),
    ac_id varchar(16) not null,
    result varchar(8) not null check (result in ('pass', 'fail', 'not_run')),
    sha varchar(64) not null,
    phase varchar(4) not null check (phase in ('pre', 'post')),
    evidence_path varchar(500),
    created_at varchar(40) not null
);

create table if not exists tool_calls (
    id integer primary key autoincrement,
    run_id integer not null references runs(id),
    agent varchar(4) not null,
    tool varchar(64) not null,
    target text,
    allowed integer not null,
    reason text,
    created_at varchar(40) not null
);

-- status의 pending, payload, attempts, next_try_at, last_error, sent_at은 06에 더한 것:
-- 알림을 먼저 쌓아 두고 따로 보내며, 실패하면 간격을 두고 다시 보낸다 (04 7.4·7.5)
create table if not exists notifications (
    id integer primary key autoincrement,
    project_id integer not null references projects(id),
    run_id integer references runs(id),
    channel varchar(8) not null check (channel in ('notion', 'slack')),
    event varchar(64) not null,
    recipient varchar(64),
    dedupe_key varchar(128) not null unique,
    status varchar(8) not null check (status in ('pending', 'sent', 'failed')),
    payload text,
    attempts integer not null default 0,
    next_try_at varchar(40),
    last_error text,
    sent_at varchar(40),
    created_at varchar(40) not null
);

-- 06에 더한 것: 프로젝트마다 Notion에 만든 페이지 (kind: project=프로젝트 상위 페이지, planning, tech_spec, checks, deploy)
-- 처음 게시할 때 만들고, 그 뒤로는 같은 페이지의 내용을 바꾼다
create table if not exists notion_pages (
    id integer primary key autoincrement,
    project_id integer not null references projects(id),
    kind varchar(16) not null,
    page_id varchar(64) not null,
    url varchar(500),
    created_at varchar(40) not null,
    unique (project_id, kind)
);
"""


def _upgrade_notifications(conn: sqlite3.Connection) -> None:
    """예전 모양의 notifications(보낼 내용 칸이 없음)를 새 모양으로 바꾼다.

    SQLite는 check 조건을 고칠 수 없어서 테이블을 다시 만든다.
    예전 모양으로는 알림을 쌓은 적이 없으므로, 비어 있을 때만 바꾼다.
    """
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(notifications)")}
    if not columns or "payload" in columns:
        return  # 아직 없거나 이미 새 모양
    if conn.execute("select count(*) from notifications").fetchone()[0]:
        raise RuntimeError("notifications 테이블이 예전 모양이고 비어 있지 않습니다. 직접 옮겨 주세요.")
    conn.execute("drop table notifications")


def init_db() -> None:
    """서버가 시작될 때 테이블을 만든다 (이미 있으면 그대로 둔다)."""
    with get_connection() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        _upgrade_notifications(conn)
        conn.executescript(SCHEMA)
