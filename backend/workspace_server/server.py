"""workspace 서버(MCP)

A03, A04, A05가 요청한 파일 작업과 명령 실행을 검사한 뒤 대신 수행

모든 도구는 같은 순서로 동작
    1. 공통 검사(역할, 실행 단계)
    2. 도구별 검사
    3. 실행
    4. 기록
이 순서를 guarded() 한 곳에 모으고, 각 도구 함수는 3. 실행 단계에서 할 일만 적는다.
"""

import shutil
import subprocess
from pathlib import Path

# FastMCP: MCP 서버를 FastAPI처럼 쉽게 만드는 클래스
from mcp.server.fastmcp import FastMCP

import config
from db import get_connection
from workspace_server import gitops, guards, sandbox
from workspace_server.record import record

# MCP 서버 객체 생성
mcp = FastMCP("orbit-workspace", host=config.WORKSPACE_HOST, port=config.WORKSPACE_PORT)

def guarded(tool: str, run_id: int, agent: str, target: str, work, extra_check=None) -> dict:
    """도구 공통 처리: 검사 -> 실행 -> 기록. 결과를 dict으로 반환.
    
    tool : 도구 이름 (TOOL_AGENTS의 키, tool_calls.tool에 저장)
    target: 기록에 남길 대상 (파일 경로 명령 등)
    work: 실제 할 일. work(run, root) 모양의 함수를 받는다 (함수를 인자로 넘기는 방식)
    extra_check: 도구별 추가 검사(DB가 필요한 것). extra_check(conn, run) 모양.

    돌려주는 모양
        성공: {"ok": True, ...work가 돌려준 값...}
        거부: {"ok": False, "denied": True,  "reason": "이유"}
        오류: {"ok": False, "denied": False, "reason": "오류 메시지"}
    예외를 밖으로 던지지 않고 항상 dict로 돌려준다.
    그래야 에이전트(LLM)가 이유를 읽고 다른 방법을 시도할 수 있다.
    """
    try:
        with get_connection() as conn:
            run = guards.check_run(conn, run_id, agent, tool)
            if extra_check:
                extra_check(conn, run)


        root = guards.workspace_dir(run["project_id"])
        # 처음이면 폴더와 git 저장소를 만듦
        gitops.ensure_repo(root)
        # 도구마다 다른 실제 작업
        result = work(run, root)

    except guards.Denied as e:
        record(run_id, agent, tool, target, False, str(e))
        return {"ok": False, "denied": True, "reason": str(e)}

    except Exception as e:
        record(run_id, agent, tool, target, True, f"실행 오류: {e}")
        return {"ok": False, "denied": False, "reason": str(e)}

    record(run_id, agent, tool, target, True)
    return {"ok": True, **result}

@mcp.tool()
def list_files(run_id: int, agent: str, path: str = ".") -> dict:
    """작업 공간의 파일 목록을 돌려준다. path로 하위 폴더만 볼 수 있다."""

    def work(run, root):
        base = guards.safe_path(run["project_id"], path)

        files = [
            # as_posix(): Windows에서도 경로 구분자를 / 로 통일한다 (git_diff 결과, Ubuntu 서버와 같게)
            p.relative_to(root).as_posix()
            for p in sorted(base.rglob("*"))
            if p.is_file() and ".git" not in p.parts
        ]
        return {"files": files[:500]}

    return guarded("list_files", run_id, agent, path, work)

@mcp.tool()
def read_file(run_id: int, agent: str, path: str) -> dict:
    """작업 공간의 파일 내용을 문자열로 돌려준다."""

    def work(run, root):
        p = guards.safe_path(run["project_id"], path)
        if p.stat().st_size > 200000:
            raise guards.Denied("200KB보다 큰 파일은 읽을 수 없습니다.")

        return {"content": p.read_text(encoding="utf-8", errors="replace")}

    return guarded("read_file", run_id, agent, path, work)

@mcp.tool()
def write_file(run_id: int, agent: str, path: str, content: str) -> dict:
    """작업 공간에서 파일을 만들거나 내용을 통째로 수정한다."""

    def work(run, root):
        p = guards.safe_path(run["project_id"], path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

        return {"path": path, "bytes": len(content.encode("utf-8"))}

    return guarded("write_file", run_id, agent, path, work)

@mcp.tool()
def run_command(run_id: int, agent: str, command: str) -> dict:
    """허용된 설치·빌드·테스트 명령을 실행하고 종료 코드와 출력을 돌려준다.
    허용 명령: npm install, npm run build, pip install -r requirements.txt, make test
    빌드·테스트는 네트워크 없이 실행된다.
    """
    def work(run, root):
        args = guards.check_command(command)

        # 서버: 명령마다 새 컨테이너에서 실행 (이 프로젝트 폴더만 보이고 토큰은 없다)
        if config.USE_CONTAINER:
            return sandbox.run(root, run["project_id"], args)

        # 개발용(이 PC): 폴더에서 바로 실행
        try:
            r = subprocess.run(
                args, cwd=root, capture_output=True, text=True,
                encoding="utf-8", errors="replace",   # Windows에서 출력이 cp949로 깨지지 않게
                timeout=config.COMMAND_TIMEOUT
            )
        except subprocess.TimeoutExpired:
            return {"exit_code": None, "timed_out": True, "stdout": "", "stderr": f"{config.COMMAND_TIMEOUT}초를 넘겨 멈췄습니다."}

        return {"exit_code": r.returncode, "timed_out": False, "stdout": r.stdout[-config.OUTPUT_LIMIT:], "stderr": r.stderr[-config.OUTPUT_LIMIT:]}

    return guarded("run_command", run_id, agent, command, work)

@mcp.tool()
def git_diff(run_id: int, agent: str) -> dict:
    """마지막 커밋 이후 바뀐 파일 목록과 변경 내용을 돌려준다. 새로 만든 파일도 포함"""

    def work(run, root):
        gitops.git(root, "add", "-A", "--intent-to-add")
        return {
            "files": gitops.git(root, "diff", "--name-only").splitlines(),
            "diff": gitops.git(root, "diff")[-config.OUTPUT_LIMIT:],
        }

    return guarded("git_diff", run_id, agent, "working tree", work)

@mcp.tool()
def git_commit(run_id: int, agent: str, message: str) -> dict:
    """지금까지의 변경을 로컬 후보 커밋으로 만들고 커밋 SHA를 돌려준다.
    메시지에 run-<실행ID>와 기능 ID(FEAT-001 형식)가 있어야 한다.
    예: "run-12 FEAT-001 예약 목록 추가"
    """

    def work(run, root):
        guards.check_commit_message(run_id, message)
        gitops.git(root, "add", "-A")  
        gitops.git(root, "commit", "-m", message)

        return {"sha": gitops.git(root, "rev-parse", "HEAD")}

    return guarded("git_commit", run_id, agent, message[:100], work)

@mcp.tool()
def git_push(run_id: int, agent: str, sha: str) -> dict:
    """G4에서 승인한 커밋을 승인된 저장소 브랜치에 그대로 올린다. A05만 사용
    저장소와 브랜치는 승인 내용에서 자동으로 정해진다.
    """

    target = {}

    def check(conn, run):
        target.update(guards.check_push(conn, run, sha))

    def work(run, root):
        remote = f"https://github.com/{target['repository']}.git"
        gitops.git(root, "push", remote, f"{sha}:refs/heads/{target['branch']}", timeout=120)
        return {"pushed_sha": sha, "repository": target["repository"], "branch": target["branch"]}

    return guarded("git_push", run_id, agent, sha, work, extra_check=check)


# compose에 넘기는 환경 변수. 서버 환경(토큰 등)은 넘기지 않는다 (compose 파일이 ${GITHUB_TOKEN}을 꺼내 쓰지 못하게)
COMPOSE_ENV = {"PATH": "/usr/local/bin:/usr/bin:/bin"}


def _compose_up(folder: Path, project: str, env: dict) -> None:
    """folder의 docker-compose.yml로 서비스를 (다시) 띄운다. 테스트에서는 이 함수를 바꿔 끼운다."""
    r = subprocess.run(
        ["docker", "compose", "-p", project, "up", "-d", "--build", "--remove-orphans"],
        cwd=folder, env={**COMPOSE_ENV, **env}, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=config.COMMAND_TIMEOUT,
    )
    if r.returncode != 0:
        raise RuntimeError(f"docker compose up 실패: {(r.stderr or r.stdout)[-1000:]}")


def _checkout(root: Path, folder: Path, sha: str) -> None:
    """작업 공간 저장소의 sha 커밋만 folder에 꺼낸다 (git worktree). 이전에 꺼낸 것은 지운다."""
    if folder.exists():
        try:
            gitops.git(root, "worktree", "remove", "--force", str(folder))
        except RuntimeError:
            pass  # git이 모르는 폴더면 그냥 지운다
        shutil.rmtree(folder, ignore_errors=True)
    gitops.git(root, "worktree", "prune")
    folder.parent.mkdir(parents=True, exist_ok=True)
    gitops.git(root, "worktree", "add", "--detach", str(folder), sha)


@mcp.tool()
def deploy_local(run_id: int, agent: str, sha: str) -> dict:
    """G4에서 승인한 커밋을 이 서버의 Docker 테스트 환경에 띄운다. A05만 사용 (02 P03).
    저장소 최상위의 docker-compose.yml을 쓴다. 컨테이너에는 ORBIT_PORT(바깥 포트)와 GIT_SHA가 주어진다.
    재배포도 이 도구로 한다 (push 없이). 돌려주는 deployment_url에서 GET /version으로 SHA를 확인할 수 있다.
    """

    def check(conn, run):
        guards.approved_g4(conn, run, sha)  # 검사·승인한 커밋만 띄운다

    def work(run, root):
        pid = run["project_id"]
        folder = config.DEPLOY_ROOT / f"p{pid}"
        _checkout(root, folder, sha)
        if not (folder / "docker-compose.yml").exists():
            raise RuntimeError("저장소 최상위에 docker-compose.yml이 없습니다 (02 실행 계약).")

        port = config.DEPLOY_PORT_BASE + pid
        project = f"orbit-test-p{pid}"
        env = {"ORBIT_PORT": str(port), "GIT_SHA": sha}
        # 서버 폴더를 붙이거나 privileged 같은 위험한 설정이 있으면 띄우기 전에 거부한다
        if config.USE_CONTAINER:
            sandbox.check_compose(folder, project, env, port)
        _compose_up(folder, project, env)
        return {
            "deployed_sha": sha,
            "deployment_id": f"{project}@{sha[:12]}",
            "deployment_url": f"http://{config.DEPLOY_HOST}:{port}",
        }

    return guarded("deploy_local", run_id, agent, sha, work, extra_check=check)

