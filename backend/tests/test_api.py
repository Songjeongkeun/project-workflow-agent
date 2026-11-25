"""API 전체 흐름 테스트 (임시 에이전트 사용). 07 2장의 흐름을 그대로 따라간다.

    pytest -q
"""

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import config
from agents import AGENTS, stubs
from db import fetch_one, get_connection, init_db, update
from main import app
from services import approvals, controller, sessions
from workspace_server.record import record

PW = "test-pw"


@pytest.fixture
def client(tmp_path, monkeypatch):
    # 테스트마다 빈 DB와 빈 data 폴더를 쓴다
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "orbit.db")
    monkeypatch.setattr(config, "NOTIFY_ENABLED", False)  # .env에서 켜 두었어도 테스트는 Slack에 보내지 않는다
    monkeypatch.setattr(config, "AGENT_INLINE", True)  # 에이전트를 요청 안에서 바로 실행
    # 서버 .env의 주소 값과 상관없이 같은 결과가 나오게 고정한다
    monkeypatch.setattr(config, "DEPLOY_HOST", "localhost")
    monkeypatch.setattr(config, "APP_URL", "http://localhost:5173")
    init_db()
    with get_connection() as conn:
        for login_id, name in (("kim", "김기획"), ("lee", "이디자인"), ("park", "박개발"), ("choi", "최배포"), ("out", "외부인")):
            sessions.create_user(conn, login_id, PW, name)
    with TestClient(app) as c:
        yield c


def login(c, login_id: str) -> dict:
    r = c.post("/api/auth/login", json={"login_id": login_id, "password": PW})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def ok(r, status: int = 200):
    assert r.status_code == status, r.text
    return r.json()


def err(r, status: int, text: str | None = None):
    """실패 응답 확인. text를 주면 detail 메시지에 그 글자가 들어 있는지도 본다."""
    assert r.status_code == status, r.text
    detail = r.json()["detail"]
    if text:
        assert text in detail, detail
    return detail


def test_auth_and_errors(client):
    assert ok(client.get("/api/health")) == {"status": "ok"}
    err(client.get("/api/me"), 401, "로그인이 필요")
    err(client.post("/api/auth/login", json={"login_id": "kim", "password": "x"}), 401, "아이디 또는 비밀번호")
    h = login(client, "kim")
    assert ok(client.get("/api/me", headers=h))["name"] == "김기획"
    ok(client.post("/api/auth/logout", headers=h))
    err(client.get("/api/me", headers=h), 401, "만료")
    err(client.get("/api/nope", headers=h), 404)


def test_full_flow(client):
    kim, lee, park, choi, out = (login(client, u) for u in ("kim", "lee", "park", "choi", "out"))

    # 프로젝트와 멤버
    pid = ok(client.post("/api/projects", json={"name": "회의실 예약"}, headers=kim), 201)["id"]
    err(client.get(f"/api/projects/{pid}", headers=out), 403, "멤버가 아닙니다")
    assert ok(client.get(f"/api/projects/{pid}/members", headers=kim))["gates_without_owner"] == ["G2", "G3", "G4"]
    for login_id, role in (("lee", "designer"), ("park", "developer"), ("choi", "deployer")):
        ok(client.post(f"/api/projects/{pid}/members", json={"login_id": login_id, "roles": [role]}, headers=kim), 201)
    err(client.post(f"/api/projects/{pid}/members", json={"login_id": "out", "roles": []}, headers=lee), 403, "만든 사람만")
    assert ok(client.get(f"/api/projects/{pid}/members", headers=lee))["gates_without_owner"] == []

    # 설명 → A01 → 질문
    err(client.get(f"/api/projects/{pid}/planning", headers=kim), 404, "초안이 없습니다")
    ok(client.post(f"/api/projects/{pid}/messages", json={"kind": "description", "body": "팀원이 회의실을 예약하게 해 줘."}, headers=kim), 202)
    conv = ok(client.get(f"/api/projects/{pid}/messages", headers=kim))
    assert conv["processing"] is False and conv["summary"]["features"] == ["팀원이 회의실을 예약하게 해 줘."]
    [q] = [q for q in conv["questions"] if q["status"] == "open"]
    plan = ok(client.get(f"/api/projects/{pid}/planning", headers=kim))
    assert plan["can_confirm"] is False and "필수 질문이 1개" in plan["blocked_reason"]
    assert ok(client.get(f"/api/projects/{pid}/run", headers=kim))["status"] == "waiting_input"

    # 답변 → 새 초안 → 확정
    ok(client.post(f"/api/projects/{pid}/messages", json={"kind": "answer", "body": "우리 팀원", "answers": [{"question_id": q["id"]}]}, headers=kim), 202)
    plan = ok(client.get(f"/api/projects/{pid}/planning", headers=kim))
    assert plan["version"] == 2 and plan["can_confirm"] is True
    assert ok(client.get(f"/api/projects/{pid}/planning/evidence?field_path=release_scope.features[0]", headers=kim))["evidence"]
    err(client.post(f"/api/projects/{pid}/planning/confirm", json={"version": 2, "hash": plan["hash"]}, headers=lee), 403, "기획 담당만")
    err(client.post(f"/api/projects/{pid}/planning/confirm", json={"version": 1, "hash": plan["hash"]}, headers=kim), 409, "바뀌었습니다")
    run_id = ok(client.post(f"/api/projects/{pid}/planning/confirm", json={"version": 2, "hash": plan["hash"]}, headers=kim))["run_id"]
    err(client.post(f"/api/projects/{pid}/messages", json={"kind": "revision", "body": "고쳐 줘"}, headers=kim), 409, "변경 요청")

    # A02 범위 확인 → G2 반려 → G2 승인
    st = ok(client.get(f"/api/projects/{pid}/run", headers=lee))
    assert st["status"] == "waiting_input" and st["input"]["type"] == "scope_confirm" and st["input"]["can_confirm"]
    err(client.post(f"/api/runs/{run_id}/scope-confirm", headers=kim), 403)
    ok(client.post(f"/api/runs/{run_id}/scope-confirm", headers=lee))
    st = ok(client.get(f"/api/projects/{pid}/run", headers=lee))
    assert st["status"] == "waiting_approval" and st["waiting"]["gate"] == "G2" and st["waiting"]["can_decide"]
    inbox = ok(client.get("/api/approvals", headers=lee))["items"]
    assert [i["gate"] for i in inbox] == ["G2"]
    aid = inbox[0]["id"]
    g2 = ok(client.get(f"/api/approvals/{aid}", headers=lee))
    assert g2["g2"]["screens"] and g2["can_decide"]
    err(client.post(f"/api/approvals/{aid}/reject", json={"target_hash": g2["target_hash"], "comment": " "}, headers=lee), 400, "의견")
    err(client.post(f"/api/approvals/{aid}/approve", json={"target_hash": g2["target_hash"]}, headers=park), 403)
    err(client.post(f"/api/approvals/{aid}/approve", json={"target_hash": "old"}, headers=lee), 409, "바뀌었습니다")
    ok(client.post(f"/api/approvals/{aid}/reject", json={"target_hash": g2["target_hash"], "comment": "취소 버튼이 없어요."}, headers=lee))
    st = ok(client.get(f"/api/projects/{pid}/run", headers=lee))
    assert st["waiting"]["gate"] == "G2" and st["waiting"]["approval_id"] != aid  # A02가 다시 만들었다
    g2b = ok(client.get(f"/api/approvals/{st['waiting']['approval_id']}", headers=lee))
    ok(client.post(f"/api/approvals/{g2b['id']}/approve", json={"target_hash": g2b["target_hash"]}, headers=lee))

    # A03 → (G3 건너뜀) → A04 → G4 → A05 → 배포 후 검사 → 완료
    st = ok(client.get(f"/api/projects/{pid}/run", headers=choi))
    assert st["waiting"]["gate"] == "G4"
    g4 = ok(client.get(f"/api/approvals/{st['waiting']['approval_id']}", headers=choi))
    assert g4["g4"]["sha"] == g4["target_hash"] and all(r["result"] == "pass" for r in g4["g4"]["results"])
    ok(client.post(f"/api/approvals/{g4['id']}/approve", json={"target_hash": g4["target_hash"]}, headers=choi))
    st = ok(client.get(f"/api/projects/{pid}/run", headers=kim))
    assert st["status"] == "done" and st["deployment_url"] and [s["status"] for s in st["steps"]] == ["completed"] * 5

    detail = ok(client.get(f"/api/runs/{run_id}", headers=kim))
    assert detail["trace"]["sha"] == g4["target_hash"] and detail["trace"]["planning_version"] == 2
    assert [a["gate"] + a["decision"] for a in detail["approvals"]] == ["G1approve", "G2reject", "G2approve", "G4approve"]
    assert {t["phase"] for t in detail["test_results"]} == {"pre", "post"}
    assert ok(client.get("/api/approvals?view=done", headers=lee))["items"][0]["decision"] == "approve"

    # 재배포: 같은 커밋으로 새 실행, G4부터
    runs = ok(client.get(f"/api/projects/{pid}/runs", headers=choi))
    assert runs["runs"][0]["can_redeploy"] is True
    err(client.post(f"/api/runs/{run_id}/redeploy", headers=kim), 403)
    re = ok(client.post(f"/api/runs/{run_id}/redeploy", headers=choi), 201)
    err(client.post(f"/api/runs/{run_id}/redeploy", headers=choi), 409, "진행 중인 실행")
    g4r = ok(client.get(f"/api/approvals/{re['approval_id']}", headers=choi))
    assert g4r["target_hash"] == g4["target_hash"]
    ok(client.post(f"/api/approvals/{g4r['id']}/approve", json={"target_hash": g4r["target_hash"]}, headers=choi))
    assert ok(client.get(f"/api/projects/{pid}/run", headers=kim))["status"] == "done"

    # 변경 요청: 새 초안 → 영향 분석 → G1 재승인 → 영향받은 단계만 다시 실행
    cid = ok(client.post(f"/api/projects/{pid}/change-requests", json={"text": "예약은 최대 2시간까지만 되게 해 줘."}, headers=kim), 201)["id"]
    assert ok(client.get(f"/api/projects/{pid}/change-requests", headers=kim))["change_requests"][0]["by"] == "김기획"
    cr = ok(client.get(f"/api/change-requests/{cid}", headers=kim))
    assert cr["status"] == "awaiting_approval" and sorted(cr["changed"]["added"]) == ["AC-002-01", "AC-002-02", "FEAT-002"]
    assert cr["reapproval"][0] == {"gate": "G1", "role": "planner", "status": "pending", "approval_id": cr["reapproval"][0]["approval_id"]}
    plan = ok(client.get(f"/api/projects/{pid}/planning", headers=kim))
    assert plan["version"] == 3 and plan["status"] == "draft"
    new_run = ok(client.post(f"/api/projects/{pid}/planning/confirm", json={"version": 3, "hash": plan["hash"]}, headers=kim))["run_id"]
    assert ok(client.get(f"/api/change-requests/{cid}", headers=kim))["status"] == "rerunning"
    assert ok(client.get(f"/api/runs/{new_run}", headers=kim))["trace"]["change_request_id"] == cid


def test_g1_reject_from_inbox_makes_new_draft(client):
    kim = login(client, "kim")
    pid = ok(client.post("/api/projects", json={"name": "p"}, headers=kim), 201)["id"]
    ok(client.post(f"/api/projects/{pid}/messages", json={"kind": "description", "body": "할 일 목록을 만들어 줘."}, headers=kim), 202)
    [g1] = ok(client.get("/api/approvals", headers=kim))["items"]
    assert g1["gate"] == "G1"
    d = ok(client.get(f"/api/approvals/{g1['id']}", headers=kim))
    err(client.post(f"/api/approvals/{g1['id']}/approve", json={"target_hash": d["target_hash"]}, headers=kim), 400, "기획서 검토 화면")
    ok(client.post(f"/api/approvals/{g1['id']}/reject", json={"target_hash": d["target_hash"], "comment": "마감일도 넣어 줘."}, headers=kim))
    plan = ok(client.get(f"/api/projects/{pid}/planning", headers=kim))
    assert plan["version"] == 2 and len(plan["features"]) == 2


def failing_test(ctx, phase, sha, url=None):
    """배포 전 검사에서 정상 조건이 늘 실패하는 A04."""
    results = [{"ac_id": c["id"], "result": "fail" if c["kind"] == "정상" else "pass"} for c in ctx["criteria"]]
    return {"sha": sha, "results": results, "summary": "실패"}


def test_fix_loop_limit_and_retry(client, monkeypatch):
    monkeypatch.setattr(config, "FIX_RETRY_LIMIT", 2)
    monkeypatch.setitem(AGENTS, "test", failing_test)

    kim, park = login(client, "kim"), login(client, "park")
    pid = ok(client.post("/api/projects", json={"name": "p"}, headers=kim), 201)["id"]
    ok(client.post(f"/api/projects/{pid}/members", json={"login_id": "park", "roles": ["developer"]}, headers=kim), 201)
    ok(client.post(f"/api/projects/{pid}/messages", json={"kind": "description", "body": "메모장"}, headers=kim), 202)
    q = next(q for q in ok(client.get(f"/api/projects/{pid}/messages", headers=kim))["questions"] if q["status"] == "open")
    ok(client.post(f"/api/projects/{pid}/messages", json={"kind": "answer", "body": "나", "answers": [{"question_id": q["id"]}]}, headers=kim), 202)
    plan = ok(client.get(f"/api/projects/{pid}/planning", headers=kim))
    with get_connection() as conn:  # 화면 없는 프로젝트로 만들어 A02를 건너뛴다
        update(conn, "projects", pid, has_ui=0)

    rid = ok(client.post(f"/api/projects/{pid}/planning/confirm", json={"version": plan["version"], "hash": plan["hash"]}, headers=kim))["run_id"]
    st = ok(client.get(f"/api/projects/{pid}/run", headers=park))
    assert st["status"] == "failed" and st["retry"] == {"count": 2, "limit": 2}
    assert st["failure"]["step"] == "A04" and st["failure"]["can_retry"] is True
    assert [s["status"] for s in st["steps"]][1] == "skipped"

    err(client.post(f"/api/runs/{rid}/retry", headers=kim), 403, "개발 담당만")
    monkeypatch.setitem(AGENTS, "test", stubs.test)  # 고쳐졌다
    ok(client.post(f"/api/runs/{rid}/retry", headers=park))
    st = ok(client.get(f"/api/projects/{pid}/run", headers=park))
    assert st["status"] == "waiting_approval" and st["waiting"]["gate"] == "G4"


# ── 겹친 요청 (승인 두 번, 버튼 연타) ─────────────────────────


def _failed_run(client, monkeypatch) -> tuple[int, int, dict, dict]:
    """기획 확정까지 하고, A04가 늘 실패해 멈춘 실행을 만든다. (pid, run_id, kim, park)"""
    monkeypatch.setattr(config, "FIX_RETRY_LIMIT", 1)
    monkeypatch.setitem(AGENTS, "test", failing_test)
    kim, park = login(client, "kim"), login(client, "park")
    pid = ok(client.post("/api/projects", json={"name": "p"}, headers=kim), 201)["id"]
    ok(client.post(f"/api/projects/{pid}/members", json={"login_id": "park", "roles": ["developer"]}, headers=kim), 201)
    ok(client.post(f"/api/projects/{pid}/messages", json={"kind": "description", "body": "메모장"}, headers=kim), 202)
    q = next(q for q in ok(client.get(f"/api/projects/{pid}/messages", headers=kim))["questions"] if q["status"] == "open")
    ok(client.post(f"/api/projects/{pid}/messages", json={"kind": "answer", "body": "나", "answers": [{"question_id": q["id"]}]}, headers=kim), 202)
    plan = ok(client.get(f"/api/projects/{pid}/planning", headers=kim))
    with get_connection() as conn:
        update(conn, "projects", pid, has_ui=0)
    rid = ok(client.post(f"/api/projects/{pid}/planning/confirm", json={"version": plan["version"], "hash": plan["hash"]}, headers=kim))["run_id"]
    assert ok(client.get(f"/api/projects/{pid}/run", headers=park))["status"] == "failed"
    return pid, rid, kim, park


def _user_id(login_id: str) -> int:
    with get_connection() as conn:
        return fetch_one(conn, "select id from users where login_id = ?", (login_id,))["id"]


def test_retry_and_decide_only_once_with_stale_read(client, monkeypatch):
    """두 요청이 같은 상태를 읽고 동시에 바꾸려 해도 한 번만 반영된다."""
    pid, rid, kim, park = _failed_run(client, monkeypatch)

    # 다시 시도: 두 요청 모두 'failed'를 읽은 상태 (라우터를 거치지 않으니 에이전트는 돌지 않는다)
    with get_connection() as conn:
        stale = controller.get_run(conn, rid)
    with get_connection() as conn:
        controller.retry(conn, dict(stale), _user_id("park"))
    with pytest.raises(HTTPException) as e, get_connection() as conn:
        controller.retry(conn, dict(stale), _user_id("park"))
    assert e.value.status_code == 409

    # 승인: 검사가 고쳐져 G4까지 간 뒤, 두 요청 모두 'pending'을 읽은 상태
    monkeypatch.setitem(AGENTS, "test", stubs.test)
    controller.advance(rid)
    with get_connection() as conn:
        req = fetch_one(conn, "select * from approval_requests where run_id = ? and gate = 'G4' and status = 'pending'", (rid,))
        stale_req = dict(req)
    ok(client.post(f"/api/projects/{pid}/members", json={"login_id": "choi", "roles": ["deployer"]}, headers=kim), 201)
    with get_connection() as conn:
        approvals.decide(conn, dict(stale_req), _user_id("choi"), "approve", stale_req["target_hash"], None)
    with pytest.raises(HTTPException) as e, get_connection() as conn:
        approvals.decide(conn, dict(stale_req), _user_id("choi"), "approve", stale_req["target_hash"], None)
    assert e.value.status_code == 409
    with get_connection() as conn:
        assert fetch_one(conn, "select count(*) as n from approvals where request_id = ?", (stale_req["id"],))["n"] == 1


def test_change_request_closed_when_a01_fails(client, monkeypatch):
    """확정 뒤 변경 요청 분석(A01)이 실패하면 변경 요청을 닫아, 다시 등록할 수 있게 한다."""
    pid, rid, kim, park = _failed_run(client, monkeypatch)

    def broken(inp):
        raise RuntimeError("LLM 응답 없음")

    monkeypatch.setitem(AGENTS, "plan", broken)
    cid = ok(client.post(f"/api/projects/{pid}/change-requests", json={"text": "제목 길이를 50자로"}, headers=kim), 201)["id"]
    assert ok(client.get(f"/api/change-requests/{cid}", headers=kim))["status"] == "rejected"
    last = ok(client.get(f"/api/projects/{pid}/messages", headers=kim))["messages"][-1]
    assert "다시 등록" in last["body"]

    monkeypatch.setitem(AGENTS, "plan", stubs.plan)  # 고쳐졌다 → 다시 등록하면 분석된다
    cid2 = ok(client.post(f"/api/projects/{pid}/change-requests", json={"text": "제목 길이를 50자로"}, headers=kim), 201)["id"]
    assert ok(client.get(f"/api/change-requests/{cid2}", headers=kim))["status"] == "awaiting_approval"


# ── 배포 대상과 A05 결과 확인 ─────────────────────────────


def _to_g4(client, monkeypatch) -> tuple[int, int, dict, dict]:
    """화면 없는 프로젝트를 G4 승인 대기까지 진행한다. (pid, run_id, kim, choi)"""
    monkeypatch.setattr(config, "DEPLOY_REPOSITORY", "team/demo")
    monkeypatch.setattr(config, "ALLOWED_REPOSITORIES", ["team/demo"])
    kim, choi = login(client, "kim"), login(client, "choi")
    pid = ok(client.post("/api/projects", json={"name": "p"}, headers=kim), 201)["id"]
    ok(client.post(f"/api/projects/{pid}/members", json={"login_id": "choi", "roles": ["deployer"]}, headers=kim), 201)
    ok(client.post(f"/api/projects/{pid}/messages", json={"kind": "description", "body": "메모장"}, headers=kim), 202)
    q = next(q for q in ok(client.get(f"/api/projects/{pid}/messages", headers=kim))["questions"] if q["status"] == "open")
    ok(client.post(f"/api/projects/{pid}/messages", json={"kind": "answer", "body": "나", "answers": [{"question_id": q["id"]}]}, headers=kim), 202)
    plan = ok(client.get(f"/api/projects/{pid}/planning", headers=kim))
    with get_connection() as conn:
        update(conn, "projects", pid, has_ui=0)
    rid = ok(client.post(f"/api/projects/{pid}/planning/confirm", json={"version": plan["version"], "hash": plan["hash"]}, headers=kim))["run_id"]
    assert ok(client.get(f"/api/projects/{pid}/run", headers=kim))["waiting"]["gate"] == "G4"
    return pid, rid, kim, choi


def _approve_g4(client, rid: int, who: dict) -> dict:
    with get_connection() as conn:
        req = fetch_one(conn, "select * from approval_requests where run_id = ? and gate = 'G4' and status = 'pending'", (rid,))
    detail = ok(client.get(f"/api/approvals/{req['id']}", headers=who))
    ok(client.post(f"/api/approvals/{req['id']}/approve", json={"target_hash": req["target_hash"]}, headers=who))
    return detail


def test_g4_shows_real_target_and_a05_is_checked(client, monkeypatch):
    pid, rid, kim, choi = _to_g4(client, monkeypatch)

    # G4에 실제 배포 대상(설정의 저장소, 프로젝트별 브랜치, 주소)이 보인다
    seen = {}

    def bad_url(ctx, sha):
        seen.update(ctx)
        return {"pushed_sha": sha, "deployment_id": "d1", "deployment_url": "http://evil:1", "summary": "배포"}

    monkeypatch.setitem(AGENTS, "deploy", bad_url)
    detail = _approve_g4(client, rid, choi)
    target = {"repository": "team/demo", "branch": f"release/p{pid}", "environment": "test", "url": f"http://localhost:{9000 + pid}"}
    assert detail["g4"]["target"] == target and detail["deploy_target"] == target
    assert seen["target"] == target and seen["redeploy"] is False

    # 승인한 주소와 다른 곳에 배포했다고 하면 실패
    st = ok(client.get(f"/api/projects/{pid}/run", headers=kim))
    assert st["status"] == "failed" and "배포 주소" in st["failure"]["reason"]


def test_a05_needs_push_record_when_workspace_is_used(client, monkeypatch):
    pid, rid, kim, choi = _to_g4(client, monkeypatch)
    monkeypatch.setattr(config, "USE_WORKSPACE", True)  # 실제 에이전트 모드: git_push 기록을 확인한다
    _approve_g4(client, rid, choi)                       # 임시 A05는 push하지 않는다
    st = ok(client.get(f"/api/projects/{pid}/run", headers=kim))
    assert st["status"] == "failed" and "git_push" in st["failure"]["reason"]


def test_g4_cannot_be_approved_without_valid_repository(client, monkeypatch):
    pid, rid, kim, choi = _to_g4(client, monkeypatch)
    monkeypatch.setattr(config, "USE_WORKSPACE", True)  # 실제 에이전트 모드에서만 push를 하므로 검사한다
    # 저장소 설정이 빈 서버에서 만든 G4 요청
    with get_connection() as conn:
        conn.execute(
            "update approval_requests set deploy_target = json_set(deploy_target, '$.repository', '') "
            "where run_id = ? and gate = 'G4'", (rid,),
        )
        req = fetch_one(conn, "select * from approval_requests where run_id = ? and gate = 'G4' and status = 'pending'", (rid,))

    detail = ok(client.get(f"/api/approvals/{req['id']}", headers=choi))
    assert "저장소" in detail["g4"]["target_problem"]   # 화면이 승인 버튼을 막고 이유를 보여 준다

    r = client.post(f"/api/approvals/{req['id']}/approve", json={"target_hash": req["target_hash"]}, headers=choi)
    assert r.status_code == 409 and "배포 대상" in r.json()["detail"]
    # 승인 요청은 그대로 대기 중이다 (설정을 고친 뒤 반려하고 다시 진행할 수 있다)
    with get_connection() as conn:
        assert fetch_one(conn, "select status from approval_requests where id = ?", (req["id"],))["status"] == "pending"


def test_redeploy_tells_a05_to_skip_push(client, monkeypatch):
    pid, rid, kim, choi = _to_g4(client, monkeypatch)
    _approve_g4(client, rid, choi)
    assert ok(client.get(f"/api/projects/{pid}/run", headers=kim))["status"] == "done"

    seen = {}

    def deploy(ctx, sha):
        """push 없이 deploy_local만 부른 A05 (workspace 서버가 남기는 기록을 흉내 낸다)."""
        seen.update(ctx)
        record(ctx["run_id"], "A05", "deploy_local", sha, True)
        return stubs.deploy(ctx, sha)

    monkeypatch.setitem(AGENTS, "deploy", deploy)
    monkeypatch.setattr(config, "USE_WORKSPACE", True)  # 재배포는 push 기록이 없어도 된다 (deploy_local 기록은 필요)
    new_rid = ok(client.post(f"/api/runs/{rid}/redeploy", headers=choi), 201)["run_id"]
    _approve_g4(client, new_rid, choi)
    assert seen["redeploy"] is True
    st = ok(client.get(f"/api/projects/{pid}/run", headers=kim))
    assert st["status"] == "done", st.get("failure")
