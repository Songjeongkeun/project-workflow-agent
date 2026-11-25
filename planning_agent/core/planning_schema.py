"""내부 상태 모델과 공개 JSON 템플릿 및 유효성 검사를 정의한다."""

from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import json
import re
import unicodedata
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, StrictBool, StrictInt, StrictStr

Value = StrictStr | StrictBool | list[StrictStr] | None
REVIEW_VERSION = '18.20'
Action = Literal[
    'record', 'extend', 'replace', 'clear', 'remove_item',
    'question', 'clarify', 'answer',
]


class Proposal(BaseModel):
    model_config = ConfigDict(extra='forbid')
    _reviewed_text: str | None = PrivateAttr(default=None)
    _reviewed_list: bool = PrivateAttr(default=False)
    _prior_evidence: list['Evidence'] = PrivateAttr(default_factory=list)
    _semantic_binding: bool = PrivateAttr(default=False)

    action: Action
    path: str | None = None
    item: str | None = None
    value: StrictBool | list[StrictStr] | None = None
    quotes: list[str] = Field(default_factory=list)
    question: str | None = None
    reply_to: str | None = None
    choice: int | None = None
    previous_text: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)


class Extraction(BaseModel):
    model_config = ConfigDict(extra='forbid')

    operations: list[Proposal]


class FeatureReview(BaseModel):
    model_config = ConfigDict(extra='forbid')

    operation_index: StrictInt | None
    item: StrictStr
    quotes: list[StrictStr]


class ExceptionReview(BaseModel):
    model_config = ConfigDict(extra='forbid')

    condition: StrictStr
    system_response: StrictStr | None
    user_guidance: StrictStr | None
    recovery: StrictStr | None


class ReviewedRecord(BaseModel):
    model_config = ConfigDict(extra='forbid')

    path: StrictStr
    item: StrictStr | None
    value: Value
    quotes: list[StrictStr]
    source_refs: list[StrictInt] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)


class DraftCorrection(BaseModel):
    model_config = ConfigDict(extra='forbid')

    operation_indices: list[StrictInt]
    records: list[ReviewedRecord]


class SemanticBinding(BaseModel):
    model_config = ConfigDict(extra='forbid')
    operation_index: StrictInt
    quotes: list[StrictStr] = Field(default_factory=list)
    source_refs: list[StrictInt] = Field(default_factory=list)
    reason: StrictStr


class CoverageTarget(BaseModel):
    model_config = ConfigDict(extra='forbid')
    path: StrictStr
    item: StrictStr | None = None


class RecoveryIssue(BaseModel):
    model_config = ConfigDict(extra='forbid')
    stage: str
    code: str
    target: str | None = None
    retryable: bool = True
    detail: str = ''


class CoverageFact(BaseModel):
    model_config = ConfigDict(extra='forbid')
    description: StrictStr
    disposition: Literal['represented', 'not_requirement', 'undecided', 'unresolved']
    targets: list[CoverageTarget] = Field(default_factory=list)
    reason: StrictStr = ''


class SourceCoverage(BaseModel):
    model_config = ConfigDict(extra='forbid')
    source_ref: StrictInt
    facts: list[CoverageFact]


class DataFieldCheck(BaseModel):
    model_config = ConfigDict(extra='forbid')
    field: StrictStr
    status: Literal['stated', 'not_stated', 'cleared', 'uncertain']
    value: Value
    quotes: list[StrictStr] = Field(default_factory=list)
    source_refs: list[StrictInt] = Field(default_factory=list)
    reason: StrictStr


class DataItemReview(BaseModel):
    model_config = ConfigDict(extra='forbid')
    item: StrictStr
    checks: list[DataFieldCheck]


class ListItemReview(BaseModel):
    model_config = ConfigDict(extra='forbid')
    input_index: StrictInt
    status: Literal['preserved', 'unsupported', 'removed']
    output_indices: list[StrictInt]
    reason: StrictStr


class PlatformAssessment(BaseModel):
    model_config = ConfigDict(extra='forbid')
    status: Literal['stated','not_stated','uncertain']
    platforms: list[StrictStr]
    quotes: list[StrictStr]
    reason: StrictStr


class FieldReview(BaseModel):
    model_config = ConfigDict(extra='forbid')
    target: CoverageTarget
    status: Literal['stated', 'not_stated', 'cleared', 'uncertain']
    value: Value
    quotes: list[StrictStr] = Field(default_factory=list)
    source_refs: list[StrictInt] = Field(default_factory=list)
    reason: StrictStr
    items: list[ListItemReview] = Field(default_factory=list)


class SourceReview(BaseModel):
    model_config = ConfigDict(extra='forbid')

    feature_changes: list[FeatureReview]
    exceptions: list[ExceptionReview]
    corrections: list[DraftCorrection] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    semantic_bindings: list[SemanticBinding] = Field(default_factory=list)
    coverage: list[SourceCoverage] = Field(default_factory=list)
    data_reviews: list[DataItemReview] = Field(default_factory=list)
    field_reviews: list[FieldReview] = Field(default_factory=list)


class Evidence(BaseModel):
    source: str
    digest: str
    start: int
    end: int
    quote: str


class Target(BaseModel, frozen=True):
    path: str
    item: str | None = None

    def key(self) -> str:
        """필드와 행을 구분하는 저장 키를 생성한다."""
        return f'{self.path}@{self.item}' if self.item else self.path


class Cell(BaseModel):
    value: Value
    evidence: list[Evidence]


class Row(BaseModel):
    path: str
    label: str


class Question(BaseModel):
    id: str
    kind: Literal['missing', 'conflict', 'clarify']
    target: Target | None = None
    answer_hint: Target | None = None
    text: str | None = None
    options: list[Cell] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)


class RequiredAnswerRecord(BaseModel):
    model_config = ConfigDict(extra='forbid')
    question: Question
    token: str
    status: Literal['answered', 'partial', 'deferred', 'cancelled', 'reopened', 'superseded']
    raw_answer: str = ''
    reason: str | None = None


class TurnOperation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    action: Literal['record', 'extend', 'replace', 'clear', 'remove_item', 'answer']
    path: str
    item: str | None = None
    value: Value
    quotes: list[str]
    reply_to: str | None = None
    previous_text: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)


class RequestDecision(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: str
    status: Literal['resolved', 'withdrawn']
    reason: str


class TurnQuestionDecision(BaseModel):
    model_config = ConfigDict(extra='forbid')
    question_id: str
    status: Literal['answered', 'partial', 'deferred', 'reopened', 'cancelled']
    target: Target | None = None


class TurnPlan(BaseModel):
    model_config = ConfigDict(extra='forbid')
    intent: Literal['requirements', 'answer', 'mixed', 'clarify', 'defer', 'proposal_revision']
    operations: list[TurnOperation]
    question_decisions: list[TurnQuestionDecision] = Field(default_factory=list)
    clarification: str | None = None
    clarification_target: Target | None = None
    proposal_revision: 'ProposalRevision | None' = None
    request_decisions: list[RequestDecision] = Field(default_factory=list)


class TurnRepairReplacement(BaseModel):
    model_config = ConfigDict(extra='forbid')
    operation_index: int = Field(ge=0, strict=True)
    operation: TurnOperation


class TurnRepair(BaseModel):
    """A patch, never a replacement for the full accepted plan."""
    model_config = ConfigDict(extra='forbid')
    replacements: list[TurnRepairReplacement] = Field(default_factory=list)
    additions: list[TurnOperation] = Field(default_factory=list)
    explanation: str = ''


class RelatedQuestionResolution(BaseModel):
    """Independent reviewer approval to close another question answered by this turn."""
    model_config = ConfigDict(extra='forbid')
    question_id: StrictStr
    reason: StrictStr
    quote: StrictStr


class TurnReview(BaseModel):
    model_config = ConfigDict(extra='forbid')
    valid: bool
    issues: list[str]
    reviewed_targets: list[str] = Field(default_factory=list)
    coverage: list[SourceCoverage] = Field(default_factory=list)
    data_reviews: list[DataItemReview] = Field(default_factory=list)
    field_reviews: list[FieldReview] = Field(default_factory=list)
    clarification_decisions: list[StrictStr] = Field(default_factory=list)
    related_question_resolutions: list[RelatedQuestionResolution] = Field(default_factory=list)


class Change(BaseModel):
    target: Target
    before: Value
    after: Value
    evidence: list[Evidence]


class SuggestedCompletion(BaseModel):
    model_config = ConfigDict(extra='forbid')
    feature_id: str
    criteria: list[str]


class CompletionConflict(BaseModel):
    model_config = ConfigDict(extra='forbid')
    feature_id: str
    criterion_index: int = Field(ge=0, strict=True)
    reason: str


class CompletionReview(BaseModel):
    model_config = ConfigDict(extra='forbid')
    conflicts: list[CompletionConflict]
    issues: list[str] = Field(default_factory=list)
    delivery_updates: list['DeliveryUpdate'] = Field(default_factory=list)
    rejected_delivery_ids: list[str] = Field(default_factory=list)
    reviewed_patch_ids: list[str] = Field(default_factory=list)


class DependentTextUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    target: Target
    previous_value: str
    value: str


class DeliveryUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    feature_id: str
    value: str
    dependents: list[DependentTextUpdate] = Field(default_factory=list)


class SuggestedTechnology(BaseModel):
    model_config = ConfigDict(extra='forbid')
    choice: str
    area: str | None
    reason: str | None


class SuggestedException(BaseModel):
    model_config = ConfigDict(extra='forbid')
    condition: str
    system_response: str
    user_guidance: str | None
    recovery: str | None
    related_features: list[str]


class SuggestedDeliveryDetail(BaseModel):
    model_config = ConfigDict(extra='forbid')
    feature_id: str
    choice: str
    reason: str


class SuggestedExceptionLink(BaseModel):
    model_config = ConfigDict(extra='forbid')
    condition: str
    feature_id: str
    reason: str


class DependentValueUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    target: Target
    previous_value: Value
    value: Value


class SuggestedPatch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    target: Target
    previous_value: Value
    value: Value
    reason: str
    related_features: list[str]
    dependents: list[DependentValueUpdate] = Field(default_factory=list)


class AdvisoryContent(BaseModel):
    model_config = ConfigDict(extra='forbid')
    completion: list[SuggestedCompletion]
    technology: list[SuggestedTechnology]
    project_type: str | None
    flow: str | None
    exceptions: list[SuggestedException]
    exception_links: list[SuggestedExceptionLink] = Field(default_factory=list)
    patches: list[SuggestedPatch] = Field(default_factory=list)


class AdvisoryDraft(AdvisoryContent):
    delivery_details: list[SuggestedDeliveryDetail] = Field(default_factory=list)


class ProposalRevision(BaseModel):
    model_config = ConfigDict(extra='forbid')
    batch_id: str
    item_ids: list[str]
    draft: AdvisoryDraft
    reason: str
    quotes: list[str]


class LocalDeliveryTime(BaseModel):
    model_config = ConfigDict(extra='forbid')
    hour: int = Field(ge=0, le=23, strict=True)
    minute: int = Field(ge=0, le=59, strict=True)


class DeliverySuggestion(BaseModel):
    model_config = ConfigDict(extra='forbid')
    feature_id: str
    channel: Literal['app_push', 'email', 'sms', 'app_screen', 'web_screen',
                     'kakao', 'messenger', 'slack', 'teams', 'phone'] | None
    previous_day_time: LocalDeliveryTime | None
    reason: str


class AdvisoryResponse(AdvisoryContent):
    delivery_details: list[DeliverySuggestion]


class AdvisoryBatch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str
    base_digest: str
    draft: AdvisoryDraft
    status: Literal['pending', 'approved', 'rejected', 'stale', 'empty', 'superseded'] = 'pending'
    selected: list[str] = Field(default_factory=list)
    decision_text: str | None = None
    review_digest: str | None = None
    delivery_updates: list[DeliveryUpdate] = Field(default_factory=list)
    reviewed_patch_ids: list[str] = Field(default_factory=list)
    revision_of: str | None = None
    revision_reason: str | None = None
    revision_evidence: list[Evidence] = Field(default_factory=list)


class PendingInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str
    text: str
    digest: str
    reason: str
    status: Literal['pending', 'resolved', 'withdrawn'] = 'pending'
    resolved_by: str | None = None


class ProjectState(BaseModel):
    model_config = ConfigDict(extra='forbid')

    version: Literal[1] = 1
    rows: dict[str, Row] = Field(default_factory=dict)
    cells: dict[str, Cell] = Field(default_factory=dict)
    questions: list[Question] = Field(default_factory=list)
    changes: list[Change] = Field(default_factory=list)
    documents: list[str] = Field(default_factory=list)
    pending_declarations: list[str] = Field(default_factory=list)
    focus: Target | None = None
    next_row: int = 1
    next_question: int = 1

    public_ids: dict[str, str] = Field(default_factory=dict)
    next_feature_id: int = 1
    next_screen_id: int = 1
    retired_public_ids: list[str] = Field(default_factory=list)
    deferred_required: list[str] = Field(default_factory=list)
    resolved_required: list[str] = Field(default_factory=list)
    pending_answer_notes: list[dict[str,str]] = Field(default_factory=list)
    required_answer_history: list[RequiredAnswerRecord] = Field(default_factory=list)
    question_priorities: dict[str, int] = Field(default_factory=dict)
    deferred_questions: list[Question] = Field(default_factory=list)
    advisory_batches: list[AdvisoryBatch] = Field(default_factory=list)
    dynamic_question_reasons: dict[str, str] = Field(default_factory=dict)
    assessment_pending: bool = False
    source_review_status: Literal['unreviewed', 'verified', 'failed'] = 'unreviewed'
    proposal_generation_status: Literal['not_requested','pending','empty','failed'] = 'not_requested'
    proposal_generation_digest: str | None = None
    proposal_error_codes: list[str] = Field(default_factory=list)
    source_review_digest: str | None = None
    source_review_version: str | None = None
    retired_optional_questions: list[Question] = Field(default_factory=list)
    input_requests: list[PendingInput] = Field(default_factory=list)


TurnPlan.model_rebuild()


IDENTITIES = {
    'goals.metrics': 'name',
    'release_scope.features': 'name',
    'release_scope.tech_stack': 'choice',
    'user_experience.screens': 'screen_or_step',
    'policies.exceptions': 'condition',
    'data_rules.fields': 'name',
    'release_plan.milestones': 'stage',
    'release_plan.glossary_and_references': 'name',
}
BOOL_FIELDS = {'release_scope.features.included_in_release', 'data_rules.fields.required'}
LABELS = {
    'overview.project_name': '프로젝트명',
    'overview.team_name': '팀명',
    'overview.team_members': '팀원',
    'overview.written_on': '작성일',
    'overview.summary': '개요',
    'user_problem.target_users': '대상 사용자',
    'user_problem.usage_context': '사용 상황',
    'user_problem.current_method': '현재 방식',
    'user_problem.pain_points': '문제점',
    'user_problem.evidence': '문제 근거',
    'goals.expected_change': '기대 변화',
    'goals.metrics.name': '지표 이름',
    'goals.metrics.current_value': '현재값',
    'goals.metrics.target_value': '목표값',
    'goals.metrics.measurement_method': '측정 방법',
    'goals.metrics.measurement_timing': '확인 시점',
    'release_scope.features.id': '기능 ID',
    'release_scope.features.name': '기능 이름',
    'release_scope.features.done_criteria': '사용자 완료 기준',
    'release_scope.project_type': '프로젝트 유형',
    'release_scope.tech_stack.area': '기술 영역',
    'release_scope.tech_stack.choice': '기술 선택',
    'release_scope.tech_stack.reason': '기술 선택 이유',
    'release_scope.tech_stack.origin': '기술 출처',
    'release_scope.features.behavior_and_value': '기능 동작',
    'release_scope.features.priority': '우선순위',
    'release_scope.features.included_in_release': '출시 포함 여부',
    'release_scope.out_of_scope': '출시 제외 범위',
    'release_scope.supported_platforms': '지원 플랫폼',
    'release_scope.service_components_and_integrations': '시스템 구성과 연동',
    'release_scope.access_install_and_run': '접근과 실행 방법',
    'user_experience.flow': '사용 흐름',
    'user_experience.screens.id': '화면 ID',
    'user_experience.screens.related_features': '화면 관련 기능',
    'user_experience.screens.screen_or_step': '화면 이름',
    'user_experience.screens.displayed_information': '표시 정보',
    'user_experience.screens.input_fields': '입력 항목',
    'user_experience.screens.user_action': '사용자 동작',
    'user_experience.screens.next_screen': '다음 화면',
    'user_experience.design_links': '디자인 링크',
    'policies.eligibility_and_permissions': '이용 자격과 권한',
    'policies.behavior_and_state_changes': '동작과 상태 변경',
    'policies.exceptions.condition': '예외 조건',
    'policies.exceptions.related_features': '예외 관련 기능',
    'policies.exceptions.system_response': '시스템 처리',
    'policies.exceptions.user_guidance': '사용자 안내',
    'policies.exceptions.recovery': '복구 방법',
    'policies.open_considerations': '추가 고려 사항',
    'data_rules.fields.name': '데이터 이름',
    'data_rules.fields.related_features': '데이터 관련 기능',
    'data_rules.fields.purpose': '데이터 목적',
    'data_rules.fields.source': '데이터 출처',
    'data_rules.fields.format_and_structure': '데이터 형식',
    'data_rules.fields.required': '필수 여부',
    'data_rules.storage_and_management': '저장과 관리',
    'data_rules.input_validation': '입력 검증',
    'data_rules.error_and_integrity_handling': '오류와 무결성 처리',
    'release_plan.milestones.stage': '작업 단계',
    'release_plan.milestones.schedule': '일정',
    'release_plan.milestones.owner': '담당자',
    'release_plan.milestones.deliverable': '산출물',
    'release_plan.milestones.completion_criteria': '완료 기준',
    'release_plan.pre_launch_checks': '출시 전 점검',
    'release_plan.post_launch_checks': '출시 후 점검',
    'release_plan.glossary_and_references.name': '용어 이름',
    'release_plan.glossary_and_references.definition': '용어 정의',
    'release_plan.glossary_and_references.url': '참고 링크',
}


def compact(text: str) -> str:
    """문자열의 공백을 정리한다."""
    return ' '.join(unicodedata.normalize('NFC', text).split())


def identity(text: str) -> str:
    """항목 이름 비교용 문자열을 정규화한다."""
    return re.sub(r'\s+', '', compact(text))


class Template:
    def __init__(self, path: Path):
        """JSON 템플릿을 읽고 필드 구조를 구성한다."""
        self.data = json.loads(path.read_text(encoding='utf-8-sig'))
        self.fields: dict[str, str] = {}
        self.rows: dict[str, dict] = {}
        for section, fields in self.data.items():
            for name, default in fields.items():
                path = f'{section}.{name}'
                if isinstance(default, list) and default and isinstance(default[0], dict):
                    self.rows[path] = default[0]
                    for field, value in default[0].items():
                        self.add_field(f'{path}.{field}', value)
                else:
                    self.add_field(path, default)

        old_rows = set(IDENTITIES) - {'release_scope.tech_stack'}
        new_fields = {
            'release_scope.features.id', 'release_scope.features.done_criteria',
            'release_scope.project_type', *(f'release_scope.tech_stack.{f}' for f in ('area','choice','reason','origin')),
            'user_experience.screens.id', 'user_experience.screens.related_features',
            'policies.exceptions.related_features', 'data_rules.fields.related_features',
        }
        new_schema = set(self.rows) == set(IDENTITIES) and set(self.fields) == set(LABELS)
        legacy_schema = set(self.rows) == old_rows and set(self.fields) == set(LABELS) - new_fields
        if not (new_schema or legacy_schema):
            raise ValueError('planning-template.json의 필드 구성이 올바르지 않아.')

    def add_field(self, path: str, default):
        """템플릿의 필드와 자료형을 등록한다."""
        self.fields[path] = (
            'bool' if path in BOOL_FIELDS else 'list' if isinstance(default, list) else 'text'
        )

    def export(self, state: ProjectState) -> dict:
        """내부 상태를 공개 요구사항 JSON으로 변환한다."""
        if 'release_scope.features.id' in self.fields:
            validate_public_ids(state)
        result = deepcopy(self.data)
        disputed = {
            q.target.key() for q in state.questions if q.kind == 'conflict' and q.target
        }
        for path in self.rows:
            section, field = path.split('.')
            result[section][field] = []
        for path in self.fields:
            parts = path.split('.')
            if len(parts) == 2 and path in state.cells and path not in disputed:
                result[parts[0]][parts[1]] = state.cells[path].value
        uncertain_exclusions = {
            identity(row.label) for item, row in state.rows.items()
            if row.path == 'release_scope.features'
            and f'release_scope.features.included_in_release@{item}' in disputed
        }
        result['release_scope']['out_of_scope'] = [
            name for name in result['release_scope']['out_of_scope']
            if identity(name) not in uncertain_exclusions
        ]
        for item, row in state.rows.items():
            section, array = row.path.split('.')
            output = deepcopy(self.rows[row.path])
            for field in output:
                target = Target(path=f'{row.path}.{field}', item=item)
                cell = state.cells.get(target.key())
                if cell and target.key() not in disputed:
                    output[field] = cell.value
            if row.path == 'release_scope.features' and 'id' in output:
                output['id'] = state.public_ids.get(item)
            elif row.path == 'user_experience.screens' and 'id' in output:

                output['id'] = None
            if row.path == 'release_scope.tech_stack' and 'origin' in output:
                choice = state.cells.get(f'{row.path}.choice@{item}')
                output['origin'] = ('approved_proposal' if choice and any(
                    ref.source == '사용자 제안 승인' and any(b.status == 'approved' and b.decision_text == ref.quote for b in state.advisory_batches)
                    for ref in choice.evidence) else 'explicit' if choice else None)
            if any(value is not None and value != [] for value in output.values()):
                result[section][array].append(output)
        return result


PUBLIC_ID_ROWS = {'release_scope.features': ('FEAT', 'next_feature_id'),
                  'user_experience.screens': ('SCR', 'next_screen_id')}
PUBLIC_ID_PATHS = {f'{path}.id' for path in PUBLIC_ID_ROWS}
FEATURE_LINK_PATHS = {
    'user_experience.screens.related_features',
    'policies.exceptions.related_features',
    'data_rules.fields.related_features',
}
SYSTEM_OWNED_PATHS = PUBLIC_ID_PATHS | {'release_scope.tech_stack.origin'}


def ensure_public_ids(state: ProjectState) -> None:
    """기존 ID를 유지하면서 필요한 공개 ID를 발급한다."""
    for row_id, row in state.rows.items():
        if row.path not in PUBLIC_ID_ROWS or row_id in state.public_ids:
            continue
        prefix, counter = PUBLIC_ID_ROWS[row.path]
        number = max(1, getattr(state, counter))
        used = set(state.public_ids.values()) | set(state.retired_public_ids)
        while f'{prefix}-{number:03d}' in used:
            number += 1
        state.public_ids[row_id] = f'{prefix}-{number:03d}'
        setattr(state, counter, number + 1)

    for prefix, counter in PUBLIC_ID_ROWS.values():
        numbers = [int(value.split('-', 1)[1]) for value in
                   [*state.public_ids.values(), *state.retired_public_ids]
                   if re.fullmatch(rf'{prefix}-\d+', value)]
        if numbers:
            setattr(state, counter, max(getattr(state, counter), max(numbers) + 1))


def validate_public_ids(state: ProjectState) -> None:
    """공개 ID의 유일성과 참조 무결성을 검증한다."""
    ensure_public_ids(state)
    known = list(state.public_ids.values())
    if set(known) & set(state.retired_public_ids):
        raise ValueError('삭제된 ID를 다시 사용했어.')
    if len(set(known)) != len(known):
        raise ValueError('중복된 기능 또는 화면 ID가 있어.')
    for row_id, public_id in state.public_ids.items():
        row = state.rows.get(row_id)
        if row is None or row.path not in PUBLIC_ID_ROWS:
            raise ValueError('사용하지 않는 기능 또는 화면 ID가 연결돼 있어.')
        prefix, _ = PUBLIC_ID_ROWS[row.path]
        if re.fullmatch(rf'{prefix}-\d{{3,}}', public_id) is None:
            raise ValueError('기능 또는 화면 ID 형식이 잘못됐어.')
    known_features = {value for row_id, value in state.public_ids.items()
                      if state.rows[row_id].path == 'release_scope.features'}
    for key, cell in state.cells.items():
        if key.partition('@')[0] in FEATURE_LINK_PATHS and (
            not isinstance(cell.value, list) or any(value not in known_features for value in cell.value)
        ):
            raise ValueError(f'존재하지 않는 관련 기능 ID가 있어: {key}')


def row_matches(state: ProjectState, row_path: str, reference: str, aliases=None) -> set[str]:
    """항목 종류를 기준으로 이름과 내부 및 공개 ID를 하나의 대상으로 해석한다."""
    raw = compact(reference)
    if not raw:
        return set()
    alias = (aliases or {}).get((row_path, raw))
    if re.fullmatch(r'(?:FEAT|SCR)-[0-9]+|R[0-9]+', raw, re.I):
        exact = {rid for rid, row in state.rows.items() if row.path == row_path
                 and raw.casefold() in (rid.casefold(), state.public_ids.get(rid, '').casefold())}
        if not exact:
            return set()
        return exact
    matches = {rid for rid, row in state.rows.items() if row.path == row_path
               and (identity(row.label) == identity(raw) or identity(rid) == identity(raw)
                    or identity(state.public_ids.get(rid, '')) == identity(raw))}
    if alias in state.rows and state.rows[alias].path == row_path:
        matches.add(alias)
    return matches


def validate_state(template: Template, state: ProjectState) -> None:
    """내부 상태의 필드 종류와 참조 및 자료형을 검증한다."""
    if (state.proposal_generation_status!='not_requested' and not state.proposal_generation_digest
            or state.proposal_generation_digest is not None and not re.fullmatch(r'[0-9a-f]{64}',state.proposal_generation_digest)
            or (state.proposal_generation_status=='failed') != bool(state.proposal_error_codes)
            or any(not code.strip() for code in state.proposal_error_codes)):
        raise ValueError('제안 생성 상태와 근거 해시·오류 기록이 일치하지 않아.')
    for i,request in enumerate(state.input_requests,1):
        if (request.id != f'I{i:04d}' or not request.text.strip() or not request.reason.strip()
                or request.digest != sha256(request.text.encode('utf-8')).hexdigest()
                or bool(request.resolved_by) != (request.status != 'pending')):
            raise ValueError('미반영 요청의 식별자·상태·원문이 올바르지 않아.')
    names = set()
    for rid, row in state.rows.items():
        if row.path not in template.rows or not rid or '@' in rid or not compact(row.label):
            raise ValueError(f'잘못된 항목 구조: {rid}')
        name = (row.path, identity(row.label))
        if name in names:
            raise ValueError(f'중복된 항목 이름: {row.label}')
        names.add(name)
    for key, cell in state.cells.items():
        path, separator, rid = key.partition('@')
        if path not in template.fields or path in SYSTEM_OWNED_PATHS:
            raise ValueError(f'저장할 수 없는 필드: {path}')
        row_path = path.rpartition('.')[0]
        if row_path in template.rows:
            if not separator or rid not in state.rows or state.rows[rid].path != row_path:
                raise ValueError(f'잘못된 항목 참조: {key}')
        elif separator:
            raise ValueError(f'단일 필드에 항목 참조가 있어: {key}')
        kind = template.fields[path]
        value = cell.value
        if not (value is None or (kind == 'text' and isinstance(value, str))
                or (kind == 'bool' and type(value) is bool)
                or (kind == 'list' and isinstance(value, list) and all(isinstance(v, str) for v in value))):
            raise ValueError(f'필드 자료형이 맞지 않아: {key}')
        if separator and path == f'{row_path}.{IDENTITIES[row_path]}' and value is not None:
            if not isinstance(value, str) or identity(value) != identity(state.rows[rid].label):
                raise ValueError(f'항목 이름과 값이 일치하지 않아: {key}')
    if len({q.id for q in state.questions}) != len(state.questions):
        raise ValueError('중복된 질문 ID가 있어.')
    for q in state.questions:
        if q.target is not None:
            target = q.target
            row_path = target.path.rpartition('.')[0]
            if target.path not in template.fields or target.path in SYSTEM_OWNED_PATHS:
                raise ValueError('질문의 저장 대상이 올바르지 않아.')
            if row_path in template.rows and (not target.item or target.item not in state.rows
                                             or state.rows[target.item].path != row_path):
                raise ValueError('질문이 존재하지 않는 항목을 참조해.')
            if row_path not in template.rows and target.item is not None:
                raise ValueError('단일 필드의 질문 대상이 올바르지 않아.')
    if len({q.id for q in [*state.questions, *state.deferred_questions]}) != len(state.questions) + len(state.deferred_questions):
        raise ValueError('대기/보류 질문 ID가 중복돼.')
    if len({q.id for q in state.retired_optional_questions}) != len(state.retired_optional_questions) or {q.id for q in state.retired_optional_questions} & {q.id for q in [*state.questions,*state.deferred_questions]}:
        raise ValueError('종료한 선택 질문 ID를 재사용할 수 없어.')
    if any(not reason.strip() or len(reason)>1200 for reason in state.dynamic_question_reasons.values()):
        raise ValueError('동적 질문 이유가 올바르지 않아.')
    if any(type(n) is not int or not 0 <= n <= 100 for n in state.question_priorities.values()):
        raise ValueError('질문 중요도 범위가 올바르지 않아.')
    if len({b.id for b in state.advisory_batches}) != len(state.advisory_batches):
        raise ValueError('제안 ID가 중복돼.')
    if sum(b.status == 'pending' for b in state.advisory_batches) > 1:
        raise ValueError('검토 대기 제안이 중복돼.')
    for i,batch in enumerate(state.advisory_batches,1):
        if batch.revision_of is not None:
            prior = next((b for b in state.advisory_batches[:i-1] if b.id == batch.revision_of), None)
            if prior is None or prior.status != 'superseded' or not batch.revision_evidence or not batch.revision_reason:
                raise ValueError('제안 개정 이력이 올바르지 않아.')
        if batch.id != f'P{i:04d}' or not re.fullmatch(r'[0-9a-f]{64}',batch.base_digest):
            raise ValueError('제안 식별자 또는 기준 해시가 올바르지 않아.')
        from proposals.proposal_flow import suggestion_items
        ids={i for i,_,_ in suggestion_items(batch.draft)}
        if len(batch.selected)!=len(set(batch.selected)) or not set(batch.selected)<=ids:
            raise ValueError('제안 승인 선택 항목이 올바르지 않아.')
        if batch.status in ('approved','rejected'):
            if not batch.decision_text or bool(batch.selected)!=(batch.status=='approved'):
                raise ValueError('제안 결정 기록이 올바르지 않아.')
        elif batch.decision_text is not None or batch.selected:
            raise ValueError('미확정 제안에 결정 기록이 있어.')
        if batch.status=='empty' and ids or batch.status!='empty' and not ids:
            raise ValueError('제안 상태와 항목이 일치하지 않아.')
    history_ids = {}
    for record in state.required_answer_history:
        qid = record.question.id
        dynamic = record.token == f'decision:{qid}'
        if not re.fullmatch(r'Q[0-9]{4,}', qid) or (
            not dynamic and (not record.question.text or not record.question.text.startswith(f'[필수확인:{record.token}]'))
        ):
            raise ValueError('질문 이력의 ID 또는 확인 대상이 올바르지 않아.')
        address=(record.token, record.question.target or record.question.answer_hint)
        if qid in history_ids and history_ids[qid] != address and not (
                history_ids[qid][0] == address[0] and history_ids[qid][1] is None and address[1] is not None):
            raise ValueError('질문 이력의 ID를 다른 대상에 재사용할 수 없어.')
        history_ids[qid] = address
    for q in state.questions:
        prior = history_ids.get(q.id)
        if prior and ((q.target or q.answer_hint) != prior[1] or
                      prior[0] != f'decision:{q.id}' and (not q.text or not q.text.startswith(f'[필수확인:{prior[0]}]'))):
            raise ValueError('질문 ID가 다른 확인 대상에 재사용됐어.')
    used_rows = set(state.rows) | {c.target.item for c in state.changes if c.target.item}
    for current, attr in ((used_rows, 'next_row'), ({q.id for q in [*state.questions, *state.deferred_questions,*state.retired_optional_questions]} | set(history_ids), 'next_question')):
        prefix = 'R' if attr == 'next_row' else 'Q'
        numbers = [int(v[1:]) for v in current if re.fullmatch(prefix + r'[0-9]+', v)]
        setattr(state, attr, max(1, getattr(state, attr), max(numbers, default=0) + 1))
    if state.focus is not None:
        focus = state.focus
        row_path = focus.path.rpartition('.')[0]
        if focus.path not in template.fields or focus.path in SYSTEM_OWNED_PATHS:
            raise ValueError('직전 수정 대상이 올바르지 않아.')
        if row_path in template.rows and (focus.item not in state.rows or state.rows[focus.item].path != row_path):
            raise ValueError('직전 수정 대상 행이 존재하지 않아.')
        if row_path not in template.rows and focus.item is not None:
            raise ValueError('직전 수정 대상이 단일 필드와 맞지 않아.')
    if 'release_scope.features.id' in template.fields:
        validate_public_ids(state)


def state_evidence(state: ProjectState):
    """상태 전체에서 근거 기록을 수집한다."""
    for batch in state.advisory_batches:
        yield from batch.revision_evidence
    for cell in state.cells.values():
        yield from cell.evidence
    for question in [*state.questions, *state.deferred_questions, *state.retired_optional_questions]:
        yield from question.evidence
        for option in question.options:
            yield from option.evidence
    for event in state.required_answer_history:
        yield from event.question.evidence
    for change in state.changes:
        yield from change.evidence


def validate_transition(template: Template, before: ProjectState, after: ProjectState, text: str):
    """새로운 근거가 현재 원문 또는 기존 근거의 정확한 구절인지 검사한다."""
    validate_state(template, after)
    if len(after.input_requests) < len(before.input_requests):
        raise ValueError('입력 요청 이력을 삭제할 수 없어.')
    for old,new in zip(before.input_requests,after.input_requests):
        if ((old.id,old.text,old.digest,old.reason) != (new.id,new.text,new.digest,new.reason)
                or (old.status != 'pending' and old != new)
                or (new != old and (not new.resolved_by or new.resolved_by not in text))):
            raise ValueError('입력 요청 이력 또는 해결 근거가 올바르지 않아.')
    for rid, public in before.public_ids.items():
        if rid in after.rows and after.public_ids.get(rid) != public:
            raise ValueError('기존 공개 ID를 변경할 수 없어.')
        if rid not in after.rows and public not in after.retired_public_ids:
            raise ValueError('삭제한 공개 ID의 재사용을 방지해야 해.')
    if not set(before.retired_public_ids).issubset(after.retired_public_ids):
        raise ValueError('삭제된 ID 기록을 변경할 수 없어.')
    if after.required_answer_history[:len(before.required_answer_history)] != before.required_answer_history:
        raise ValueError('기존 질문 답변 이력을 변경할 수 없어.')
    for event in after.required_answer_history[len(before.required_answer_history):]:
        if event.raw_answer and event.raw_answer not in text:
            raise ValueError('질문 답변 이력의 원문을 확인할 수 없어.')
    if after.retired_optional_questions[:len(before.retired_optional_questions)] != before.retired_optional_questions:
        raise ValueError('종료한 선택 질문 이력을 변경할 수 없어.')
    if len(after.advisory_batches) < len(before.advisory_batches):
        raise ValueError('기존 제안 이력을 삭제할 수 없어.')
    if any(b.status not in ('pending','empty') for b in after.advisory_batches[len(before.advisory_batches):]):
        raise ValueError('신규 제안이 승인 없이 확정됐어.')
    for old, new in zip(before.advisory_batches, after.advisory_batches):
        if (old.id, old.base_digest, old.draft) != (new.id, new.base_digest, new.draft) or (old.status != 'pending' and old != new):
            raise ValueError('기존 제안 원본 또는 확정 이력을 변경할 수 없어.')
        if new != old and new.status in ('approved', 'rejected') and (not new.decision_text or new.decision_text not in text):
            raise ValueError('제안 결정에 사용자 승인/반려 근거가 없어.')
    if after.changes[:len(before.changes)] != before.changes:
        raise ValueError('기존 변경 이력을 수정할 수 없어.')
    prior = {}
    for ref in state_evidence(before):
        prior.setdefault(ref.digest, []).append(ref)
    source_texts = {sha256(t.encode('utf-8')).hexdigest(): t for t in (text, text.strip())}
    verified = set()
    for ref in state_evidence(after):
        signature = (ref.source, ref.digest, ref.start, ref.end, ref.quote)
        if signature in verified:
            continue
        verified.add(signature)
        if ref.end <= ref.start or ref.start < 0 or not ref.quote or ref.end-ref.start != len(ref.quote):
            raise ValueError('원문 근거의 위치가 올바르지 않아.')
        current = source_texts.get(ref.digest)
        if current is not None and ref.end <= len(current) and current[ref.start:ref.end] == ref.quote:
            continue
        if any(old.source == ref.source and old.start <= ref.start and ref.end <= old.end
               and old.quote[ref.start-old.start:ref.end-old.start] == ref.quote
               for old in prior.get(ref.digest, [])):
            continue
        raise ValueError('현재 원문 또는 기존 기록에서 확인할 수 없는 근거가 있어.')
    for key, cell in after.cells.items():
        if cell.value not in (None, [], '') and before.cells.get(key) != cell and not cell.evidence:
            raise ValueError(f'새로운 값에 원문 근거가 없어: {key}')
