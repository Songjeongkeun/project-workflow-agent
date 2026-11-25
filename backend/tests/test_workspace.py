"""workspace 서버 테스트.

guards.py의 검사 함수는 DB 없이 입력 → 결과만 본다.
마지막 테스트는 실제 DB와 작업 공간 폴더로 도구를 불러 본다 (MCP 연결 없이 함수를 바로 부른다).
실행: backend 폴더에서 pytest -q
"""

import pytest

import config
import db
from workspace_server import guards, sandbox


def test_safe_path_blocks_escape(tmp_path, monkeypatch):
    # 테스트 동안만 작업 공간 위치를 임시 폴더로 바꾼다 (진짜 data 폴더를 건드리지 않는다)
    monkeypatch.setattr(config, "WORKSPACE_ROOT", tmp_path)

    # 작업 공간 안의 경로는 그 절대 경로가 돌아온다
    assert guards.safe_path(3, "src/app.py") == (tmp_path / "p3" / "src" / "app.py").resolve()

    with pytest.raises(guards.Denied):
        guards.safe_path(3, "../p4/secret.txt")   # 다른 프로젝트 폴더
    with pytest.raises(guards.Denied):
        guards.safe_path(3, ".git/config")        # git 내부 파일


def test_command_allowlist():
    assert guards.check_command("make test") == ["make", "test"]
    assert guards.check_command("pip install -r requirements.txt") == ["pip", "install", "-r", "requirements.txt"]

    # 뒤에 다른 명령을 붙이면 단어 리스트가 달라져서 막힌다
    with pytest.raises(guards.Denied):
        guards.check_command("make test; rm -rf ~")


def test_commit_message_needs_ids():
    guards.check_commit_message(12, "run-12 FEAT-001 예약 화면")   # 예외가 없으면 통과

    with pytest.raises(guards.Denied):
        guards.check_commit_message(12, "예약 화면 추가")          # ID 없음
    with pytest.raises(guards.Denied):
        guards.check_commit_message(12, "run-13 FEAT-001 예약 화면")  # 다른 실행 번호
    with pytest.raises(guards.Denied):
        guards.check_commit_message(12, "run-12 F-001 예약 화면")     # 예전 형식은 기획서 ID가 아니다


@pytest.fixture
def a03_run(tmp_path, monkeypatch):
    """임시 DB에 A03 단계에서 진행 중인 실행 하나를 만든다. 실행 id를 돌려준다."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "orbit.db")
    monkeypatch.setattr(config, "WORKSPACE_ROOT", tmp_path / "workspaces")
    db.init_db()
    with db.get_connection() as conn:
        user_id = db.insert(conn, "users", login_id="t", name="테스트", password_hash="x")
        pid = db.insert(conn, "projects", name="시험", created_by=user_id)
        plan_id = db.insert(
            conn, "run_plans", project_id=pid, version=1, steps=["A03"], input_versions={},
            gates={}, target={"repository": "team/test", "branch": "release"}, limits={},
        )
        return db.insert(conn, "runs", project_id=pid, plan_id=plan_id, status="running", current_step="A03")


def test_tools_end_to_end(a03_run):
    from workspace_server import server

    run_id = a03_run

    # 쓰고, 목록에 보이고, 읽힌다
    assert server.write_file(run_id, "A03", "src/hello.txt", "안녕")["ok"]
    assert server.list_files(run_id, "A03")["files"] == ["src/hello.txt"]   # Windows에서도 / 로 나온다
    assert server.read_file(run_id, "A03", "src/hello.txt")["content"] == "안녕"
    assert server.write_file(run_id, "A03", "hello.txt", "안녕")["ok"]
    assert server.read_file(run_id, "A03", "hello.txt")["content"] == "안녕"

    # 작업 공간 밖, 허용 목록 밖 명령, 차례가 아닌 에이전트는 막힌다
    assert server.read_file(run_id, "A03", "../../orbit.db")["denied"]
    assert server.run_command(run_id, "A03", "make test; rm -rf ~")["denied"]
    out = server.git_push(run_id, "A05", "abc")
    assert out["denied"] and "차례가 아닙니다" in out["reason"]

    # 커밋 메시지 검사와 실제 커밋
    assert server.git_commit(run_id, "A03", "첫 커밋")["denied"]
    commit = server.git_commit(run_id, "A03", f"run-{run_id} FEAT-001 첫 커밋")
    assert commit["ok"] and len(commit["sha"]) == 40

    # 모든 호출이 tool_calls에 남는다 (허용 6건: 쓰기 2·목록·읽기 2·커밋, 거부 4건)
    with db.get_connection() as conn:
        rows = db.fetch_all(conn, "select allowed from tool_calls where run_id = ?", (run_id,))
    assert len(rows) == 10
    assert sum(1 for r in rows if not r["allowed"]) == 4


def test_push_only_to_allowed_target(a03_run, monkeypatch):
    """git_push 검사: 승인한 커밋이어도 허용 목록 밖 저장소, main 브랜치, 재배포는 막는다."""
    from services import approvals

    monkeypatch.setattr(config, "ALLOWED_REPOSITORIES", ["team/demo"])
    sha = "a" * 40
    with db.get_connection() as conn:
        db.update(conn, "runs", a03_run, current_step="A05", tested_sha=sha)
        run = db.fetch_one(conn, "select * from runs where id = ?", (a03_run,))

    def approve(target: dict) -> dict:
        with db.get_connection() as conn:
            req = approvals.create_request(conn, run["project_id"], "G4", sha, run_id=a03_run, deploy_target=target)
            db.update(conn, "approval_requests", req["id"], status="approved")
            return db.fetch_one(conn, "select * from runs where id = ?", (a03_run,))

    ok_target = {"repository": "team/demo", "branch": "release/p1", "environment": "test"}
    r = approve(ok_target)
    with db.get_connection() as conn:
        assert guards.check_push(conn, r, sha) == ok_target

    for bad, why in (
        ({**ok_target, "repository": "someone/else"}, "허용 목록"),
        ({**ok_target, "repository": ""}, "설정되지 않았습니다"),
        ({**ok_target, "branch": "main"}, "브랜치"),
        ({**ok_target, "branch": "release/../main"}, "브랜치"),
    ):
        r = approve(bad)
        with pytest.raises(guards.Denied, match=why), db.get_connection() as conn:
            guards.check_push(conn, r, sha)

    r = approve(ok_target)
    with pytest.raises(guards.Denied, match="재배포"), db.get_connection() as conn:
        guards.check_push(conn, {**r, "redeploy_of_run_id": 1}, sha)


def test_deploy_local_runs_approved_commit(a03_run, tmp_path, monkeypatch):
    """deploy_local: 승인한 커밋만 꺼내서 docker compose로 띄운다. 다시 불러도(재배포) 새로 꺼낸다."""
    from services import approvals
    from workspace_server import server

    monkeypatch.setattr(config, "DEPLOY_ROOT", tmp_path / "deployments")
    calls = []
    monkeypatch.setattr(server, "_compose_up", lambda folder, project, env: calls.append((folder, project, env)))

    # A03이 docker-compose.yml이 있는 커밋을 만든다
    run_id = a03_run
    server.write_file(run_id, "A03", "docker-compose.yml", "services: {}\n")
    sha1 = server.git_commit(run_id, "A03", f"run-{run_id} FEAT-001 첫 버전")["sha"]
    server.write_file(run_id, "A03", "app.txt", "v2")
    sha2 = server.git_commit(run_id, "A03", f"run-{run_id} FEAT-001 둘째 버전")["sha"]

    with db.get_connection() as conn:
        db.update(conn, "runs", run_id, current_step="A05", tested_sha=sha1)
        pid = db.fetch_one(conn, "select project_id from runs where id = ?", (run_id,))["project_id"]

    # 승인 전에는 막힌다
    assert server.deploy_local(run_id, "A05", sha1)["denied"]

    with db.get_connection() as conn:
        req = approvals.create_request(conn, pid, "G4", sha1, run_id=run_id, deploy_target={})
        db.update(conn, "approval_requests", req["id"], status="approved")

    # 승인하지 않은 커밋, A05가 아닌 에이전트는 막힌다
    assert server.deploy_local(run_id, "A05", sha2)["denied"]
    assert server.deploy_local(run_id, "A03", sha1)["denied"]

    out = server.deploy_local(run_id, "A05", sha1)
    assert out["ok"], out
    assert out["deployment_url"] == f"http://{config.DEPLOY_HOST}:{config.DEPLOY_PORT_BASE + pid}"
    folder, project, env = calls[-1]
    assert project == f"orbit-test-p{pid}" and env == {"ORBIT_PORT": str(config.DEPLOY_PORT_BASE + pid), "GIT_SHA": sha1}
    assert (folder / "docker-compose.yml").exists() and not (folder / "app.txt").exists()  # sha1 시점의 파일만

    # 같은 프로젝트를 다시 띄워도 된다 (이전에 꺼낸 폴더를 지우고 다시 꺼낸다)
    assert server.deploy_local(run_id, "A05", sha1)["ok"]
    assert len(calls) == 2

    # docker-compose.yml이 없는 커밋이면 실행 오류 (거부가 아니라 실패: 실행 계약을 어긴 것)
    from workspace_server import gitops
    root = guards.workspace_dir(pid)
    gitops.git(root, "rm", "-q", "docker-compose.yml")
    gitops.git(root, "commit", "-q", "-m", f"run-{run_id} FEAT-001 compose 삭제")
    sha3 = gitops.git(root, "rev-parse", "HEAD")
    with db.get_connection() as conn:
        db.update(conn, "runs", run_id, tested_sha=sha3)
        req = approvals.create_request(conn, pid, "G4", sha3, run_id=run_id, deploy_target={})
        db.update(conn, "approval_requests", req["id"], status="approved")
    out = server.deploy_local(run_id, "A05", sha3)
    assert not out["ok"] and not out["denied"] and "docker-compose.yml" in out["reason"]
    assert len(calls) == 2  # compose는 부르지 않았다


# ---- 컨테이너 격리 (sandbox.py). 실제 docker 없이 인자와 검사 결과만 본다 ----

def test_sandbox_isolates_command(tmp_path):
    test = sandbox.docker_args(tmp_path, 3, ["make", "test"], "c1")
    install = sandbox.docker_args(tmp_path, 3, ["npm", "install"], "c2")

    assert test[test.index("--network") + 1] == "none"          # 테스트는 네트워크 없이
    assert install[install.index("--network") + 1] == "bridge"  # 설치만 네트워크 허용
    assert f"{tmp_path}:/workspace" in test                     # 이 프로젝트 폴더만
    assert sum(a == "-v" for a in test) == 1                    # 다른 폴더는 안 붙임
    assert "--cap-drop" in test and "--privileged" not in test
    assert not any("TOKEN" in a for a in test)                  # 토큰 같은 환경 변수가 없음
    assert test[-2:] == ["make", "test"]


def test_sandbox_kills_container_on_timeout(tmp_path, monkeypatch):
    """시간을 넘기면 subprocess만 끊지 않고 컨테이너도 멈춘다."""
    import subprocess

    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        if args[:2] == ["docker", "run"]:
            raise subprocess.TimeoutExpired(args, 1)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(sandbox.subprocess, "run", fake_run)
    out = sandbox.run(tmp_path, 3, ["make", "test"])
    assert out["timed_out"] is True
    name = calls[0][calls[0].index("--name") + 1]
    assert calls[1] == ["docker", "kill", name]


def test_compose_check_blocks_host_access(tmp_path):
    ok = {"services": {"web": {"build": {"context": str(tmp_path)},
                               "ports": [{"target": 8000, "published": "9003"}]}}}
    assert sandbox.compose_problems(ok, tmp_path, 9003) == []

    bad = {"services": {"web": {
        "privileged": True,
        "volumes": [{"type": "bind", "source": "/"}],
        "build": {"context": str(tmp_path.parent)},
        "ports": [{"target": 22, "published": "22"}],
    }}, "volumes": {"x": {"driver_opts": {"o": "bind", "device": "/"}}}}
    # privileged, 서버 / 붙이기, 저장소 밖 빌드, 다른 포트, driver_opts 우회
    assert len(sandbox.compose_problems(bad, tmp_path, 9003)) == 5


def test_docker_compose_build_not_allowed():
    with pytest.raises(guards.Denied):
        guards.check_command("docker compose build")
