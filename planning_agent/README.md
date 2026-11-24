# Planning Agent

자연어 또는 TXT 문서에서 요구사항을 추출하고, 검증·수정·보완하여 **구조화된 요구사항 JSON과 기획서(Word/PDF)를 생성하는 LangGraph 기반 AI Agent**입니다.

## 1. 프로젝트 개요 및 실행 환경

### 1.1 프로젝트 개요

Planning Agent는 사용자가 입력한 자연어 또는 기획 문서를 분석해 소프트웨어 개발에 필요한 요구사항을 정리하는 AI Agent입니다.

추출된 요구사항을 검증하고, 불명확하거나 누락된 내용을 질문하며, 사용자의 수정 요청과 AI 보완 제안을 반영할 수 있습니다.

최종적으로 요구사항 JSON을 생성하고, 이를 기반으로 Word/PDF 형식의 기획서 초안을 자동 작성합니다.

**주요 기능**

- 자연어 및 TXT 파일 기반 요구사항 추출
- 요구사항 JSON 생성 및 구조·의미 검증
- 요구사항 추가·수정·삭제
- 누락 정보 확인 및 추가 질문 관리
- AI 기반 요구사항 보완 제안과 선택적 승인·반려
- 자연어 및 CLI 명령 처리
- 여러 작업이 포함된 복합 자연어 요청 순차 실행
- 요구사항 JSON 기반 Word/PDF 기획서 자동 생성
- 기획서 승인 상태 및 변경 이력 관리
- 프로젝트별 State 및 진단 로그 관리

**처리 흐름**

```text
사용자 입력 (자연어 / TXT)
          ↓
    LangGraph Router
          ↓
  요구사항 추출 / 수정 / 명령 처리
          ↓
    검증 및 오류 복구
          ↓
     누락 정보 확인
          ↓
     요구사항 JSON 저장
          ↓
    Word / PDF 기획서 생성
          ↓
      사용자 검토·승인
```

LangGraph는 총 14개의 업무 노드로 구성되어 있으며, 요청 유형에 따라 필요한 노드를 선택적으로 실행합니다.

조회나 AI 제안만 필요한 요청은 불필요한 요구사항 저장 및 문서 생성 과정을 거치지 않습니다.

### 1.2 기술 스택

| 구분 | 기술 |
|---|---|
| 개발 언어 | Python 3.12 |
| Agent 워크플로 | LangGraph |
| AI 처리 | LLM API |
| 데이터 형식 | JSON |
| Word 문서 생성 | python-docx |
| PDF 변환 | LibreOffice |
| 실행 환경 | Windows / CLI |

실제 Python 패키지 의존성은 `requirements.txt`를 참고합니다.

### 1.3 설치 방법

**② 가상환경 생성 및 활성화**

Windows CMD 기준:

```bat
python -m venv .venv
.venv\Scripts\activate
```

**③ 패키지 설치**

```bat
pip install -r requirements.txt
```

**④ LibreOffice 설치**

Word 문서를 PDF로 변환하기 위해 LibreOffice가 필요합니다.

- 공식 사이트: https://www.libreoffice.org/
- 설치 후 `soffice` 실행 파일이 사용 가능한지 확인합니다.
- 설치 환경에 따라 LibreOffice 경로 설정이 필요할 수 있습니다.

### 1.4 환경변수 설정

프로젝트 최상위 경로에 `.env` 파일을 생성합니다.

LLM API를 사용하려면 해당 API의 인증 키와 프로젝트에서 요구하는 환경변수를 설정해야 합니다.

```text
planning_agent/
├── .env
├── main.py
├── planning_agent.py
└── planning_graph.py
```

환경변수 이름과 설정값은 실제 코드에서 사용하는 항목을 기준으로 작성합니다.

**주의사항**

- `.env`에는 실제 API 키 등 민감한 정보가 포함될 수 있습니다.
- `.env` 파일은 Git에 업로드하지 않습니다.
- 팀원에게는 실제 인증 키 대신 필요한 환경변수 목록을 공유합니다.

### 1.5 실행 방법

**① LangGraph 구성 확인**

```bat
python main.py --check-graph
```

**② 신규 프로젝트 실행**

```bat
python main.py --project P001 --diagnostics
```

실행 후 `사용자>` 입력창에 자연어로 요구사항을 입력합니다.

**③ TXT 파일 기반 실행**

```bat
python main.py --project P002 --file "input.txt" --diagnostics
```

**④ 기존 프로젝트 재실행**

```bat
python main.py --project P001 --diagnostics
```

동일한 프로젝트 ID를 사용하면 저장된 프로젝트 데이터를 기반으로 작업을 이어갈 수 있습니다.

**⑤ 자동 테스트**

```bat
python -m unittest discover -s tests -p "test_*.py" -v
```

2026년 11월 기준 자동 회귀 테스트 64개 통과를 확인했습니다.

실제 LLM 기반 신규 프로젝트 요구사항 추출 및 JSON·Word·PDF 생성도 확인했습니다. 단, 모든 오류·재시작 상황에 대한 실환경 검증이 완료된 것은 아닙니다.

---

## 2. 프로젝트 폴더 및 파일 구조

### 2.1 디렉터리 구조 및 파일별 역할

```text
planning_agent/
│
├── core/ - Agent 공통 처리 및 요구사항 데이터 관리
│   ├── __init__.py - Python 패키지 초기화
│   ├── action_routing.py - CLI·자연어 의도 분류 및 복합 작업 계획
│   ├── command_handlers.py - 조회·관리·AI 제안 등 명령 처리
│   ├── object_contract.py - 데이터 객체 구조 및 유효성 검증
│   ├── planning_logic.py - 요구사항 처리 공통 로직
│   └── planning_schema.py - 요구사항 JSON 스키마 및 자료 구조 정의
│
├── extraction/ - 초기 요구사항 추출 및 품질 검증
│   ├── __init__.py - Python 패키지 초기화
│   ├── field_audit.py - 원문 대비 추출 필드 누락 점검
│   ├── initial_quality.py - 초기 추출 결과의 품질 검사
│   ├── response_normalization.py - LLM 응답 형식 정규화
│   └── structured_initial.py - 자연어 입력의 구조화된 요구사항 추출
│
├── questions/ - 추가 질문 생성 및 관리
│   ├── __init__.py - Python 패키지 초기화
│   ├── decision_flow.py - 질문 답변 및 결정 처리 흐름
│   ├── question_consistency.py - 질문 간 일관성 검증
│   ├── question_policy.py - 질문 생성 및 우선순위 정책
│   ├── question_scope.py - 질문 대상과 범위 관리
│   ├── required_check.py - 필수 요구사항 누락 확인
│   └── simple_questions.py - 추가 질문 생성 관련 처리
│
├── proposals/ - AI 기반 요구사항 보완 제안
│   ├── __init__.py - Python 패키지 초기화
│   └── proposal_flow.py - AI 제안 생성·조회·승인·반려 처리
│
├── updates/ - 기존 요구사항 변경 및 검증
│   ├── __init__.py - Python 패키지 초기화
│   ├── answer_flow.py - 사용자 답변 반영
│   ├── release_dependencies.py - 출시 범위 및 기능 간 의존성 검증
│   ├── scope_consistency.py - 요구사항 범위의 일관성 검증
│   └── turn_repair.py - 수정 요청 처리 중 오류 복구
│
├── document/ - 기획서 생성 및 승인 관리
│   ├── __init__.py - Python 패키지 초기화
│   ├── document_flow.py - Word/PDF 생성 흐름 및 승인 상태 관리
│   └── planning_word.py - 요구사항 JSON 기반 Word 기획서 생성
│
├── storage/ - 데이터 저장 및 입력 관리
│   ├── __init__.py - Python 패키지 초기화
│   ├── evidence_store.py - 원문 근거 데이터 저장 및 관리
│   ├── file_reader.py - 입력 파일 읽기
│   └── request_state.py - 요청 및 처리 상태 관리
│
├── templates/ - 기획서 생성용 템플릿
│   ├── planning-template.json - 기획서 JSON 기본 템플릿
│   └── planning_form.pdf - 기획서 PDF 양식 파일
│
├── tests/ - 자동 테스트 및 회귀 검증
│   ├── __init__.py - Python 패키지 초기화
│   ├── test_document_integration.py - 문서 생성·승인 통합 테스트
│   ├── test_question_tier_regression.py - 질문 우선순위 회귀 테스트
│   ├── test_semantic_repair_scope.py - 의미 교정 범위 검증 테스트
│   └── test_graph_workflow_regression.py - LangGraph 실행 및 복합 작업 회귀 테스트
│
├── projects/ - 프로젝트별 State 및 입력 이력 저장
├── outputs/ - 생성된 요구사항 JSON·Word·PDF 저장
├── diagnostics/ - 실행 진단 및 오류 로그 저장
│
├── main.py - CLI 실행 진입점 및 사용자 입출력 처리
├── planning_agent.py - Planning Agent 핵심 처리 로직
├── planning_graph.py - LangGraph 14개 노드 및 실행 흐름 구성
├── requirements.txt - Python 패키지 의존성 목록
├── README.md - 프로젝트 소개 및 실행 안내
├── .gitignore - Git 추적 제외 파일 및 폴더 설정
└── .env - API 키 및 실행 환경변수 설정 (Git 업로드 제외)
```

### 2.2 주요 파일 실행 관계

```text
main.py
  └── planning_graph.py
        ├── core/          - 명령 분류 및 공통 처리
        ├── extraction/    - 신규 요구사항 추출
        ├── updates/       - 요구사항 수정
        ├── questions/     - 누락 질문 관리
        ├── proposals/     - AI 보완 제안
        ├── storage/       - State 및 데이터 관리
        └── document/      - Word/PDF 생성 및 승인
```

LangGraph는 14개의 업무 노드를 통해 요청을 처리하며, 사용자 요청에 따라 필요한 기능을 선택적으로 실행합니다.

요구사항에 실제 변경이 발생한 경우에는 검증 후 JSON을 저장하고 Word/PDF를 생성합니다. 조회 또는 AI 제안만 요청한 경우에는 불필요한 문서 재생성을 수행하지 않습니다.

### 2.3 실행 데이터 관리

`projects/`, `outputs/`, `diagnostics/`는 Agent 실행 과정에서 생성되는 데이터 저장 폴더입니다.

-> 현재 git에 제외시켜 업로드 된 상태입니다.

### 2.4 주요 LangGraph 노드

현재 Planning Agent는 14개의 업무 노드를 사용합니다.

| 노드 | 역할 |
|---|---|
| `route` | 자연어·CLI 명령 분류 및 작업 계획 |
| `extract` | 신규 요구사항 추출 |
| `interpret` | 기존 요구사항 수정 요청 해석 |
| `command` | 조회·관리·AI 제안 등의 명령 실행 |
| `candidate_validation` | 추출·수정 결과 검증 |
| `source_repair` | 원문 기반 추출 오류 수정 |
| `review_recovery` | 검토 및 검증 오류 복구 |
| `source_stop` | 복구 실패 시 안전한 중단 |
| `completeness` | 요구사항 누락 및 완전성 확인 |
| `question` | 추가 질문 생성·관리 |
| `save_requirements` | 검증된 요구사항 JSON 저장 |
| `generate_document` | Word/PDF 기획서 생성 |
| `document_review` | 문서 승인 및 상태 검증 |
| `finish` | 전체 작업 결과 정리 |

노드 간 연결은 사용자 요청의 유형과 처리 결과에 따라 달라집니다.

---

## 3. 향후 계획

현재 Planning Agent는 요구사항 추출부터 기획서 생성·승인까지의 핵심 기능을 구현한 **1차 개발 완료 버전**입니다.

향후에는 기능 안정성을 개선하고, 팀 프로젝트의 다른 전문 Agent와 연결할 예정입니다.

### 3.1 Planning Agent 안정성 개선

- 복합 요청 중간 실패 및 재실행 상황 검증
- AI 보완 제안의 전체·일부 승인 안전성 강화
- 모호한 자연어 요청 및 예외 처리 검증 확대
- 요구사항 추출의 의미적 정확성 및 누락 검증 개선
- 추가 질문과 보류 정책의 사용성 개선

### 3.2 Supervisor Agent 연동

향후 상위 Supervisor Agent가 Planning Agent를 호출하고 작업 결과를 전달받을 수 있도록 연동할 예정입니다.

주요 연동 목표는 다음과 같습니다.

- Supervisor를 통한 기획 작업 요청
- Planning Agent 실행 결과 및 상태 전달
- 승인된 요구사항 JSON·기획서 산출물 공유
- 다른 Agent와의 작업 순서 및 승인 흐름 연결

구체적인 호출 방식과 데이터 전달 규격은 팀 공통 아키텍처 확정 후 결정합니다.

### 3.3 다른 전문 Agent와 연계

향후 프로젝트의 전체 처리 흐름은 다음과 같이 확장할 계획입니다.

```text
                  사용자
                    │
                    ▼
              Supervisor Agent
                    │
          ┌─────────┼─────────┐
          ▼         ▼         ▼
      Planning    Design    Coding
       Agent      Agent     Agent
          │         │         │
          └─────────┼─────────┘
                    ▼
             통합 작업 결과
```

Planning Agent에서 생성한 요구사항과 기획서를 후속 디자인·개발 작업의 입력 자료로 활용하는 것을 목표로 합니다.

### 3.4 사용자 인터페이스

현재는 CLI 기반으로 실행하고 있으며, 향후 웹 기반 사용자 인터페이스와 연결할 예정입니다.

일반 사용자가 별도의 CLI 명령어를 학습하지 않아도 자연어 입력과 승인 UI를 통해 이용할 수 있도록 확장할 계획입니다.

---

**개발 상태:** Planning Agent 1차 구현 및 주요 통합 검증 완료

**현재 범위:** 요구사항 추출·수정·검증, AI 보완 제안, JSON·Word·PDF 생성 및 기획서 승인

**향후 범위:** 안정성 개선, Supervisor 연동, 전문 Agent 간 산출물 공유 및 웹 UI 통합


**현재 기존에 올라와있던 계발 2단계 planning_word.py 파일은 수정 후 planning_agent/docuument/ 에 들어있습니다.**