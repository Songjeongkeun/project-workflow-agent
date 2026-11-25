"""Slack 알림 테스트. 진짜 Slack에는 보내지 않고, 보내기 함수를 가짜로 바꿔 시험한다.

    pytest -q tests/test_notify.py
"""

import pytest
from fastapi.testclient import TestClient

import config
from db import fetch_all, get_connection, init_db, insert, update
from main import app
from services import notify, sessions

PW = "test-pw"
SLACK = {"kim": "U_KIM", "lee": "U_LEE", "park": "U_PARK", "choi": "U_CHOI"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "orbit.db")
    monkeypatch.setattr(config, "NOTIFY_ENABLED", False)    # 서버의 작업자는 보내지 않는다 (테스트가 직접 부른다)
    monkeypatch.setattr(config, "AGENT_INLINE", True)  # 에이전트를 요청 안에서 바로 실행
    monkeypatch.setattr(config, "APP_URL", "http://localhost:5173")  # 서버 .env의 주소와 상관없이 고정
    monkeypatch.setattr(config, "DEPLOY_HOST", "localhost")
    monkeypatch.setattr(config, "SLACK_CHANNEL_ID", "C_TEAM")
    monkeypatch.setattr(config, "SLACK_BOT_TOKEN", "xoxb-secret-token")
    monkeypatch.setattr(config, "NOTION_TOKEN", "")          # Notion은 따로 켜는 테스트에서만
    init_db()
    with get_connection() as conn:
        for login_id, name in (("kim", "김기획"), ("lee", "이디자인"), ("park", "박개발"), ("choi", "최배포")):
            uid = sessions.create_user(conn, login_id, PW, name)
            update(conn, "users", uid, slack_user_id=SLACK[login_id])
    with TestClient(app) as c:
        yield c


def login(c, login_id: str) -> dict:
    r = c.post("/api/auth/login", json={"login_id": login_id, "password": PW})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def ok(r, status: int = 200):
    assert r.status_code == status, r.text
    return r.json()


def sent_events() -> list[tuple[str, str]]:
    """쌓인 알림 (이벤트, 받는 곳) 목록."""
    with get_connection() as conn:
        rows = fetch_all(conn, "select event, recipient from notifications where channel = 'slack' order by id")
    return [(r["event"], r["recipient"]) for r in rows]


def test_events_follow_the_run(client):
    kim, lee, choi = (login(client, u) for u in ("kim", "lee", "choi"))
    pid = ok(client.post("/api/projects", json={"name": "회의실 예약"}, headers=kim), 201)["id"]
    for login_id, role in (("lee", "designer"), ("park", "developer"), ("choi", "deployer")):
        ok(client.post(f"/api/projects/{pid}/members", json={"login_id": login_id, "roles": [role]}, headers=kim), 201)

    # A01 초안 두 번 (설명, 답변) → 기획 담당에게 두 번
    ok(client.post(f"/api/projects/{pid}/messages", json={"kind": "description", "body": "회의실 예약"}, headers=kim), 202)
    q = [q for q in ok(client.get(f"/api/projects/{pid}/messages", headers=kim))["questions"] if q["status"] == "open"][0]
    ok(client.post(f"/api/projects/{pid}/messages", json={"kind": "answer", "body": "우리 팀원", "answers": [{"question_id": q["id"]}]}, headers=kim), 202)
    plan = ok(client.get(f"/api/projects/{pid}/planning", headers=kim))
    run_id = ok(client.post(f"/api/projects/{pid}/planning/confirm", json={"version": plan["version"], "hash": plan["hash"]}, headers=kim))["run_id"]

    # 범위 확인 → G2 반려 → G2 승인 → A03 → G4 승인 → 배포 완료
    ok(client.post(f"/api/runs/{run_id}/scope-confirm", headers=lee))
    aid = ok(client.get(f"/api/projects/{pid}/run", headers=lee))["waiting"]["approval_id"]
    g2 = ok(client.get(f"/api/approvals/{aid}", headers=lee))
    ok(client.post(f"/api/approvals/{aid}/reject", json={"target_hash": g2["target_hash"], "comment": "취소 버튼이 없어요."}, headers=lee))
    aid = ok(client.get(f"/api/projects/{pid}/run", headers=lee))["waiting"]["approval_id"]
    g2 = ok(client.get(f"/api/approvals/{aid}", headers=lee))
    ok(client.post(f"/api/approvals/{aid}/approve", json={"target_hash": g2["target_hash"]}, headers=lee))
    aid = ok(client.get(f"/api/projects/{pid}/run", headers=choi))["waiting"]["approval_id"]
    g4 = ok(client.get(f"/api/approvals/{aid}", headers=choi))
    ok(client.post(f"/api/approvals/{aid}/approve", json={"target_hash": g4["target_hash"]}, headers=choi))
    assert ok(client.get(f"/api/projects/{pid}/run", headers=kim))["status"] == "done"

    assert sent_events() == [
        ("a01_done", "U_KIM"), ("a01_done", "U_KIM"),          # 초안 v1, v2
        ("scope_waiting", "U_LEE"),                             # Figma 범위 확인
        ("g2_requested", "U_LEE"),                              # G2
        ("rejected", "U_LEE"),                                  # G2 반려 → A02 다시
        ("g2_requested", "U_LEE"),                              # 다시 만든 G2
        ("a03_done", "U_PARK"),                                 # 개발 완료
        ("g4_requested", "U_PARK"), ("g4_requested", "U_CHOI"),  # G4: 개발·배포 담당
        ("deployed", "U_CHOI"), ("deployed", "C_TEAM"),         # 배포 완료: 배포 담당과 채널
    ]

    # 메시지에는 요약과 링크만: 요구사항 원문(답변 "우리 팀원")은 들어가지 않는다
    with get_connection() as conn:
        texts = [r["payload"]["text"] for r in fetch_all(conn, "select payload from notifications")]
    assert all("우리 팀원" not in t for t in texts)
    assert any("http://localhost:5173/approvals/" in t for t in texts)

    # 보내기: 켜고 가짜 보내기 함수로 모두 보낸다
    config.NOTIFY_ENABLED = True
    got = []
    assert notify.send_pending(sender=lambda to, text: got.append(to)) == 11
    assert len(got) == 11
    assert notify.send_pending(sender=lambda to, text: got.append(to)) == 0  # 이미 보낸 것은 다시 안 보낸다


def test_failure_is_retried_later_and_token_is_hidden(client):
    with get_connection() as conn:
        uid = fetch_all(conn, "select id from users where login_id = 'kim'")[0]["id"]
        pid = insert(conn, "projects", name="p", created_by=uid)
        mid = insert(conn, "project_members", project_id=pid, user_id=uid, invited_by=uid)
        insert(conn, "member_roles", member_id=mid, role="planner", assigned_by=uid)
        assert notify.enqueue(conn, pid, "test", "k1", "안녕", roles=("planner",)) == 1
        assert notify.enqueue(conn, pid, "test", "k1", "안녕", roles=("planner",)) == 0  # 같은 키는 한 번만

    config.NOTIFY_ENABLED = True

    def broken(to, text):
        raise RuntimeError("invalid_auth for xoxb-secret-token")

    assert notify.send_pending(sender=broken) == 0
    with get_connection() as conn:
        [row] = fetch_all(conn, "select * from notifications")
    assert row["status"] == "failed" and row["attempts"] == 1
    assert "xoxb-secret-token" not in row["last_error"]  # 토큰은 DB에 남지 않는다
    assert row["next_try_at"] > row["created_at"]          # 바로 다시 보내지 않고 나중에

    # 아직 다음 시도 시각 전이라 보내지 않는다
    assert notify.send_pending(sender=lambda to, text: None) == 0
    # 시각이 지나면 다시 보내고 sent가 된다
    with get_connection() as conn:
        update(conn, "notifications", row["id"], next_try_at=row["created_at"])
    assert notify.send_pending(sender=lambda to, text: None) == 1


class FakeNotion:
    """integrations.notion 대신 쓰는 가짜. 만든 페이지와 바꾼 내용을 기억한다."""

    page_id_of = staticmethod(lambda value: "parent")
    is_missing = staticmethod(lambda e: "object_not_found" in str(e))

    def __init__(self):
        self.pages = {}   # id → {"parent", "title", "markdown"}
        self.fail_next = None

    def create_page(self, parent_id, title, intro=None, icon=None):
        pid = f"page{len(self.pages) + 1}"
        self.pages[pid] = {"parent": parent_id, "title": title, "markdown": None}
        return {"id": pid, "url": f"https://notion.so/{pid}"}

    def replace_markdown(self, page_id, markdown):
        if self.fail_next:
            error, self.fail_next = self.fail_next, None
            raise RuntimeError(error)
        self.pages[page_id]["markdown"] = markdown

    def titled(self, title):
        return next(p for p in self.pages.values() if p["title"] == title)


def run_to_done(client, kim, lee, choi, pid):
    """설명 → 답변 → G1 확정 → 범위 확인 → G2 승인 → G4 승인 → 배포 완료."""
    ok(client.post(f"/api/projects/{pid}/messages", json={"kind": "description", "body": "회의실 예약"}, headers=kim), 202)
    q = [q for q in ok(client.get(f"/api/projects/{pid}/messages", headers=kim))["questions"] if q["status"] == "open"][0]
    ok(client.post(f"/api/projects/{pid}/messages", json={"kind": "answer", "body": "우리 팀원", "answers": [{"question_id": q["id"]}]}, headers=kim), 202)
    plan = ok(client.get(f"/api/projects/{pid}/planning", headers=kim))
    run_id = ok(client.post(f"/api/projects/{pid}/planning/confirm", json={"version": plan["version"], "hash": plan["hash"]}, headers=kim))["run_id"]
    ok(client.post(f"/api/runs/{run_id}/scope-confirm", headers=lee))
    for headers in (lee, choi):  # G2, G4
        aid = ok(client.get(f"/api/projects/{pid}/run", headers=headers))["waiting"]["approval_id"]
        req = ok(client.get(f"/api/approvals/{aid}", headers=headers))
        ok(client.post(f"/api/approvals/{aid}/approve", json={"target_hash": req["target_hash"]}, headers=headers))
    assert ok(client.get(f"/api/projects/{pid}/run", headers=kim))["status"] == "done"
    return plan["version"]


def test_notion_pages_follow_the_run(client, monkeypatch):
    monkeypatch.setattr(config, "NOTION_TOKEN", "ntn_secret-token")
    monkeypatch.setattr(config, "NOTION_PARENT_PAGE", "https://www.notion.so/Orbit-0123456789abcdef0123456789abcdef")
    kim, lee, choi = (login(client, u) for u in ("kim", "lee", "choi"))
    pid = ok(client.post("/api/projects", json={"name": "회의실 예약"}, headers=kim), 201)["id"]
    for login_id, role in (("lee", "designer"), ("park", "developer"), ("choi", "deployer")):
        ok(client.post(f"/api/projects/{pid}/members", json={"login_id": login_id, "roles": [role]}, headers=kim), 201)
    version = run_to_done(client, kim, lee, choi, pid)

    with get_connection() as conn:
        rows = fetch_all(conn, "select event, recipient from notifications where channel = 'notion' order by id")
    assert [(r["event"], r["recipient"]) for r in rows] == [
        ("a01_done", "planning"), ("a01_done", "planning"),   # 초안 v1, v2
        ("g1_confirmed", "planning"),                         # 확정 v2
        ("a03_done", "tech_spec"), ("checks", "checks"), ("deployed", "deploy"),
    ]

    config.NOTIFY_ENABLED = True
    fake = FakeNotion()
    fake.fail_next = "rate_limited for ntn_secret-token"       # 처음 실제로 게시하는 것(확정 기획서)은 실패한다
    # Slack 9 + Notion 3 (초안 v1·v2는 더 새 확정본이 있어 건너뛰고, 확정본은 실패)
    assert notify.send_pending(sender=lambda to, text: None, notion=fake) == 12

    with get_connection() as conn:
        rows = fetch_all(conn, "select event, status, last_error from notifications where channel = 'notion' order by id")
        url = lambda: fetch_all(conn, "select external_url from artifact_versions where kind = 'planning' order by version desc limit 1")[0]["external_url"]
        assert [r["status"] for r in rows] == ["sent", "sent", "failed", "sent", "sent", "sent"]
        assert rows[0]["last_error"] == "더 새 내용이 있어 건너뜀"
        assert "ntn_secret-token" not in rows[2]["last_error"]   # 토큰은 기록에 남지 않는다
        assert url() is None

    # 프로젝트 페이지 하나 아래에 문서 페이지 네 개
    assert fake.titled("회의실 예약")["parent"] == "parent"
    assert sorted(p["title"] for p in fake.pages.values() if p["parent"] == "page1") == ["검사 결과", "기술 명세", "기획서와 완료 조건", "배포 기록"]
    assert "통과" in fake.titled("배포 기록")["markdown"]
    assert fake.titled("기획서와 완료 조건")["markdown"] is None

    # 시각이 지나면 다시 게시한다. 이미 만든 페이지에 쓰므로 페이지가 늘지 않는다
    with get_connection() as conn:
        update(conn, "notifications", fetch_all(conn, "select id from notifications where event = 'g1_confirmed'")[0]["id"], next_try_at="2000-01-01")
    assert notify.send_pending(sender=lambda to, text: None, notion=fake) == 1
    assert len(fake.pages) == 5
    assert f"확정 v{version}" in fake.titled("기획서와 완료 조건")["markdown"]
    with get_connection() as conn:
        assert url() == "https://notion.so/page2"   # 기획서 화면의 [Notion 페이지] 링크
