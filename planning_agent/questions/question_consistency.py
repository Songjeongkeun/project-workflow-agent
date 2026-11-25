"""질문 답변과 요구사항 사이의 일관성을 검사한다.

Guarded resolution of multiple active questions answered by one user turn.

No lexical similarity heuristics and no automatic same-target closure: a second
independent semantic review must explicitly approve each resolution. Python
checks that the proposed approval is grounded and that the related question
shares the main answered target before any state mutation.
"""
from questions.required_check import record_required


def verify_related_resolutions(before, candidate, plan, resolutions, source_text):
    """related resolutions 관련 작업을 수행한다"""
    active = {q.id: q for q in before.questions}
    remaining = {q.id for q in candidate.questions}
    primary = set()
    for decision in plan.question_decisions:
        if decision.status != 'answered' or decision.question_id not in active:
            continue
        q = active[decision.question_id]
        primary_target = q.target or q.answer_hint
        if primary_target is not None and any(
            op.reply_to == q.id and op.path == primary_target.path
            and op.item == primary_target.item for op in plan.operations
        ):
            primary.add(primary_target.key())
    accepted, errors, seen = [], [], set()
    for item in resolutions:
        qid = item.question_id
        q = active.get(qid)
        if (qid in seen or q is None or qid not in remaining
                or q.kind == 'conflict' or not (q.target or q.answer_hint)
                or (q.target or q.answer_hint).key() not in primary):
            errors.append('invalid_related_question_resolution:' + qid)
        elif (not item.reason.strip() or len(item.reason) > 1200
                or not item.quote.strip() or item.quote not in source_text):
            errors.append('ungrounded_related_question_resolution:' + qid)
        else:
            accepted.append(q)
        seen.add(qid)
    return accepted, errors


def apply_related_resolutions(candidate, questions, source_text):
    """related resolutions 관련 작업을 적용한다"""
    for q in questions:
        matches = [x for x in candidate.questions if x.id == q.id]
        if len(matches) != 1:
            raise ValueError('related_question_not_active:' + q.id)
        candidate.questions = [x for x in candidate.questions if x.id != q.id]
        record_required(candidate, q, 'answered', source_text)
