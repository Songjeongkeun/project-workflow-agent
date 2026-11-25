"""사용자의 결정과 답변 의미를 검증한다."""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from core.planning_schema import FEATURE_LINK_PATHS, SYSTEM_OWNED_PATHS, Target, identity

BEHAVIOR = 'release_scope.features.behavior_and_value'
FEATURES = 'release_scope.features'
FLOW = 'user_experience.flow'
PERMISSION = 'policies.eligibility_and_permissions'
OPEN_CONSIDERATIONS = 'policies.open_considerations'
EXCEPTION = 'policies.exceptions'
EXCEPTION_RECOVERY = EXCEPTION + '.recovery'
FEAT_ID = re.compile(r'(?<![A-Za-z0-9])FEAT-\d+(?!\d)', re.I)


class BoundDecisionAnswer(BaseModel):
    model_config = ConfigDict(extra='forbid')
    question_id: str
    target: Target
    decision: Literal['answered', 'partial', 'clarify', 'deferred']
    spans: list[str] = Field(default_factory=list)
    boolean: bool | None = None


class BoundDecisionReview(BaseModel):
    model_config = ConfigDict(extra='forbid')
    question_id: str
    target: Target
    valid: bool
    reason: Literal['unresolved', 'out_of_scope', 'conflict', 'candidate_mismatch'] | None = None


def one_decision(text):
    """질문 문자열의 길이와 문자 형식만 검사한다."""
    return isinstance(text, str) and 5 <= len(text.strip()) <= 350 and '\x00' not in text


def usable_target(template, state, target):
    """질문을 연결할 수 있는 필드와 행인지 확인한다."""
    if not target or target.path not in template.fields or target.path in FEATURE_LINK_PATHS | SYSTEM_OWNED_PATHS:
        return False
    parent = target.path.rpartition('.')[0]
    if parent not in template.rows:
        return target.item is None
    row = state.rows.get(target.item)
    if not row or row.path != parent:
        return False
    scope = state.cells.get(Target(path=FEATURES + '.included_in_release', item=target.item).key())
    return parent != FEATURES or scope is None or scope.value is not False


def matching_basis(state, basis, target=None):
    """저장된 근거에서 인용 구절과 실제 위치를 찾는다."""
    from core.planning_logic import locate
    sources = [(key.partition('@')[2] or None,cell.evidence) for key,cell in state.cells.items()]
    sources.extend(((q.target or q.answer_hint).item if q.target or q.answer_hint else None,q.evidence)
                   for q in [*state.questions,*state.deferred_questions])
    for row,evidence in sources:
        for ref in evidence:
            if ref.start < 0 or ref.end-ref.start != len(ref.quote):
                continue
            found = locate(ref.quote, basis)
            if found:
                start, end = found
                yield row, ref.model_copy(update={'start':ref.start+start, 'end':ref.start+end,
                                                  'quote':ref.quote[start:end]})


def basis_parts(basis):
    """구조적으로 인용된 근거 구절을 나눈다."""
    if isinstance(basis, list):
        return basis
    matches = list(re.finditer(r'["“]([^"“”]+)["”]', basis))
    remainder = re.sub(r'["“][^"“”]+["”]', '', basis)
    separators = ' \t\r\n,;，；、'
    return [match[1] for match in matches] if matches and not remainder.strip(separators) else [basis]


def question_basis(state, basis, target):
    """질문의 모든 근거 인용이 저장된 원문에 있는지 확인한다."""
    parts = basis_parts(basis) if basis else []
    if (not parts or any(not isinstance(part, str) or not part.strip() for part in parts)
            or not 4 <= len(identity(' '.join(parts))) or sum(map(len, parts)) > 500):
        return []
    refs = []
    for part in parts:
        match = next(matching_basis(state, part, target), None)
        if match is None:
            return []
        refs.append(match[1])
    return refs


def assessment_issue(template, state, item, allowed):
    """질문의 대상과 위험 분류 및 근거 형식을 검사한다."""
    if (('decision_topics' in item.model_fields_set and len(item.decision_topics) != 1)
            or any(not topic.strip() for topic in item.decision_topics)):
        return 'multiple_or_empty_decision_topics'
    target = Target(path=item.path, item=item.row_id)
    if target not in allowed or not usable_target(template, state, target):
        return 'invalid_target'
    if not one_decision(item.question) or not 1 <= len(item.reason.strip()) <= 1200:
        return 'invalid_question_text'
    public_ids = {m.upper() for m in FEAT_ID.findall(item.question)}
    if public_ids - set(state.public_ids.values()):
        return 'unknown_feature'
    if item.risk is None:
        return 'missing_risk_category'
    if not item.basis or not question_basis(state, item.basis, target):
        return 'unverified_source_basis'
    return None


def legacy_feature_target(state, question):
    """저장된 질문의 대상만 반환한다."""
    return question.target or question.answer_hint


def decision_issues(answer, question, target, raw, kind):
    """이전 답변 콜백의 구조와 원문 인용을 검사한다."""
    if answer.question_id != question.id or answer.target != target:
        return ['answer_target_mismatch']
    if answer.decision != 'answered':
        return ['unconfirmed_answer_contains_value'] if answer.spans or answer.boolean is not None else []
    if not answer.spans or len(answer.spans) != len(set(answer.spans)):
        return ['answer_missing_or_duplicate_quotes']
    if kind == 'bool' and type(answer.boolean) is not bool or kind != 'bool' and answer.boolean is not None:
        return ['answer_value_type_mismatch']
    rest = raw.strip()
    for span in answer.spans:
        if not span or not rest.startswith(span):
            return ['answer_ungrounded_or_out_of_order']
        rest = rest[len(span):].lstrip(' \t\r\n.;。')
    return ['answer_omits_user_text'] if rest.strip(' .。!') else []


def update_dependent_behavior(state, before, targets=None):
    """이전 호출 형식을 유지하며 의미 기반 수정은 공통 모델 계획에 맡긴다."""
    return None
