# Orbit: 프로젝트 진행 지원 AI 에이전트

기획(A01) → 화면 설계(A02) → 개발(A03) → 테스트(A04) → 배포(A05)를 에이전트가 맡고,
사람은 승인 지점(G1~G4)에서 확인하는 서비스입니다. 기준 문서는 이 폴더의 docx 6개입니다.

지금은 **서버와 화면의 뼈대**만 있습니다. 에이전트 자리에는 정해진 결과를 바로 돌려주는 임시 구현이 들어 있어,
에이전트 없이도 G1~G4 전체 흐름을 화면에서 끝까지 돌려볼 수 있습니다.

코드는 수업에서 배운 방식에 맞췄습니다.
- 서버: FastAPI + `sqlite3`(직접 SQL), 오류는 `HTTPException`, 실행 제어는 LangGraph `StateGraph`
- 화면: React(JavaScript, Vite), `createBrowserRouter`, Context, 커스텀 Hook, CSS Module

## 폴더

```
backend/                  FastAPI 서버
  main.py                 app = FastAPI(...), 라우터 연결, 시작할 때 init_db()
  config.py               설정 (.env에서 읽는다)
  db.py                   get_connection(), 테이블 만들기(create table ...), 조회·저장 도우미
  routers/                API 28개 (auth, projects, planning, runs, approvals, changes)
  services/               DB 작업과 흐름
    planning.py           대화 접수, A01 실행, G1 확정, 변경 요청 분석
    controller.py         ★ LangGraph 실행 제어기: A02 → G2 → A03 → (G3) → A04 → G4 → A05 → 배포 후 검사
    approvals.py          승인 요청과 결정
  agents/
    schemas.py            ★ 에이전트 입출력 모양 (TypedDict)
    stubs.py              임시 에이전트
    __init__.py           ★ 어떤 에이전트를 쓸지 고르는 곳 (AGENTS)
  seed_demo.py            팀원 계정과 예시 프로젝트 만들기
  create_user.py          계정 만들기
  tests/test_api.py       API 전체 흐름 테스트
frontend/                 React + Vite (JavaScript)
  src/
    main.jsx, App.jsx     시작점, createBrowserRouter로 화면 연결
    api/client.js         서버 요청 함수 (API_URL = "/api")
    context/AuthContext.jsx  로그인 상태 (AuthProvider)
    hooks/useApi.js       조회 + 3초마다 다시 조회하는 커스텀 Hook
    components/           공통 컴포넌트 (폴더마다 X.jsx + X.module.css)
    layouts/              왼쪽 메뉴, 프로젝트 메뉴와 위쪽 표시줄 (Outlet)
    pages/                화면 10개 + NotFound
    index.css             :root 색 변수 (Figma에 맞출 때 여기부터 바꾼다)
data/                     DB와 산출물 파일 (git에 넣지 않는다)
```

## 처음 실행

필요한 것: Python 3.12, Node.js 20 이상.

1. 서버 준비 (backend 폴더에서)

```bash
cd backend
```

```bash
python -m venv .venv
```

Windows는 `.venv\Scripts\activate`, Mac은 `source .venv/bin/activate`로 가상환경을 켭니다.

```bash
pip install -r requirements.txt
```

```bash
python seed_demo.py
```

> 이 PC처럼 `python` 명령이 없고 uv만 있으면 `uv venv --python 3.12 .venv` 후 `uv pip install -r requirements.txt`로 같은 가상환경을 만들 수 있습니다.

팀원 계정은 `jeonggeun`(송정근), `juyeong`(조주영), `hyeongjun`(심형준), `nayeong`(양나영), `suyeon`(양수연)이고
비밀번호는 모두 `1111`입니다 (개발용). 다섯 명 모두 "회의실 예약" 프로젝트에서 네 역할을 다 가지고 있어 누구나 G1~G4를 시험할 수 있습니다.
프로젝트를 만든 사람은 `jeonggeun`이라 멤버 화면에서 역할을 바꿀 수 있습니다.
처음부터 다시 하려면 `data/` 폴더를 지우고 `python seed_demo.py`를 다시 실행합니다.

2. 서버 켜기 (backend 폴더, 가상환경을 켠 상태)

```bash
uvicorn main:app --reload
```

API 문서: http://127.0.0.1:8000/docs

3. 화면 켜기 (새 터미널에서)

```bash
cd frontend
```

```bash
npm install
```

```bash
npm run dev
```

브라우저에서 http://localhost:5173 을 엽니다. `/api` 요청은 Vite가 8000번 서버로 넘깁니다 (vite.config.js).

새 계정은 운영 담당자가 서버에서 만듭니다 (회원가입 화면과 API는 없다):

```bash
python create_user.py kim --name 김기획
```

## 흐름 한 번 돌려보기

아무 팀원 계정으로 로그인해도 됩니다 (모두 네 역할을 가지고 있다).

1. 회의실 예약 프로젝트 → **대화**에서 만들 서비스를 설명합니다.
2. 임시 A01이 "주로 쓰는 사람"을 묻습니다. 답하면 기획서 v2가 나오고 **기획서**에서 [확정](G1)할 수 있습니다.
3. 실행 상태에서 [범위 확인] → 승인함에서 G2 승인(또는 의견을 적고 반려).
4. 승인함에서 G4 승인 → 배포와 배포 후 검사까지 끝나고 **완료**가 됩니다.
5. **실행 기록**에서 추적 경로·검사 결과·승인 이력을, **변경 요청**에서 확정 뒤 변경과 영향 범위를 볼 수 있습니다.

## 에이전트 붙이기 (팀원용)

1. `backend/agents/schemas.py`에서 맡은 에이전트의 입력과 출력 모양을 확인합니다. 서버는 dict를 넘기고 dict를 돌려받습니다.

| 에이전트 | 함수 | 입력 → 출력 |
|---|---|---|
| A01 기획 | `plan(inp)` | PlanningInput → PlanningOutput |
| A02 화면 설계 | `propose_scope(ctx)`, `design(ctx)` | RunContext → FigmaScope, DesignOutput |
| A03 개발 | `develop(ctx)` | RunContext → DevelopOutput (`out_of_plan`이 있으면 G3를 거친다) |
| A04 테스트 | `test(ctx, phase, sha, url)` | RunContext → TestOutput |
| A05 배포 | `deploy(ctx, sha)` | RunContext → DeployOutput |

2. 같은 모양으로 함수를 만들고 `agents/__init__.py`의 `AGENTS`에서 해당 줄만 바꿉니다.
   LangGraph로 만들었다면 `graph.invoke(...)` 결과에서 필요한 키를 꺼내 dict로 돌려주면 됩니다.
3. 에이전트는 DB를 건드리지 않습니다. 결과만 돌려주면 버전·해시·승인 요청·기록은 서버가 만듭니다.
   실패하면 `raise Exception("이유")`. 실행이 실패로 기록되고 담당 역할에게 [다시 시도]가 보입니다.
4. 가짜 에이전트를 끼워 테스트하는 예시는 `tests/test_api.py`의 `test_fix_loop_limit_and_retry`에 있습니다.

## 실행 제어기 (LangGraph)

`services/controller.py`의 `build_run_graph()`가 그래프를 만듭니다.

- 노드: `A02_scope`, `A02`, `G2`, `A03`, `G3`, `A04`, `G4`, `A05`, `A04_post` (단계 하나 = 노드 하나)
- 상태: `RunState(TypedDict)` = `run_id`, `step`(다음 단계), `stop`(멈춘 이유)
- `START`에서 DB의 `runs.current_step`을 보고 시작 노드를 고르고(`add_conditional_edges`),
  각 노드 뒤에서는 `stop`이 있으면 `END`, 없으면 `step`의 노드로 갑니다.
- 승인 지점(G2·G4)에서는 승인 요청을 만들고 멈춥니다. 사람이 승인하면 그 단계부터 그래프를 다시 실행합니다.
- 테스트가 실패하면 A03으로 돌아가고, `ORBIT_FIX_RETRY_LIMIT`(기본 5)회에 닿으면 멈춥니다.

## 테스트

```bash
cd backend
```

```bash
pytest -q
```

```bash
cd frontend
```

```bash
npm run build
```

## 문서와 다르게 정한 것 (팀 확인 필요)

06 데이터 모델에 아래 필드를 더했습니다 (06 4장: 필드가 더 필요하면 문서를 함께 고친다).

| 테이블 | 더한 것 | 이유 |
|---|---|---|
| auth_sessions (새 테이블) | 토큰 해시, 만료, 로그아웃 시각 | 07 7장의 토큰 로그인과 로그아웃 |
| questions | `required`, 상태 `dropped` | 필수 질문만 G1을 막는다. 다시 정리했더니 더 묻지 않게 된 질문을 닫는다 |
| approval_requests | `detail` | 승인 상세 화면의 G2·G3·G4 내용 |
| change_requests | `source_message_id` | 추적 경로 5 (변경 요청 → 원문 메시지) |
| runs | `candidate_sha`, `failure`, `waiting_input` | 후보 커밋(G3·A04 대상), 실패 이유, 범위 확인 대기 내용 |
| run_steps | `links` | 실행 상태의 단계별 산출물 링크 |
| notifications | 상태 `pending`, `payload`, `attempts`, `next_try_at`, `last_error`, `sent_at` | 알림을 먼저 쌓아 두고 따로 보내며, 실패하면 간격을 두고 다시 보낸다 (04 7.5) |
| notion_pages (새 테이블) | 프로젝트, 문서 종류, Notion 페이지 ID와 주소 | 문서마다 페이지 하나를 만들고 그 뒤로는 내용을 바꾼다 (02 4.6) |

- 오류 응답은 FastAPI 기본 모양 `{"detail": "메시지"}`입니다 (07 명세의 `{error: {code, message}}` 대신).
- 에이전트는 요청 안에서 바로 실행합니다 (예: 메시지를 보내면 A01이 끝난 뒤 응답). 실제 에이전트가 오래 걸리면
  FastAPI `BackgroundTasks`나 작업 큐로 옮길 자리입니다. 화면은 이미 3초마다 다시 조회합니다.
- Slack 알림과 Notion 게시는 `services/notify.py`가 `notifications`에 쌓고, 서버의 백그라운드 작업자가 MCP로 보냅니다.
  `.env`의 `ORBIT_NOTIFY_ENABLED=true`일 때만 실제로 보냅니다. 연결 확인: `python scripts/slack_check.py`, `python scripts/notion_check.py`
- Notion은 프로젝트 페이지 아래에 기획서와 완료 조건, 기술 명세, 검사 결과, 배포 기록 페이지를 하나씩 두고 내용을 바꿔 갑니다.
  일정 데이터베이스(02 4.6 일정 표)는 아직 없습니다.
