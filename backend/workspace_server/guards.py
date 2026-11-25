""" -- workspace 서버 --
workspace 서버의 실행  전 검사
막을 때는 Denied(이유)를 던진다.

에이전트가 부탁한 일을 실제로 하기 전에 "해도 되는 일인가"를 확인
검사에 걸리면 Denied(이유) 예외를 던지고, 통과하면 지나감
FastAPI에서 HTTPException을 던져 요청을 막는 것과 같은 방식
모든 판단은 python logic으로 검사
"""

import re
import shlex
import config

from pathlib import Path
from db import fetch_one

class Denied(Exception):
    """
    검사에서 막힌 요청.
    메시지: 거부 이유
    """

# 도구 -> 쓸 수 있는 에이전트 지정
TOOL_AGENT = {
    "list_files": ("A03", "A04", "A05"),
    "read_file": ("A03", "A04", "A05"),
    "write_file": ("A03", "A04"),
    "run_command": ("A03", "A04"),
    "git_diff": ("A03", "A04", "A05"),
    "git_commit": ("A03", "A04"),
    "git_push": ("A05",),
    "deploy_local": ("A05",),
}

# 에이전트 -> 그 에이전트가 일할 수 있는 실행 단계
AGENT_STEPS = {"A03": ("A03",), "A04": ("A04", "A04_post"), "A05": ("A05",)}


def check_run(conn, run_id: int, agent: str, tool: str) -> dict:
    """
    확인하는 것
    1. 이 에이전트가 이 도구를 쓸 수 있는가
    2. 실행이 진행 중(running)인가
    3. 지금 실행 단계가 이 에이전트 차례인가
    """

    # 1. 역할 확인. TOOL_AGENT에 없는 도구 이름이면 .get()이 빈 튜플 반환
    if agent not in TOOL_AGENT.get(tool, ()):
        raise Denied(f"{agent}는 {tool}을 쓸 수 없습니다.")
    run = fetch_one(conn, "select * from runs where id = ?", (run_id,))

    # 2. 실행 확인. 프로젝트 id를 DB에서 찾는다.
    if run is None or run["status"] != "running":
        raise Denied("진행 중인 실행이 아닙니다.")

    # 3. 차례 확인. LLM이 agent="A01"라고 거짓으로 적어도 실행이 A01 단계가 아니면 막는다.
    if run["current_step"] not in AGENT_STEPS[agent]:
        raise Denied(f"지금은 {run['current_step']} 단계라 {agent}의 차례가 아닙니다.")
    return run

def workspace_dir(project_id: int) -> Path:
    """
    프로젝트의 작업 공간 폴더 경로. ex) .../data/workspace/p3    
    """
    return (config.WORKSPACE_ROOT / f"p{project_id}").resolve()

def safe_path(project_id: int, rel: str) -> Path:
    """
    에이전트가 준 경로가 작업 공간인지 확인하고, 안전한 절대 경로를 return

    막는 ex)
    - "../p4/app.py"          → 다른 프로젝트 폴더
    - "../../../backend/.env" → Orbit 서버의 비밀 값 파일
    - "C:/Windows/..."        → 절대 경로 (root / 절대경로 는 절대경로가 된다)
    - ".git/config"           → git 내부 파일 (git 도구로만 다루게 한다)
    """
    root = workspace_dir(project_id)
    path = (root / rel).resolve()

    # 실제 위치가 작업 공간인지 확인
    if not path.is_relative_to(root):
        raise Denied(f"작업 공간 밖의 경로입니다 : {rel}")

    # 작업 공간이 .git이면 막는다.
    if ".git" in path.relative_to(root).parts:
        raise Denied(".git 폴더는 직접 다룰 수 없습니다. git 도구를 쓰게요.")
    return path

def check_command(command: str) -> list[str]:
    """
    허용 목록과 정확히 같은 명령만 통과시키고, 단어 리스트 return

    ex) 
        "npm run build"             -> ["npm", "run", "build"]
        "make test; rm -rf"         -> ["make", "test;", "rm", "-rf"] -> 목록에 없으니 막힘
    """
    args = shlex.split(command)
    # 리스트끼리 in 비교: 요소와 순서가 모두 같은 리스트가 목록에 있어야 True
    if args not in config.ALLOWED_COMMANDS:
        raise Denied(f"허용 목록에 없는 명령입니다: {command}")
    return args

def check_commit_message(run_id: int, message: str) -> None:
    """
    커밋 메시지에 실행 ID와 기능 ID가 있는지 확인한다

    나중에 "이 커밋은 어느 실행에서, 어느 기능 때문에 만들었나"를 추적하기 위함
    ex) "run-12 FEAT-001 예약 목록 화면 추가"
    """

    # \b는 단어 경꼐. "xrun-12"나 "run-123"을 run-12로 잘못 읽지 않게 한다.
    # (\d+)는 숫자 부분을 따로 꺼낼 수 있게 묶은 그룹 -> m.group(1)
    m = re.search(r"\brun-(\d+)\b", message)
    # ID가 없거나, 다른 실행의 번호를 적었다면 막는다.
    # re.group: 정규 표현식과 일치한 전체 문자열 반환
    if not m or int(m.group(1)) != run_id:
        raise Denied(f"커밋 메시지에 실행 ID(run-{run_id})가 없습니다.")

    # 기능 ID는 기획서(A01)의 FEAT-001 형식. 하나 이상 있으면 된다
    if not re.search(r"\bFEAT-\d+\b", message):
        raise Denied("커밋 메시지에 기능 ID(FEAT-001 형식)가 없습니다.")

def approved_g4(conn, run: dict, sha: str) -> dict:
    """이 실행의 마지막 G4 승인을 돌려준다. 승인이 없거나 커밋이 다르면 Denied. (git_push, deploy_local 공통)"""
    # 승인 중 가장 최근의 승인만 가져온다.
    # 반려 뒤 다시 승인받을 수 있으므로 order by id desc limit 1로 마지막 승인만 가져옴
    req = fetch_one(
        conn,
        "select * from approval_requests where run_id = ? and gate = 'G4' and status = 'approved' "
        "order by id desc limit 1",
        (run["id"],),
    )
    if req is None:
        raise Denied("G4 승인을 받지 않은 실행입니다.")

    # target_hash: 배포 담당자가 승인 화면에서 본 커밋 SHA
    # tested_sha: A04가 검사를 통과시킨 커밋 SHA (controller.py가 저장)
    # 세 값이 모두 같아야 된다. "사용자가 본 것 = 검사한 것 = 올리는 것(띄우는 것)"
    if sha != req["target_hash"] or sha != run["tested_sha"]:
        raise Denied("이 커밋은 검사·승인한 커밋과 다릅니다.")
    return req


def check_push(conn, run: dict, sha: str) -> dict:
    """
    git push 전용 검사.
    통과하면 승인된 게시 대상 {"repository", "branch", "environment"}을 return
    
    확인하는 것
    1. 이 실행에 승인이 있는가
    2. 올리려는 커밋 = G에서 승인한 커밋 = A가 검사한 커밋인가
    repository와 branch는 에이전트에게 받지 않고 승인 요청에 적힌 값을 return
    """
    req = approved_g4(conn, run, sha)

    # 재배포는 이미 올라간 예전 커밋을 다시 띄우는 것. 다시 올리면 브랜치가 뒤로 가므로 거부된다 (02 P03부터)
    if run.get("redeploy_of_run_id"):
        raise Denied("재배포는 게시(push)하지 않습니다. 배포(P03)부터 진행하세요.")

    # deploy_target은 db.py의 JSON_COLUMNS에 들어 있어서 fetch_one이 dict로 바꿔 준다.
    target = req["deploy_target"] or {}
    check_deploy_target(target)
    return target


# 브랜치 이름: 영문, 숫자, / . _ - 만. "main"과 "master"에는 올리지 않는다
BRANCH_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9/._-]*")


def check_deploy_target(target: dict) -> None:
    """승인된 게시 대상이 서버의 허용 목록 안에 있는지 확인한다 (04 7.3)."""
    repo, branch = target.get("repository") or "", target.get("branch") or ""
    if not repo:
        raise Denied("게시할 저장소가 설정되지 않았습니다 (ORBIT_DEPLOY_REPOSITORY).")
    if repo not in config.ALLOWED_REPOSITORIES:
        raise Denied(f"허용 목록에 없는 저장소입니다: {repo}")
    if not BRANCH_PATTERN.fullmatch(branch) or ".." in branch or branch in ("main", "master"):
        raise Denied(f"올릴 수 없는 브랜치입니다: {branch}")


