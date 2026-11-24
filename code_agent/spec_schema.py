"""A03 기술 명세 스키마와 공통 점검 축.

공통 점검 축(AXES)은 프롬프트 생성과 코드 검증이 같은 목록을 쓰도록 여기 한 곳에서만 정의한다.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# (id, 이름, 점검 질문)
AXES: list[tuple[str, str, str]] = [
    ("duplicate", "중복 방지", "같은 요청이 두 번 오거나 같은 데이터가 중복될 수 있는가"),
    ("state", "상태 전이", "상태가 있다면 허용되는 전환과 금지되는 전환은 무엇인가"),
    ("permission", "권한과 열람 범위", "누가 만들고, 보고, 바꿀 수 있는가"),
    ("period", "기간·만료", "보관 기간, 적용 기간, 만료가 있는가"),
    ("undo", "취소·정정·되돌리기", "이미 처리한 것을 취소하거나 고칠 수 있는가"),
    ("failure", "실패와 재시도", "저장 실패, 외부 호출 실패, 일부 실패 시 어떻게 되는가"),
    ("history", "이력·기록", "남겨야 하는 변경 기록이 있는가"),
    ("calculation", "계산·집계 기준", "계산식, 반올림, 집계 기간이 있는가"),
    ("limit", "한도·수량", "최대·최소, 재고, 사용 한도가 있는가"),
]
AXIS_IDS = [a[0] for a in AXES]

AxisId = Literal[
    "duplicate", "state", "permission", "period", "undo", "failure", "history", "calculation", "limit"
]


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Component(_Base):
    name: str
    role: str
    feature_ids: list[str] = Field(default_factory=list)


class DataField(_Base):
    name: str
    data_type: str
    is_required: bool
    rule: str | None = None


class Entity(_Base):
    name: str
    fields: list[DataField] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    feature_ids: list[str] = Field(default_factory=list)


class ApiError(_Base):
    condition: str
    status: int
    message: str


class Api(_Base):
    method: str
    path: str
    purpose: str
    request: str | None = None   # 요청 본문/파라미터 설명 (자유 JSON은 strict 모드에서 불가해 문자열로 둠)
    response: str | None = None
    errors: list[ApiError] = Field(default_factory=list)
    feature_ids: list[str] = Field(default_factory=list)
    linked_ac_ids: list[str] = Field(default_factory=list)


class Transition(_Base):
    from_state: str
    to_state: str
    trigger: str


class StateMachine(_Base):
    entity: str
    states: list[str] = Field(default_factory=list)
    transitions: list[Transition] = Field(default_factory=list)
    feature_ids: list[str] = Field(default_factory=list)


class ChecklistResult(_Base):
    feature_id: str
    axis: AxisId
    applies: bool
    decision: str  # 적용: 어떻게 설계했는가 / 비적용: 이유 한 줄
    linked_ac_ids: list[str] = Field(default_factory=list)


class OpenQuestion(_Base):
    feature_id: str
    axis: AxisId
    question: str
    proposal: str | None = None


class TechSpec(_Base):
    components: list[Component] = Field(default_factory=list)
    data_entities: list[Entity] = Field(default_factory=list)
    apis: list[Api] = Field(default_factory=list)
    state_machines: list[StateMachine] = Field(default_factory=list)
    checklist_results: list[ChecklistResult] = Field(default_factory=list)
    open_questions: list[OpenQuestion] = Field(default_factory=list)
