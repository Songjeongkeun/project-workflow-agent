"""필수 요구사항과 부족한 항목, 질문 상태를 관리한다."""

import re
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr
from core.planning_schema import ProjectState, Question, Target, Template, RequiredAnswerRecord
from questions.decision_flow import BEHAVIOR, PERMISSION, assessment_issue, one_decision, question_basis, usable_target

KEYS = ('purpose', 'users', 'features', 'behavior', 'scope')
LEGACY_KEYS = ('done', 'technology')
ROW_KEYS = {'behavior', 'scope', 'done'}
PREFIX = '[필수확인:'

class RequiredQuestion(BaseModel):
    model_config = ConfigDict(extra='forbid')
    key: Literal['purpose','users','features','behavior','scope','done','technology']
    row_id: str | None
    question: str
    priority: int | None = Field(default=None, ge=0, le=100)
    decision_topics: list[str] = Field(default_factory=list)

class DynamicQuestion(BaseModel):
    model_config = ConfigDict(extra='forbid')
    path: str
    row_id: str | None
    question: str
    reason: str
    priority: int = Field(ge=0, le=100)
    risk: Literal['security','legal','external_contract','irreversible','core_intent'] | None = None
    basis: str | list[str] | None = None
    decision_topics: list[str] = Field(default_factory=list)

class ExistingQuestionDecision(BaseModel):
    model_config = ConfigDict(extra='forbid')
    question_id: str
    needed: bool
    priority: int = Field(ge=0, le=100)
    basis: str | list[str] | None = None

class RequiredAssessment(BaseModel):
    model_config = ConfigDict(extra='forbid')
    _question_revisions: list[dict] = PrivateAttr(default_factory=list)
    _queue_tiers: list[dict] = PrivateAttr(default_factory=list)
    _new_question_tiers: list[dict] = PrivateAttr(default_factory=list)
    questions: list[RequiredQuestion] = Field(default_factory=list)
    dynamic_questions: list[DynamicQuestion] = Field(default_factory=list)
    existing_decisions: list[ExistingQuestionDecision] = Field(default_factory=list)


class QuestionMeaningCheck(BaseModel):
    model_config = ConfigDict(extra='forbid')
    input_key: str
    action: Literal['keep', 'split', 'drop']
    decisions: list[str]
    output_keys: list[str]
    reason: str


class QuestionDecisionAudit(BaseModel):
    model_config = ConfigDict(extra='forbid')
    output_key: str
    missing_decision: str
    known_fact_refs: list[str]
    why_user_decision: str


class QuestionSemanticReview(BaseModel):
    model_config = ConfigDict(extra='forbid')
    assessment: RequiredAssessment
    checks: list[QuestionMeaningCheck]
    issues: list[str] = Field(default_factory=list)
    decision_audits: list[QuestionDecisionAudit] = Field(default_factory=list)
    missing_questions: list['MissingQuestionCheck'] = Field(default_factory=list)
    coverage_checks: list['QuestionCoverageCheck'] = Field(default_factory=list)


QUESTION_REVIEW_AREAS=(*KEYS,'security','legal','external_contract','irreversible','core_intent')


class MissingQuestionCheck(BaseModel):
    model_config = ConfigDict(extra='forbid')
    output_key: str
    reason: str


class QuestionCoverageCheck(BaseModel):
    model_config = ConfigDict(extra='forbid')
    area: str
    status: Literal['needs_question','addressed_or_not_applicable']
    output_keys: list[str]
    reason: str


QuestionSemanticReview.model_rebuild()


def assessment_questions(assessment):
    """questions 관련 작업을 수행한다"""
    return {**{f'required:{i}':q for i,q in enumerate(assessment.questions)},
            **{f'dynamic:{i}':q for i,q in enumerate(assessment.dynamic_questions)}}


def question_review_issues(original, review, known_facts=None, *, required=False, require_all_areas=True):
    """Validate an independent semantic verdict without parsing question meaning."""
    inputs=assessment_questions(original);outputs=assessment_questions(review.assessment)
    issues=['question_semantic:'+issue for issue in review.issues]
    seen=set();assigned=[]
    if review.assessment.existing_decisions != original.existing_decisions:
        issues.append('question_review_changed_existing_decisions')
    for check in review.checks:
        if check.input_key not in inputs or check.input_key in seen:
            issues.append('question_review_invalid_input:'+check.input_key)
        seen.add(check.input_key)
        if (not check.reason.strip() or any(not d.strip() for d in check.decisions)
                or len(check.decisions)!=len(set(check.decisions))):
            issues.append('question_review_invalid_decisions:'+check.input_key)
        if check.action=='drop':
            if check.output_keys:
                issues.append('question_review_drop_has_outputs')
        elif (not check.decisions or len(check.decisions)!=len(check.output_keys)
                or (check.action=='keep' and len(check.decisions)!=1)
                or (check.action=='split' and len(check.decisions)<2)):
            issues.append('question_review_decision_count:'+check.input_key)
        for key in check.output_keys:
            if key not in outputs:
                issues.append('question_review_unknown_output:'+key)
            assigned.append(key)
    if seen!=set(inputs):
        issues.append('question_review_missing_inputs')
    for missing in review.missing_questions:
        if missing.output_key not in outputs or not missing.reason.strip():
            issues.append('question_review_invalid_missing_decision')
        assigned.append(missing.output_key)
    if set(assigned)!=set(outputs) or len(assigned)!=len(set(assigned)):
        issues.append('question_review_output_scope')
    for key,q in outputs.items():
        if len(q.decision_topics)!=1 or not q.decision_topics[0].strip():
            issues.append('question_review_non_atomic_output:'+key)
    if required or review.decision_audits:
        audited=set()
        for audit in review.decision_audits:
            if audit.output_key not in outputs or audit.output_key in audited:
                issues.append('question_decision_audit_output')
            audited.add(audit.output_key)
            if not audit.missing_decision.strip() or not audit.why_user_decision.strip():
                issues.append('question_decision_audit_missing_reason')
            if len(audit.known_fact_refs)!=len(set(audit.known_fact_refs)) or any(ref not in (known_facts or {}) for ref in audit.known_fact_refs):
                issues.append('question_decision_audit_unknown_fact')
        if audited!=set(outputs):issues.append('question_decision_audit_missing_outputs')
    if required or review.coverage_checks:
        areas=set()
        covered_outputs=set()
        for check in review.coverage_checks:
            if check.area not in QUESTION_REVIEW_AREAS or check.area in areas or not check.reason.strip():
                issues.append('question_coverage_invalid_area')
            areas.add(check.area)
            covered_outputs.update(check.output_keys)
            if (len(check.output_keys)!=len(set(check.output_keys)) or any(key not in outputs for key in check.output_keys)
                    or (check.status=='needs_question') != bool(check.output_keys)):
                issues.append('question_coverage_output_mismatch')
        if require_all_areas and areas!=set(QUESTION_REVIEW_AREAS):issues.append('question_coverage_missing_areas')
        if covered_outputs!=set(outputs):issues.append('question_coverage_missing_outputs')
    return list(dict.fromkeys(issues))


def question_key(question: Question) -> str | None:
    """필수 질문에서 내부 확인 키를 추출한다."""
    if not question.text or not question.text.startswith(PREFIX) or ']' not in question.text:
        return None
    token = question.text.partition(']')[0][len(PREFIX):]
    if token in ('purpose', 'users', 'features', 'technology') or re.fullmatch(r'(?:behavior|scope|done):FEAT-[0-9]+', token):
        return token
    return None


def record_required(state, question, status, text=''):
    """질문의 처리 상태와 원래 답변을 이력에 추가한다."""
    token = question_key(question) or f'decision:{question.id}'
    prior = latest_required(state, token)
    reason = state.dynamic_question_reasons.get(question.id) or (prior.reason if prior else None)
    event = RequiredAnswerRecord(question=question.model_copy(deep=True), token=token, status=status,
                                 raw_answer=text, reason=reason)
    if not state.required_answer_history or state.required_answer_history[-1] != event:
        state.required_answer_history.append(event)
    if re.fullmatch(r'Q[0-9]{4,}', question.id):
        state.next_question = max(state.next_question, int(question.id[1:]) + 1)


def latest_required(state, token):
    """질문 대상의 최신 처리 기록을 찾는다."""
    return next((entry for entry in reversed(state.required_answer_history) if entry.token == token), None)


def valid_required_target(state, question):
    """필수 질문이 실제 저장 가능한 대상과 연결되어 있는지 확인한다."""
    token = question_key(question)
    if not token:
        return False
    key, _, public = token.partition(':')
    target = question.target
    if key in ('technology', 'features'):
        return target is None
    if key in ('purpose', 'users'):
        paths = ('overview.summary', 'goals.expected_change') if key == 'purpose' else ('user_problem.target_users',)
        return target is not None and target.item is None and target.path in paths
    if not target or not target.item or state.public_ids.get(target.item) != public:
        return False
    row = state.rows.get(target.item)
    field = {'behavior':'behavior_and_value', 'scope':'included_in_release', 'done':'done_criteria'}.get(key)
    if not row or row.path != 'release_scope.features' or target.path != f'release_scope.features.{field}':
        return False
    scope = state.cells.get(Target(path='release_scope.features.included_in_release', item=target.item).key())
    return key == 'scope' or not scope or scope.value is not False


def requirement_value(document, token):
    """필수 확인 대상의 현재 값을 비교 가능한 형태로 반환한다."""
    key, _, row = token.partition(':')
    scope = document.get('release_scope', {})
    if key == 'purpose':
        return tuple((document.get(section) or {}).get(field) for section, field in (('goals','expected_change'), ('overview','summary'), ('user_problem','pain_points')))
    if key == 'users':
        return (document.get('user_problem') or {}).get('target_users')
    if key == 'features':
        return [(f.get('id'), f.get('name')) for f in scope.get('features', []) if f.get('included_in_release') is not False]
    if key == 'technology':
        return [t.get('choice') for t in scope.get('tech_stack', []) if t.get('choice')]
    feature = next((f for f in scope.get('features', []) if f.get('id') == row), None)
    if not feature or feature.get('included_in_release') is False and key != 'scope':
        return None
    return feature.get({'behavior':'behavior_and_value', 'scope':'included_in_release', 'done':'done_criteria'}.get(key, ''))


def requirement_filled(document, token):
    """필수 확인 대상에 실질적인 값이 존재하는지 판단한다."""
    value = requirement_value(document, token)
    if token.startswith('scope:'):
        return type(value) is bool
    if token == 'purpose':
        return any(_present(v) for v in value)
    return _present(value)


def _present(value) -> bool:
    """값이 비어 있는지 확인한다."""
    if not isinstance(value, str):
        return bool(value)
    return bool(value.strip())


def requirement_sufficient(document, token):
    """값의 존재만 확인한다. 의미의 충분성은 모델의 해석·질문 검토가 판단한다."""
    return requirement_filled(document, token)


def missing_candidates(document: dict, state: ProjectState) -> list[dict]:
    """필수 요구사항의 검토 후보를 수집한다."""
    features = (document.get('release_scope') or {}).get('features') or []
    missing = []
    def add(key, reason, row_id=None):
        """고정 질문 후보를 추가한다."""
        token = _token(key, row_id)
        if token not in state.deferred_required and not requirement_sufficient(document, token):
            missing.append({'key':key,'row_id':row_id,'reason':reason,'token':token})
    add('purpose', '프로젝트 목적 없음')
    add('users', '주 사용자 없음')
    add('features', '핵심 기능 없음')
    for item in features:
        row_id = item.get('id')
        if row_id and item.get('included_in_release') is not False:
            add('behavior', '기능 동작 없음', row_id)
            add('scope', '이번 출시 포함 여부 미확정', row_id)
    return missing


def _token(key, row_id):
    """질문 종류와 행을 결합해 확인 키를 만든다."""
    return f'{key}:{row_id}' if row_id else key


def reconcile_questions(state: ProjectState, candidates: list[dict], document=None) -> None:
    """해결된 필수 질문을 대기 목록에서 정리한다."""
    active = {item['token'] for item in candidates}
    active.update(question_key(q) for q in state.questions if question_key(q) and question_key(q).split(':',1)[0] in LEGACY_KEYS)
    if document is not None:
        active.update(token for q in state.questions if (token := question_key(q))
                      and valid_required_target(state, q) and one_decision((q.text or '').partition(']')[2])
                      and requirement_sufficient(document, token))
    active.update(question_key(q) for q in state.questions if question_key(q) and (event:=latest_required(state,question_key(q))) and event.status=='reopened' and event.question.id==q.id)
    for q in state.questions:
        token = question_key(q)
        if token and token not in active and token not in state.deferred_required and token not in state.resolved_required:
            record_required(state, q, 'cancelled')
    state.questions = [q for q in state.questions if question_key(q) is None or question_key(q) in active]


def register_questions(state: ProjectState, document: dict, candidates: list[dict],
                       assessment: RequiredAssessment) -> list[str]:
    """유효하고 중복되지 않는 필수 질문을 등록한다."""
    allowed = {x['token'] for x in candidates}
    emitted=[]
    for item in assessment.questions:
        if (('decision_topics' in item.model_fields_set and len(item.decision_topics) != 1)
                or any(not topic.strip() for topic in item.decision_topics)):
            continue
        key,row = item.key,item.row_id
        token=_token(key,row)
        if key in ROW_KEYS and (not row or not row.startswith('FEAT-')): continue
        if key not in ROW_KEYS and row is not None: continue
        if token not in allowed or token in state.deferred_required: continue
        if key == 'scope' and requirement_filled(document, token): continue
        if any(question_key(q)==token for q in state.questions): continue
        question = item.question.strip()
        if not one_decision(question): continue
        target = None
        if key == 'users': target=Target(path='user_problem.target_users')
        elif key == 'purpose': target=Target(path='overview.summary')
        elif key in ROW_KEYS:
            field = {'behavior':'behavior_and_value','scope':'included_in_release','done':'done_criteria'}[key]
            for rid,pid in state.public_ids.items():
                if pid==row and rid in state.rows and state.rows[rid].path=='release_scope.features':
                    target=Target(path=f'release_scope.features.{field}',item=rid)
                    break
            if target is None:continue

        if key=='purpose' and any((q.target or q.answer_hint) in (Target(path='goals.expected_change'),Target(path='overview.summary')) for q in state.questions):
            continue
        if target is not None and any(q.target == target or
                (q.kind == 'clarify' and q.answer_hint == target and
                 not requirement_filled(document, token)) for q in state.questions):
            continue
        used = {q.id for q in state.questions} | {e.question.id for e in state.required_answer_history}
        while f'Q{state.next_question:04d}' in used:
            state.next_question += 1
        state.questions.append(Question(id=f'Q{state.next_question:04d}',kind='missing',target=target,
                                        text=f'{PREFIX}{token}] {question}'))
        if item.priority is not None:
            state.question_priorities[state.questions[-1].id] = item.priority
        state.next_question+=1
        emitted.append(token)
    return emitted


def resolve_satisfied_questions(state, document, original, previous_document=None, message='', *, trusted=True):
    """값이 실제로 변경된 필수 질문만 해결하거나 다시 검토 대상으로 전환한다."""
    if previous_document is None:
        return
    for token in sorted(set(original.resolved_required + original.deferred_required)):
        if requirement_value(document, token) != requirement_value(previous_document, token):
            state.resolved_required = [x for x in state.resolved_required if x != token]
            if trusted and requirement_sufficient(document, token):
                state.deferred_required = [x for x in state.deferred_required if x != token]
            event=latest_required(state,token)
            if event and event.status in ('answered','deferred') and token not in state.deferred_required:
                record_required(state,event.question,'superseded',message)
    for q in original.questions:
        token = question_key(q)
        if not token or token in state.deferred_required:
            continue
        if not valid_required_target(state, q):
            if token.split(':', 1)[0] in ROW_KEYS:
                record_required(state, q, 'cancelled', message)
                state.questions = [x for x in state.questions if x.id != q.id]
            continue
        changed = requirement_value(document, token) != requirement_value(previous_document, token)
        conflict = any(x.kind == 'conflict' and x.target == q.target and q.target is not None for x in state.questions)
        if trusted and changed and requirement_sufficient(document, token) and not conflict:
            state.questions = [x for x in state.questions if x.id != q.id and question_key(x) != token]
            if token not in state.resolved_required:
                state.resolved_required.append(token)
            record_required(state, q, 'answered', message)
        elif not any(x.id == q.id or question_key(x) == token for x in state.questions) and not conflict:
            state.questions.append(q.model_copy(deep=True))
    state.questions.sort(key=lambda q: int(q.id[1:]) if re.fullmatch(r'Q[0-9]+', q.id) else 0)


def prune_required_status(state, original, document, message):
    """삭제·출시 제외로 더 이상 유효하지 않은 확인 상태를 정리한다."""
    active = {f.get('id') for f in document.get('release_scope', {}).get('features', []) if f.get('included_in_release') is not False}
    for token in sorted(set(state.deferred_required + state.resolved_required)):
        key, _, public = token.partition(':')
        if key in ROW_KEYS and public not in active and not (key == 'scope' and public in state.public_ids.values()):
            state.deferred_required = [x for x in state.deferred_required if x != token]
            state.resolved_required = [x for x in state.resolved_required if x != token]
            event = latest_required(state, token)
            if event and event.status in ('answered', 'deferred') and key != 'scope':
                record_required(state, event.question, 'cancelled', message)
    existing = set(state.public_ids.values())
    deleted = {entry.token for entry in state.required_answer_history
               if entry.token.partition(':')[0] in ROW_KEYS and entry.token.partition(':')[2] not in existing}
    for token in sorted(deleted):
        event = latest_required(state, token)
        if event and event.status in ('answered', 'deferred', 'reopened'):
            record_required(state, event.question, 'cancelled', message)
    for q in original.questions:
        token = question_key(q)
        if not token or ':' not in token or token.partition(':')[2] in active:
            continue
        event = latest_required(state, token)
        if not event or event.question.id != q.id or event.status not in ('answered', 'cancelled'):
            record_required(state, q, 'cancelled', message)
        state.questions = [x for x in state.questions if x.id != q.id]


def essential_fallback_questions(document: dict, candidates: list[dict], state: ProjectState) -> RequiredAssessment:
    """명백히 비어 있는 필수 항목의 최소 질문을 생성한다."""
    scope = document.get('release_scope') or {}
    features = {f.get('id'): f for f in scope.get('features') or []}
    prompts = {
        'purpose': '이 프로젝트로 해결하려는 핵심 문제는 무엇이야?',
        'users': '이 서비스를 주로 사용하는 사람은 누구야?',
        'features': '이번에 만들 핵심 기능을 알려줄래?',
        'behavior': '이 기능은 어떤 상황에서 어떻게 동작해야 해?',
        'scope': '이 기능은 이번 출시 범위에 포함돼?',
        'done': '이 기능이 완료됐다고 판단할 수 있는 기준을 알려줄래?',
        'technology': '사용할 기술이 정해져 있어? 아직 미정이거나 회사에서 정한 기술이 있다면 알려줘.',
    }
    seen = {question_key(q) for q in state.questions}
    response = []
    for candidate in candidates:
        key = candidate['key']
        row_id = candidate.get('row_id')
        token = candidate['token']
        if token in seen or token in state.deferred_required:

            continue
        needed = not requirement_sufficient(document, token)
        if key in ROW_KEYS and row_id not in features:
            needed = False
        if needed:
            prompt = prompts[key]
            question = f"{features[row_id].get('name') or row_id} ({row_id}) · {prompt}" if row_id in features else prompt
            response.append(RequiredQuestion(key=key, row_id=row_id, question=question))
    return RequiredAssessment(questions=response)


DYNAMIC_FIELDS = frozenset({
    'overview.summary', 'user_problem.target_users',
    BEHAVIOR, 'release_scope.features.included_in_release', 'release_scope.out_of_scope', 'release_scope.supported_platforms',
    'release_scope.service_components_and_integrations', 'release_scope.access_install_and_run',
    'policies.eligibility_and_permissions', 'policies.behavior_and_state_changes',
    'policies.exceptions.condition', 'policies.exceptions.system_response',
    'policies.exceptions.user_guidance', 'policies.exceptions.recovery',
    'data_rules.fields.name', 'data_rules.fields.purpose', 'data_rules.fields.source',
    'data_rules.fields.format_and_structure', 'data_rules.fields.required',
    'data_rules.storage_and_management', 'data_rules.input_validation', 'data_rules.error_and_integrity_handling',
    'user_experience.flow','user_experience.screens.input_fields','user_experience.screens.user_action','user_experience.screens.next_screen',
    'user_problem.usage_context','user_problem.pain_points','goals.expected_change',
    'goals.metrics.name','goals.metrics.current_value','goals.metrics.target_value','goals.metrics.measurement_method','goals.metrics.measurement_timing',
    'release_plan.milestones.schedule','release_plan.pre_launch_checks','release_plan.post_launch_checks',
})


def dynamic_targets(template, state):
    """동적 질문의 허용 대상 목록을 구성한다."""
    return {target for path in DYNAMIC_FIELDS if path in template.fields
            for rid in ([r for r, row in state.rows.items() if row.path == path.rpartition('.')[0]]
                        if path.rpartition('.')[0] in template.rows else [None])
            if usable_target(template, state, target := Target(path=path, item=rid))}


def advisory_question(q):
    """제안으로 처리할 기존 질문인지 판별한다."""
    token=question_key(q)
    if token: return token.split(':',1)[0] in LEGACY_KEYS
    target=q.target or q.answer_hint
    return bool(target and (target.path=='release_scope.features.done_criteria' or target.path.startswith('release_scope.tech_stack.')))


def prune_advisory_questions(state, original=None):

    """기존 완료 조건과 기술 질문을 이력에 남긴다."""
    for q in list(state.questions):
        if q.kind=='conflict' or not advisory_question(q):continue
        if not question_key(q) and (original is None or any(old.id==q.id for old in original.questions)):continue
        token=question_key(q)
        previous=latest_required(state,token) if token else None
        if previous and previous.question.id==q.id and previous.status=='reopened':continue
        if token:
            record_required(state,q,'deferred')
            if token not in state.deferred_required: state.deferred_required.append(token)
        elif q not in state.deferred_questions:
            state.deferred_questions.append(q)
        state.questions.remove(q)


def question_signature(target, text):
    """Only normalize whitespace; do not infer semantic equivalence from text."""
    return target, ' '.join((text or '').split())


def dynamic_question_already_known(state, target, text, original=None):
    """An exact active/deferred question is the same decision, not a new ID.

    A currently answered question is not repeated. If its actual target value
    later changes, allow a new independently reviewed decision; stale history
    must not suppress a necessary question forever. No fuzzy text matching.
    """
    signature=question_signature(target,text)
    def matches(q):
        """matches 관련 작업을 수행한다"""
        content=(q.text or '').partition(']')[2].strip() if question_key(q) else q.text
        return question_signature(q.target or q.answer_hint,content)==signature
    if any(matches(q) for q in [*state.questions,*state.deferred_questions]):
        return True
    latest={event.question.id:(i,event) for i,event in enumerate(state.required_answer_history)}
    for i,event in latest.values():
        if event.status!='answered' or not matches(event.question):continue
        old=original.cells.get(target.key()) if original is not None else None
        now=state.cells.get(target.key())
        if (original is not None and i<len(original.required_answer_history)
                and (old.value if old else None)!=(now.value if now else None)):
            continue
        return True
    return False


def register_dynamic_questions(template,state,assessment,original=None):
    """검증된 동적 질문을 대기열에 등록한다."""
    targets = dynamic_targets(template, state)
    added = []
    for item in assessment.dynamic_questions:
        if assessment_issue(template, state, item, targets):
            continue
        target = Target(path=item.path, item=item.row_id)
        if dynamic_question_already_known(state,target,item.question,original):
            continue
        cell = state.cells.get(target.key())
        clarify = bool(cell and _present(cell.value))
        q = Question(id=f'Q{state.next_question:04d}', kind='clarify' if clarify else 'missing',
                     target=None if clarify else target, answer_hint=target if clarify else None,
                     text=item.question.strip(), evidence=question_basis(state,item.basis,target))
        state.next_question += 1
        state.questions.append(q)
        state.question_priorities[q.id] = item.priority
        state.dynamic_question_reasons[q.id] = item.reason.strip()
        added.append(q.id)
    return added


def fixed_target_token(state, target):
    """저장 대상의 고정 필수 확인 키를 찾는다."""
    if not target: return None
    mapping={'overview.summary':'purpose','user_problem.target_users':'users'}
    if target.path in mapping: return mapping[target.path]
    field=target.path.rpartition('.')[2]
    if target.path.startswith('release_scope.features.') and target.item in state.public_ids:
        key={'name':'features','behavior_and_value':'behavior','included_in_release':'scope'}.get(field)
        if key: return key if key=='features' else f'{key}:{state.public_ids[target.item]}'
    return None


def question_decision_issues(state, assessment, original=None):
    """중요 질문을 닫는 근거가 이번 변경에서 새로 확인됐는지 검사한다."""
    from core.planning_logic import locate
    prior = {(r.source,r.digest,r.start,r.end,r.quote) for c in original.cells.values() for r in c.evidence} if original else set()
    fresh = [r for c in state.cells.values() for r in c.evidence
             if (r.source,r.digest,r.start,r.end,r.quote) not in prior]
    questions = {q.id:q for q in state.questions}
    issues = []
    for decision in assessment.existing_decisions:
        q = questions.get(decision.question_id)
        if q is None or decision.needed or q.id not in state.dynamic_question_reasons:
            continue
        from questions.decision_flow import basis_parts
        parts = basis_parts(decision.basis or '')
        if (not question_basis(state,decision.basis,q.target or q.answer_hint)
                or not any(part and locate(r.quote,part) for part in parts for r in fresh)):
            issues.append('existing_question_requires_new_evidence:' + q.id)
    return issues


def apply_question_decisions(state,document,assessment,original=None):
    """기존 질문의 필요성과 중요도를 갱신한다."""
    blocked = {issue.rpartition(':')[2] for issue in question_decision_issues(state,assessment,original)}
    pairs={d.question_id:d for d in assessment.existing_decisions}
    for q in list(state.questions):
        d=pairs.get(q.id)
        if not d or q.id in blocked: continue
        token=question_key(q)
        state.question_priorities[q.id]=d.priority
        if d.needed or q.kind=='conflict': continue
        target = q.target or q.answer_hint
        if q.id in state.dynamic_question_reasons:
            if not question_basis(state, d.basis, target):
                continue
        fixed=token or fixed_target_token(state,q.target or q.answer_hint)
        if fixed and fixed.split(':',1)[0] in KEYS and not requirement_sufficient(document,fixed): continue
        if token:
            history=latest_required(state,token)
            if history and history.status=='reopened': continue
            if token.split(':',1)[0] not in LEGACY_KEYS and not requirement_sufficient(document,token): continue
            record_required(state,q,'cancelled')
        else:
            if q.id in state.dynamic_question_reasons:
                record_required(state, q, 'cancelled')
            state.retired_optional_questions.append(q.model_copy(deep=True))
        state.questions.remove(q)


def rank_questions(state):

    """충돌 및 중요도에 따라 질문 순서를 정한다."""
    defaults={'purpose':92,'users':88,'features':90,'scope':80,'behavior':75,'done':30,'technology':25}
    def score(q):
        """질문 정렬 기준을 계산한다."""
        token=question_key(q);key=token.split(':',1)[0] if token else ''
        return (0 if q.kind=='conflict' else 1,
                -state.question_priorities.get(q.id,defaults.get(key,65)),
                int(q.id[1:]) if re.fullmatch(r'Q\d+',q.id) else 0)
    state.questions.sort(key=score)
    for q in list(state.questions):
        token = question_key(q) or ''
        if not token.startswith('scope:') or q.kind == 'conflict' or not q.target:
            continue
        earlier = next((other for other in state.questions[:state.questions.index(q)]
                       if (other.target or other.answer_hint) == Target(path=BEHAVIOR,item=q.target.item)
                       and other.kind != 'conflict'), None)
        if earlier:
            state.questions.remove(q)
            state.questions.insert(state.questions.index(earlier), q)
    keep={q.id for q in state.questions+state.deferred_questions}
    state.question_priorities={k:v for k,v in state.question_priorities.items() if k in keep}
    state.dynamic_question_reasons={k:v for k,v in state.dynamic_question_reasons.items() if k in keep}


def close_removed_dynamic_questions(template, state, original, message):
    """사라진 동적 질문을 이력에 남긴다."""
    for queue in (state.questions, state.deferred_questions):
        for q in list(queue):
            target = q.target or q.answer_hint
            if q.id in state.dynamic_question_reasons and target and not usable_target(template,state,target):
                queue.remove(q)
                if q not in state.retired_optional_questions:
                    state.retired_optional_questions.append(q)
    preserve = {q.id for q in state.questions + state.deferred_questions}
    for q in original.questions + original.deferred_questions:
        if q.id not in preserve and q.id in original.dynamic_question_reasons:
            if any(event.question.id == q.id for event in state.required_answer_history[len(original.required_answer_history):]):
                continue
            record_required(state, q, 'cancelled', message)


def current_question(state):
    """현재 표시할 질문 한 개를 반환한다."""
    return state.questions[0] if state.questions else None
