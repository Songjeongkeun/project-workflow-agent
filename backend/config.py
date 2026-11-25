"""서버 설정. 값은 .env 파일이나 환경 변수로 바꾼다 (.env.example 참고)."""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent  # backend 폴더
REPO_ROOT = BASE_DIR.parent

load_dotenv(REPO_ROOT / ".env")

# DB 파일과 산출물(기획서, 화면 계획 등) 파일을 두는 곳
DATA_DIR = Path(os.getenv("ORBIT_DATA_DIR", REPO_ROOT / "data"))
DB_PATH = Path(os.getenv("ORBIT_DB_PATH", DATA_DIR / "orbit.db"))

# 로그인 유지 시간
TOKEN_HOURS = int(os.getenv("ORBIT_TOKEN_HOURS", "12"))

# 화면 주소 (CORS 허용)
CORS_ORIGINS = os.getenv("ORBIT_CORS_ORIGINS", "http://localhost:5173").split(",")

# 테스트 실패 → 다시 개발을 몇 번까지 반복할지
FIX_RETRY_LIMIT = int(os.getenv("ORBIT_FIX_RETRY_LIMIT", "5"))

# -- workspace 서버 --
# 생성 프로젝트의 작업 공간 폴더 (프로젝트마다 p{id} 폴더)
WORKSPACE_ROOT = Path(os.getenv("ORBIT_WORKSPACE_ROOT", DATA_DIR / "workspaces"))

# workspace 서버 주소 (에이전트가 여기로 접속)
WORKSPACE_HOST = os.getenv("ORBIT_WORKSPACE_HOST", "127.0.0.1")
WORKSPACE_PORT = int(os.getenv("ORBIT_WORKSPACE_PORT", "8100"))
WORKSPACE_URL = f"http://{WORKSPACE_HOST}:{WORKSPACE_PORT}/mcp"

# 명령 하나의 실행 시간 한도(초)와 돌려줄 출력 길이 지정
COMMAND_TIMEOUT = int(os.getenv("ORBIT_COMMAND_TIMEOUT", "600"))
OUTPUT_LIMIT = 8000

# 허용 명령 목록
ALLOWED_COMMANDS = [
    ["npm", "install"],
    ["npm", "run", "build"],
    ["pip", "install", "-r", "requirements.txt"],
    ["make", "test"],
    ["docker", "compose", "build"]
] 

# True면 명령을 프로젝트 컨테이너 안에서 실행한다. 
# False면 폴더에서 바로 실행
USE_CONTAINER = os.getenv("ORBIT_USE_CONTAINER", "false") == "true"

# -- 게시와 배포 대상 (04 6장, 7.3) --
# 생성 서비스를 올릴 GitHub 테스트 저장소 "owner/name". 비어 있으면 게시(push)를 막는다
DEPLOY_REPOSITORY = os.getenv("ORBIT_DEPLOY_REPOSITORY", "")
# 올릴 브랜치. {project_id}가 프로젝트 번호로 바뀐다 (프로젝트끼리 브랜치를 덮어쓰지 않게)
DEPLOY_BRANCH = os.getenv("ORBIT_DEPLOY_BRANCH", "release/p{project_id}")
DEPLOY_ENVIRONMENT = os.getenv("ORBIT_DEPLOY_ENVIRONMENT", "test")
# 테스트 환경 주소: http://{DEPLOY_HOST}:{DEPLOY_PORT_BASE + 프로젝트 번호}
DEPLOY_HOST = os.getenv("ORBIT_DEPLOY_HOST", "localhost")
DEPLOY_PORT_BASE = int(os.getenv("ORBIT_DEPLOY_PORT_BASE", "9000"))
# push를 허용할 저장소 목록 (쉼표로 구분). 비우면 DEPLOY_REPOSITORY 하나만 허용
ALLOWED_REPOSITORIES = [r.strip() for r in os.getenv("ORBIT_ALLOWED_REPOSITORIES", DEPLOY_REPOSITORY).split(",") if r.strip()]
# 배포할 커밋을 꺼내 둘 폴더 (프로젝트마다 p{id}). 작업 공간과 따로 둬서 개발 중인 파일과 섞이지 않게 한다
DEPLOY_ROOT = Path(os.getenv("ORBIT_DEPLOY_ROOT", DATA_DIR / "deployments"))

# True면 제어기가 작업 공간의 실제 기록을 확인한다: G3는 git으로 바뀐 파일을 비교하고, A05는 git_push 기록을 확인한다.
# 실제 A03 에이전트가 workspace 서버로 커밋을 만들 때 켠다.
# 임시 에이전트는 커밋을 만들지 않으므로 기본값은 false (A03이 돌려준 out_of_plan을 쓴다)
USE_WORKSPACE = os.getenv("ORBIT_USE_WORKSPACE", "false") == "true"

# -- Slack 알림 (02 4.7, 04 7.5) --
# true면 쌓인 알림을 실제로 보낸다. false면 쌓기만 한다 (테스트, 토큰 준비 전)
NOTIFY_ENABLED = os.getenv("ORBIT_NOTIFY_ENABLED", "false") == "true"

# 알림 메시지의 링크가 가리킬 화면 주소
APP_URL = os.getenv("ORBIT_APP_URL", "http://localhost:5173")

# 토큰과 ID는 .env에서만 읽는다. DB, 로그, LLM 입력에 넣지 않는다 (04 2장)
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "")
SLACK_TEAM_ID = os.getenv("SLACK_TEAM_ID", "")
# 프로젝트 알림 채널. 비우면 개인 메시지만 보낸다 (봇을 채널에 초대해야 한다)
SLACK_CHANNEL_ID = os.getenv("SLACK_CHANNEL_ID", "")

# 알림 보내기: 몇 초마다 확인할지, 몇 번까지 다시 보낼지, 첫 재시도까지 몇 초 기다릴지 (두 배씩 늘어난다)
# -- Notion 게시 (02 4.6, 04 7.4) --
# 내부 통합(Internal integration) 토큰과, 그 통합에 공유한 게시용 상위 페이지(주소나 ID)
# 둘 다 있을 때만 게시할 문서를 쌓는다. 보내기는 NOTIFY_ENABLED를 따른다
NOTION_TOKEN = os.getenv("NOTION_TOKEN", "")
NOTION_PARENT_PAGE = os.getenv("NOTION_PARENT_PAGE", "")


def notion_on() -> bool:
    return bool(NOTION_TOKEN and NOTION_PARENT_PAGE)


NOTIFY_INTERVAL = int(os.getenv("ORBIT_NOTIFY_INTERVAL", "10"))
NOTIFY_MAX_ATTEMPTS = 5
NOTIFY_RETRY_SECONDS = 30


# -- GitHub : 게시한 커밋 확인용. 읽기 전용 토큰 --
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_MCP_URL = os.getenv("GITHUB_MCP_URL", "https://api.githubcopilot.com/mcp/")


# -- 에이전트 작업자 --
AGENT_WORKERS = int(os.getenv("ORBIT_AGENT_WORKERS", "2")) # 동시에 일하는 실행 수
AGENT_SWEEP_SECONDS = int(os.getenv("ORBIT_AGENT_SWEEP_SECONDS", "15"))
AGENT_INLINE = os.getenv("ORBIT_AGENT_INLINE", "false") == "true" # 테스트 용