"""
A01에서 전달받을 데이터와 A02가 생성할 screen_plan.json의 스키마 정의

라이브러리 설치
python -m pip install pydantic
"""
from typing import Any
from pydantic import BaseModel, ConfigDict, Field


class Feature(BaseModel):
    id: str | None = None
    name: str
    behavior_and_value: str | None = None
    priority: str | None = None
    included_in_release: bool | None = None
    done_criteria: list[str] = Field(default_factory=list)


class ReleaseScope(BaseModel):
    features: list[Feature] = Field(default_factory=list)
    supported_platforms: list[str] = Field(default_factory=list)
    project_type: str | None = None


class UXScreen(BaseModel):
    id: str | None = None
    screen_or_step: str
    displayed_information: str | None = None
    input_fields: list[Any] = Field(default_factory=list)
    user_action: str | None = None
    next_screen: str | None = None
    related_features: list[str] = Field(default_factory=list)


class UserExperience(BaseModel):
    flow: str | None = None
    screens: list[UXScreen] = Field(default_factory=list)
    design_links: list[Any] = Field(default_factory=list)


class PolicyException(BaseModel):
    condition: str | None = None
    system_response: str | None = None
    user_guidance: str | None = None
    recovery: str | None = None
    related_features: list[str] = Field(default_factory=list)


class Policies(BaseModel):
    eligibility_and_permissions: str | None = None
    behavior_and_state_changes: str | None = None
    exceptions: list[PolicyException] = Field(default_factory=list)


class PlanningInput(BaseModel):
    # 실제 A01 JSON에는 project_id가 없으므로
    # 프로젝트 ID는 최종적으로 제어기가 별도로 전달해야 한다.
    project_id: str | None = None

    overview: dict[str, Any]
    user_problem: dict[str, Any]
    goals: dict[str, Any]
    release_scope: ReleaseScope
    user_experience: UserExperience
    policies: Policies
    data_rules: dict[str, Any]
    release_plan: dict[str, Any]

    model_config = ConfigDict(extra="forbid")


class ScreenElement(BaseModel):
    """
    화면에 배치할 개별 UI 요소 모델
    (요소 유형, 표시 문구, 적용 상태, 사용자 행동 정의)
    """
    type: str
    label: str
    state: str | None = None
    action: str | None = None


# A02 출력 구조
class Screen(BaseModel):
    """
    개별 화면의 구성 정보 모델
    (화면 ID, 관련 기능, UI 요소, 상태, 화면 이동 관계와 
    Figma 디자인 참조 정보 관리)
    """
    screen_id: str
    name: str
    related_features: list[str]
    elements: list[ScreenElement] = Field(default_factory=list)
    states: list[str] = Field(default_factory=list)
    next_screens: list[str] = Field(default_factory=list)
    design_ref: dict[str, str] | None = None


# A02가 생성한 전체 화면 계획
class ScreenPlan(BaseModel):
    """
    A02에서 생성하는 전체 화면 계획 데이터 모델
    Figma 생성 및 A03 개발 단계에 전달되는 데이터 구조 정의
    """
    project_id: str
    supported_platforms: list[str] = Field(default_factory=list)
    screens: list[Screen]