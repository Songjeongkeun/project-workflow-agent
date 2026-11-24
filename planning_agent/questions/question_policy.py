"""질문 우선순위 및 보류 정책을 검증·적용한다.

Question termination policy: ask only blocking P0 questions.

P1/P2 stay in the existing deferred queue; no approved requirement is invented.
Never auto-defer an unresolved conflict or a fixed mandatory question.
"""
from questions.required_check import question_key, KEYS


def validate_queue_tiers(tiers,state):
    """Validate LLM tier references without changing question lifecycle.

    Active questions can be re-tiered by the normal workflow. A previously
    deferred question may be mentioned again as P1/P2, but is already out of
    the active queue, so the repeated classification is informational only.
    Re-promoting a deferred question to P0 requires an explicit reopening
    workflow; never silently reactivate it (or silently discard that intent).
    """
    active={q.id:q for q in state.questions}
    deferred={q.id:q for q in state.deferred_questions}
    seen=set();issues=[]
    for item in tiers:
        qid=item.question_id
        if qid in seen or not item.reason.strip():
            issues.append('invalid_question_tier:'+qid)
        elif qid in active:
            if item.tier!='P0' and (active[qid].kind=='conflict' or
                    (question_key(active[qid]) or '').split(':',1)[0] in KEYS):
                issues.append('blocking_question_cannot_be_deferred:'+qid)
        elif qid in deferred:
            if item.tier=='P0':
                issues.append('deferred_question_requires_reopening:'+qid)
        else:
            issues.append('invalid_question_tier:'+qid)
        seen.add(qid)
    return issues


def apply_question_tiers(state,assessment,trace=None):
    """Move reviewed optional questions out of interactive queue, preserving ID and history."""
    from core.planning_schema import Target
    approved={x['question_id']:x for x in getattr(assessment,'_queue_tiers',[])}
    fresh=getattr(assessment,'_new_question_tiers',[])
    for q in list(state.questions):
        token=question_key(q)
        if q.kind=='conflict' or (token and token.split(':',1)[0] in KEYS):
            continue
        decision=approved.get(q.id)
        if decision is None:
            target=q.target or q.answer_hint
            for entry in fresh:
                t=entry['target']
                if 'fixed' in t or entry['question'].strip()!=q.text.strip():
                    continue
                if target==Target(path=t['path'],item=t.get('item')):
                    decision=entry
                    break
        if not decision or decision['tier']=='P0':
            continue
        state.questions.remove(q)
        if not any(old.id==q.id for old in state.deferred_questions):
            state.deferred_questions.append(q)
        if trace:
            trace('question_tier_deferred',{'id':q.id,'tier':decision['tier'],
                'reason':decision.get('reason','optional_decision')})
    return not state.questions


def termination_readiness(state, document):
    """Observable, fail-closed stop gate; never makes missing facts up.

    A deferred P1/P2 item is not a blocker. Missing fixed essentials, an
    unresolved conflict, an active P0 or a failed assessment remain blockers.
    This is an audit/reporting decision, not a means to silently close Q IDs.
    """
    from questions.required_check import missing_candidates
    blockers=[]
    if missing_candidates(document,state):
        blockers.append('missing_mandatory_requirements')
    if any(q.kind=='conflict' for q in state.questions):
        blockers.append('unresolved_conflict')
    if state.questions:
        blockers.append('active_p0_or_unreviewed_questions')
    if state.assessment_pending:
        blockers.append('assessment_pending')
    return {'ready':not blockers,'blockers':blockers,
            'active_questions':len(state.questions),
            'deferred_questions':len(state.deferred_questions)}
