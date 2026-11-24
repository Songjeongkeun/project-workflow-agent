"""기획서 JSON 스키마 (planning_template.json과 동일한 구조).

모든 값은 기본 None / 빈 리스트다. 사용자가 말하지 않은 값은 추측하지 않고
None으로 남겨, 이후 단계에서 "빠진 정보"로 질문할 수 있게 한다.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Overview(_Base):
    project_name: str | None = None
    project_type: str | None = None
    team_name: str | None = None
    team_members: list[str] = Field(default_factory=list)
    written_on: str | None = None  # YYYY-MM-DD
    summary: str | None = None


class UserProblem(_Base):
    target_users: str | None = None
    usage_context: str | None = None
    current_method: str | None = None
    pain_points: str | None = None
    evidence: list[str] = Field(default_factory=list)  # 근거가 된 사용자 발언 원문


class Metric(_Base):
    name: str | None = None
    current_value: str | None = None
    target_value: str | None = None
    measurement_method: str | None = None
    measurement_timing: str | None = None


class Goals(_Base):
    expected_change: str | None = None
    metrics: list[Metric] = Field(default_factory=list)


class Feature(_Base):
    id: str | None = None  # 문서에 FEAT-001 같은 ID가 있으면 그대로
    name: str | None = None
    behavior_and_value: str | None = None
    priority: str | None = None
    included_in_release: bool | None = None
    completion_criteria: str | None = None  # 기능별 완료 기준


class TechItem(_Base):
    area: str | None = None
    technology: str | None = None
    reason: str | None = None
    source: str | None = None


class ReleaseScope(_Base):
    features: list[Feature] = Field(default_factory=list)
    out_of_scope: list[str] = Field(default_factory=list)
    supported_platforms: list[str] = Field(default_factory=list)
    service_components_and_integrations: str | None = None
    access_install_and_run: str | None = None
    tech_stack: list[TechItem] = Field(default_factory=list)


class Screen(_Base):
    id: str | None = None  # 화면 ID (SCR-001 등)
    screen_or_step: str | None = None
    displayed_information: str | None = None
    input_fields: list[str] = Field(default_factory=list)
    user_action: str | None = None
    next_screen: str | None = None
    related_feature_ids: list[str] = Field(default_factory=list)


class UserExperience(_Base):
    flow: str | None = None
    screens: list[Screen] = Field(default_factory=list)
    design_links: list[str] = Field(default_factory=list)


class ExceptionCase(_Base):
    condition: str | None = None
    system_response: str | None = None
    user_guidance: str | None = None
    recovery: str | None = None
    related_feature_ids: list[str] = Field(default_factory=list)


class Policies(_Base):
    eligibility_and_permissions: str | None = None
    behavior_and_state_changes: str | None = None
    exceptions: list[ExceptionCase] = Field(default_factory=list)
    open_considerations: list[str] = Field(default_factory=list)


class DataField(_Base):
    name: str | None = None
    purpose: str | None = None
    source: str | None = None
    format_and_structure: str | None = None
    required: bool | None = None
    related_feature_ids: list[str] = Field(default_factory=list)


class DataRules(_Base):
    fields: list[DataField] = Field(default_factory=list)
    storage_and_management: str | None = None
    input_validation: str | None = None
    error_and_integrity_handling: str | None = None


class Milestone(_Base):
    stage: str | None = None
    schedule: str | None = None
    owner: str | None = None
    deliverable: str | None = None
    completion_criteria: str | None = None


class Reference(_Base):
    name: str | None = None
    definition: str | None = None
    url: str | None = None


class ReleasePlan(_Base):
    milestones: list[Milestone] = Field(default_factory=list)
    pre_launch_checks: list[str] = Field(default_factory=list)
    post_launch_checks: list[str] = Field(default_factory=list)
    glossary_and_references: list[Reference] = Field(default_factory=list)


class PlanningDoc(_Base):
    overview: Overview = Field(default_factory=Overview)
    user_problem: UserProblem = Field(default_factory=UserProblem)
    goals: Goals = Field(default_factory=Goals)
    release_scope: ReleaseScope = Field(default_factory=ReleaseScope)
    user_experience: UserExperience = Field(default_factory=UserExperience)
    policies: Policies = Field(default_factory=Policies)
    data_rules: DataRules = Field(default_factory=DataRules)
    release_plan: ReleasePlan = Field(default_factory=ReleasePlan)


def strict_json_schema(model_cls: type[BaseModel] = PlanningDoc) -> dict[str, Any]:
    """OpenAI Structured Outputs(strict)용 JSON Schema.

    strict 모드는 모든 키가 required여야 하고 default/title 같은 키워드를 허용하지 않는다.
    값이 없을 수 있는 필드는 이미 `anyOf: [타입, null]`이라 null로 표현된다.
    """
    schema = model_cls.model_json_schema()

    def fix(node: Any) -> None:
        if isinstance(node, dict):
            node.pop("default", None)
            node.pop("title", None)  # 스키마 키워드 title (필드 이름 title은 properties 안에서 보존)
            props = node.get("properties")
            if isinstance(props, dict):
                node["required"] = list(props)
                node["additionalProperties"] = False
            for k, v in node.items():
                if k == "properties" and isinstance(v, dict):
                    for sub in v.values():  # 필드 이름은 건드리지 않고 각 필드의 스키마만 정리
                        fix(sub)
                else:
                    fix(v)
        elif isinstance(node, list):
            for v in node:
                fix(v)

    fix(schema)
    return schema


def find_missing(data: Any, path: str = "") -> list[str]:
    """값이 비어 있는(None) 항목의 경로를 모은다. 빈 리스트는 '해당 없음/미입력'으로 보고 경로만 남긴다."""
    missing: list[str] = []
    if isinstance(data, dict):
        for key, value in data.items():
            missing += find_missing(value, f"{path}.{key}" if path else key)
    elif isinstance(data, list):
        if not data:
            missing.append(f"{path}[]")
        for i, item in enumerate(data):
            missing += find_missing(item, f"{path}[{i}]")
    elif data is None:
        missing.append(path)
    return missing


def diff_docs(old: Any, new: Any, path: str = "") -> list[tuple[str, Any, Any]]:
    """두 기획서의 변경점을 (경로, 이전값, 새값) 목록으로 돌려준다. 순서는 새 문서 기준."""
    out: list[tuple[str, Any, Any]] = []
    if isinstance(old, dict) and isinstance(new, dict):
        for k in list(new) + [k for k in old if k not in new]:
            out += diff_docs(old.get(k), new.get(k), f"{path}.{k}" if path else k)
    elif isinstance(old, list) and isinstance(new, list):
        for i in range(max(len(old), len(new))):
            o = old[i] if i < len(old) else None
            n = new[i] if i < len(new) else None
            out += diff_docs(o, n, f"{path}[{i}]")
    elif old != new:
        out.append((path, old, new))
    return out
