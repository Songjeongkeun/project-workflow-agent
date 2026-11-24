"""질문 생성·검토 대상 범위를 계산한다.

Question assessment scope, derived from a verified state transition.

No domain words or fuzzy semantic matching.  A small, bound answer can reuse
an already completed project assessment; an uncertain or broad change cannot.
The full target registry remains available for real cross-field implications.
Only the context and source quotes are projected.  Fresh evidence and an
independent meaning review are required for new dynamic questions in delta mode.
"""
from core.planning_schema import FEATURE_LINK_PATHS, REVIEW_VERSION, Target


def _ref_key(ref):
    """key 관련 작업을 수행한다"""
    return (ref.source, ref.digest, ref.start, ref.end, ref.quote)


def _target_q(q):
    """q 관련 작업을 수행한다"""
    return q.target or q.answer_hint


def _value(state, key):
    """value 관련 작업을 수행한다"""
    cell = state.cells.get(key)
    return cell.value if cell else None


def _target_key(key):
    """key 관련 작업을 수행한다"""
    path, _, item = key.partition('@')
    return Target(path=path, item=item or None)


def latest_terminal_ids(state):
    """Only durable, actually closed questions. Deferred/reopened are not closed."""
    latest = {event.question.id: event for event in state.required_answer_history}
    open_ids = {q.id for q in [*state.questions, *state.deferred_questions]}
    return {qid for qid,event in latest.items()
            if event.status in ('answered','cancelled','superseded') and qid not in open_ids}


def _broad(target):
    """broad 관련 작업을 수행한다"""
    return (target.path.startswith(('overview.', 'user_problem.'))
            or (target.path.startswith('release_scope.') and target.path!='release_scope.features.behavior_and_value')
            or target.path in {'goals.expected_change','user_experience.flow'})


def _full(reason, targets, evidence, context):
    """full 관련 작업을 수행한다"""
    return targets, evidence, {**context,'question_scope':{'mode':'full','reason':reason}}, None


def verified_assessment_base(template, original, state):
    """assessment base 관련 작업을 수행한다"""
    from proposals.proposal_flow import digest
    return (bool(original.cells) and not original.assessment_pending and not state.assessment_pending
            and original.source_review_status=='verified' and original.source_review_version==REVIEW_VERSION
            and original.source_review_digest==digest(template.export(original)))


def can_reuse_queue_assessment(template,original,state,candidates,*,explicit_queue_command=False,verified_turn=False):
    """Deferral/reopening alone does not create a new factual decision scope.

    Only reuse a complete, current assessment. Nothing is inferred from natural
    language keywords: a verified durable queue-transition record (or an actual
    explicit queue command) is necessary. Missing fixed requirements still need
    an assessment/fallback if no pending question already covers them.
    """
    from questions.required_check import question_key
    if not verified_assessment_base(template,original,state):return False
    if (original.cells!=state.cells or original.rows!=state.rows or original.public_ids!=state.public_ids
            or original.documents!=state.documents):return False
    if state.required_answer_history[:len(original.required_answer_history)]!=original.required_answer_history:return False
    if (original.questions==state.questions and original.deferred_questions==state.deferred_questions
            and original.required_answer_history==state.required_answer_history):return False
    events=state.required_answer_history[len(original.required_answer_history):]
    if any(event.status not in ('deferred','reopened') for event in events):return False
    if not explicit_queue_command and not (verified_turn and events):return False
    covered={question_key(q) for q in [*state.questions,*state.deferred_questions]}|set(state.deferred_required)
    return all(c['token'] in covered for c in candidates)


def project_context(template, state, targets, evidence, context, *, original, enabled):
    """Return scoped context + allowed fresh evidence keys (None for full review).

    Keep all allowed target keys so a genuinely new cross-field implication can
    be raised.  Deterministic scope is a guard on newly generated questions,
    not an attempt to classify the domain or declare a decision resolved.
    """
    from planning_agent import windows

    context={**context,'closed_question_ids':sorted(latest_terminal_ids(state))}
    def full(reason):
        """전체 질문 범위를 구성해 반환한다."""
        return _full(reason,targets,evidence,context)
    if not enabled or not original.cells:
        return full('initial_or_nonlocal_turn')
    if not verified_assessment_base(template,original,state):
        return full('previous_assessment_not_reusable')
    if (original.documents != state.documents or
        any(q.kind=='conflict' for q in [*original.questions,*state.questions,*state.deferred_questions])):
        return full('document_or_conflict_change')
    if state.required_answer_history[:len(original.required_answer_history)] != original.required_answer_history:
        return full('history_not_append_only')
    prior_questions={q.id:q for q in [*original.questions,*original.deferred_questions]}
    decisions=[event for event in state.required_answer_history[len(original.required_answer_history):]
               if event.status in ('answered','partial') and event.question.id in prior_questions]
    bound=set()
    if not decisions:
        return full('no_bound_answer')
    compact_ok=(original.rows==state.rows and original.public_ids==state.public_ids)
    for event in decisions:
        target=_target_q(event.question)
        previous=_target_q(prior_questions[event.question.id])
        if target is None or target!=previous:
            compact_ok=False
        if target is not None:
            bound.add(target.key())
            if _broad(target):compact_ok=False
    bound_rows={_target_key(k).item for k in bound if _target_key(k).item}
    keys=set(original.cells)|set(state.cells)
    changed={key for key in keys if _value(original,key)!=_value(state,key)}
    if (set(original.rows)-set(state.rows) or
            any((key in original.cells and key not in state.cells) or
                (_value(original,key) not in (None,'',[]) and _value(state,key) in (None,'',[]))
                for key in changed)):
        return full('destructive_change')
    if any(_broad(_target_key(key)) or
           (key not in bound and _target_key(key).item not in bound_rows) for key in changed):
        compact_ok=False
    affected = changed | bound
    affected_rows={_target_key(k).item for k in affected if _target_key(k).item}
    context_rows=set(affected_rows)
    related_public=set()
    for rid in affected_rows:
        if rid in state.public_ids and rid in state.rows and state.rows[rid].path=='release_scope.features':
            related_public.add(state.public_ids[rid])
        for path in FEATURE_LINK_PATHS:
            cell=state.cells.get(Target(path=path,item=rid).key())
            if cell and isinstance(cell.value,list):related_public.update(cell.value)
    for rid,pid in state.public_ids.items():
        if pid in related_public:context_rows.add(rid)
    for key,cell in state.cells.items():
        t=_target_key(key)
        if t.path in FEATURE_LINK_PATHS and isinstance(cell.value,list) and related_public.intersection(cell.value):
            context_rows.add(t.item)
    context_keys={k for k in state.cells if k in affected or _target_key(k).item in context_rows}
    question_items=[*state.questions,*state.deferred_questions]
    question_targets={t.key() for q in question_items if (t:=_target_q(q)) is not None}
    prior_refs={_ref_key(ref) for c in original.cells.values() for ref in c.evidence}
    refs=[ref for key in sorted(context_keys|affected) if key in state.cells
          for ref in state.cells[key].evidence]
    fresh={ref.quote for key in sorted(affected) if key in state.cells
           for ref in state.cells[key].evidence if _ref_key(ref) not in prior_refs}
    basis={ref.quote for ref in refs}
    basis.update(ref.quote for q in question_items for ref in q.evidence)
    basis.update(ref.quote for key in question_targets if key in state.cells for ref in state.cells[key].evidence)
    basis.update(fresh)
    def spans(quotes):
        """spans 관련 작업을 수행한다"""
        return {piece for quote in quotes for piece in
                ([quote] if len(quote)<=400 else [quote[a:b].strip() for a,b in windows(quote,400,40)]) if piece.strip()}
    allowed_quotes=spans(basis)
    fresh_quotes=spans(fresh)
    scoped_evidence={key:quote for key,quote in evidence.items() if quote in allowed_quotes} if compact_ok else evidence
    fresh_keys={key for key,quote in scoped_evidence.items() if quote in fresh_quotes}
    if changed and not fresh_keys:
        return full('changed_value_without_fresh_cell_basis')
    document=context['requirements']
    summary={
        'purpose':document['overview'].get('summary'),
        'users':document['user_problem'].get('target_users'),
        'project_type':document['release_scope'].get('project_type'),
        'platforms':document['release_scope'].get('supported_platforms'),
        'out_of_scope':document['release_scope'].get('out_of_scope'),
        'features':[{k:f.get(k) for k in ('id','name','included_in_release')}
                    for f in document['release_scope'].get('features',[])],
    }
    def address(key):
        """address 관련 작업을 수행한다"""
        t=_target_key(key);row=state.rows.get(t.item)
        return {'path':t.path,'row_label':row.label if row else None,'public_id':state.public_ids.get(t.item)}
    def q_context(q):
        """context 관련 작업을 수행한다"""
        t=_target_q(q)
        return {'id':q.id,'kind':q.kind,'text':q.text,'target':t.model_dump() if t else None,
                'target_value':_value(state,t.key()) if t else None,
                'row_label':state.rows[t.item].label if t and t.item in state.rows else None,
                'public_id':state.public_ids.get(t.item) if t else None,
                'reason':state.dynamic_question_reasons.get(q.id),'priority':state.question_priorities.get(q.id)}
    rows=[]
    for rid in sorted(context_rows):
        row=state.rows.get(rid)
        if row is None:continue
        rows.append({'collection':row.path,'label':row.label,'public_id':state.public_ids.get(rid),
                     'values':{_target_key(k).path.rpartition('.')[2]:state.cells[k].value
                               for k in sorted(context_keys) if _target_key(k).item==rid}})
    compact={'project_context':summary,
             'fields':{k:state.cells[k].value for k in sorted(context_keys) if not _target_key(k).item},
             'rows':rows}
    relevant_targets=[key for key,value in targets.items() if 'fixed' not in value and
                      (Target(path=value['path'],item=value.get('item')).key() in affected or
                       (value.get('item') is not None and value.get('item') in context_rows))]
    scoped_context={**context,'requirements':compact if compact_ok else document,
                    'existing_questions':[q_context(q) for q in state.questions],
                    'deferred_questions':[q_context(q) for q in state.deferred_questions],
                    'evidence':scoped_evidence,
                    'question_scope':{'mode':'incremental','context':'compact' if compact_ok else 'full',
                        'reason':'verified_bound_answer' if compact_ok else 'verified_wider_answer',
                        'changed_fields':[{**address(key),'before':_value(original,key),'after':_value(state,key)}
                                          for key in sorted(affected)],
                        'answered_this_turn':[{'id':e.question.id,'status':e.status,'question':e.question.text}
                                              for e in decisions],
                        'affected_targets':relevant_targets,'fresh_evidence':sorted(fresh_keys)}}
    return targets,scoped_evidence,scoped_context,fresh_keys
