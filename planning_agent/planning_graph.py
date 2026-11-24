"""LangGraph 노드, 상태 전이, 재시도와 오류 복구를 구성한다."""

from typing import TypedDict, Any, Callable
from pathlib import Path
from copy import deepcopy
from time import sleep
import re
from dataclasses import dataclass
from pydantic import ValidationError
from core.planning_logic import Source, TurnResult, label
from core.action_routing import plan_actions, classify_readonly_natural
from langgraph.graph import StateGraph, START, END
from core.planning_schema import ProjectState, ensure_public_ids, validate_public_ids, validate_state, validate_transition
from updates.answer_flow import is_explicit_edit, process_turn
from questions.required_check import (missing_candidates, reconcile_questions,
                            register_questions, resolve_satisfied_questions, prune_required_status, essential_fallback_questions, RequiredAssessment, requirement_value,
                            question_key, requirement_sufficient, dynamic_targets, register_dynamic_questions,
                            apply_question_decisions, question_decision_issues, prune_advisory_questions, rank_questions, current_question,
                            QuestionSemanticReview, assessment_questions, question_review_issues)
from proposals.proposal_flow import create_proposal, apply_decision, invalidate_stale
from questions.decision_flow import assessment_issue
from questions.required_check import close_removed_dynamic_questions, record_required

MAX_EXTRA_ATTEMPTS = 4
MAX_STAGE_ATTEMPTS = 2
MAX_LINK_ATTEMPTS = 2
RETRYABLE_KINDS = frozenset({'connection', 'server_transient', 'response_schema', 'candidate_validation', 'rate_limited_transient'})


@dataclass
class RecoveryBudget:
    remaining: int = MAX_EXTRA_ATTEMPTS


class ModelStageState(TypedDict, total=False):
    attempts: int
    status: str
    kind: str
    issues: list[str]
    response: Any
    error: Any
    failed_stage: str


def validated_model_response(value, schema):
    """모델 응답이 실제 작업의 구조화 스키마에 맞는지 확인한다."""
    from planning_agent import ModelError
    if not isinstance(value, schema):
        raise ModelError('모델 응답이 작업의 스키마와 일치하지 않아.', kind='response_schema')
    try:
        schema.model_validate(value.model_dump(warnings=False), strict=True)
    except ValidationError as exc:
        raise ModelError('모델 응답의 필드 또는 자료형이 올바르지 않아.', kind='response_schema') from exc
    return value


def model_error_kind(exc):
    """재시도 가능한 모델 오류와 중단해야 하는 오류를 구분한다."""
    from planning_agent import ModelError
    if isinstance(exc, ModelError):
        return exc.kind
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return 'connection'
    if isinstance(exc, ValidationError):
        return 'response_schema'
    return 'unexpected'


def run_model_stage(model, payload, schema, stage, *, trace=None, budget=None, check=None, preserve_rejected=False):
    """모델 호출 하나를 독립적인 복구 그래프로 실행한다."""
    from planning_agent import ModelError
    if budget is None:
        budget = RecoveryBudget()
    graph = StateGraph(ModelStageState)

    def rejected(exc,attempt,candidate=None):
        """rejected 관련 작업을 수행한다"""
        from core.planning_schema import RecoveryIssue
        kind=model_error_kind(exc)
        origin=getattr(exc,'failed_stage',stage)
        issues=list(getattr(exc,'issues',()))
        if not issues:issues=[kind]
        if trace:
            trace('model_stage_failure',{'stage':stage,'failed_stage':origin,'attempt':attempt,'kind':kind,'issues':issues})
            trace('recovery_issues',{'issues':[RecoveryIssue(stage=origin,code=kind,retryable=kind in RETRYABLE_KINDS,
                  detail=message).model_dump() for message in issues]})
            if candidate is not None:trace('model_stage_rejected_response',{'stage':stage,'attempt':attempt,'response':candidate.model_dump(mode='json')})
        return {'attempts':attempt,'status':'rejected' if kind=='candidate_validation' and candidate is not None else 'failed',
                'kind':kind,'issues':issues,'response':candidate,'error':exc,'failed_stage':origin}

    def attempt_node(data):
        """node 관련 작업을 수행한다"""
        attempt=data.get('attempts',0)+1
        arguments=deepcopy(payload)
        if attempt>1:
            budget.remaining-=1
            if data.get('kind') == 'rate_limited_transient':
                delay=max(1.0, min(10.0, getattr(data.get('error'),'retry_after',1.0)))
                if trace:trace('model_rate_limit_backoff',{'stage':stage,'seconds':delay})
                sleep(delay)
            arguments['recovery_feedback']={'stage':stage,'kind':data['kind'],'issues':data.get('issues',[])}
            if data.get('response') is not None:
                arguments['recovery_feedback']['previous_response']=data['response'].model_dump(mode='json')
        if trace:trace('model_stage_attempt',{'stage':stage,'attempt':attempt,'retry_budget_remaining':budget.remaining})
        try:
            candidate=validated_model_response(model(arguments),schema)
            from extraction.response_normalization import normalize_model_response
            candidate=validated_model_response(normalize_model_response(candidate),schema)
        except Exception as exc:
            return rejected(exc,attempt)
        return {'attempts':attempt,'status':'generated','response':candidate,'kind':'','issues':[],'failed_stage':stage}

    def validate_node(data):
        """node 관련 작업을 검증한다"""
        candidate=data['response']
        if trace:trace('graph_node',{'node':'validate_'+stage,'status':'start'})
        try:
            issues=check(candidate) if check else []
            if issues:raise ModelError('모델이 제안한 변경안의 검증에 실패했어.',kind='candidate_validation',issues=issues)
        except Exception as exc:
            return rejected(exc,data['attempts'],candidate)
        if trace:trace('model_stage_success',{'stage':stage,'attempt':data['attempts']})
        return {'status':'ok','kind':'','issues':[],'error':None,'failed_stage':stage}

    def route_attempt(data):
        """실패한 작업만 재시도하며 입력 전체의 재시도 한도를 적용한다."""
        if (data['status'] != 'ok' and data['kind'] in RETRYABLE_KINDS
                and data.get('failed_stage',stage)==stage
                and data['attempts'] < MAX_STAGE_ATTEMPTS and budget.remaining > 0):
            return 'retry'
        return 'finish'

    graph.add_node('attempt',attempt_node)
    graph.add_node('validate',validate_node)
    graph.add_edge(START,'attempt')
    graph.add_conditional_edges('attempt',lambda d:'validate' if d['status']=='generated' else route_attempt(d),{'validate':'validate','retry':'attempt','finish':END})
    graph.add_conditional_edges('validate',route_attempt,{'retry':'attempt','finish':END})
    outcome = graph.compile().invoke({'attempts':0})
    if outcome['status'] == 'ok':
        return outcome['response']
    if preserve_rejected and outcome['status'] == 'rejected' and outcome.get('failed_stage',stage)==stage:
        if trace:
            trace('model_stage_exhausted', {'stage':stage, 'kind':outcome['kind'], 'policy':'existing_validation_guards', 'attempts':outcome['attempts']})
        return outcome['response']
    if trace:
        trace('model_stage_exhausted', {'stage':stage, 'kind':outcome['kind'], 'policy':'stop', 'attempts':outcome['attempts']})
    error=outcome['error']
    if not hasattr(error,'failed_stage'):error.failed_stage=stage
    raise error


PROPOSAL_REQUEST = re.compile(
    r"^\s*(?:(?:이|현재|지금|우리)\s*)?(?:기획(?:서|안)?|요구사항|프로젝트|서비스)(?:에|에서|의|을|를|에 대해|에 대한)?\s*"
    r"(?:개선(?:할\s*점|점|안)?|보완(?:할\s*점|점|안)?|AI\s*제안|추가\s*제안|추천(?:할\s*점|사항)?)(?:을|를)?\s*"
    r"(?:제안|추천|알려|말해|뽑아|생성|작성)(?:해|해줘|해\s*줘|해\s*주세요|해주세요|줘|주세요)?[.!?。！？\s]*$",
    re.I,
)
PROPOSAL_DIRECT = re.compile(
    r"^\s*(?:AI\s*)?(?:개선(?:할\s*점|점|안)?|보완(?:할\s*점|점|안)?|추가\s*제안|제안|추천)(?:을|를)?\s*"
    r"(?:(?:제안|추천|설명|생성|작성|말|알려)?(?:해|해줘|해\s*줘|해주세요|해\s*주세요)|줘|주세요|부탁해|부탁해요|말해줘|알려줘|생성해줘)?[.!?。！？\s]*$",
    re.I,
)


def is_proposal_request(message: str) -> bool:
    """전문이 명시적 제안 요청인 짧은 입력만 별도 제안 명령으로 분리."""
    if not isinstance(message, str):
        return False
    text = message.strip()
    if not text or len(text) > 120 or '\n' in text or text.startswith('/'):
        return False
    return bool(PROPOSAL_REQUEST.fullmatch(text) or PROPOSAL_DIRECT.fullmatch(text))


class PlanningGraphState(TypedDict, total=False):
    original: ProjectState
    text: str
    source_name: str | None
    trace: Any
    result: Any
    document: dict
    candidates: list[dict]
    assessment: RequiredAssessment
    answer_handled: bool
    repair_attempts: int
    recovery_status: str
    budget: RecoveryBudget
    explicit_edit: bool
    command: str
    recovery_feedback: dict
    source_cache: dict
    source_error: Any
    source_passes: int
    document_action: str
    requirements_path: str
    document_result: dict
    managed: bool
    persist_action: Any
    execute_command: Any
    actions: list[dict]
    action_index: int
    action_results: list[str]
    advance_pending: bool
    request_text: str
    saved_document_ids: list[str]
    current_command: str | None
    command_response: str | None
    requirements_changed: bool
    saved_ok: bool
    skip_persist: bool
    modified_in_request: bool
    stop_remaining: bool


def combine_action_results(results):
    """Combine action replies without displaying the *same* proposal twice.

    A proposal action can return the complete proposal followed by a
    /suggestions action returning the same content. Only discard a later
    standalone proposal if an earlier reply contains its identical block;
    keep status messages, different proposals and other duplicate replies.
    """
    combined = []
    for item in results:
        if not item:
            continue
        proposal = item.strip()
        if re.match(r'^AI 제안 P\d+\s*\(미확정', proposal) and any(
            preceding.strip() == proposal or preceding.strip().endswith('\n' + proposal)
            for preceding in combined
        ):
            continue
        combined.append(item)
    return '\n'.join(combined)


def build_planning_graph(processor):
    """처리 노드와 조건부 분기로 LangGraph를 구성한다."""
    graph = StateGraph(PlanningGraphState)

    def generate_document_node(data: PlanningGraphState):
        """저장 성공이 확인된 JSON을 Word·PDF로 변환한다."""
        from document.document_flow import generate_document
        if data.get('managed') and not data['original'].cells:
            return {'action_results': [*data.get('action_results', []), '저장된 요구사항이 없어. 먼저 요구사항을 입력해 줘.'], 'advance_pending': True}
        try:
            document_result = generate_document(Path(data['requirements_path']))
            if data.get('managed'):
                message = (f"기획서 초안 · {document_result['pdf']}\n"
                           f"편집용 Word · {document_result['docx']}")
                return {'document_result': document_result,
                        'action_results': [*data.get('action_results', []), message],
                        'advance_pending': True}
            return {'document_result': document_result}
        except Exception as exc:
            if not data.get('managed'):
                raise
            return {'action_results': [*data.get('action_results', []),
                    f'기획서 생성 실패 · {exc}\n/generate-document로 다시 생성할 수 있어.'],
                    'advance_pending': True}

    def document_review_node(data: PlanningGraphState):
        """기획서 승인 명령을 검증하고 승인 기록을 갱신한다."""
        from document.document_flow import approve_document
        if data.get('managed') and len(data.get('actions', [])) > 1:
            return {'action_results': [*data.get('action_results', []),
                    '복합 요청에서는 기획서를 자동 승인하지 않아. 새 기획서를 검토한 뒤 별도 메시지로 승인해 줘.'],
                    'advance_pending': True}
        if data.get('managed') and not data['original'].cells:
            return {'action_results': [*data.get('action_results', []), '저장된 요구사항이 없어. 먼저 요구사항을 입력해 줘.'], 'advance_pending': True}
        result = approve_document(Path(data['requirements_path']))
        if data.get('managed'):
            return {'document_result': result,
                    'action_results': [*data.get('action_results', []),
                        f"기획서를 승인했어.\nPDF · {result['pdf']}\nWord · {result['docx']}"],
                    'advance_pending': True}
        return {'document_result': result}

    def route_node(data: PlanningGraphState):
        """CLI/자연어를 공통 작업으로 계획하고 다음 작업을 선택한다."""
        if data.get('managed'):
            actions = data.get('actions')
            if actions is None:
                actions = plan_actions(data['text'], source_name=data.get('source_name'),
                                       is_proposal_request=is_proposal_request)
                if (len(actions) == 1 and not actions[0]['command'] and
                        data.get('source_name') is None):
                    inferred = classify_readonly_natural(data['text'], processor)
                    if inferred:
                        actions[0]['command'] = inferred
                index = 0
                results = []
                request_text = data['text']
            else:
                index = data.get('action_index', 0) + (1 if data.get('advance_pending') else 0)
                results = data.get('action_results', [])
                request_text = data.get('request_text', data['text'])
            if data.get('stop_remaining') and actions is not None:
                index = len(actions)
            if index >= len(actions):
                return {'actions': actions, 'action_index': index,
                        'advance_pending': False, 'action_results': results}
            action = actions[index]
            text = action['text']
            return {'actions': actions, 'action_index': index, 'text': text,
                    'current_command': action['command'], 'request_text': request_text,
                    'advance_pending': False, 'action_results': results,
                    'budget': RecoveryBudget(), 'explicit_edit': data.get('source_name') is None and (is_explicit_edit(text) or
                        (bool(data['original'].cells) and bool(re.search(r'(?:수정|변경|삭제)(?:해\s*줘|해줘|할게)?[.!?\s]*$', text)))),
                    'repair_attempts': 0, 'recovery_status': '', 'answer_handled': False,
                    'source_cache': {}, 'source_passes': 0, 'source_error': None,
                    'command': '', 'skip_persist': False, 'requirements_changed': False,
                    'saved_ok': False, 'command_response': None}
        explicit_edit = data.get('source_name') is None and is_explicit_edit(data['text'])
        return {'budget':RecoveryBudget(), 'explicit_edit':explicit_edit, 'repair_attempts':0, 'recovery_status':'', 'answer_handled':False,
                'source_cache':{},'source_passes':0,'source_error':None}

    def route_input(data: PlanningGraphState):
        """명령과 기존 상태 유무에 따라 처리 노드를 고른다."""
        if data.get('managed'):
            if data.get('action_index', 0) >= len(data.get('actions', [])):
                return 'finish'
            current = data.get('current_command')
            if current == '/generate-document':
                return 'generate_document'
            if current == '/approve-document':
                return 'document_review'
            if current and current.split(' ', 1)[0] not in ('/resume',):
                return 'command'
        if data.get('document_action') == 'generate':
            return 'generate_document'
        if data.get('document_action') == 'approve':
            return 'document_review'
        if data.get('source_name') is None and (re.match(r'^\s*/(?:propose|approve|reject|defer)\b',data['text'],re.I) or is_proposal_request(data['text'])):
            route='command'
        elif (data['original'].cells or data['original'].questions or data['original'].required_answer_history
              or data.get('explicit_edit') or re.match(r'^\s*/resume\b', data['text'], re.I)):
            route = 'interpret'
        else:
            route = 'extract'
        if data.get('trace'):
            data['trace']('graph_route', {'route':route})
        return route

    def interpret_node(data: PlanningGraphState):
        """기존 상태와 입력을 함께 해석하고 의미 검토를 통과한 변경안만 반환한다."""
        if data.get('trace'):
            data['trace']('graph_node', {'node':'interpret', 'status':'start'})
        result = process_turn(processor, data['original'], data['text'], data.get('source_name'),
                              trace=data.get('trace'), budget=data['budget'])
        return {'result':result, 'answer_handled':True}

    def extract_node(data: PlanningGraphState):
        """신규 요구사항 또는 일반 자연어 입력을 처리한다."""
        if data.get('trace'):
            data['trace']('graph_node', {'node':'extract','status':'start'})
        try:
            result = processor.run_core(data['original'], data['text'], data.get('source_name'), trace=data.get('trace'), budget=data['budget'],
                                        source_cache=data['source_cache'])
            return {'result':result,'source_error':None}
        except Exception as exc:
            if not processor.require_quality_audits:raise
            return {'source_error':exc}

    def source_route(data):
        """route 관련 작업을 수행한다"""
        error=data.get('source_error')
        if error is None:return 'candidate'
        origin=getattr(error,'failed_stage','')
        retry=(not data.get('answer_handled') and model_error_kind(error)=='candidate_validation' and origin.startswith('review_')
               and data['source_passes']<1 and data['budget'].remaining>0)
        return 'repair' if retry else 'stop'

    def source_repair_node(data):
        """repair node 관련 작업을 수행한다"""
        data['budget'].remaining-=1
        if data.get('trace'):
            data['trace']('source_repair_route',{'failed_stage':getattr(data['source_error'],'failed_stage',''),
                'pass':data['source_passes']+1,'retry_budget_remaining':data['budget'].remaining})
        return {**extract_node(data),'source_passes':data['source_passes']+1}

    def source_stop_node(data):
        """stop node 관련 작업을 수행한다"""
        raise data['source_error']

    def candidate_node(data):
        """node 관련 작업을 수행한다"""
        result=data['result']
        validate_state(processor.template,result.state)
        validate_public_ids(result.state)
        if (processor.require_quality_audits and data.get('recovery_status')=='recovered'
                and not result.recovery_context.get('revalidated')):
            try:
                from core.planning_logic import coverage_issues,data_review_inventory,data_review_issues
                from extraction.field_audit import field_review_request,field_review_issues
                from core.planning_schema import TurnReview
                from proposals.proposal_flow import digest
                if processor.review_turn is None or data['budget'].remaining<=0:
                    from planning_agent import ModelError
                    raise ModelError('복구 후보를 재검증할 수 없어.',kind='candidate_validation')
                data['budget'].remaining-=1
                before=data['original'];candidate=result.state;targets=sorted(candidate.cells)
                payload={'source_text':data['text'],'user_message':data['text'],
                    'requirements':processor.template.export(before),'candidate_requirements':processor.template.export(candidate),
                    'candidate_questions':[q.model_dump(mode='json') for q in candidate.questions],
                    'review_scope':'project','required_review_targets':targets,'plan':{'operations':[]},
                    'existing_evidence':{key:[e.model_dump() for e in c.evidence] for key,c in candidate.cells.items()},
                    'data_review_inventory':data_review_inventory(processor.template,before,candidate,full=True),
                    'field_review_inventory':field_review_request(processor.template,before,candidate,candidate,full=True,include_delivery=True)}
                def check(verdict):
                    """check 관련 작업을 확인한다"""
                    issues=list(verdict.issues)
                    if not verdict.valid:issues.append('recovery_semantic_rejected')
                    if sorted(verdict.reviewed_targets)!=targets:issues.append('recovery_scope_mismatch')
                    issues+=coverage_issues(verdict,data['text'],processor.template,candidate,required=True)
                    issues+=data_review_issues(verdict,data['text'],processor.template,before,candidate,full=True,
                        required=processor.require_data_receipts,sparse=not processor.require_data_receipts)
                    issues+=field_review_issues(verdict,data['text'],processor.template,before,candidate,candidate,
                        full=True,include_delivery=True,required=processor.require_detailed_receipts,
                        sparse=not processor.require_detailed_receipts)
                    return issues
                run_model_stage(processor.review_turn,payload,TurnReview,'review_recovered_candidate',trace=data.get('trace'),budget=data['budget'],check=check)
                result.recovery_context={'revalidated':True,'candidate_digest':digest(processor.template.export(candidate))}
                result.reviews=[];result.validation_issues=[];result.review_blocked=False
                result.source_review_verified=True;result.reviewed_project=True
            except Exception as exc:
                if not hasattr(exc,'failed_stage'):exc.failed_stage='review_recovered_candidate'
                return {'source_error':exc}
        if data.get('trace'):
            data['trace']('candidate_revalidated',{'source_verified':result.source_review_verified,
                'blocked':result.review_blocked,'remaining_issues':len(result.reviews)})
        return {'source_error':None}

    def candidate_route(data):
        """Skip unnecessary question generation for a verified identical edit."""
        if data.get('source_error') is not None:
            return source_route(data)
        target = review_recovery_route(data)
        if (target == 'finish' and data.get('managed') and data.get('explicit_edit')
                and processor.template.export(data['result'].state) ==
                    processor.template.export(data['original'])):
            return 'save'
        return target

    def command_node(data: PlanningGraphState):
        """기존 내부 명령 또는 Python 기반 프로젝트 명령을 실행한다."""
        text = (data.get('current_command') or data['text']).strip()
        before=data['original']
        if data.get('managed') and text.lower().split(' ', 1)[0] not in ('/propose','/approve','/reject','/defer'):
            handler = data.get('execute_command')
            if handler is None:
                raise ValueError('명령 처리 함수가 연결되지 않았어.')
            output, updated_state = handler(text)
            return {'command': 'external', 'result': TurnResult(state=updated_state),
                    'original': updated_state, 'action_results': [*data.get('action_results', []), output],
                    'advance_pending': True, 'skip_persist': True}
        if re.match(r'^/(?:approve|reject)\b',text,re.I):
            return {'result':apply_decision(processor,before,data['text'], trace=data.get('trace'), budget=data['budget']), 'command':'decision'}
        result=TurnResult(state=before.model_copy(deep=True))
        if text.lower()=='/propose' or is_proposal_request(text):
            if before.assessment_pending and processor.assess is not None:
                return {'result':result,'command':'reassess_proposal'}
            document = processor.template.export(result.state)
            reconcile_questions(result.state, missing_candidates(document,result.state), document)
            create_proposal(processor,result,data['budget'],data.get('trace'),force=True)
            return {'result':result,'command':'proposal'}
        if text.lower()=='/defer':
            q=current_question(result.state)
            if q is None:
                result.reviews.append('보류할 질문이 없어.')
            else:
                result.state.questions.remove(q)
                token=question_key(q)
                if token:
                    if token not in result.state.deferred_required:result.state.deferred_required.append(token)
                    result.state.resolved_required=[v for v in result.state.resolved_required if v!=token]
                    record_required(result.state,q,'deferred',data['text'])
                else:
                    if q.id in result.state.dynamic_question_reasons:
                        record_required(result.state,q,'deferred',data['text'])
                    result.state.deferred_questions.append(q)
                result.updates.append(f'{q.id} 질문을 보류했어. 확정값은 바꾸지 않았어.');result.saved=1
            return {'result':result,'command':'defer'}
        result.reviews.append('지원하지 않는 명령 형식이야.')
        return {'result':result,'command':'invalid'}

    def review_recovery_route(data: PlanningGraphState) -> str:
        """관련 기능의 복구 가능성과 남은 재시도 한도를 검사한다."""
        result = data['result']
        return 'retry' if (getattr(result, 'retry_links', None) and processor.repair is not None
                           and data.get('repair_attempts', 0) < MAX_LINK_ATTEMPTS and data['budget'].remaining > 0
                           and data.get('recovery_status') not in ('terminal', 'unresolved')) else 'finish'

    def review_recovery_node(data: PlanningGraphState):
        """실패한 관련 기능 연결만 별도의 복구 노드에서 다시 검증한다."""
        attempt = data.get('repair_attempts', 0) + 1
        trace = data.get('trace')
        data['budget'].remaining -= 1
        if trace:
            trace('review_recovery_attempt', {'attempt':attempt, 'issues':len(data['result'].retry_links), 'retry_budget_remaining':data['budget'].remaining})
        status = 'rejected'
        feedback = {}
        try:
            if processor.recover_review_links(data['result'], data['text'], source_name=data.get('source_name'),
                                              trace=trace, feedback=data.get('recovery_feedback')):
                status = 'recovered'
                result = data['result']
                if data.get('source_name') and not result.reviews:
                    digest = Source(data['text'].strip(), data['source_name']).digest
                    if digest not in result.state.documents:
                        result.state.documents.append(digest)
            else:
                status = 'unresolved'
        except Exception as exc:
            kind = model_error_kind(exc)
            status = 'rejected' if kind in RETRYABLE_KINDS else 'terminal'
            issues = list(getattr(exc, 'issues', ())) or [str(exc) if kind in ('candidate_validation', 'response_schema') else kind]
            feedback = {'stage':'review_links', 'kind':kind, 'issues':issues}
            if trace:
                trace('review_recovery_failure', {'attempt':attempt, 'type':type(exc).__name__, 'kind':kind, 'issues':issues})
        return {'repair_attempts':attempt, 'recovery_status':status, 'recovery_feedback':feedback}

    def completeness_node(data: PlanningGraphState):
        """필수 요구사항 확인 후보를 수집한다."""
        if data.get('trace'):
            data['trace']('graph_node', {'node':'completeness','status':'start'})
        result = data['result']
        result.source_reviews=list(result.reviews)
        document = processor.template.export(result.state)
        if data.get('answer_handled') and result.reviews and not result.saved:
            return {'document': document, 'candidates': []}
        if not data.get('answer_handled'):
            baseline = data['original'].model_copy(deep=True)
            ensure_public_ids(baseline)
            previous = processor.template.export(baseline)
            if data.get('explicit_edit') and result.reviews and document == previous:
                if data.get('trace'):
                    data['trace']('required_skipped', {'reason':'rejected_edit_no_document_change'})
                return {'document': document, 'candidates': []}
            resolve_satisfied_questions(result.state, document, data['original'], previous, data['text'], trusted=not bool(result.reviews))
        prune_required_status(result.state, data['original'], document, data['text'])
        candidates = missing_candidates(document, result.state)
        reconcile_questions(result.state, candidates, document)
        trace = data.get('trace')
        if trace:
            trace('required_candidates', {'candidates': candidates, 'existing_questions':len(result.state.questions)})
        return {'document': document,'candidates':candidates}

    def required_route(data: PlanningGraphState) -> str:
        """필수 질문 생성 여부를 결정한다."""
        result=data['result']
        if result.review_blocked:
            result.state.assessment_pending = True
            return 'finish'
        if result.replay or (result.reviews and not result.saved):
            return 'finish'
        simple=(getattr(processor,'use_simple_questions',False) and processor.assess == processor.model.assess
                and processor.review_questions == processor.model.review_questions)
        if simple and result.saved and not result.reviews and not data.get('source_name'):
            from questions.question_scope import can_reuse_queue_assessment
            explicit=(data.get('command')=='defer' or bool(re.match(r'^\s*/resume\b',data['text'],re.I)))
            if can_reuse_queue_assessment(processor.template,data['original'],result.state,data['candidates'],
                    explicit_queue_command=explicit,verified_turn=result.source_review_verified):
                if data.get('trace'):
                    data['trace']('question_assessment_reused',{'reason':'verified_queue_only_change','existing_questions':len(result.state.questions)})
                return 'finish'
        return 'ask' if processor.assess is not None else 'finish'

    def question_node(data: PlanningGraphState):
        """필요한 필수 질문을 생성하고 등록한다."""
        if data.get('trace'):
            data['trace']('graph_node', {'node':'question','status':'start'})
        state = data['result'].state
        if not data.get('answer_handled'):
            prune_advisory_questions(state, data['original'])
        active_ids = {q.id for q in state.questions}
        deferred_ids = {q.id for q in state.deferred_questions}
        latest_by_id = {event.question.id: event for event in state.required_answer_history}
        answered = {qid: event for qid, event in latest_by_id.items()
                    if event.status == 'answered' and qid not in active_ids and qid not in deferred_ids}
        assessment = None
        added = []
        payload = {
            'requirements':data['document'],
            'candidates':data['candidates'],
            'existing_questions':[{'id':q.id,'text':q.text,'kind':q.kind,'target':(q.target or q.answer_hint).model_dump() if q.target or q.answer_hint else None, 'reason':state.dynamic_question_reasons.get(q.id), 'priority':state.question_priorities.get(q.id)} for q in state.questions],
            'dynamic_targets':[dict(t.model_dump(), label=state.rows[t.item].label if t.item else None,
                                    public_id=state.public_ids.get(t.item))
                               for t in sorted(dynamic_targets(processor.template,state),key=lambda t:t.key())],
            'source_basis':list(dict.fromkeys(
                ref.quote for item in [*state.questions, *state.deferred_questions, *state.cells.values()] for ref in item.evidence
            )),
            'answered_questions':[{'id':e.question.id,'text':e.question.text,'answer':e.raw_answer,
                                   'target':(e.question.target or e.question.answer_hint).model_dump()
                                           if e.question.target or e.question.answer_hint else None}
                                  for e in state.required_answer_history if answered.get(e.question.id) is e],
            'deferred_questions':[q.model_dump(mode='json') for q in state.deferred_questions],
            'deferred':list(state.deferred_required),
            'user_message':data['text'],
        }

        def check_questions(candidate):
            """허용된 필수 질문 대상과 질문 형식을 검사한다."""
            allowed = {(c['key'], c.get('row_id')) for c in data['candidates']}
            issues = []
            seen = set()
            for i, q in enumerate(candidate.questions):
                if (('decision_topics' in q.model_fields_set and len(q.decision_topics) != 1)
                        or any(not t.strip() for t in q.decision_topics)):
                    issues.append(f'questions[{i}]:single_decision_required')
                key = (q.key, q.row_id)
                token = f'{q.key}:{q.row_id}' if q.row_id else q.key
                if q.key in ('done','technology') or requirement_sufficient(data['document'], token):
                    continue
                if key not in allowed:
                    issues.append(f'questions[{i}]:invalid_target')
                if not 5 <= len(q.question.strip()) <= 350:
                    issues.append(f'questions[{i}]:invalid_length')
                if key in seen:
                    issues.append(f'questions[{i}]:duplicate_target')
                seen.add(key)
            targets = dynamic_targets(processor.template,state)
            for i, q in enumerate(candidate.dynamic_questions):
                if issue := assessment_issue(processor.template,state,q,targets):
                    issues.append(f'dynamic_question[{i}]:{issue}')
            decision_seen = set()
            for d in candidate.existing_decisions:
                confirms_answer = not d.needed and d.question_id in answered
                if d.question_id in decision_seen or (d.question_id not in active_ids and not confirms_answer):
                    issues.append('invalid_existing_question')
                decision_seen.add(d.question_id)
            issues.extend(question_decision_issues(state,candidate,data['original']))
            return issues

        try:
            if (getattr(processor,'use_simple_questions',False)
                    and processor.assess == processor.model.assess
                    and processor.review_questions == processor.model.review_questions):
                from questions.simple_questions import assess_simple
                assessment=assess_simple(processor.model,payload,processor.template,state,data['original'],data['budget'],data.get('trace'),check_questions,
                    incremental=bool(data.get('answer_handled') and not data.get('command') and not data.get('source_name')
                        and not data.get('explicit_edit') and data['result'].source_review_verified and not data['result'].reviews))
            else:
                assessment = run_model_stage(processor.assess, payload, RequiredAssessment, 'required_questions',
                                             trace=data.get('trace'), budget=data['budget'], check=check_questions, preserve_rejected=True)
                if processor.require_quality_audits or (assessment_questions(assessment) and (processor.require_semantic_audits or processor.review_questions is not None)):
                    if processor.review_questions is None:
                        from planning_agent import ModelError
                        raise ModelError('질문 의미 검토 모델이 없어.',kind='candidate_validation')
                    original_assessment=assessment
                    if data.get('trace'):
                        data['trace']('question_generation',{'response':assessment.model_dump(mode='json')})
                    review_payload={
                        'requirements':data['document'],'source_basis':payload['source_basis'],
                        'user_message':payload['user_message'],
                        'known_facts':{key:cell.value for key,cell in state.cells.items() if cell.value is not None},
                        'existing_questions':payload['existing_questions'],'answered_questions':payload['answered_questions'],
                        'deferred_questions':payload['deferred_questions'],'dynamic_targets':payload['dynamic_targets'],
                        'fixed_candidates':data['candidates'],
                        'questions':[{'input_key':key,'candidate':q.model_dump(mode='json',exclude={'decision_topics'})}
                                     for key,q in assessment_questions(assessment).items()],
                        'existing_decisions':[d.model_dump(mode='json') for d in assessment.existing_decisions],
                    }
                    def check_question_review(verdict):
                        """question review 관련 작업을 확인한다"""
                        issues=question_review_issues(original_assessment,verdict,review_payload['known_facts'],
                            required=processor.require_quality_audits,
                            require_all_areas=processor.require_detailed_receipts)+check_questions(verdict.assessment)
                        prior_texts={q['text'].strip() for group in ('existing_questions','answered_questions','deferred_questions')
                                     for q in review_payload[group]}
                        outputs=assessment_questions(verdict.assessment)
                        if any(m.output_key in outputs and outputs[m.output_key].question.strip() in prior_texts
                               for m in verdict.missing_questions):
                            issues.append('question_review_restored_existing_question')
                        return issues
                    verdict=run_model_stage(processor.review_questions,review_payload,QuestionSemanticReview,'review_questions',
                        trace=data.get('trace'),budget=data['budget'],check=check_question_review)
                    assessment=verdict.assessment
                    if data.get('trace'):
                        data['trace']('question_semantic_review',{'response':verdict.model_dump(mode='json')})
            trial = state.model_copy(deep=True)
            issues = check_questions(assessment)
            critical = any(issue.startswith(('dynamic_question[','existing_question_requires_new_evidence:')) or issue == 'invalid_existing_question'
                           for issue in issues)
            trial.assessment_pending = critical
            if not issues:
                apply_question_decisions(trial,data['document'],assessment,data['original'])
                from questions.simple_questions import apply_question_revisions
                apply_question_revisions(trial,assessment)
            dynamic_added=register_dynamic_questions(processor.template,trial,assessment,data['original'])
            if critical:
                data['result'].reviews.append('중요 결정의 대상이나 근거를 검증하지 못했어. /propose로 다시 검토할 수 있어.')
            allowed_dynamic = dynamic_targets(processor.template, trial)
            if data.get('trace'):
                for item in assessment.dynamic_questions:
                    issue = assessment_issue(processor.template, trial, item, allowed_dynamic)
                    if issue:
                        data['trace']('dynamic_question_filtered', {'path':item.path,'row_id':item.row_id,'reason':issue})
            added = dynamic_added + register_questions(trial, data['document'], data['candidates'], assessment)
            from questions.question_policy import apply_question_tiers
            apply_question_tiers(trial,assessment,data.get('trace'))
            from questions.question_policy import termination_readiness
            readiness=termination_readiness(trial,data['document'])
            if data.get('trace'):
                data['trace']('question_termination_audit',readiness)
            validate_state(processor.template, trial)
            data['result'].state = state = trial
        except Exception as exc:
            assessment = None
            state.assessment_pending = True
            data['result'].reviews.append('필수 요구사항 추가 질문 검토를 완료하지 못했어.')
            if data.get('trace'):
                data['trace']('required_question_error', {'type':type(exc).__name__, 'kind':model_error_kind(exc),
                    'failed_stage':getattr(exc,'failed_stage',None),'issues':list(getattr(exc,'issues',())),'source_review_verified':data['result'].source_review_verified})
        finally:
            fallback_added = []
            try:
                trial = state.model_copy(deep=True)
                fallback = essential_fallback_questions(data['document'], data['candidates'], trial)
                fallback_added = register_questions(trial, data['document'], data['candidates'], fallback)
                validate_state(processor.template, trial)
                data['result'].state = trial
            except Exception as exc:
                data['result'].reviews.append('필수 요구사항 최소 질문 생성에 실패했어.')
                if data.get('trace'):
                    data['trace']('required_fallback_error', {'type':type(exc).__name__})
            if data.get('trace'):
                data['trace']('required_fallback', {'added':fallback_added, 'reason':'explicit_missing_essential_only'})
                data['trace']('required_questions', {
                    'generated':added, 'fallback_generated':fallback_added,
                    'status':'completed' if assessment is not None else 'assessment_failed',
                    'response':assessment.model_dump(mode='json') if isinstance(assessment, RequiredAssessment) else None,
                })
        return {'assessment':assessment} if assessment is not None else {}

    def finish_node(data: PlanningGraphState):
        """최종 ID와 상태의 무결성을 검증한다."""
        if data.get('trace'):
            data['trace']('graph_node', {'node':'finish','status':'start'})
        result = data['result']
        ensure_public_ids(result.state)

        for q in list(result.state.deferred_questions):
            target=q.target or q.answer_hint
            if target and target.item and re.fullmatch(r'R\d+',target.item) and target.item not in result.state.rows:
                result.state.deferred_questions.remove(q)
                result.state.retired_optional_questions.append(q)
        close_removed_dynamic_questions(processor.template,result.state,data['original'],data['text'])
        if result.state != data['original']:
            rank_questions(result.state)
        invalidate_stale(processor.template,result.state)
        if data.get('command') == 'reassess_proposal':
            create_proposal(processor,result,data['budget'],data.get('trace'),force=True)
        validate_transition(processor.template, data['original'], result.state, data['text'])
        if data.get('trace'):
            data['trace']('recovery_budget', {'used':MAX_EXTRA_ATTEMPTS-data['budget'].remaining, 'remaining':data['budget'].remaining})
            data['trace']('required_finish', {'question_count':len(result.state.questions), 'questions':[{'id':q.id,'text':q.text} for q in result.state.questions], 'warnings':list(result.reviews)})
        return {'result':result}

    def save_requirements_node(data: PlanningGraphState):
        """각 작업의 기존 무결성 검증을 완료한 후 안전 저장 콜백을 실행한다."""
        finalized = finish_node(data)['result']
        callback = data.get('persist_action')
        if callback is None:
            raise RuntimeError('저장 콜백을 찾을 수 없어.')
        outcome = callback(finalized, data['text'], data['original'],
                           data.get('action_index', 0) == 0)
        message = finalized.response()
        if (data.get('explicit_edit') and not outcome.get('requirements_changed')
                and not finalized.blocking_reviews):
            message = '이미 동일한 내용이야. JSON과 문서를 다시 만들지 않았어.'
        results = [*data.get('action_results', []), message]
        if outcome.get('document_id'):
            results.append('TXT 원문 ID · ' + outcome['document_id'])
        return {'result': finalized, 'original': finalized.state,
                'requirements_changed': bool(outcome.get('requirements_changed')),
                'saved_ok': True, 'modified_in_request': bool(data.get('modified_in_request') or outcome.get('requirements_changed')),
                'stop_remaining': bool(finalized.input_rejected or finalized.review_blocked or
                                       (finalized.blocking_reviews and not finalized.saved)),
                'action_results': results,
                'advance_pending': True}

    def next_after_save(data: PlanningGraphState):
        return 'generate_document' if data.get('requirements_changed') else 'route'

    def next_after_command(data: PlanningGraphState):
        if not data.get('managed'):
            return 'completeness' if data.get('command') in ('defer','reassess_proposal') else 'finish'
        if data.get('command') == 'external':
            return 'route'
        return 'completeness' if data.get('command') in ('defer','reassess_proposal') else 'save_requirements'

    def next_after_required(data: PlanningGraphState):
        return 'save_requirements' if data.get('managed') else 'finish'

    def complete_node(data: PlanningGraphState):
        """Legacy: final validity checks. Managed: one combined response."""
        if not data.get('managed'):
            return finish_node(data)
        summary = combine_action_results(data.get('action_results', []))
        state = data['original']
        return {'result': TurnResult(state=state, updates=[summary] if summary else [])}

    graph.add_node('generate_document', generate_document_node)
    graph.add_node('document_review', document_review_node)
    graph.add_node('command', command_node)
    graph.add_node('route', route_node)
    graph.add_node('interpret', interpret_node)
    graph.add_node('extract', extract_node)
    graph.add_node('completeness', completeness_node)
    graph.add_node('review_recovery',review_recovery_node)
    graph.add_node('question', question_node)
    graph.add_node('finish', complete_node)
    graph.add_node('save_requirements',save_requirements_node)
    graph.add_node('candidate_validation',candidate_node)
    graph.add_node('source_repair',source_repair_node)
    graph.add_node('source_stop',source_stop_node)
    graph.add_edge(START,'route')
    graph.add_conditional_edges('route',route_input, {'interpret':'interpret','extract':'extract','command':'command',
                                                      'generate_document':'generate_document','document_review':'document_review','finish':'finish'})
    graph.add_conditional_edges('command',next_after_command, {'completeness':'completeness','finish':'finish','route':'route','save_requirements':'save_requirements'})
    graph.add_edge('interpret','candidate_validation')
    graph.add_conditional_edges('extract',source_route,{'candidate':'candidate_validation','repair':'source_repair','stop':'source_stop'})
    graph.add_conditional_edges('source_repair',source_route,{'candidate':'candidate_validation','repair':'source_repair','stop':'source_stop'})
    graph.add_conditional_edges('candidate_validation',candidate_route,{'retry':'review_recovery','finish':'completeness','repair':'source_repair','stop':'source_stop','save':'save_requirements'})
    graph.add_conditional_edges('review_recovery',review_recovery_route,{'retry':'review_recovery','finish':'candidate_validation'})
    graph.add_conditional_edges('completeness', lambda d: 'ask' if required_route(d) == 'ask' else next_after_required(d), {'ask':'question','finish':'finish','save_requirements':'save_requirements'})
    graph.add_conditional_edges('question',next_after_required, {'save_requirements':'save_requirements','finish':'finish'})
    graph.add_edge('finish',END)
    graph.add_conditional_edges('save_requirements', next_after_save, {'generate_document':'generate_document','route':'route'})
    graph.add_conditional_edges('generate_document',lambda d:'route' if d.get('managed') else 'end',{'route':'route','end':END})
    graph.add_conditional_edges('document_review',lambda d:'route' if d.get('managed') else 'end',{'route':'route','end':END})
    return graph.compile()


def invoke_planning_graph(processor, state, text, source_name=None, *, trace=None):
    """구성된 LangGraph를 실행하고 결과를 반환한다."""
    if not isinstance(text,str) or not text.strip() or '\x00' in text:
        raise ValueError('읽을 수 있는 입력 텍스트가 없어.')
    if trace:
        trace('graph_start', {'implementation':'StateGraph', 'version':'A01-STEP01-REQUIREMENTS-18.42'})
    graph=build_planning_graph(processor)
    result=graph.invoke({'original':state,'text':text,'source_name':source_name,'trace':trace})
    return result['result']


def invoke_document_graph(processor, state, requirements_path, *, action='generate'):
    """요구사항 저장 이후 동일한 LangGraph의 문서 생성·검토 노드를 실행한다."""
    if action not in ('generate', 'approve'):
        raise ValueError('지원하지 않는 기획서 작업이야.')
    graph = build_planning_graph(processor)
    outcome = graph.invoke({'original': state, 'text': '', 'source_name': None,
                            'document_action': action, 'requirements_path': str(requirements_path)})
    return outcome['document_result']


def invoke_managed_graph(processor, state, text, *, source_name=None, trace=None,
                         requirements_path, persist_action, execute_command):
    """Single graph run with persisted sequential actions and document edges."""
    if not isinstance(text, str) or not text.strip() or '\x00' in text:
        raise ValueError('읽을 수 있는 입력 텍스트가 없어.')
    graph = build_planning_graph(processor)
    outcome = graph.invoke({'original': state, 'text': text, 'source_name': source_name,
                            'trace': trace, 'managed': True,
                            'requirements_path': str(requirements_path),
                            'persist_action': persist_action,
                            'execute_command': execute_command}, config={'recursion_limit': 180})
    return combine_action_results(outcome.get('action_results', []))
