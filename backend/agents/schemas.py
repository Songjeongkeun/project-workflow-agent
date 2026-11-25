"""에이전트 입출력 모양 (팀원이 에이전트를 만들 때 맞출 약속).

서버는 에이전트를 dict 하나를 넘겨 부르고 dict 하나를 돌려받는다. 모양은 아래 TypedDict와 같다.
LangGraph로 만든다면 그래프의 마지막 상태에서 이 키들을 꺼내 돌려주면 된다.

규칙
- 에이전트는 DB를 직접 건드리지 않는다. 받은 입력으로 결과만 돌려주고, 저장·버전·승인은 서버가 한다.
- 실패하면 예외를 던진다 (raise Exception("이유")). 서버가 실행을 실패로 기록하고 [다시 시도]를 보여준다.
- usage(토큰 사용량)는 없어도 된다.
"""

from typing import Literal, TypedDict


class Usage(TypedDict, total=False):
    input_tokens: int
    output_tokens: int


# ── A01 기획: plan(PlanningInput) -> PlanningOutput ──────────────


class SourceMessage(TypedDict):
    id: int
    sender: Literal["user", "agent"]
    kind: str  # description | answer | revision | agent
    body: str
    answers_question: str | None  # 답변이면 어떤 질문에 대한 답인지


class PlanningInput(TypedDict):
    project_id: int
    messages: list[SourceMessage]  # 대화 전체
    previous: dict | None  # 이전 기획 묶음 {"planning", "evidence", "questions", "criteria"}. 기능·조건 ID를 유지할 때 쓴다
    change_request: str | None  # 확정 뒤 변경 요청이면 그 내용


class PlanningQuestion(TypedDict):
    body: str
    field_path: str | None
    required: bool  # True면 답하기 전에는 G1 확정을 못 한다


class PlanningOutput(TypedDict, total=False):
    planning: dict  # 요구사항 JSON (overview, user_problem, release_scope, user_experience)
    evidence: list[dict]  # [{field_path, message_id, quote}]
    brief_markdown: str  # 기획서 본문
    criteria: list[dict]  # 완료 조건 [{id, feature_id, kind, condition, source, origin, required}]
    questions: list[PlanningQuestion]
    reply: str  # 대화 화면에 보일 에이전트 답변
    has_ui: bool  # 화면이 있는 서비스인지 (없으면 A02를 건너뛴다)
    usage: Usage


# ── A02~A05 공통 입력 ─────────────────────────────────────────────


class RunContext(TypedDict):
    project_id: int
    run_id: int
    planning: dict  # 확정한 요구사항 JSON
    criteria: list[dict]  # 확정한 완료 조건
    screen_plan: dict | None
    tech_spec: dict | None
    candidate_sha: str | None
    feedback: list[str]  # 반려 의견, 실패한 완료 조건 등 고쳐야 할 것
    target: dict  # 저장소, 브랜치, 테스트 환경, 주소(url)
    redeploy: bool  # 재배포면 True. A05는 게시(push)를 건너뛰고 배포(P03)부터 한다


# ── A02 화면 설계: propose_scope(ctx) -> FigmaScope, design(ctx) -> DesignOutput ──


class FigmaScope(TypedDict):
    """A02가 Figma에 쓰기 전에 디자인 담당에게 보여줄 작업 범위."""

    figma_file: str
    page: str
    frames: list[str]
    changes: str


class DesignOutput(TypedDict, total=False):
    screen_plan: dict
    screens: list[dict]  # [{id, name, figma_url}]
    check: dict  # {passed: bool, missing: [str]}
    summary: str
    usage: Usage


# ── A03 개발: develop(ctx) -> DevelopOutput ─────────────────────────


class DevelopOutput(TypedDict, total=False):
    tech_spec: dict
    candidate_sha: str
    out_of_plan: list[dict]  # 기술 명세 목록 밖에서 바뀐 파일 [{path, reason}]. 있으면 G3를 거친다
    summary: str
    usage: Usage


# ── A04 테스트: test(ctx, phase, sha, url) -> TestOutput ────────────


class TestOutcome(TypedDict, total=False):
    ac_id: str
    result: Literal["pass", "fail", "not_run"]
    evidence_path: str | None
    reason: str | None


class TestOutput(TypedDict, total=False):
    sha: str
    results: list[TestOutcome]
    summary: str
    usage: Usage


# ── A05 배포: deploy(ctx, sha) -> DeployOutput ─────────────────────


class DeployOutput(TypedDict, total=False):
    pushed_sha: str
    deployment_id: str
    deployment_url: str
    summary: str
    usage: Usage
