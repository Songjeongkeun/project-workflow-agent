"""기존 요구사항에 대한 사용자 답변·수정 요청을 반영한다."""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from core.planning_schema import (Proposal, TurnPlan, TurnReview, IDENTITIES, FEATURE_LINK_PATHS,
                             validate_transition, ensure_public_ids, SYSTEM_OWNED_PATHS, Question, Target, REVIEW_VERSION, TurnRepair)
from core.planning_logic import (Source, TurnResult, apply_batch, context, source_evidence, review_segments,
                            coverage_issues, data_review_inventory, data_review_issues)
from questions.required_check import question_key, record_required, latest_required
from extraction.field_audit import (field_review_request,field_review_issues,DELIVERY_FIELDS,assess_platform,
                         platform_consistency,optional_turn_field_inventory)
from storage.evidence_store import turn_operation_grounding, turn_candidate_evidence
from extraction.response_normalization import normalize_turn_plan, normalize_turn_review, normalize_supporting_operation
from updates.turn_repair import patch_turn_plan, align_repair_indices


class QuestionTargetAlignment(BaseModel):
    """Independent approval only; never a model-owned target rewrite."""
    model_config = ConfigDict(extra='forbid')
    aligned: bool
    reason: str


QUESTION_ALIGNMENT_PROMPT = """독립 검토: 이미 저장된 질문의 답변 경로가 질문의 의미와 일치하지 않는지를 검사한다.
질문의 실제 문장, 기존 저장 대상, 사용자의 최신 원문, 변경안의 proposed_target/value를 함께 대조한다.
모델이 제안한 다른 필드에 사용자가 명시적으로 답한 사실을 저장하는 것이 정확하고, 기존 질문에
저장된 대상이 질문의 의미와 명백히 어긋나는 경우에만 aligned=true다.
현재 질문과 무관한 기능, 원문에 없는 추가/삭제, 다른 질문의 답변, 단순히 필드가 가까운 경우,
모호하거나 상충하는 경우는 false다. 기존 데이터나 질문 내용의 임의 변경을 승인하지 않는다.
boolean aligned와 구체적인 reason만 반환한다. Python이 모든 실제 변경과 근거를 별도 검증한다."""


class TechnologyDetail(BaseModel):
    model_config = ConfigDict(extra='forbid')
    choice: str
    area: str | None = None
    reason: str | None = None


class AnswerInterpretation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    decision: Literal['answered', 'deferred', 'clarify']
    spans: list[str] = Field(default_factory=list)
    tech_choice: str | None = None
    tech_choices: list[str] = Field(default_factory=list)
    tech_details: list[TechnologyDetail] = Field(default_factory=list)
    note: str | None = None


def is_explicit_edit(text):
    """명시적인 수정 명령 구문만 식별한다."""
    return bool(re.match(r'^\s*/(?:edit|new)\b', text, re.I))


_EXPLICIT_QUESTION_ID = re.compile(r'(?<![A-Za-z0-9])Q\d{4,}(?!\d)', re.I)


def active_reply_context(state, text):
    """reply context 관련 작업을 수행한다"""
    if not state.questions or is_explicit_edit(text) or _EXPLICIT_QUESTION_ID.search(text):
        return None
    active = state.questions[0]
    concise = text.strip()
    if len(concise) > 24 or len(concise.split()) > 5:
        return None
    if not re.match(r'^(?:응|웅|네|넵|예|ㅇㅇ|아니|아뇨|ㄴㄴ|맞아|그렇게|제외|포함|삭제|허용|불허|필요|불필요)(?:\b|\s|$|해|할|하지)', concise, re.I):
        return None
    return active.id


def _choose(state, text):
    """질문 번호가 명시된 경우에만 해당 질문을 찾는다."""
    match = re.match(r'^\s*(Q\d{4,})\s*[:：]', text, re.I)
    if not match:
        return None
    qid = match[1].upper()
    return next((q for q in [*state.questions, *state.deferred_questions] if q.id == qid), None)


def is_question_reply(state, text):
    """명시된 질문 번호의 존재 여부를 확인한다."""
    return _choose(state, text) is not None


def scope_decision(text):
    """문맥 없는 자연어 범위 판단을 수행하지 않는다."""
    return None


def technology_records(parsed, text):
    """기술 선택값의 원문 인용을 검사한다."""
    records = list(parsed.tech_details)
    if not records:
        names = parsed.tech_choices or ([parsed.tech_choice] if parsed.tech_choice else [])
        records = [TechnologyDetail(choice=name) for name in names]
    issues = []
    for row in records:
        if any(value is not None and value not in text for value in (row.choice, row.area, row.reason)):
            issues.append('technology_ungrounded_value')
    return records, issues


def resume_required(processor, original, text):
    """저장된 질문 번호나 확인 키로 질문을 다시 연다."""
    result = TurnResult(state=original.model_copy(deep=True))
    address = re.sub(r'^\s*/resume\s*', '', text, flags=re.I).strip()
    question = next((q for q in original.deferred_questions if q.id == address.upper()), None)
    if question is None:
        event = next((e for e in reversed(original.required_answer_history)
                      if e.question.id == address.upper() or e.token.lower() == address.lower()), None)
        if event and (event.status in ('cancelled', 'superseded')
                      or latest_required(original,event.token).question.id != event.question.id):
            result.reviews.append('종료되거나 다른 질문으로 대체된 이력은 다시 열 수 없어.')
            return result
        question = event.question if event else None
    if question is None:
        token = address.lower()
        key, separator, public = token.partition(':')
        token = key + ':' + public.upper() if separator else key
        if token in original.deferred_required + original.resolved_required:
            paths = {'purpose':'overview.summary', 'users':'user_problem.target_users',
                     'behavior':'release_scope.features.behavior_and_value',
                     'scope':'release_scope.features.included_in_release',
                     'done':'release_scope.features.done_criteria'}
            rid = next((rid for rid,fid in original.public_ids.items() if fid == public.upper()), None) if separator else None
            if ((key in ('features','technology') and not separator)
                    or (key in ('purpose','users') and not separator)
                    or (key in ('behavior','scope','done') and separator and rid)):
                target = Target(path=paths[key],item=rid) if key in paths else None
                used = {q.id for q in [*original.questions,*original.deferred_questions,*original.retired_optional_questions]}
                used.update(e.question.id for e in original.required_answer_history)
                number = original.next_question
                while f'Q{number:04d}' in used:
                    number += 1
                question = Question(id=f'Q{number:04d}',kind='missing',target=target,
                                    text=f'[필수확인:{token}] 이 항목을 다시 확인해 줘.')
    if question is None:
        result.reviews.append('다시 열 질문의 Q번호를 확인해 줘.')
        return result
    qid = question.id
    target = question.target or question.answer_hint
    if target and target.item and target.item not in original.rows:
        result.reviews.append('삭제된 항목의 질문은 다시 열 수 없어.')
        return result
    if any(q.id == qid for q in original.questions):
        result.updates.append(f'{qid} 질문은 이미 열려 있어.')
        return result
    state = result.state
    token = question_key(question)
    state.deferred_questions = [q for q in state.deferred_questions if q.id != qid]
    state.deferred_required = [value for value in state.deferred_required if value != token]
    state.resolved_required = [value for value in state.resolved_required if value != token]
    if not any(q.id == qid for q in state.questions):
        state.questions.append(question.model_copy(deep=True))
    record_required(state, question, 'reopened', text)
    result.saved = 1
    result.updates.append(f'{qid} 질문을 다시 열었어.')
    return result


def process_question_reply(processor, original, text, *, trace=None, budget=None):
    """질문 답변을 공통 입력 처리기로 전달한다."""
    return process_turn(processor, original, text, trace=trace, budget=budget)


def process_turn(processor, original, text, source_name=None, *, trace=None, budget=None):
    """입력을 모델로 해석하고 검증된 변경안만 원자적으로 적용한다."""
    from planning_graph import run_model_stage, RecoveryBudget
    from planning_agent import windows
    from proposals.proposal_flow import digest

    if budget is None:
        budget = RecoveryBudget()
    if source_name is None and re.match(r'^\s*/resume\b', text, re.I):
        return resume_required(processor, original, text)
    source = Source(text=text.strip(), name=source_name, allow_question_reply=False)
    if source_name and source.digest in original.documents:
        return TurnResult(state=original.model_copy(deep=True), replay=True)
    if processor.plan_turn is None:
        return TurnResult(state=original.model_copy(deep=True), reviews=['자연어 변경·답변을 검토할 모델이 연결되지 않았어.'])
    result = TurnResult(state=original.model_copy(deep=True))
    all_windows_reviewed = True
    all_requests_verified = True
    known = {q.id: q for q in [*original.questions, *original.deferred_questions]}
    for entry in reversed(original.required_answer_history):
        if entry.status not in ('cancelled','superseded') and latest_required(original,entry.token) == entry:
            known.setdefault(entry.question.id, entry.question)
    for start, end in windows(source.text):
        before = result.state.model_copy(deep=True)
        payload = {'source_text':source.text[start:end], 'source_name':source_name,
                   'source_segments':review_segments(source.text[start:end]),
                   'previous_context':source.text[max(0, start-800):start],
                   'requirements':processor.template.export(before), 'fields':processor.fields,
                   'identities':{key:value for key,value in IDENTITIES.items()
                                 if key in processor.template.rows},
                   'input_intent':'edit' if is_explicit_edit(source.text) else None,
                   **context(before),
                   'question_history':[entry.model_dump(mode='json') for entry in before.required_answer_history],
                   'pending_proposal': next((batch.model_dump(mode='json') for batch in reversed(before.advisory_batches) if batch.status == 'pending'), None),
                   'pending_inputs': [request.model_dump(mode='json') for request in before.input_requests if request.status == 'pending'],
                   'deferred_questions':[q.model_dump(mode='json') for q in before.deferred_questions]}
        active_reply = active_reply_context(before, source.text[start:end]) if not source_name else None
        if active_reply:
            payload['questions'] = [q for q in payload['questions'] if q['id'] == active_reply]
            payload['answer_binding'] = {
                'question_id': active_reply,
                'mode': 'active_single_question',
                'instruction': '이 입력을 질문에 대한 답으로 해석할 때는 이 ID만 사용한다. '
                               '다른 질문을 추정하지 않는다. 별도 요구사항이면 질문을 완료하지 않는다.',
            }
        elif before.questions:
            payload['answer_binding'] = {
                'question_id': before.questions[0].id,
                'mode': 'context_only',
                'instruction': '현재 화면 질문의 맥락이다. 명시적으로 다른 질문에 답하거나 독립 편집한 경우는 해당 의도를 유지한다.',
            }
        if trace:
            trace('turn_context',{'source_text':source.text[start:end],
                'source_digest':source.digest,'requirements_before':payload['requirements'],
                'questions_before':payload['questions']})
        trial = None
        accepted_plan = None
        review_attempts = 0
        platform_cache = {}
        repair_depth = 0
        approved_question_alignment = {}

        def check(plan):
            """변경안의 형식과 대상을 검사하고 독립 의미 검토를 요청한다."""
            nonlocal trial, review_attempts, accepted_plan, repair_depth
            trial = None
            accepted_plan = None
            approved_question_alignment.clear()
            plan, normalization_issues, conversions = normalize_turn_plan(plan, processor.template, before, known)
            grounding, grounding_issues = turn_operation_grounding(plan.operations, before, source, start, end)
            if trace and conversions:
                trace('turn_normalized', {'conversions':conversions})
            if normalization_issues or grounding_issues:
                return normalization_issues + grounding_issues
            if plan.proposal_revision is not None or plan.intent == 'proposal_revision':
                if (plan.proposal_revision is None or plan.intent != 'proposal_revision'
                        or plan.operations or plan.question_decisions or plan.clarification):
                    return ['proposal_revision_must_not_write_requirements']
                from proposals.proposal_flow import revise_proposal
                candidate = revise_proposal(processor,before,plan.proposal_revision,source,start,end,
                                            trace=trace,budget=budget,request_decisions=plan.request_decisions)
                if candidate.reviews:
                    return candidate.reviews
                trial = candidate
                accepted_plan = plan
                return []
            if plan.intent == 'clarify':
                partial = plan.question_decisions
                if (not plan.operations and plan.clarification and not plan.request_decisions
                        and partial and all(d.status == 'partial' and d.target is None
                                        and any(q.id == d.question_id for q in before.questions)
                                        for d in partial)
                        and len({d.question_id for d in partial}) == len(partial)):
                    plan.question_decisions = []
                    if trace:
                        trace('turn_normalized', {'conversions':['clarify_partial_preserved_question']})
                if (plan.operations or plan.question_decisions or plan.request_decisions
                        or not plan.clarification):
                    return ['clarification_must_preserve_state']
            if is_explicit_edit(source.text) and (plan.question_decisions or any(op.reply_to for op in plan.operations)):
                return ['explicit_edit_cannot_answer_question']
            if active_reply and plan.intent in ('answer', 'defer', 'mixed'):
                answered_elsewhere = [d.question_id for d in plan.question_decisions
                                      if d.question_id != active_reply]
                answered_elsewhere.extend(op.reply_to for op in plan.operations
                                           if op.reply_to and op.reply_to != active_reply)
                if answered_elsewhere:
                    return [f'active_question_mismatch:현재 화면의 질문은 {active_reply}야. '
                            f'다른 질문 {sorted(set(answered_elsewhere))}에 대한 답변으로 해석하지 마. '
                            '현재 질문에 대한 답이면 그 질문 ID와 target을 쓰고, '
                            '독립 변경이면 reply_to 없이 requirements로 처리해.']
            decisions = {d.question_id:d for d in plan.question_decisions}
            if len(decisions) != len(plan.question_decisions) or any(qid not in known for qid in decisions):
                return ['unknown_or_duplicate_question']
            if any(op.reply_to and op.reply_to not in decisions for op in plan.operations):
                return ['answer_missing_question_decision']
            candidate = TurnResult(state=before.model_copy(deep=True))
            bound_questions = {}
            for qid, decision in decisions.items():
                q = known[qid].model_copy(deep=True)
                writes = [op for op in plan.operations if op.reply_to == qid]
                if decision.status in ('deferred', 'reopened', 'cancelled') and writes:
                    return ['non_answer_cannot_write_question_value']
                if decision.status == 'cancelled' and q.kind == 'conflict':
                    return ['conflict_requires_explicit_answer']
                if decision.status == 'answered' and not writes:
                    return ['answered_question_requires_bound_operation']
                target = q.target or q.answer_hint
                if decision.target is not None:
                    if target is not None and target != decision.target:
                        return ['cannot_rebind_existing_question_target']
                    target = decision.target
                    parent = target.path.rpartition('.')[0]
                    row = before.rows.get(target.item) if target.item else None
                    if (target.path not in processor.fields or target.path in SYSTEM_OWNED_PATHS
                            or (parent in processor.template.rows and (row is None or row.path != parent))
                            or (parent not in processor.template.rows and target.item is not None)):
                        return ['invalid_question_binding']
                    q.answer_hint = target
                bound_questions[qid] = q
                if writes:
                    candidate.state.questions = [q if old.id == qid else old for old in candidate.state.questions]
                    if not any(old.id == qid for old in candidate.state.questions):
                        candidate.state.questions.append(q)
                if target and writes and not any(op.path == target.path and op.item == target.item for op in writes):
                    alternatives = {(op.path, op.item) for op in writes}
                    eligible = (q.kind == 'clarify' and q.target is None and q.answer_hint is not None
                                and len(alternatives) == 1 and len(writes) == 1
                                and decision.status == 'answered' and qid in {x.id for x in before.questions}
                                and not is_explicit_edit(source.text) and getattr(processor,'align_question_target',None) is not None)
                    if not eligible:
                        return ['answer_target_mismatch']
                    alternate = Target(path=writes[0].path,item=writes[0].item)
                    parent = alternate.path.rpartition('.')[0]
                    row = before.rows.get(alternate.item) if alternate.item else None
                    if (alternate.path not in processor.fields or alternate.path in SYSTEM_OWNED_PATHS
                            or (parent in processor.template.rows and (row is None or row.path != parent))
                            or (parent not in processor.template.rows and alternate.item is not None)):
                        return ['answer_target_mismatch']
                    from planning_agent import ModelError
                    audit_payload = {'question_id': qid, 'question_text': q.text,
                                     'original_target': target.model_dump(mode='json'),
                                     'proposed_target': alternate.model_dump(mode='json'),
                                     'proposed_value': writes[0].value,
                                     'proposed_action': writes[0].action,
                                     'source_text': source.text[start:end],
                                     'existing_requirements': processor.template.export(before)}
                    def alignment_check(verdict):
                        """check 관련 작업을 수행한다"""
                        return [] if verdict.aligned and verdict.reason.strip() else ['question_target_alignment_rejected']
                    try:
                        audit = run_model_stage(processor.align_question_target,audit_payload,
                            QuestionTargetAlignment,'question_target_alignment',trace=trace,budget=budget,
                            check=alignment_check)
                    except ModelError:
                        return ['answer_target_mismatch']
                    q.answer_hint = alternate
                    candidate.state.questions = [q if old.id == qid else old for old in candidate.state.questions]
                    bound_questions[qid] = q
                    approved_question_alignment[qid] = {'original_target':target.model_dump(mode='json'),
                        'verified_target':alternate.model_dump(mode='json'),'reason':audit.reason}
                    if trace:trace('question_target_alignment_approved',{'question_id':qid,
                        **approved_question_alignment[qid]})
                supporting = [op for op in writes if target and (op.path != target.path or op.item != target.item)]
                support_issues, support_conversions = [], []
                for index, op in enumerate(plan.operations):
                    if op.reply_to != qid or not any(op is item for item in supporting):
                        continue
                    issues, conversion = normalize_supporting_operation(op, processor.template, before)
                    support_issues.extend(issues)
                    if conversion:
                        support_conversions.append({'operation_index':index,'conversion':conversion})
                if trace and support_conversions:
                    trace('turn_normalized', {'conversions':support_conversions})
                if support_issues:
                    return list(dict.fromkeys(support_issues))
                if target is None and writes:
                    token = question_key(q)
                    prefix = {'features':'release_scope.features.', 'technology':'release_scope.tech_stack.'}.get(token)
                    if not prefix or any(not op.path.startswith(prefix) for op in writes):
                        return ['answer_target_mismatch']
            operations = []
            for op in plan.operations:
                question = known.get(op.reply_to)
                bound = bound_questions.get(op.reply_to)
                anchor = (bound.target or bound.answer_hint) if bound else None
                supporting = bool(anchor and (op.path != anchor.path or op.item != anchor.item))
                options = [index for index,option in enumerate(question.options,1) if option.value == op.value] if question and question.kind == 'conflict' else []
                choice = options[0] if len(options) == 1 and op.action == 'answer' else None
                proposal = Proposal(action='record' if supporting and op.action=='answer' else op.action, path=op.path, item=op.item, choice=choice,
                                    value=None if isinstance(op.value, str) or choice else op.value,
                                    quotes=op.quotes, reply_to=None if supporting else op.reply_to, previous_text=op.previous_text,
                                    evidence_refs=op.evidence_refs)
                if isinstance(op.value, str) and choice is None:
                    proposal._reviewed_text = op.value
                if isinstance(op.value,list):
                    proposal._reviewed_list = True
                if op.path in FEATURE_LINK_PATHS or op.path == 'release_scope.features.included_in_release':
                    proposal._semantic_binding = True
                operations.append(proposal)
            apply_batch(candidate, processor.template, operations, source, start, end)
            if candidate.reviews:
                return ['turn_application:' + issue for issue in candidate.reviews[:10]]
            from updates.release_dependencies import reconcile_feature_exclusions
            reconcile_feature_exclusions(before,candidate,plan,source,start,end,trace)
            for qid, decision in decisions.items():
                if decision.status == 'answered' and any(q.id==qid for q in candidate.state.questions):
                    return ['answered_question_change_not_applied:'+qid]
            if plan.clarification_target is not None and not plan.clarification:
                return ['clarification_target_requires_text']
            if plan.clarification and (not before.questions or plan.clarification_target is not None):
                target = plan.clarification_target
                if target is not None:
                    parent = target.path.rpartition('.')[0]
                    row = candidate.state.rows.get(target.item) if target.item else None
                    if (target.path not in processor.fields or target.path in SYSTEM_OWNED_PATHS
                            or (parent in processor.template.rows and (row is None or row.path != parent))
                            or (parent not in processor.template.rows and target.item is not None)):
                        return ['invalid_clarification_target']
                if not any(((q.target or q.answer_hint) == target
                             and q.text.strip() == plan.clarification.strip())
                           for q in candidate.state.questions):
                    qid = f'Q{candidate.state.next_question:04d}'
                    candidate.state.next_question += 1
                    candidate.state.questions.insert(0,Question(id=qid,kind='clarify',answer_hint=target,
                        text=plan.clarification,evidence=source_evidence(source,start,end,[source.text[start:end]])))
                    for existing in candidate.state.questions[1:]:
                        old_priority = candidate.state.question_priorities.get(existing.id,65)
                        candidate.state.question_priorities[existing.id] = min(old_priority,99)
                    candidate.state.question_priorities[qid] = 100
            for qid, decision in decisions.items():
                q = bound_questions[qid]
                token = question_key(q)
                if decision.status in ('answered', 'deferred', 'cancelled'):
                    candidate.state.questions = [old for old in candidate.state.questions if old.id != qid]
                if decision.status == 'answered':
                    candidate.state.deferred_questions = [old for old in candidate.state.deferred_questions if old.id != qid]
                    candidate.state.deferred_required = [value for value in candidate.state.deferred_required if value != token]
                    if token and token not in candidate.state.resolved_required:
                        candidate.state.resolved_required.append(token)
                elif decision.status == 'deferred':
                    candidate.state.resolved_required = [value for value in candidate.state.resolved_required if value != token]
                    if not any(old.id == qid for old in candidate.state.deferred_questions):
                        candidate.state.deferred_questions.append(q.model_copy(deep=True))
                    if token and token not in candidate.state.deferred_required:
                        candidate.state.deferred_required.append(token)
                elif decision.status == 'cancelled':
                    from questions.required_check import KEYS, requirement_sufficient
                    if token and token.split(':',1)[0] in KEYS and not requirement_sufficient(processor.template.export(candidate.state),token):
                        return ['cannot_cancel_missing_required_field']
                    candidate.state.deferred_questions = [old for old in candidate.state.deferred_questions if old.id != qid]
                    candidate.state.deferred_required = [value for value in candidate.state.deferred_required if value != token]
                elif not any(old.id == qid for old in candidate.state.questions):
                    candidate.state.questions.append(q.model_copy(deep=True))
                    candidate.state.resolved_required = [value for value in candidate.state.resolved_required if value != token]
                record_required(candidate.state, q, decision.status, source.text)
            requests = {request.id:request for request in candidate.state.input_requests if request.status == 'pending'}
            decisions_seen = set()
            for decision in plan.request_decisions:
                if (decision.request_id not in requests or decision.request_id in decisions_seen
                        or not decision.reason.strip()):
                    return ['invalid_pending_input_decision']
                if decision.status == 'resolved' and not (plan.operations or plan.question_decisions):
                    return ['resolved_input_requires_applied_change']
                request = requests[decision.request_id]
                request.status,request.resolved_by = decision.status,source.text
                decisions_seen.add(decision.request_id)
            ensure_public_ids(candidate.state)
            try:
                validate_transition(processor.template, before, candidate.state, source.text)
            except ValueError as exc:
                return ['turn_transition:' + str(exc)]
            if not plan.operations and not plan.question_decisions and not plan.request_decisions and processor.review_turn is None:
                trial = candidate
                accepted_plan = plan
                return []
            if processor.review_turn is None:
                return ['semantic_review_model_missing']
            project_review = (before.source_review_version != REVIEW_VERSION or before.source_review_status != 'verified'
                              or before.source_review_digest != digest(processor.template.export(before)))
            targets = set(before.cells) | set(candidate.state.cells)
            if not project_review:
                targets = {key for key in targets if before.cells.get(key) != candidate.state.cells.get(key)}
            if review_attempts and budget is not None:
                if budget.remaining <= 0:
                    return ['semantic_review_retry_budget_exhausted']
                budget.remaining -= 1
            review_attempts += 1
            delivery_review=project_review or any(before.cells.get(path)!=candidate.state.cells.get(path) for path in DELIVERY_FIELDS)
            if delivery_review and 'verdict' not in platform_cache:
                platform_cache['verdict']=assess_platform(processor,source.text[start:end],before,budget,trace)
            platform_verdict=platform_cache.get('verdict')
            if platform_verdict is not None:
                payload['source_environment']=platform_verdict.model_dump(mode='json')
            removed_targets={change.target.key() for change in candidate.state.changes[len(before.changes):]
                if change.target.key() in before.cells and change.after in (None,[], '')
                and (change.target.key() not in candidate.state.cells or candidate.state.cells[change.target.key()].value in (None,[],''))
                and any(ref.digest==source.digest and ref.start>=0 and ref.end<=len(source.text)
                        and source.text[ref.start:ref.end]==ref.quote for ref in change.evidence)}
            review_payload={
                'source_environment':platform_verdict.model_dump(mode='json') if platform_verdict else None,
                **payload, 'plan':plan.model_dump(mode='json',exclude={'operations':{'__all__':{'evidence_refs'}}}),
                'operation_grounding':grounding,
                'candidate_evidence':turn_candidate_evidence(before,candidate.state,targets,source),
                'removed_targets':sorted(removed_targets),
                'optional_field_review_inventory':optional_turn_field_inventory(processor.template,before,candidate.state,targets),
                'candidate_requirements':processor.template.export(candidate.state),
                'candidate_questions':[q.model_dump(mode='json') for q in candidate.state.questions],
                'question_target_alignments':approved_question_alignment.copy(),
                'review_scope':'clarification' if plan.intent == 'clarify' else ('project' if project_review else 'changes'),
                'clarification_context': ({
                    'original_questions':[q.model_dump(mode='json') for q in before.questions],
                    'proposed_question':plan.clarification,
                    'proposed_target':plan.clarification_target.model_dump(mode='json') if plan.clarification_target else None,
                    'preserved_requirements':True,
                    'preserved_existing_questions':True,
                } if plan.intent == 'clarify' else None),
                'required_review_targets':sorted(targets),
                'data_review_inventory':data_review_inventory(processor.template,before,candidate.state,
                                                             candidate.row_aliases,full=project_review),
                'field_review_inventory':field_review_request(processor.template,before,before,candidate.state,
                    full=project_review,include_delivery=delivery_review,include_proposed=True),
                'existing_evidence':{key:[ref.model_dump(mode='json') for ref in before.cells[key].evidence]
                                     for key in sorted(targets) if key in before.cells},
            }
            def validate_related_resolutions(verdict):
                """mutation or an automatic same-field closure."""
                resolutions = verdict.related_question_resolutions
                if not resolutions:
                    return [], []
                if not verdict.valid or verdict.issues or plan.intent == 'clarify':
                    return [], ['related_question_resolution_requires_valid_review']
                from questions.question_consistency import verify_related_resolutions
                return verify_related_resolutions(before, candidate.state, plan,
                                                  resolutions, source.text[start:end])
            semantic_issues = []
            review_verdict = None
            def check_review(raw):
                """review 관련 작업을 확인한다"""
                nonlocal semantic_issues, review_verdict
                verdict = normalize_turn_review(raw,processor.template,before,candidate.state,candidate.row_aliases)
                review_verdict = verdict
                semantic_issues = []
                scope_issues = []
                if set(verdict.reviewed_targets) != targets:
                    scope_issues.append('turn_review_scope_mismatch')
                if plan.clarification and (processor.require_semantic_audits or verdict.clarification_decisions):
                    if len(verdict.clarification_decisions)>1:
                        semantic_issues.append('turn_clarification_requires_one_reviewed_decision')
                    elif len(verdict.clarification_decisions)!=1 or not verdict.clarification_decisions[0].strip():
                        scope_issues.append('turn_clarification_requires_one_reviewed_decision')
                elif verdict.clarification_decisions:
                    scope_issues.append('turn_review_unrequested_clarification')
                if plan.intent == 'clarify':
                    if not verdict.valid or verdict.issues:
                        return ['turn_clarification_semantic:'+issue for issue in verdict.issues] or ['turn_clarification_rejected']
                    if not verdict.clarification_decisions or len(verdict.clarification_decisions) != 1:
                        return ['turn_clarification_requires_one_reviewed_decision']
                    if any(before.cells.get(k) != candidate.state.cells.get(k)
                           for k in set(before.cells)|set(candidate.state.cells)):
                        return ['clarification_changed_requirements']
                    return scope_issues
                missing = coverage_issues(verdict,source.text[start:end],processor.template,candidate.state,
                    candidate.row_aliases,required=processor.require_semantic_audits,
                    previous_state=before,removed_targets=removed_targets)
                missing.extend(data_review_issues(verdict,source.text[start:end],processor.template,before,candidate.state,
                    candidate.row_aliases,full=project_review,required=processor.require_data_receipts,
                    sparse=not processor.require_data_receipts,allow_deleted_rows=True))
                missing.extend(field_review_issues(verdict,source.text[start:end],processor.template,before,before,candidate.state,
                    candidate.row_aliases,full=project_review,include_delivery=delivery_review,include_proposed=True,
                    required=processor.require_detailed_receipts,sparse=not processor.require_detailed_receipts,
                    optional_turn_targets=targets))
                missing.extend(platform_consistency(platform_verdict,candidate.state))
                if trace:
                    trace('turn_semantic_review', {'valid':verdict.valid,'issues':verdict.issues,
                        'coverage':[item.model_dump(mode='json') for item in verdict.coverage],
                        'data_reviews':[item.model_dump(mode='json') for item in verdict.data_reviews],
                        'field_reviews':[item.model_dump(mode='json') for item in verdict.field_reviews],
                        'coverage_issues':missing,'scope_issues':scope_issues,
                        'intent':plan.intent,'question_ids':list(decisions)})
                related, related_issues = validate_related_resolutions(verdict)
                scope_issues.extend(related_issues)
                if not verdict.valid or verdict.issues:
                    semantic_issues.extend(['turn_semantic:'+issue for issue in verdict.issues] or ['turn_semantic_rejected'])
                reviewer_owner = getattr(processor.review_turn, '__self__', None)
                if (getattr(processor, 'model', None) is not None
                        and reviewer_owner is processor.model and verdict.valid):
                    from updates.release_dependencies import stale_excluded_feature_summary
                    summary_issues = stale_excluded_feature_summary(before, candidate.state)
                    if summary_issues:
                        semantic_issues.extend('turn_semantic:'+issue for issue in summary_issues)
                        if trace:
                            trace('release_summary_inconsistency', {'issues':summary_issues})
                semantic_codes=('coverage_unresolved:', 'coverage_unrepresented_target:', 'platform_source_candidate_mismatch')
                semantic_issues.extend('turn_coverage:'+issue for issue in missing if issue.startswith(semantic_codes))
                if semantic_issues:
                    return []
                all_review_issues = scope_issues+['turn_coverage:'+issue for issue in missing]
                if not all_review_issues and related:
                    from questions.question_consistency import apply_related_resolutions
                    try:
                        apply_related_resolutions(candidate.state, related, source.text)
                    except (ValueError, TypeError) as exc:
                        return ['related_question_resolution_application:'+str(exc)]
                    if trace:
                        trace('turn_related_questions_resolved', {'question_ids':[q.id for q in related]})
                return all_review_issues
            run_model_stage(processor.review_turn,review_payload,TurnReview,'review_turn',trace=trace,budget=budget,check=check_review)
            if semantic_issues:
                repair_model = getattr(processor, 'repair_turn', None)
                if (repair_model is not None and getattr(processor,'_enable_turn_repair',False)
                        and (getattr(processor.review_turn,'__self__',None) is getattr(processor,'model',None)
                             or getattr(processor,'_explicit_repair_for_tests',False))
                        and repair_depth < 2 and budget.remaining > 0
                        and plan.operations and plan.intent != 'proposal_revision'):
                    repair_depth += 1
                    revision_context = {
                        'source_text':source.text[start:end],
                        'question_decisions':[d.model_dump(mode='json') for d in plan.question_decisions],
                        'plan':plan.model_dump(mode='json'),
                        'semantic_issues':semantic_issues,
                        'reviewed_coverage':[c.model_dump(mode='json') for c in review_verdict.coverage] if review_verdict else [],
                        'row_aliases':[{'parent':parent,'alias':alias,'row_id':actual} for (parent,alias),actual in sorted(candidate.row_aliases.items(),key=lambda kv:str(kv[0]))],
                        'candidate_requirements':processor.template.export(candidate.state),
                        'existing_relevant_fields':{key:before.cells[key].value for key in targets if key in before.cells},
                        'feature_ids':dict(candidate.state.public_ids),
                    }
                    unresolved = set()
                    if review_verdict:
                        for section in review_verdict.coverage:
                            for fact in section.facts:
                                if fact.disposition == 'unresolved':
                                    unresolved.update((t.path,t.item) for t in fact.targets)
                    for key in targets:
                        if any(key in issue for issue in semantic_issues):
                            path, sep, item = key.partition('@')
                            unresolved.add((path,item if sep else None))
                    for key, saved in before.cells.items():
                        path, sep, item = key.partition('@')
                        if (path in processor.template.fields
                                and path not in SYSTEM_OWNED_PATHS
                                and saved.value is not None
                                and any(key in issue for issue in semantic_issues)):
                            unresolved.add((path,item if sep else None))
                    aliases = candidate.row_aliases
                    scoped = set(unresolved)
                    for path,item in unresolved:
                        for alias_key, actual in aliases.items():
                            if actual == item and isinstance(alias_key,tuple) and len(alias_key)==2:
                                parent, alias = alias_key
                                if path.startswith(parent+'.'):
                                    scoped.add((path,alias))
                    revision_context['allowed_targets']=[{'path':path,'item':item} for path,item in sorted(scoped,key=str)]
                    reviewed_existing = {}
                    for key,saved in before.cells.items():
                        path,sep,item = key.partition('@')
                        identity = (path,item if sep else None)
                        if identity in scoped and saved.value is not None:
                            reviewed_existing[identity] = saved.value
                    revision_context['repair_existing_fields']=[{'path':path,'item':item,'value':val}
                        for (path,item),val in sorted(reviewed_existing.items(),key=str)]
                    def verify_patch(patch):
                        """patch 관련 작업을 수행한다"""
                        aligned, _ = align_repair_indices(plan, patch, scoped)
                        _, issues = patch_turn_plan(plan,aligned,source.text[start:end],scoped,reviewed_existing)
                        return issues
                    try:
                        patch = run_model_stage(repair_model,revision_context,TurnRepair,
                            'repair_turn',trace=trace,budget=budget,check=verify_patch)
                        aligned, corrections = align_repair_indices(plan,patch,scoped)
                        revised, issues = patch_turn_plan(plan,aligned,source.text[start:end],scoped,reviewed_existing)
                        if not issues and revised is not None:
                            if trace:
                                if corrections:
                                    trace('turn_repair_index_aligned', {'corrections': corrections})
                                trace('turn_partial_repair', {'replacements':[x.operation_index for x in aligned.replacements],
                                    'additions':len(aligned.additions),'round':repair_depth})
                            result_issues = check(revised)
                            if not result_issues:
                                return []
                            from planning_agent import ModelError
                            failure = ModelError('부분 복구 후보의 최종 검증에 실패했어.',
                                kind='candidate_validation',issues=result_issues)
                            failure.failed_stage = 'repair_turn'
                            raise failure
                    except Exception as exc:
                        if trace:
                            trace('turn_partial_repair_failed',{'kind':getattr(exc,'kind',type(exc).__name__),
                                'issues':list(getattr(exc,'issues',()))})
                    from planning_agent import ModelError
                    failure = ModelError('부분 복구의 의미 검토가 완료되지 않았어.',
                        kind='candidate_validation',issues=semantic_issues)
                    failure.failed_stage = 'repair_turn'
                    raise failure
                return semantic_issues
            trial = candidate
            accepted_plan = plan
            trial.reviewed_project = project_review
            trial.source_review_verified = True
            trial.request_verified = True
            return []
        try:
            plan = run_model_stage(processor.plan_turn, payload, TurnPlan, 'interpret_turn',
                                   trace=trace, budget=budget, check=check)
        except Exception as exc:
            if trace:
                trace('turn_rejected', {'kind':getattr(exc, 'kind', type(exc).__name__),
                                        'failed_stage':getattr(exc,'failed_stage','interpret_turn'),
                                        'issues':list(getattr(exc, 'issues', ())), 'error':str(exc)})
            return TurnResult(state=original.model_copy(deep=True), input_rejected=True, reviews=['입력의 의미·대상·변경 범위를 검증하지 못했어. 기존 데이터와 질문을 유지했어.'])
        plan = accepted_plan or plan
        result.state = trial.state
        all_windows_reviewed = all_windows_reviewed and trial.source_review_verified
        all_requests_verified = all_requests_verified and trial.request_verified
        result.reviewed_project = result.reviewed_project or trial.reviewed_project
        result.saved += trial.saved + bool(plan.question_decisions) + bool(plan.request_decisions)
        result.updates.extend(trial.updates)
        if plan.clarification:
            result.reviews.append(plan.clarification)
        if trace:
            trace('turn_applied', {'intent':plan.intent, 'operations':len(plan.operations),
                                   'applied_operations':[op.model_dump(mode='json') for op in plan.operations],
                                   'question_decisions':[d.model_dump() for d in plan.question_decisions]})
    result.source_review_verified = all_windows_reviewed and not result.reviews
    result.request_verified = all_requests_verified and not result.reviews
    if result.request_verified:
        from storage.request_state import resolve_retried_input
        resolve_retried_input(result.state,source.text)
    result.review_carries_forward = all_windows_reviewed and not result.reviews
    if source_name and result.source_review_verified:
        result.state.documents.append(source.digest)
    return result
