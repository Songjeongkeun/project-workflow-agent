# Design Agent

A01에서 작성한 **서비스 기획서 JSON**을 입력받아 화면 계획(`screen_plan.json`)을 생성하고, **Codex App Server + Figma MCP**를 통해 편집 가능한 Figma 디자인을 만드는 에이전트입니다.

Figma 작업 후 화면별 URL과 노드 ID를 검증해 `screen_plan.json`의 `design_ref`에 저장하고, 이를 A03 개발 에이전트에 전달합니다.

> **담당 범위:** MVP 기능의 선정은 A01에서 수행합니다. A02는 A01에서 확정한 출시 범위를 바탕으로 화면을 설계하며, MVP를 다시 선정하지 않습니다.

## 1. 주요 기능

- **D01 | 화면 계획 생성:** Claude API로 A01 기획서를 분석하여 화면 구성 요소(`elements`), 상태(`states`), 이동 관계(`next_screens`) 생성
- **화면 계획 검증:** Pydantic 및 `validator.py`로 화면·기능 ID, 이동 참조 관계 등 기본 구조 검증
- **D02 | Figma 디자인 생성 및 기존 화면 보완:** Codex App Server가 Figma MCP를 호출하여 Figma Design 프레임과 편집 가능한 레이어 생성
- **Figma 결과 검증 및 참조 정보 저장:** Codex의 MCP 기반 확인 결과와 Python의 화면 ID·URL·노드 ID 검사 후 `design_ref` 기록
- **A03 전달:** 최종 `screen_plan.json`을 후속 개발 단계의 입력으로 사용

> **검증 범위:** `validator.py`는 화면 계획의 구조와 참조 관계를 검사합니다. Figma 화면의 실제 내용 검증은 Codex가 수행한 MCP 조회·검증에 의존하며, Python이 모든 Figma 레이어를 독립적으로 확인하는 것은 아닙니다.

## 2. 처리 흐름

```text
A01 기획서 JSON (출시/MVP 범위 포함)
      │
      ▼
main.py → llm_client.py (Claude API)
      │
      ▼
ScreenPlan 생성 → validator.py
      │
      ▼
screen_plan.json (design_ref: null)
      │
      ▼
figma_runner.py → figma_prompt.py
      │
      ▼
codex_app_server.py (Codex App Server / JSON-RPC)
      │
      ▼
Codex → Figma MCP (OAuth) → Figma Design
      │
      ▼
생성 결과 확인 및 참조 검증
      │
      ▼
screen_plan.json에 design_ref 저장 → A03 전달
```

## 3. 프로젝트 파일

```text
FigmaAgent/
├── main.py                  # A01 기획서 입력, 화면 계획 생성·검증·저장
├── llm_client.py            # Claude API 기반 화면 계획 생성
├── schemas.py               # A01 입력 및 ScreenPlan Pydantic 모델
├── validator.py             # 화면 계획 구조 및 ID 참조 검증
├── figma_prompt.py          # Codex용 Figma 작업 지시문 작성
├── figma_runner.py          # Figma 작업 실행, 결과 검증, design_ref 저장
├── codex_app_server.py      # Codex App Server 통신 및 승인 요청 처리
├── requirements.txt         # Python 패키지 의존성
├── README.md                # 설치 및 실행 안내
├── .gitignore               # Git 추적 제외 대상
├── .env.example             # 환경 변수 작성 예시 (실제 키 제외)
└── data/
    └── planning.json       # A01에서 전달받는 입력 파일 (직접 준비)
```

> `data/planning.json`은 실제 실행 시 준비하는 파일입니다. GitHub에 예제를 제공한다면 `data/planning_example.json` 등으로 이름을 구분하세요. 예전의 규칙 기반 `planner.py`는 현재 실행 흐름에서 사용하지 않으므로 포함하지 않아도 됩니다. 단, 다른 코드에서 import하지 않는지 확인해야 합니다.

`screen_plan.json`, `figma_codex_result.json`, `figma_output_schema.json` 등은 실행 과정에서 생성되거나 갱신될 수 있습니다.

## 4. 실행 환경

**개발 도구**

- Python **3.11 이상 권장** (실제 검증한 Python 버전이 있다면 그 버전을 우선)
- Node.js, npm, Git
- Codex CLI (개발 중 사용한 버전: `codex-cli 0.169.0`)

**외부 서비스 및 권한**

- Claude API 키
- Codex 사용 권한이 있는 OpenAI 계정
- Figma 계정, Figma MCP OAuth 인증, 대상 Figma Design 파일에 대한 적절한 편집 권한
- Figma 캔버스 생성·수정 기능을 사용할 수 있는 플랜과 좌석 권한 (예: 지원되는 Professional/Education 플랜의 적절한 좌석)

**검증 환경:** Windows CMD에서 동작을 확인했습니다. macOS용 설치 명령어도 제공하지만, `codex_app_server.py`를 포함한 전체 파이프라인의 macOS 실행은 별도 검증이 필요합니다.

## 5. 설치 및 환경 설정

### 5.1 프로젝트 다운로드

**Windows CMD / macOS Terminal 공통**

```bash
git clone <REPOSITORY_URL>
cd FigmaAgent
```

`<REPOSITORY_URL>`을 실제 GitHub 저장소 주소로 바꾸세요. 다운로드한 폴더명이 다르면 `cd` 경로도 변경합니다.

### 5.2 Python 가상환경 생성 및 라이브러리 설치

**Windows (CMD)**

```cmd
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
```

**macOS (Terminal)**

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

현재 프로젝트의 직접 Python 의존성은 다음과 같습니다.

```txt
anthropic
python-dotenv
pydantic
```

실제 `requirements.txt`는 최종 테스트에서 검증된 버전으로 고정하는 것을 권장합니다. Codex CLI는 Python 패키지가 아니므로 별도 설치합니다.

### 5.3 Claude API 환경 변수 설정

저장소의 `.env.example`을 `.env`로 복사합니다.

**Windows (CMD)**

```cmd
copy .env.example .env
```

**macOS (Terminal)**

```bash
cp .env.example .env
```

생성된 `.env`에서 자신의 API 키를 입력합니다.

```dotenv
ANTHROPIC_API_KEY=your_actual_anthropic_api_key
ANTHROPIC_MODEL=claude-sonnet-5-5
```

- 모델 ID는 실제 계정에서 사용할 수 있는 값으로 설정합니다.
- 현재 구현에서는 `ANTHROPIC_WORKSPACE_ID` 설정이 필요하지 않습니다.
- **실제 API 키가 들어 있는 `.env`는 GitHub에 업로드하지 마세요.** `.env.example`에는 예시 값만 넣고 저장소에 포함합니다.

### 5.4 Codex CLI 설치 및 로그인

**Windows CMD / macOS Terminal 공통**

```bash
npm install -g @openai/codex
codex --version
codex login
```

브라우저 인증을 완료합니다. 각 실행자는 **자신의 Codex 계정**으로 로그인해야 합니다.

> Windows에서는 Codex CLI 버전과 설치 환경에 따라 네이티브 실행 또는 WSL 사용 여부가 달라질 수 있습니다. 이 프로젝트는 Windows CMD에서 테스트한 환경을 기준으로 작성되었습니다.

### 5.5 Figma MCP 등록 및 OAuth 인증

**Windows CMD / macOS Terminal 공통**

```bash
codex mcp add figma --url https://mcp.figma.com/mcp
codex mcp login figma
codex mcp list
```

- 이미 `figma` MCP가 등록되어 있다면 `add` 명령을 다시 실행할 필요는 없습니다.
- Figma OAuth에서 디자인에 접근할 **본인 Figma 계정**으로 인증합니다.
- `codex mcp list`에서 서버 등록 및 인증 상태를 확인합니다.
- 이 프로젝트는 Figma REST API 키가 아니라 MCP OAuth 연결을 사용하므로 `.env`에 Figma 토큰을 넣지 않습니다.
- Figma MCP 연결만으로 캔버스 편집 권한이 생기지는 않습니다. 계정 플랜·좌석·파일 편집 권한을 확인하세요.

Figma 공식 문서에서는 지원되는 클라이언트의 **Figma Skills**를 디자인 생성·편집 작업에 활용할 것을 안내합니다. 현재 프로젝트의 필수 설치 항목으로 확정된 것은 아니지만, 기능이 원활하지 않다면 사용 중인 Codex·Figma MCP 버전과 Skills 지원 여부를 확인하세요.

## 6. 실행 방법

### STEP 1. A01 기획서 준비 및 화면 계획 생성 (D01)

A01에서 전달받은 JSON 파일을 `data/planning.json` 경로에 저장합니다. 입력 데이터는 `schemas.py`의 `PlanningInput` 구조를 만족해야 하며, 출시 범위가 적절하게 설정되어 있어야 합니다.

**Windows CMD / macOS Terminal 공통 (가상환경 활성화 후)**

```bash
python main.py data/planning.json
```

정상 실행 로그 예시:

```text
[Claude 토큰 사용량] 입력 ..., 출력 ...
[화면 계획 검증 성공]
JSON 저장 완료: .../screen_plan.json
```

`screen_plan.json`에는 화면 ID, 연결된 기능, UI 요소, 상태, 화면 이동 관계가 포함됩니다. Figma 작업 전 `design_ref`는 일반적으로 `null`입니다.

### STEP 2. 대상 Figma 파일 지정

1. Figma에서 새 **Figma Design** 파일을 만들거나 편집 가능한 기존 파일을 엽니다.
2. 해당 파일의 URL을 복사합니다.
3. `.env`의 `FIGMA_URL`을 실제 URL로 변경합니다.

```python
FIGMA_URL = "https://www.figma.com/design/YOUR_FILE_KEY/YOUR_FILE_NAME"
```


### STEP 3. Figma 디자인 생성 및 기존 화면 보완 (D02)

```bash
python figma_runner.py
```

`codex_app_server.py`가 Codex App Server를 시작하고 Figma MCP 도구를 호출합니다. 승인 요청이 표시되면 요청된 작업을 확인한 뒤 허용 여부를 선택합니다.

성공 로그 예시:

```text
[Codex App Server 실행 시작]
[Codex App Server 시작]
[Figma 작업 진행 중 — 필요한 승인만 요청합니다]
[Figma 도구] figma use_figma completed
[완료] design_ref 3개 저장
[완료] Figma 작업 결과 저장 성공
```

화면 수와 도구 호출 횟수는 기획서 및 파일 상태에 따라 다릅니다. 중간에 MCP 호출이 실패했더라도 최종 실행 결과와 Figma 파일을 함께 확인하세요.

### STEP 4. 결과 확인

각 화면의 `design_ref`에 Figma URL과 노드 ID가 저장됐는지 확인합니다.

```json
{
  "screen_id": "SCR-001",
  "design_ref": {
    "figma_url": "https://www.figma.com/design/YOUR_FILE_KEY/YOUR_FILE_NAME?node-id=1-2",
    "node_id": "1:2"
  }
}
```

위 JSON은 **일부 필드만 보여주는 예시**이며 전체 `ScreenPlan` 형식은 아닙니다.

Figma 파일에서도 프레임 이름, UI 요소, 기본·예외 상태, 편집 가능한 레이어, 실제 구성된 프로토타입 연결을 직접 확인하세요.

## 7. A03 개발 에이전트로 전달

A03에 전달할 주요 산출물은 Figma 참조 정보가 채워진 **`screen_plan.json`**입니다.

| 필드 | 의미 |
|---|---|
| `project_id` | 프로젝트 식별자 |
| `screen_id`, `name` | 화면 식별자와 이름 |
| `related_features` | 해당 화면과 연결된 출시 기능 ID |
| `elements` | UI 구성 요소 및 사용자 행동 |
| `states` | 기본 및 예외 상태 |
| `next_screens` | 화면 이동 대상 |
| `design_ref` | 실제 Figma 프레임 URL 및 노드 ID |

A03는 화면의 기본 상태뿐 아니라 JSON에 정의된 상태별 UI도 참고해야 합니다. Figma 파일 접근 권한은 별도로 부여해야 합니다.

## 8. 실행 시 주의사항

- **실행 순서:** 처음 생성할 때는 `main.py` → `figma_runner.py` 순서로 실행합니다. 기존 디자인 수정 중 `main.py`를 재실행하면 `screen_plan.json`의 기존 `design_ref`가 덮어써질 수 있습니다.
- **중복 생성 방지:** `figma_prompt.py`는 동일한 `screen_id`에 대응하는 화면이 있으면 재사용하도록 지시합니다. 실제 적용 여부는 실행 후 확인이 필요합니다.
- **수정 기능의 범위:** 기존 화면 보완을 지시할 수 있지만, **사용자 자연어 피드백을 별도로 입력받아 디자인만 수정하는 전용 인터페이스는 아직 구현되지 않았습니다.**
- **계정 및 권한:** GitHub에 코드를 공개해도 Codex·Figma 사용 권한이 공유되지 않습니다. 각 실행자는 본인 계정으로 인증해야 하며, 계정 비밀번호·OAuth 토큰을 공유하지 않습니다.
- **자동화 범위:** Figma 화면은 디자인 산출물입니다. 버튼의 업로드·다운로드 같은 백엔드 기능까지 구현하는 것은 아닙니다.
- **플랫폼 정보:** A01이 웹·모바일 등 지원 플랫폼을 명시한 경우, 해당 값이 `schemas.py`와 Claude 프롬프트에 전달되는지 확인해야 합니다. 미정의 필드는 입력 검증 과정에서 반영되지 않을 수 있습니다.

## 9. 문제 해결

| 증상 | 확인할 부분 |
|---|---|
| `ANTHROPIC_API_KEY` 오류 | `.env` 위치, API 키 값, `python-dotenv` 설치 확인 |
| Claude API 400/404 | 모델 ID, API 키의 모델 접근 권한, 실제 API 오류 메시지 확인 |
| `codex` 명령을 찾을 수 없음 | Node.js/npm, Codex CLI 설치, PATH 설정 확인 |
| Figma MCP 연결 실패 | `codex mcp list`, `codex mcp login figma`, OAuth 및 파일 접근 권한 확인 |
| Figma 편집 권한 오류 | Figma 플랜·좌석·대상 파일 편집 권한 확인 |
| MCP 승인 거부 | 비대화형 `codex exec`가 아닌 현재 구현된 App Server 흐름 사용 여부 확인 |
| Figma 결과가 `failed` | `figma_codex_result.json`의 `error` 및 실제 Figma 상태 확인 |
| `design_ref` 누락 | 최종 결과의 `status`, 화면 ID·파일 키·노드 ID 검증 결과 확인 |
| macOS에서 Python 실행 실패 | 가상환경 활성화 여부 및 `python3` 사용 가능 여부 확인 |

## 10. GitHub 업로드 전 확인

`.gitignore` 예시:

```gitignore
# 환경 변수 및 인증 정보
.env
.env.*
!.env.example

# Python 가상환경 및 캐시
.venv/
venv/
__pycache__/
*.pyc

# Codex 작업 결과
figma_codex_result.json
figma_output_schema.json

# IDE 설정
.vscode/
.idea/
```

- `.env.example`에는 **예시 API 키만** 포함하고 GitHub에 업로드합니다.
- 실제 `.env`, Codex 로그인 정보, Figma OAuth 토큰 등 인증 정보를 저장소에 포함하지 마세요.
- 이미 `.env`를 Git이 추적하고 있다면 `git rm --cached .env`로 추적을 해제하세요. **이미 원격 저장소에 공개된 API 키는 반드시 폐기하고 재발급**해야 합니다.
- `requirements.txt`에는 검증된 Python 패키지 버전을 명시하면 팀원의 실행 환경을 재현하기 쉽습니다.

---

### 실행 요약

```bash
python -m pip install -r requirements.txt
python main.py data/planning.json
python figma_runner.py
```

> 위 요약은 **Codex CLI 설치 및 로그인, Figma MCP OAuth 연결, `.env` 설정, `FIGMA_URL` 지정, 가상환경 활성화가 모두 완료된 상태**를 전제로 합니다.
