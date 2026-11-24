"""추가 질문 생성과 검토 단계의 응답을 처리한다.

Small domain-independent question generation/review contract.

Meaning, necessity, atomicity and importance are model decisions. Python owns
allowed targets, source references, question IDs, and state transitions.
"""
import re
from typing import Literal
from pydantic import BaseModel, ConfigDict, StrictStr, StrictBool, StrictInt, Field
from questions.required_check import RequiredAssessment, RequiredQuestion, DynamicQuestion, ExistingQuestionDecision, question_decision_issues, question_key, question_signature, dynamic_question_already_known
from questions.decision_flow import assessment_issue, one_decision
from core.planning_schema import Target
from questions.question_scope import latest_terminal_ids, project_context

Risk = Literal['security','legal','external_contract','irreversible','core_intent']

class Need(BaseModel):
    model_config = ConfigDict(extra='forbid')
    target: StrictStr
    question: StrictStr
    reason: StrictStr
    risk: Risk | None
    evidence: list[StrictStr]
    priority: StrictInt = Field(ge=0, le=100)
    tier: Literal['P0','P1','P2'] = 'P0'

class ExistingUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    question_id: StrictStr
    close: StrictBool
    evidence: list[StrictStr]
    priority: StrictInt = Field(ge=0, le=100)

class QueueTier(BaseModel):
    model_config = ConfigDict(extra='forbid')
    question_id: StrictStr
    tier: Literal['P0','P1','P2']
    reason: StrictStr

class Plan(BaseModel):
    model_config = ConfigDict(extra='forbid')
    questions: list[Need]
    existing_updates: list[ExistingUpdate]
    queue_tiers: list[QueueTier] = Field(default_factory=list)

class ExistingRevision(BaseModel):
    """Narrow an active question after a partial answer without changing its ID."""
    model_config = ConfigDict(extra='forbid')
    question_id: StrictStr
    question: StrictStr
    reason: StrictStr
    evidence: list[StrictStr]
    priority: StrictInt = Field(ge=0, le=100)


class InvalidDraftVerdict(BaseModel):
    """Independent review decision for a structurally invalid draft.

    No guessed target is accepted; needed P0 decisions must be reissued in add.
    """
    model_config = ConfigDict(extra='forbid')
    draft_id: StrictStr
    disposition: Literal['reissue','defer_optional']
    reason: StrictStr
    replacement_target: StrictStr | None = None


class Review(BaseModel):
    model_config = ConfigDict(extra='forbid')
    remove: list[StrictStr]
    add: list[Need]
    keep_open: list[StrictStr]
    complete: StrictBool
    unresolved_reason: StrictStr | None
    revise_existing: list[ExistingRevision] = Field(default_factory=list)
    queue_tiers: list[QueueTier] = Field(default_factory=list)
    invalid_draft_verdicts: list[InvalidDraftVerdict] = Field(default_factory=list)

class QuestionPatch(BaseModel):
    """Wire review: only actual changes and blocking review problems.
    Pending user decisions are represented by questions, never by a flag.
    """
    model_config = ConfigDict(extra='forbid')
    remove: list[StrictStr]
    add: list[Need]
    cancel_closures: list[StrictStr]
    blocking_issues: list[StrictStr]
    revise_existing: list[ExistingRevision] = Field(default_factory=list)
    queue_tiers: list[QueueTier] = Field(default_factory=list)
    invalid_draft_verdicts: list[InvalidDraftVerdict] = Field(default_factory=list)


def to_review(patch):
    """Compatibility with the old internal Review type, not a model judgment."""
    from extraction.response_normalization import unique, unique_models
    return Review(remove=unique(patch.remove), add=unique_models(patch.add),
                  keep_open=unique(patch.cancel_closures),
                  complete=not patch.blocking_issues,
                  unresolved_reason='; '.join(patch.blocking_issues) or None,
                  revise_existing=[q.model_copy(deep=True) for q in patch.revise_existing],
                  queue_tiers=[q.model_copy(deep=True) for q in patch.queue_tiers],
                  invalid_draft_verdicts=[v.model_copy(deep=True) for v in patch.invalid_draft_verdicts])


RULES = '''질문 정책: P0는 없으면 목적/사용자/핵심기능/핵심동작/출시범위를 확정할 수 없거나, 확인된 중대한 정책 충돌·보안·법적·계약·비가역 위험 때문에 진행할 수 없는 사용자 결정만 해당한다. P1은 중요하지만 초기 기획 완료 전에 결정하지 않아도 되는 세부 정책, P2는 구현 선택·UX·입력 형식·권장 설정이다. 모든 신규 질문에 tier(P0/P1/P2)를 명시하고 P1/P2는 질문으로 화면에 출력하지 말고 보류/제안 대상으로 남긴다. 기존 활성 질문도 queue_tiers에서 전부 다시 분류한다. P0의 이유는 단순 중요함이 아니라 지금 질문 없이는 진행할 수 없는 구체적 차단 사유여야 한다. 권한 주체가 미정이어도 기획을 작성하고 개발 전에 확정 가능하면 P1로 보류하되 보류 이유에 '개발 전 확인 필요'를 명시한다. 반대로 실제 권한 충돌이나 무단 접근 위험으로 핵심 흐름 자체를 결정할 수 없다면 P0다. 이미 알려진 보안·권한 정보를 다른 표현으로 재질문하지 않는다. 근거가 부족하면 임의로 P0를 단정하지 않는다. 필수 다섯 요소가 충분하고 중대한 충돌이 없으면 P0 질문은 0개이고 질문을 종료한다. 원문·확정 요구사항·답변 이력을 보고 사용자만 결정할 수 있는 중요한 미정 사항을 질문한다. 고정 필수 목적/사용자/핵심 기능/동작/범위가 의미상 충분하면 묻지 않는다. 보안·권한(주체와 허용 범위 구분)·법적·외부 계약·비가역 처리·핵심 의도 등 잘못 결정하면 실제 문제가 생기는 사항과 필수 검증 결정은 묻는다. 기술·완료 기준·합리적 구현 선택은 제안으로 넘긴다. 질문 하나에 한 결정, 대상 이름을 명확히 한다. 기존/답변/보류 질문과 표현이 달라도 같은 결정은 중복 질문하지 않는다. 서로 다른 결정이면 같은 대상도 가능하다. target은 제공된 targets 레지스트리의 정확한 FC/DT 키 하나(예: DT7)이며 FEAT-001 같은 기능 ID, 필드 이름, 여러 대상 이름 조합은 target이 아니다. evidence는 제공된 evidence 레지스트리의 정확한 E 키만(예: E2, 설명문이나 'E2: 인용문'이 아닌 키만; 합계 500자 이내), risk는 fixed만 null 가능. priority 0~100은 중요도와 의존 관계 기준. 사용자 사실을 확정하지 않는다.'''
SCOPE_RULES = '''\nquestion_scope.mode=full은 전체 검토, incremental은 changed_fields와 answered_this_turn으로 생긴 결정만 검토한다. 생략된 요구사항을 미정으로 보지 않는다. 새로운 동적 질문에는 fresh_evidence 키 하나 이상과 직접적인 변경 이유가 필요하다. 다른 필드라도 이번 변경의 직접 영향은 가능하나 예전 사실만으로 독립 질문을 만들지 않는다. 기존 질문은 모두 확인하여 이번 답변으로 실제 해결됐으면 새 근거로 종료, 나머지는 유지한다. closed_question_ids는 종료 이력으로 변경 대상이 아니다.'''
PLAN_PROMPT = RULES + SCOPE_RULES + '''\nquestions는 새 질문(각 tier 필수), existing_updates는 필요한 대기 질문 우선순위/해결 여부 변경만 반환한다. queue_tiers는 현재 활성 질문 전체의 P0/P1/P2 분류와 간단한 차단/보류 이유를 기록한다. 대기 중인 선택 질문은 P1/P2로 보류하되 기존 ID와 이력을 보존한다. 미변경 질문에 existing_updates를 내지 않는다. 새 질문의 물음이 실제로 결정하는 내용과 지정 target 필드(행 포함)가 의미상 일치하는지 확인한다. 출시 범위 선택을 물으면서 데이터 사용 목적에 연결하는 식으로 다른 결정 영역을 연결하지 않는다. 새 원문으로 실제 해결된 경우만 close=true, 충돌 해결은 답변 단계에 맡긴다. 없으면 빈 배열. 유효한 targets에 없는 UX 선택 사항은 P0로 만들지 말고 보류/제안으로 취급한다.'''
REVIEW_PROMPT = RULES + SCOPE_RULES + '''\n독립 의미 검토를 수행하며 draft가 비어도 중요 누락을 확인한다. queue_tiers는 독립적으로 재평가해 실제 진행을 막는 P0만 유지하고, P1/P2는 보류한다. 모든 활성 질문을 후보로 확인하고 누락된 등급 분류가 없는지 감사한다. 승인·반납·삭제 등 권한이 미정이면 '지금 설계 진행 불가'인지 '개발 전 결정 필요'인지 이유를 비교하여 P0 또는 P1로 결정한다. 원문상 확정된 권한은 다시 물어보지 않는다. 검토 완료 시 필수 다섯 요소, 중대 충돌, 활성 P0 존재를 각각 확인한다. 별도 FEAT로 나뉜 하위 동작이 출시 목록에 이름 그대로 없다는 사유만으로는 출시 제외 또는 P0 충돌이 아니다. 원문에 명시된 기능 동작과 출시 목록을 함께 해석한다. 정말 서로 모순되는 출시 범위라면 그 기능을 *명시적으로 제외한 원문 근거*를 제시하고 release_scope.features.included_in_release의 해당 기능 행을 질문 대상으로 사용한다. 명시적 배제 근거가 없는 단순 목록 생략은 blocking_issues/unresolved_reason으로 보고하지 않는다. 기존 질문의 분류를 임의로 모두 P0로 두지 않는다. 신규 질문에도 tier를 지정하고 선택 질문은 P1/P2로 분류한다. draft와 add의 모든 질문은 질문 문장·답변이 변경할 정확한 target 경로/행이 일치하는지 의미적으로 감사한다. 불일치하면 잘못된 draft DQ를 remove하고 올바른 targets 레지스트리의 FC/DT 키로 add한다. 정답 target이 레지스트리에 없으면 근거 없이 다른 필드에 억지로 연결하지 않고 blocking_issues에 필요한 대상 누락을 표시한다. 현재 이미 확정된 목적·정책과 출시 포함 결정을 구분한다. remove는 이번 draft.questions에 있는 DQ 키만, add는 새로운 질문만, 기존 대기 질문은 삭제 후 재생성하지 않는다. 이미 답변한 부분만 질문에서 빼고 아직 미정인 결정이 남았다면 revise_existing에 기존 Q ID와 좁혀진 단일 질문·이유·E 근거 키(반드시 evidence 레지스트리에 있는 키, fresh_evidence 포함)·priority를 넣는다. 기존 질문 ID와 원문 이력은 반드시 유지하며, 예전 정보에 없는 기간 기준 등을 임의 확정하지 않는다. cancel_closures는 부당한 종료 요청의 기존 질문 ID만 반환한다. 정상 질문은 자동 유지. incremental에서 변경과 무관한 질문을 제거한다. blocking_issues는 교정 후에도 검토할 수 없는 실제 오류만, 사용자 미결정/일반 설명은 제외한다. incremental에서 꼭 필요한 교차 영향의 문맥이나 근거가 생략되어 판단 불가할 때만 scope_insufficient: 구체적 이유를 기록해 전체 검토를 요청한다. 정상이면 빈 배열. invalid_draft_questions가 있으면 하나하나 invalid_draft_verdicts에 disposition=reissue(검토 결과 실제 필수 P0이며 반드시 replacement_target=add 질문의 올바른 FC/DT 대상과 E 근거를 포함) 또는 defer_optional(보류 가능한 P1/P2로서 이 판단의 구체적인 이유 제시)를 반환한다. 잘못된 FEAT ID/자유 텍스트 target을 다른 필드에 임의 대입하지 않는다. invalid_draft_questions를 검토하지 않고 complete=true로 응답하지 않는다.'''


def canonical_ref(value):
    """ref 관련 작업을 수행한다"""
    text=value.strip()
    return text.upper() if re.fullmatch(r'(?:FC|DT|DQ|E|Q)[0-9]+',text,re.I|re.ASCII) else value


def normalize_plan(plan,state=None,evidence=None):
    """plan 관련 작업을 정규화한다"""
    from extraction.response_normalization import unique, unique_models
    p=plan.model_copy(deep=True)
    def evidence_key(value):
        """key 관련 작업을 수행한다"""
        key=canonical_ref(value)
        if evidence is None or key in evidence:
            return key
        match=re.fullmatch(r'\s*(E[0-9]+)\s*:\s*(.+?)\s*',value,re.I|re.S)
        if match:
            candidate=canonical_ref(match.group(1))
            if candidate in evidence and match.group(2).strip()==evidence[candidate].strip():
                return candidate
        return key
    for q in p.questions:
        q.target=canonical_ref(q.target)
        q.evidence=unique([evidence_key(key) for key in q.evidence])
    for u in p.existing_updates:
        u.question_id=canonical_ref(u.question_id)
        u.evidence=unique([evidence_key(key) for key in u.evidence])
    p.questions=unique_models(p.questions)
    for u in p.queue_tiers:
        u.question_id=canonical_ref(u.question_id)
    seen=set();updates=[]
    for u in p.existing_updates:
        marker=(u.question_id,u.close,u.priority,frozenset(u.evidence))
        if marker not in seen:
            seen.add(marker);updates.append(u)
    p.existing_updates=updates
    if state is not None:
        closed=latest_terminal_ids(state)
        p.existing_updates=[u for u in p.existing_updates if not (u.close and u.question_id in closed)]
    return p


def registry(payload):
    """Give models only opaque allowed references, not internal row IDs to invent."""
    from planning_agent import windows
    targets, evidence = {}, {}
    for i, candidate in enumerate(payload['candidates'], 1):
        targets[f'FC{i}'] = {'fixed': candidate}
    for i, target in enumerate(payload['dynamic_targets'], 1):
        targets[f'DT{i}'] = target
    quotes = []
    for quote in payload['source_basis']:
        if len(quote) <= 400:
            quotes.append(quote)
        else:
            quotes.extend(quote[a:b].strip() for a,b in windows(quote,400,40))
    for i, quote in enumerate(dict.fromkeys(q for q in quotes if q.strip()), 1):
        evidence[f'E{i}'] = quote
    display_targets = {key: (value if 'fixed' in value else {k:v for k,v in value.items() if k != 'item'})
                       for key,value in targets.items()}
    context = {k:v for k,v in payload.items() if k not in ('candidates','dynamic_targets','source_basis')}
    return targets, evidence, {**context,'targets':display_targets,'evidence':evidence}


def _need_signature(q,target):
    """signature 관련 작업을 수행한다"""
    if 'fixed' in target:
        c=target['fixed']
        return (('fixed',c['key'],c.get('row_id')),' '.join(q.question.split()))
    return question_signature(Target(path=target['path'],item=target.get('item')),q.question)


def _known_need(q,target,state,original):
    """need 관련 작업을 수행한다"""
    if 'fixed' in target:
        c=target['fixed'];token=c.get('token') or (f"{c['key']}:{c['row_id']}" if c.get('row_id') else c['key'])
        return any(question_key(old)==token for old in [*state.questions,*state.deferred_questions])
    return dynamic_question_already_known(state,Target(path=target['path'],item=target.get('item')),q.question,original)


def _prefer_fresh(candidate, existing, fresh_keys):
    """fresh 관련 작업을 수행한다"""
    return (fresh_keys is not None and bool(fresh_keys.intersection(candidate.evidence))
            and not fresh_keys.intersection(existing.evidence))


def quarantine_invalid_drafts(plan,targets,evidence,template,state,original,allowed,fresh_keys=None):
    """Keep individually valid drafts; require a separate verdict for rejected ones.

    This is a planner-only fallback AFTER normal retries. Existing-state updates
    are never quarantined, and no FEAT/path/quote is silently re-bound.
    """
    plan=normalize_plan(plan,state,evidence)
    if plan.existing_updates:
        updates_only=plan.model_copy(update={'questions':[], 'queue_tiers':[]},deep=True)
        _,update_issues=convert(updates_only,targets,evidence,template,state,original,allowed,fresh_keys=fresh_keys)
        if update_issues:
            return None
    valid=[]; invalid=[]
    for i,q in enumerate(plan.questions,1):
        one=plan.model_copy(update={'questions':[q],'existing_updates':[],'queue_tiers':[]},deep=True)
        _,issues=convert(one,targets,evidence,template,state,original,allowed,fresh_keys=fresh_keys)
        if issues:
            invalid.append({'draft_id':f'BAD{i}', 'question':q.model_dump(mode='json'), 'issues':issues})
        else:
            valid.append(q)
    if not invalid:
        return None
    if not valid and not invalid:
        return None
    active_ids={q.id for q in state.questions}
    kept_tiers=[x for x in plan.queue_tiers if x.question_id in active_ids]
    rejected_targets={q['question']['target'] for q in invalid if 'question' in q}
    for t in plan.queue_tiers:
        if t.question_id not in active_ids:
            if t.question_id in rejected_targets:
                continue
            invalid.append({'draft_id':f'BAD-TIER-{len(invalid)+1}',
                            'queue_tier':t.model_dump(mode='json'),
                            'issues':['invalid_question_tier_reference']})
    kept=plan.model_copy(update={'questions':valid,'queue_tiers':kept_tiers},deep=True)
    return kept,invalid


def invalid_verdict_issues(review,invalid):
    """No rejected draft may disappear without an explicit independent ruling."""
    if not invalid:
        return ['unexpected_invalid_draft_verdict'] if review.invalid_draft_verdicts else []
    expected={x['draft_id']:x for x in invalid}
    seen=set();issues=[]
    for v in review.invalid_draft_verdicts:
        if v.draft_id not in expected or v.draft_id in seen:
            issues.append('invalid_draft_verdict_id:'+v.draft_id);continue
        seen.add(v.draft_id)
        if not v.reason.strip() or len(v.reason)>1200:
            issues.append('invalid_draft_verdict_reason:'+v.draft_id)
        if v.disposition=='defer_optional':
            draft=expected[v.draft_id]
            if draft.get('question',{}).get('tier')=='P0' and len(v.reason.strip())<24:
                issues.append('invalid_draft_p0_downgrade_requires_reason:'+v.draft_id)
        elif v.disposition=='reissue':
            if not v.replacement_target or v.replacement_target not in {q.target for q in review.add}:
                issues.append('invalid_draft_reissue_without_matching_add:'+v.draft_id)
    for key in expected.keys()-seen:
        issues.append('invalid_draft_unreviewed:'+key)
    return issues


def prune_validated_plan(plan,targets,state,original,*,fresh_keys=None,evidence=None):
    """Once all candidates are structurally valid, keep exact decisions once.

    Preserve input order; if a duplicate has fresh proof, keep that complete
    candidate instead of a stale-proof copy. No synthesized evidence or priority.
    """
    plan=normalize_plan(plan,state,evidence)
    keep={}
    for q in plan.questions:
        target=targets[q.target]
        if _known_need(q,target,state,original):continue
        marker=_need_signature(q,target)
        if marker not in keep or _prefer_fresh(q,keep[marker],fresh_keys):keep[marker]=q
    return plan.model_copy(update={'questions':list(keep.values())},deep=True)


def convert(plan, targets, evidence, template, state, original, allowed_dynamic, *, fresh_keys=None):
    """Convert references without guessing. Validate before dropping duplicates.

    For incremental final review, fresh_keys is the set of real source spans
    added by the answer. An unrelated old-fact-only draft must be removed by
    the semantic reviewer; it cannot become a newly registered question.
    """
    plan=normalize_plan(plan,state,evidence)
    out, errors, accepted = RequiredAssessment(), [], {}
    from updates.scope_consistency import verified_scope_question
    public_by_internal = state.public_ids
    requirements = template.export(state)
    for i,q in enumerate(plan.questions):
        prefix = f'question[{i}]'
        target = targets.get(q.target)
        if target is None:
            errors.append(prefix+':unknown_target'); continue
        if not one_decision(q.question) or not 1 <= len(q.reason.strip()) <= 1200:
            errors.append(prefix+':invalid_text'); continue
        if _known_need(q,target,state,original):
            continue
        if any(k not in evidence for k in q.evidence):
            errors.append(prefix+':unknown_evidence'); continue
        if 'fixed' not in target and not verified_scope_question(
                {**target, 'public_id':public_by_internal.get(target.get('item'))},
                [evidence[k] for k in q.evidence], requirements):
            errors.append(prefix+':scope_requires_explicit_exclusion_evidence'); continue
        if 'fixed' in target:
            c=target['fixed']
            item=RequiredQuestion(key=c['key'],row_id=c.get('row_id'),question=q.question,
                                  priority=q.priority,decision_topics=[q.question])
        else:
            item = DynamicQuestion(path=target['path'],row_id=target.get('item'),question=q.question,
                        reason=q.reason,priority=q.priority,risk=q.risk,
                        basis=[evidence[k] for k in dict.fromkeys(q.evidence)],decision_topics=[q.question])
            if err := assessment_issue(template,state,item,allowed_dynamic):
                errors.append(prefix+':'+err); continue
        marker=_need_signature(q,target)
        if marker not in accepted or _prefer_fresh(q,accepted[marker][0],fresh_keys):
            accepted[marker]=(q,item,prefix,'fixed' in target)
    for q,item,prefix,is_fixed in accepted.values():
        if is_fixed:
            out.questions.append(item)
        else:
            if fresh_keys is not None and not fresh_keys.intersection(q.evidence):
                errors.append(prefix+':new_question_requires_current_change_evidence'); continue
            out.dynamic_questions.append(item)
    active = {q.id:q for q in state.questions}
    updated = set()
    for u in plan.existing_updates:
        q = active.get(u.question_id)
        if q is None or u.question_id in updated or any(k not in evidence for k in u.evidence):
            errors.append('invalid_existing_update:'+u.question_id); continue
        updated.add(u.question_id)
        if u.close and (not u.evidence or q.kind=='conflict'):
            errors.append('invalid_close:'+u.question_id); continue
        if u.close and fresh_keys is not None and not fresh_keys.intersection(u.evidence):
            errors.append('existing_question_requires_new_evidence:'+u.question_id);continue
        out.existing_decisions.append(ExistingQuestionDecision(question_id=u.question_id,needed=not u.close,
                    priority=u.priority,basis=[evidence[k] for k in dict.fromkeys(u.evidence)] or None))
    errors.extend(question_decision_issues(state,out,original))
    return out,errors


def patch_plan(plan, review):
    """Keep untouched questions byte-for-byte; reviewer returns changes only."""
    if not review.complete:
        raise ValueError('question_review_unresolved:'+ (review.unresolved_reason or 'no reason'))
    from extraction.response_normalization import unique
    keys = {f'DQ{i}':q for i,q in enumerate(plan.questions,1)}
    remove=unique([canonical_ref(k) for k in review.remove])
    keep_open=unique([canonical_ref(k) for k in review.keep_open])
    if not set(remove) <= set(keys):
        raise ValueError('unknown_or_duplicate_question_patch')
    if set(keep_open) & set(remove) or not set(keep_open) <= (set(keys) | {u.question_id for u in plan.existing_updates if u.close}):
        raise ValueError('unknown_close_veto')
    return normalize_plan(Plan(questions=[q.model_copy(deep=True) for k,q in keys.items() if k not in remove]+[q.model_copy(deep=True) for q in review.add],
                existing_updates=[u.model_copy(update={'close':False,'evidence':[]},deep=True) if u.question_id in keep_open else u.model_copy(deep=True) for u in plan.existing_updates],
                queue_tiers=[u.model_copy(deep=True) for u in (review.queue_tiers or plan.queue_tiers)]))


def validated_revisions(review, plan, targets, evidence, state, original, fresh_keys):
    """Validate existing-question refinements without deleting their identity/history.

    A reviewer may revise only an active question that is still unresolved and
    whose target received verified new evidence. No LLM-supplied evidence IDs
    are guessed or silently re-bound.
    """
    from questions.decision_flow import one_decision, question_basis
    from core.planning_schema import Target
    from questions.required_check import question_key
    from extraction.response_normalization import unique
    errors=[]; revisions=[]; seen=set()
    active={q.id:q for q in state.questions}
    pending_closures={u.question_id for u in plan.existing_updates if u.close}
    for r in review.revise_existing:
        qid=canonical_ref(r.question_id)
        if qid in seen or qid not in active or qid in pending_closures:
            errors.append('invalid_question_revision:'+qid);continue
        seen.add(qid);q=active[qid];target=q.target or q.answer_hint
        if (not target or question_key(q) or q.kind=='conflict'
                or target.path not in {v['path'] for v in targets.values() if 'path' in v}):
            errors.append('invalid_revision_target:'+qid);continue
        if not one_decision(r.question) or not r.reason.strip() or len(r.reason)>1200:
            errors.append('invalid_revision_text:'+qid);continue
        keys=unique([canonical_ref(k) for k in r.evidence])
        if not keys or any(k not in evidence for k in keys):
            errors.append('revision_unknown_evidence:'+qid);continue
        if fresh_keys is not None and not (set(keys)&fresh_keys):
            errors.append('revision_requires_fresh_evidence:'+qid);continue
        cell=state.cells.get(target.key());old=original.cells.get(target.key()) if original else None
        prior={(x.source,x.digest,x.start,x.end,x.quote) for x in old.evidence} if old else set()
        own_fresh={x.quote for x in cell.evidence if (x.source,x.digest,x.start,x.end,x.quote) not in prior} if cell else set()
        if not own_fresh or not any(evidence[k] in own_fresh for k in keys):
            errors.append('revision_unrelated_change:'+qid);continue
        refs=question_basis(state,[evidence[k] for k in keys],target)
        if not refs:
            errors.append('revision_unverified_basis:'+qid);continue
        if q.text.strip()==r.question.strip():
            continue
        revisions.append({'question_id':qid,'text':r.question.strip(),
                          'reason':r.reason.strip(),'priority':r.priority,
                          'evidence':refs})
    return revisions,errors


def apply_question_revisions(state, assessment):
    """Call on the trial state only, after all reviews have passed."""
    revisions=getattr(assessment,'_question_revisions',[])
    for r in revisions:
        q=next((q for q in state.questions if q.id==r['question_id']),None)
        if q is None:raise ValueError('question_revision_missing_at_commit:'+r['question_id'])
        q.text=r['text']
        seen={(x.source,x.digest,x.start,x.end,x.quote) for x in q.evidence}
        for ref in r['evidence']:
            key=(ref.source,ref.digest,ref.start,ref.end,ref.quote)
            if key not in seen:q.evidence.append(ref);seen.add(key)
        state.question_priorities[q.id]=r['priority']
        if q.id in state.dynamic_question_reasons:
            state.dynamic_question_reasons[q.id]=r['reason']


def assess_simple(model,payload,template,state,original,budget,trace,check,*,incremental=False):
    """simple 관련 작업을 수행한다"""
    from planning_graph import run_model_stage
    from planning_agent import ModelError
    from questions.required_check import dynamic_targets
    from hashlib import sha256
    targets,evidence,context = registry(payload)
    targets,evidence,context,fresh_keys = project_context(template,state,targets,evidence,context,
                                                         original=original,enabled=incremental)
    allowed = dynamic_targets(template,state)
    if trace:
        trace('simple_question_contract',{'targets':len(targets),'evidence':len(evidence),
            'scope':context['question_scope'],
            'plan_prompt_sha256':sha256(PLAN_PROMPT.encode()).hexdigest(),
            'review_prompt_sha256':sha256(REVIEW_PROMPT.encode()).hexdigest()})
    def validate(p,*,final=False):
        """validate 관련 작업을 검증한다"""
        a,e=convert(p,targets,evidence,template,state,original,allowed,
                    fresh_keys=fresh_keys if final else None)
        return e+check(a)
    raw = run_model_stage(model.simple_question_plan,context,Plan,'simple_question_plan',trace=trace,budget=budget,check=validate,preserve_rejected=True)
    initial_assessment, initial_issues = convert(raw,targets,evidence,template,state,original,allowed)
    initial_issues += check(initial_assessment)
    invalid_drafts=[]
    if initial_issues:
        quarantine=quarantine_invalid_drafts(raw,targets,evidence,template,state,original,allowed)
        if quarantine is None:
            raise ModelError('질문 생성 검증 오류: 기존 상태 변경은 격리할 수 없어.',kind='candidate_validation',issues=initial_issues)
        raw,invalid_drafts=quarantine
        if trace:
            trace('question_invalid_draft_quarantined',{'invalid_drafts':invalid_drafts,'original_issues':initial_issues})
    plan = prune_validated_plan(raw,targets,state,original,fresh_keys=fresh_keys,evidence=evidence)
    if trace:
        trace('simple_question_normalized',{'discarded_questions':len(raw.questions)-len(plan.questions),
            'discarded_existing_updates':len(raw.existing_updates)-len(plan.existing_updates),
            'closed_noop_ids':sorted({canonical_ref(u.question_id) for u in raw.existing_updates if u.close and canonical_ref(u.question_id) in latest_terminal_ids(state)})})
        trace('simple_question_draft',{'response':plan.model_dump(mode='json')})
    def normalize_scope_review(r):
        """scope review 관련 작업을 정규화한다"""
        from updates.scope_consistency import review_omission_claim
        fid=review_omission_claim(r,context['requirements'],list(evidence.values()))
        if fid is None:
            return r
        if trace:
            trace('scope_list_omission_not_a_conflict',{'feature_id':fid,
                'reason':r.unresolved_reason})
        return r.model_copy(update={'complete':True,'unresolved_reason':None})
    def validate_review(r):
        """review 관련 작업을 검증한다"""
        r=normalize_scope_review(r)
        _, revision_errors = validated_revisions(r,plan,targets,evidence,state,original,fresh_keys)
        if revision_errors:return revision_errors
        invalid_issues=invalid_verdict_issues(r,invalid_drafts)
        if invalid_issues:return invalid_issues
        if (fresh_keys is not None and not r.complete and r.unresolved_reason
                and r.unresolved_reason.startswith('scope_insufficient:')
                and r.unresolved_reason.partition(':')[2].strip()):
            raise ModelError('전체 질문 검토 범위가 필요해.',kind='question_scope_insufficient',issues=[r.unresolved_reason])
        try:
            from questions.question_policy import validate_queue_tiers
            patched=patch_plan(plan,r)
            return validate_queue_tiers(patched.queue_tiers,state)+validate(patched,final=True)
        except ValueError as e:
            return [str(e)]
    try:
        review = run_model_stage(model.simple_question_review,{**context,
            'draft':{f'DQ{i}':q.model_dump(mode='json') for i,q in enumerate(plan.questions,1)},
            'existing_updates':[u.model_dump(mode='json') for u in plan.existing_updates],
            'queue_tiers':[u.model_dump(mode='json') for u in plan.queue_tiers],
            'invalid_draft_questions':invalid_drafts},
            Review,'simple_question_review',trace=trace,budget=budget,check=validate_review)
    except ModelError as exc:
        if fresh_keys is None or exc.kind!='question_scope_insufficient' or budget.remaining<2:
            raise
        budget.remaining-=2
        if trace:trace('simple_question_scope_escalated',{'reason':list(exc.issues),'remaining':budget.remaining})
        return assess_simple(model,payload,template,state,original,budget,trace,check,incremental=False)
    review = normalize_scope_review(review)
    if invalid_verdict_issues(review,invalid_drafts):
        raise ModelError('격리된 질문의 독립 검토 실패',kind='candidate_validation',issues=invalid_verdict_issues(review,invalid_drafts))
    revised = patch_plan(plan,review)
    from questions.question_policy import validate_queue_tiers
    tier_errors=validate_queue_tiers(revised.queue_tiers,state)
    if tier_errors:
        raise ModelError('질문 우선순위 분류 오류',kind='candidate_validation',issues=tier_errors)
    revisions, revision_errors = validated_revisions(review,plan,targets,evidence,state,original,fresh_keys)
    assessment, errors = convert(revised,targets,evidence,template,state,original,allowed,fresh_keys=fresh_keys)
    if errors or revision_errors or check(assessment):
        raise ModelError('질문 교정 결과 검증 실패',kind='candidate_validation',issues=errors+revision_errors+check(assessment))
    assessment._question_revisions=revisions
    assessment._queue_tiers=[t.model_dump() for t in revised.queue_tiers]
    assessment._new_question_tiers=[{'target':targets[q.target], 'question':q.question, 'tier':q.tier} for q in revised.questions]
    if trace:
        trace('simple_question_review_accepted',{'patch':review.model_dump(mode='json'),
              'kept':len(plan.questions)-len(review.remove),'added':len(review.add),
              'final_count':len(assessment.questions)+len(assessment.dynamic_questions)})
    return assessment
