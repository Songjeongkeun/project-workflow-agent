"""AI 보완안 생성, 선택 승인과 반려를 담당한다."""


import json
import re
from hashlib import sha256
from collections import Counter
from core.planning_schema import (AdvisoryBatch, AdvisoryDraft, AdvisoryResponse, Cell, Change, CompletionReview, Evidence, Row, Target, REVIEW_VERSION, row_matches,
                             identity, validate_state)
from questions.required_check import requirement_sufficient, advisory_question, KEYS
from questions.decision_flow import BEHAVIOR

CHANNEL_LABELS = {
    'app_push': '앱 푸시', 'email': '이메일', 'sms': '문자',
    'app_screen': '앱 화면', 'web_screen': '웹 화면', 'kakao': '카카오톡',
    'messenger': '메신저', 'slack': '슬랙', 'teams': 'Teams', 'phone': '전화',
}
DELIVERY_ERROR = 'delivery_detail_invalid_or_conflicting'

PATCH_FIELDS = {
    'release_scope.features.behavior_and_value', 'release_scope.features.done_criteria',
    'user_experience.flow', 'user_experience.screens.input_fields',
    'user_experience.screens.displayed_information', 'user_experience.screens.user_action',
    'user_experience.screens.next_screen', 'policies.behavior_and_state_changes',
    'policies.open_considerations', 'data_rules.storage_and_management',
    'data_rules.input_validation', 'data_rules.error_and_integrity_handling',
    'data_rules.fields.purpose', 'data_rules.fields.format_and_structure',
    'data_rules.fields.required',
}


def normalize_patch_targets(draft, state, template):
    """Resolve only unique existing rows; retain the canonical IDs through approval."""
    from planning_agent import ModelError
    result = draft.model_copy(deep=True)
    for patch in result.patches:
        for change in [patch, *patch.dependents]:
            target = change.target
            parent = target.path.rpartition('.')[0]
            if parent not in template.rows:
                continue
            matches = row_matches(state, parent, target.item or '')
            if len(matches) != 1:
                raise ModelError('제안 수정 대상을 유일하게 확인하지 못했어.', kind='candidate_validation',
                                 issues=['proposal_target_unknown_or_ambiguous:'+target.key()])
            change.target = Target(path=target.path, item=next(iter(matches)))
    return result



def normalize_redundant_completion_patches(draft, state, trace=None):
    """Discard only proven identical completion/patch duplicates, never conflicts.

    The completion channel owns done_criteria for a feature. A patch containing
    precisely the same proposed criteria is redundant; a differing patch remains
    subject to the original hard conflict guard. In either case no saved state
    or prior value is silently changed.
    """
    copy = draft.model_copy(deep=True)
    by_target = {
        Target(path='release_scope.features.done_criteria', item=rid).key(): item.criteria
        for item in copy.completion
        for rid, fid in state.public_ids.items()
        if fid == item.feature_id
    }
    retained, redundant = [], []
    for patch in copy.patches:
        key = patch.target.key()
        if (key in by_target and patch.value == by_target[key]
                and not patch.dependents):
            redundant.append(key)
            continue
        retained.append(patch)
    copy.patches = retained
    if redundant and trace:
        trace('proposal_redundant_patches_removed', {'targets': redundant})
    return copy



def quarantine_unverified_relation_patches(draft, state, trace=None):
    """Quarantine optional patches with unverified row/feature links.

    Never invent or broaden a row's related_features to make a proposal pass.
    Retained proposal channels must pass all original guards and semantic review.
    A patch referenced as another patch's dependent cannot be silently removed:
    keep it so the original validator rejects the conflicting bundle.
    """
    result = draft.model_copy(deep=True)
    problematic = set()
    for item in result.patches:
        target = item.target
        parent = target.path.rpartition('.')[0]
        if parent not in ('user_experience.screens', 'data_rules.fields'):
            continue
        links = state.cells.get(Target(path=parent + '.related_features', item=target.item).key())
        if not links or not isinstance(links.value, list) or not set(item.related_features).issubset(set(links.value)):
            problematic.add(target.key())
    if not problematic:
        return result
    referenced = {dependency.target.key() for item in result.patches for dependency in item.dependents}
    if problematic & referenced:
        return result
    result.patches = [item for item in result.patches if item.target.key() not in problematic]
    if trace:
        trace('proposal_unverified_relation_patches_quarantined', {'targets': sorted(problematic),
              'reason': 'row_feature_relation_not_verified', 'remaining_patch_count': len(result.patches)})
    return result

def detail_value_valid(kind, value):
    """value valid 관련 작업을 수행한다"""
    if kind == 'bool':
        return type(value) is bool
    strings = value if kind == 'list' and isinstance(value,list) else [value] if kind == 'text' and isinstance(value,str) else []
    return bool(strings) and all(isinstance(v,str) and v.strip() and '\x00' not in v for v in strings)


def patch_issues(draft, state, template, document):
    """issues 관련 작업을 수행한다"""
    if not draft.patches:
        return []
    if state is None or template is None:
        return ['proposal_patch_context_missing']
    issues, seen = [], set()
    primary_targets = {patch.target.key() for patch in draft.patches}
    dependent_targets = set()
    included = {f['id'] for f in document['release_scope']['features'] if f['included_in_release'] is True}
    occupied = {Target(path='release_scope.features.done_criteria', item=rid).key()
                for completion in draft.completion for rid, fid in state.public_ids.items()
                if fid == completion.feature_id}
    occupied.update(Target(path=BEHAVIOR, item=rid).key()
                    for detail in draft.delivery_details for rid,fid in state.public_ids.items()
                    if fid == detail.feature_id)
    if draft.flow is not None:
        occupied.add('user_experience.flow')
    for patch in draft.patches:
        target = patch.target
        key = target.key()
        row = state.rows.get(target.item) if target.item else None
        parent = target.path.rpartition('.')[0]
        old = state.cells.get(key)
        kind = template.fields.get(target.path)
        value = patch.value
        valid_type = detail_value_valid(kind,value)
        strings = value if isinstance(value,list) else [value] if isinstance(value,str) else []
        scoped = ((parent in template.rows and row is not None and row.path == parent)
                  or (parent not in template.rows and target.item is None))
        related = patch.related_features
        if (target.path not in PATCH_FIELDS or not scoped or key in seen | occupied
                or patch.previous_value != (old.value if old else None)
                or value == patch.previous_value or not valid_type
                or any('\x00' in v for v in strings) or not patch.reason.strip()
                or not related or len(related) != len(set(related)) or set(related) - included):
            if key in occupied:
                issues.append('proposal_patch_duplicates_specialized_channel:'+key)
            elif patch.previous_value != (old.value if old else None):
                issues.append('proposal_patch_previous_value_mismatch:'+key)
            else:
                issues.append('proposal_patch_scope_type_or_value_mismatch:'+key)
        if parent == 'release_scope.features' and (state.public_ids.get(target.item) not in related or len(related) != 1):
            issues.append('proposal_patch_feature_mismatch')
        if row and parent in ('user_experience.screens','data_rules.fields'):
            links = state.cells.get(Target(path=parent+'.related_features',item=target.item).key())
            if not links or not set(related).issubset(links.value):
                issues.append('proposal_patch_unverified_relation')
        for dependent in patch.dependents:
            destination = dependent.target
            dep_key = destination.key()
            prior = state.cells.get(dep_key)
            dep_parent = destination.path.rpartition('.')[0]
            dep_row = state.rows.get(destination.item) if destination.item else None
            allowed_scope = (dep_parent not in template.rows and destination.item is None)
            if dep_parent == 'release_scope.features':
                allowed_scope = state.public_ids.get(destination.item) in related
            elif dep_parent in ('user_experience.screens','data_rules.fields'):
                relation = state.cells.get(Target(path=dep_parent+'.related_features',item=destination.item).key())
                allowed_scope = bool(dep_row and dep_row.path == dep_parent and relation
                                     and set(related).intersection(relation.value))
            if (destination.path not in PATCH_FIELDS
                    or dep_key in primary_targets | dependent_targets | occupied or not allowed_scope
                    or (prior.value if prior else None) != dependent.previous_value
                    or not detail_value_valid(template.fields.get(destination.path),dependent.value)):
                issues.append('proposal_patch_dependency_scope_or_value_mismatch')
            dependent_targets.add(dep_key)
        seen.add(key)
    if len(draft.patches) > 30:
        issues.append('proposal_count_limit')
    return issues


def normalize_advisory_response(response: AdvisoryResponse) -> AdvisoryDraft:
    """모델의 선택값을 승인 및 저장에 사용하는 기존 형식으로 변환한다."""
    details = []
    for detail in response.delivery_details:
        timing = detail.previous_day_time
        channel = CHANNEL_LABELS[detail.channel] if detail.channel else ''
        choice = channel
        if timing:
            hour = '오전' if timing.hour < 12 else '오후'
            minute = f' {timing.minute}분' if timing.minute else ''
            choice = f'전날 {hour} {timing.hour % 12 or 12}시{minute}에'
            if channel:
                choice += f" {channel}{'으로' if channel.endswith('면') else '로'}"
        details.append({'feature_id':detail.feature_id,'choice':choice,'reason':detail.reason})
    return AdvisoryDraft.model_validate({**response.model_dump(exclude={'delivery_details'}),
                                         'delivery_details':details})


def delivery_issues(details, document):
    """전달 후보를 합칠 수 없거나 적용 범위와 충돌하는 이유를 판정한다."""
    included = {f['id'] for f in document['release_scope']['features'] if f['included_in_release'] is True}
    counts = Counter(detail.feature_id for detail in details)
    issues = {}
    for index, detail in enumerate(details):
        if detail.feature_id not in included or counts[detail.feature_id] != 1:
            issues[index] = 'invalid_or_duplicate_target'
        elif not detail.choice.strip() or not detail.reason.strip() or '\x00' in detail.choice + detail.reason:
            issues[index] = 'invalid_choice_or_reason'
    return issues


def review_key(document, draft):
    """검토한 요구사항과 제안의 해시를 계산한다."""
    return digest({'requirements':digest(document), 'draft':draft.model_dump(mode='json'), 'review_version':REVIEW_VERSION})


def proposal_review_issues(review, draft, state=None):
    """제안 검토 결과의 참조와 통합 대상 범위를 검사한다."""
    issues = list(review.issues)
    expected_patches = [f'S{i}' for i in range(1, len(draft.patches) + 1)]
    if sorted(review.reviewed_patch_ids) != sorted(expected_patches):
        issues.append('proposal_patch_review_scope_mismatch')
    criteria = {c.feature_id:c.criteria for c in draft.completion}
    seen = set()
    for conflict in review.conflicts:
        key = conflict.feature_id, conflict.criterion_index
        if key in seen or key[1] >= len(criteria.get(key[0], [])) or not conflict.reason.strip():
            issues.append('proposal_review_invalid_reference')
        else:
            issues.append(f'completion[{key[0]}].criteria[{key[1]}]: {conflict.reason.strip()}')
        seen.add(key)
    supplied = [update.feature_id for update in review.delivery_updates]
    declared = {detail.feature_id for detail in draft.delivery_details}
    rejected = review.rejected_delivery_ids
    if len(rejected) != len(set(rejected)) or set(rejected) - declared:
        issues.append('delivery_rejection_invalid_target')
    elif rejected:
        issues.append(DELIVERY_ERROR)
    required = [detail.feature_id for detail in draft.delivery_details if detail.feature_id not in rejected]
    if len(supplied) != len(set(supplied)) or set(supplied) != set(required):
        issues.append('delivery_integration_target_mismatch')
    if any(not update.value.strip() or '\x00' in update.value for update in review.delivery_updates):
        issues.append('delivery_integration_invalid_value')
    dependent_targets = set()
    for update in review.delivery_updates:
        for patch in update.dependents:
            target = patch.target
            key = target.key()
            old = state.cells.get(key) if state else None
            row = state.rows.get(target.item) if state and target.item else None
            related = state.cells.get(Target(path='user_experience.screens.related_features', item=target.item).key()) if row else None
            allowed = (target.path == 'user_experience.flow' and target.item is None) or (
                row is not None and row.path == 'user_experience.screens'
                and target.path.startswith(row.path + '.')
                and target.path in {row.path + '.' + field for field in ('displayed_information','user_action','next_screen')}
                and related is not None and update.feature_id in related.value)
            if (not allowed or key in dependent_targets or old is None or not isinstance(old.value, str)
                    or old.value != patch.previous_value or not patch.value.strip() or '\x00' in patch.value):
                issues.append('delivery_dependency_scope_or_value_mismatch')
            dependent_targets.add(key)
    return issues


def review_payload(document, draft, state=None):
    """전체 제안과 확정 요구사항을 검토 입력으로 구성한다."""
    return {'requirements':document, 'draft':draft.model_dump(mode='json'),
            'patch_review_manifest':[{'id':f'S{i}', 'target':patch.target.model_dump(mode='json')}
                                     for i,patch in enumerate(draft.patches,1)],
            'reviewed_patch_ids_expected':[f'S{i}' for i in range(1,len(draft.patches)+1)],
            'completion':[c.model_dump(mode='json') for c in draft.completion],
            'existing_cells':{key:cell.value for key,cell in state.cells.items()} if state else {}}



def digest(document):
    """요구사항 본문의 해시를 계산한다."""
    return sha256(json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def handoff_status(template, state):
    """본문 버전의 원문 검토 기록과 미결 핵심 요구사항을 확인한다."""
    document = template.export(state)
    revision = digest(document)
    unresolved = [request for request in state.input_requests if request.status == 'pending']
    if unresolved:
        status = 'pending_input'
    elif state.source_review_status == 'failed':
        status = 'verification_failed'
    elif not planning_ready(document, state):
        status = 'user_confirmation_required'
    elif (state.source_review_status != 'verified' or state.source_review_digest != revision
          or state.source_review_version != REVIEW_VERSION):
        status = 'semantic_review_required'
    else:
        status = 'ready'
    return {'status': status, 'requirements_sha256': revision, 'source_review_status':state.source_review_status,
            'assessment_pending':state.assessment_pending,
            'proposal_generation_status':state.proposal_generation_status if state.proposal_generation_digest == revision else 'not_requested',
            'pending_inputs': [{'id':r.id, 'reason':r.reason, 'digest':r.digest} for r in unresolved],
            'requirements': document if status == 'ready' else None}


def record_source_review(template, before, result):
    """검토를 마친 요구사항 버전을 기록하고 미검토 변경의 인계를 보류한다."""
    state = result.state
    current = digest(template.export(state))
    previous = digest(template.export(before))
    covered = (before.source_review_status == 'verified' and before.source_review_digest == previous
               and before.source_review_version == REVIEW_VERSION)
    source_issues = result.source_reviews if result.source_reviews is not None else result.blocking_reviews
    if source_issues and result.saved:
        state.source_review_status = 'failed'
        state.source_review_digest = None
    elif not source_issues and (
            (result.source_review_verified and (covered or not before.cells or result.reviewed_project))
            or (covered and result.review_carries_forward)):
        state.source_review_status = 'verified'
        state.source_review_digest = current
        state.source_review_version = REVIEW_VERSION
    elif current != previous:
        state.source_review_status = 'unreviewed'
        state.source_review_digest = None


def planning_ready(document, state, *, check_pending_inputs=True):
    """제안 생성 전 필수 정보 충족 여부를 검사한다."""
    if check_pending_inputs and any(request.status == 'pending' for request in state.input_requests):
        return False
    if state.assessment_pending or state.questions or any(q.kind == 'conflict' or not advisory_question(q) for q in state.deferred_questions) or state.pending_answer_notes or any(t.split(':',1)[0] in KEYS for t in state.deferred_required):
        return False
    if any(not requirement_sufficient(document,k) for k in ('purpose','users','features')):
        return False
    features = document['release_scope']['features']
    return bool(any(f['included_in_release'] is True for f in features)) and all(
        type(f['included_in_release']) is bool and (f['included_in_release'] is False or
        requirement_sufficient(document, 'behavior:'+f['id'])) for f in features)


def pending_batch(state):
    """현재 검토 대기 중인 제안을 찾는다."""
    return next((b for b in reversed(state.advisory_batches) if b.status=='pending'), None)


def find_proposal(state, proposal_id):
    """Find a proposal by stable ID, including already decided proposals."""
    return next((b for b in reversed(state.advisory_batches) if b.id == proposal_id), None)


def invalidate_stale(template, state):
    """요구사항이 변경된 제안을 만료 처리한다."""
    batch = pending_batch(state)
    if batch and (batch.base_digest != digest(template.export(state)) or not planning_ready(template.export(state),state,check_pending_inputs=False)):
        batch.status = 'stale'
        return True
    return False


def suggestion_items(draft):
    """제안 항목을 승인용 식별자와 연결한다."""
    items = []
    for prefix, values in (('C',draft.completion),('T',draft.technology),('E',draft.exceptions),('D',draft.delivery_details),('L',draft.exception_links),('S',draft.patches)):
        items.extend((f'{prefix}{i}',prefix,v) for i,v in enumerate(values,1))
    if draft.project_type: items.append(('P1','P',draft.project_type))
    if draft.flow: items.append(('F1','F',draft.flow))
    return items


def proposal_text(state):
    """미확정 제안과 승인 명령을 표시한다."""
    batch = pending_batch(state)
    if not batch:
        return '검토 대기 중인 제안이 없어. 최소 필수 정보가 확정되면 /propose로 제안을 생성할 수 있어.'
    lines = [f'AI 제안 {batch.id} (미확정 — 요구사항 JSON에는 아직 반영하지 않았어.)']
    for item,kind,value in suggestion_items(batch.draft):
        if kind=='C': text=f'{value.feature_id} 완료 조건: '+ ' / '.join(value.criteria)
        elif kind=='T': text=f'기술: {value.choice}'+(f' ({value.area})' if value.area else '')+(f' · {value.reason}' if value.reason else '')
        elif kind=='E': text=f'예외: {value.condition} → {value.system_response} ({", ".join(value.related_features)})'+(f' · 안내: {value.user_guidance}' if value.user_guidance else '')+(f' · 복구: {value.recovery}' if value.recovery else '')
        elif kind=='D': text=f'{value.feature_id} 전달 세부사항 · {value.choice} ({value.reason})'
        elif kind=='L': text=f'{value.condition} 예외를 {value.feature_id}에도 적용 · {value.reason}'
        elif kind=='S':
            from core.planning_logic import label, describe
            text=f'{label(state,value.target)} · {describe(value.previous_value)} → {describe(value.value)} · {value.reason}'
        else: text=('프로젝트 유형: ' if kind=='P' else '사용 흐름: ')+value
        lines.append(f'[{item}] {text}')
    lines.extend([f'전체 승인: /approve {batch.id}',f'일부 승인: /approve {batch.id} C1,T1 (실제 표시된 항목 ID 사용; 나머지는 확정하지 않음)',f'반려: /reject {batch.id}'])
    return '\n'.join(lines)


def check_draft(draft, document, state=None, template=None):

    """제안의 범위와 형식 및 기존값 충돌을 검사한다."""
    issues=[]; scope=document['release_scope']; features={f['id']:f for f in scope['features'] if f['included_in_release'] is True}
    def valid(v, limit=2000):
        """제안 문자열의 허용 길이와 형식을 검사한다."""
        return isinstance(v,str) and 0<len(v.strip())<=limit and '\x00' not in v
    def optional(v, limit=2000):
        """입력값이 비어 있거나 허용 조건을 만족하는지 확인한다."""
        return v is None or valid(v,limit)
    if len(draft.completion)>len(features) or len(draft.technology)>20 or len(draft.exceptions)>30:
        issues.append('proposal_count_limit')
    seen=set()
    for c in draft.completion:
        if c.feature_id not in features or features[c.feature_id]['done_criteria'] or c.feature_id in seen:
            issues.append('completion_invalid_or_existing_target')
        seen.add(c.feature_id)
        if not c.criteria or len(c.criteria)>20 or any(not valid(v,1000) for v in c.criteria) or len(set(c.criteria))!=len(c.criteria):
            issues.append('completion_empty_or_duplicate')
    if any(fid not in seen for fid,f in features.items() if not f['done_criteria']):
        issues.append('completion_missing_included_feature')
    tech=set()
    confirmed_tech = {identity(t['choice']) for t in scope['tech_stack'] if t['choice']}
    for t in draft.technology:
        if not valid(t.choice,150) or not optional(t.area,150) or not optional(t.reason,1000) or identity(t.choice) in tech | confirmed_tech:
            issues.append('technology_invalid_or_duplicate')
        tech.add(identity(t.choice))
    if draft.project_type is not None and (scope['project_type'] or not valid(draft.project_type,150)):
        issues.append('project_type_already_confirmed_or_invalid')
    if draft.flow is not None and (document['user_experience']['flow'] or not valid(draft.flow,5000)):
        issues.append('flow_already_confirmed_or_invalid')
    conditions={identity(e['condition']) for e in document['policies']['exceptions'] if e['condition']}
    for e in draft.exceptions:
        if (not valid(e.condition,500) or identity(e.condition) in conditions or not valid(e.system_response,1000)
                or not optional(e.user_guidance,1000) or not optional(e.recovery,1000)
                or not e.related_features or len(set(e.related_features))!=len(e.related_features)
                or any(fid not in features for fid in e.related_features)):
            issues.append('exception_invalid_duplicate_or_unknown_feature')
        conditions.add(identity(e.condition))
    if delivery_issues(draft.delivery_details, document):
        issues.append(DELIVERY_ERROR)
    links = set()
    for link in draft.exception_links:
        candidates = [e for e in document['policies']['exceptions'] if e['condition'] == link.condition]
        pair = (link.condition, link.feature_id)
        if (len(candidates) != 1 or link.feature_id not in features or pair in links
                or not valid(link.reason,1000) or link.feature_id in (candidates[0]['related_features'] or [])):
            issues.append('exception_link_unknown_duplicate_or_excluded')
        links.add(pair)
    if len(draft.exception_links) > 30:
        issues.append('proposal_count_limit')
    issues.extend(patch_issues(draft, state, template, document))
    return sorted(set(issues))


def create_proposal(processor, result, budget, trace=None, *, force=False):
    """요구사항을 변경하지 않고 제안 후보를 생성한다."""
    from planning_graph import run_model_stage, validated_model_response
    from planning_agent import ModelError
    state=result.state; document=processor.template.export(state)
    invalidate_stale(processor.template,state)
    if not planning_ready(document,state):
        if force:
            unresolved=[r.id for r in state.input_requests if r.status=='pending']
            if unresolved:
                result.reviews.append('미처리 수정 요청 '+', '.join(unresolved)
                    +' 때문에 제안을 생성하지 않았어. /pending-inputs로 확인하고 /retry-input I번호 또는 /withdraw-input I번호로 처리해 줘.')
                if trace: trace('proposal_blocked_pending_inputs',{'request_ids':unresolved})
            else:
                result.reviews.append('아직 중요한 필수 정보 또는 보류 결정이 남아 있어. 먼저 확인해 줘.')
        return
    if result.reviews:
        return
    if pending_batch(state):
        if force: result.updates.append('기존 제안을 먼저 승인하거나 반려해 줘.')
        return
    if processor.suggest is None:
        if force: result.reviews.append('제안 모델이 연결되지 않았어.')
        return
    base=digest(document)
    if not force and any(b.base_digest==base for b in state.advisory_batches): return
    issues = []
    def build_draft(payload):
        """새 응답 구조와 기존 검증용 후보 구조를 같은 저장 형식으로 맞춘다."""
        response = processor.suggest(payload)
        if isinstance(response, AdvisoryResponse):
            response = normalize_advisory_response(validated_model_response(response, AdvisoryResponse))
        response = validated_model_response(response, AdvisoryDraft)
        normalized = normalize_redundant_completion_patches(
            normalize_patch_targets(response, state, processor.template), state, trace)
        return quarantine_unverified_relation_patches(normalized, state, trace)

    review = None
    review_attempts = 0
    def check(candidate):
        """모든 제안과 확정 정책의 양립 여부를 기존 검토 호출에서 확인한다."""
        nonlocal issues, review, review_attempts
        issues = check_draft(candidate, document, state, processor.template)
        review = None
        if issues:
            return issues
        if processor.review_completion is None:
            return ['proposal_semantic_review_missing']
        if review_attempts and budget is not None:
            if budget.remaining <= 0:
                return ['proposal_review_retry_budget_exhausted']
            budget.remaining -= 1
        review_attempts += 1
        review=run_model_stage(processor.review_completion,review_payload(document,candidate,state),CompletionReview,
                               'review_proposal',trace=trace,budget=budget)
        issues = proposal_review_issues(review, candidate, state)
        if trace:
            trace('proposal_semantic_review', review.model_dump(mode='json'))
        return issues

    try:
        draft=run_model_stage(build_draft,{'requirements':document,
                              'existing_cells':{key:cell.value for key,cell in state.cells.items()},
                              'rows':{rid:row.model_dump() for rid,row in state.rows.items()},
                              'public_ids':state.public_ids,'allowed_patch_fields':sorted(PATCH_FIELDS)},AdvisoryDraft,
                              'advisory_proposal',trace=trace,budget=budget,check=check,preserve_rejected=True)
        if issues and set(issues) == {DELIVERY_ERROR} and budget.remaining > 0:
            rejected = set(review.rejected_delivery_ids) if review is not None else {
                draft.delivery_details[index].feature_id for index in delivery_issues(draft.delivery_details,document)}
            trimmed = draft.model_copy(deep=True)
            trimmed.delivery_details = [detail for detail in trimmed.delivery_details if detail.feature_id not in rejected]
            if rejected and not check_draft(trimmed,document,state,processor.template) and processor.review_completion is not None:
                budget.remaining -= 1
                review = run_model_stage(processor.review_completion,review_payload(document,trimmed,state),
                    CompletionReview,'review_trimmed_proposal',trace=trace,budget=budget,
                    check=lambda verdict:proposal_review_issues(verdict,trimmed,state))
                draft, issues = trimmed, []
                if trace:
                    trace('proposal_delivery_removed',{'feature_ids':sorted(rejected),'core_reviewed':True})
        if issues:
            raise ModelError('제안 후보가 검증을 통과하지 못했어.', kind='candidate_validation', issues=issues)
        if not suggestion_items(draft):
            state.proposal_generation_status='empty'
            state.proposal_generation_digest=base
            state.proposal_error_codes=[]
            state.advisory_batches.append(AdvisoryBatch(id=f'P{len(state.advisory_batches)+1:04d}',base_digest=base,draft=draft,status='empty'))
            if force: result.updates.append('추가로 제안할 항목이 없어.')
            return
        batch=AdvisoryBatch(id=f'P{len(state.advisory_batches)+1:04d}', base_digest=base, draft=draft.model_copy(deep=True),
                            review_digest=review_key(document, draft), delivery_updates=review.delivery_updates, reviewed_patch_ids=review.reviewed_patch_ids)
        state.advisory_batches.append(batch)
        state.proposal_generation_status='pending'
        state.proposal_generation_digest=base
        state.proposal_error_codes=[]
        if trace: trace('proposal_created',{'batch':batch.model_dump(mode='json')})
    except Exception as exc:
        state.proposal_generation_status='failed'
        state.proposal_generation_digest=base
        state.proposal_error_codes=list(getattr(exc,'issues',())) or [type(exc).__name__]
        result.add_advisory_review('AI 제안 생성 또는 검증에 실패했어. 확정된 요구사항은 그대로 유지했어. /propose로 다시 시도할 수 있어.')
        if trace: trace('proposal_failed',{'type':type(exc).__name__,'issues':list(getattr(exc,'issues',()))})


def apply_decision(processor, original, message, *, trace=None, budget=None):
    """명시적 승인 또는 반려를 원자적으로 반영한다."""
    from core.planning_logic import TurnResult, save_value
    result=TurnResult(state=original.model_copy(deep=True)); state=result.state
    command=re.fullmatch(r'\s*/(approve|reject)\s+(P\d{4,})(?:\s+([A-Z]\d+(?:,[A-Z]\d+)*))?\s*',message,re.I)
    if not command:
        result.reviews.append('제안 결정은 /approve P0001 또는 /reject P0001 형식으로 입력해 줘.')
        return result
    action,bid,selection=command.groups()
    bid = bid.upper()
    batch = find_proposal(state, bid)
    if batch is None:
        result.reviews.append(f'{bid} 제안이 존재하지 않아.')
        return result
    if batch.status == 'approved':
        result.reviews.append(f'{bid}는 이미 승인된 제안이야.')
        return result
    if batch.status == 'rejected':
        result.reviews.append(f'{bid}는 이미 반려된 제안이야. /propose로 새로운 제안을 생성해 줘.')
        return result
    if batch.status in ('stale', 'superseded'):
        result.reviews.append(f'{bid}는 만료되거나 새 제안으로 대체된 제안이야. /propose로 다시 생성해 줘.')
        return result
    if batch.status != 'pending' or pending_batch(state) is not batch:
        result.reviews.append(f'{bid}는 현재 승인 가능한 검토 대기 제안이 아니야.')
        return result
    if any(request.status == 'pending' for request in state.input_requests):
        result.reviews.append('미해결 입력이 있어. /pending-inputs로 확인하고 해결하거나 철회한 뒤 제안을 결정해 줘.')
        return result
    if invalidate_stale(processor.template,state):
        result.reviews.append('요구사항이 변경된 이전 제안이야. /propose로 새 제안을 생성해 줘.')
        return result
    if action.lower()=='reject':
        if selection:
            result.reviews.append('반려는 배치 전체에 적용돼. 선택 승인을 이용해 줘.')
            return result
        batch.status='rejected';batch.decision_text=message.strip()
        result.updates.append(f'{batch.id} 반려했어. 요구사항은 변경하지 않았어.')
        result.saved=1
        return result
    by_id={i:(k,v) for i,k,v in suggestion_items(batch.draft)}
    selected=list(by_id) if not selection else selection.upper().split(',')
    if len(selected)!=len(set(selected)) or any(i not in by_id for i in selected):
        result.reviews.append('제안 항목 ID가 잘못됐어. 아무 값도 변경하지 않았어.')
        return result
    document=processor.template.export(state)
    try:
        batch.draft=normalize_patch_targets(batch.draft,state,processor.template)
    except Exception:
        result.reviews.append('현재 제안의 대상을 유일하게 확인하지 못했어. 아무 값도 변경하지 않았어.')
        return result
    if check_draft(batch.draft,document,state,processor.template):
        result.reviews.append('현재 제안의 대상 검증에 실패했어. 아무 값도 변경하지 않았어.')
        return result
    if batch.review_digest != review_key(document, batch.draft):
        if processor.review_completion is None:
            result.reviews.append('이전 제안의 의미 검토 모델이 연결되지 않았어.')
            return result
        from planning_graph import run_model_stage
        try:
            review = run_model_stage(processor.review_completion, review_payload(document, batch.draft, state),
                                     CompletionReview, 'review_existing_proposal', trace=trace, budget=budget,
                                     check=lambda verdict:proposal_review_issues(verdict, batch.draft, state))
        except Exception:
            result.reviews.append('이전 제안이 현재 의미 검토를 통과하지 못했어. 승인 대기 상태로 유지했어.')
            return TurnResult(state=original.model_copy(deep=True), reviews=result.reviews)
        batch.review_digest = review_key(document, batch.draft)
        batch.delivery_updates = review.delivery_updates
        batch.reviewed_patch_ids = review.reviewed_patch_ids
    receipt = CompletionReview(conflicts=[], delivery_updates=batch.delivery_updates, reviewed_patch_ids=batch.reviewed_patch_ids)
    if proposal_review_issues(receipt, batch.draft, state):
        result.reviews.append('제안 통합안의 대상 또는 기존 값이 맞지 않아. 아무 값도 변경하지 않았어.')
        return result
    evidence=[Evidence(source='사용자 제안 승인',digest=sha256(message.strip().encode()).hexdigest(),start=0,end=len(message.strip()),quote=message.strip())]
    def store(path, item, value):
        """기존 확정값을 보호하며 승인된 필드를 기록한다."""
        if value is None: return
        target=Target(path=path,item=item); old=state.cells.get(target.key())
        if old and old.value not in (None,[],''): raise ValueError('이미 확정된 값 덮어쓰기 금지')
        state.cells[target.key()]=Cell(value=value,evidence=evidence)
        state.changes.append(Change(target=target,before=old.value if old else None,after=value,evidence=evidence))
    def row(path, label):
        """새 내부 행 식별자를 할당한다."""
        rid=f'R{state.next_row:04d}'
        while rid in state.rows:
            state.next_row+=1;rid=f'R{state.next_row:04d}'
        state.next_row+=1;state.rows[rid]=Row(path=path,label=label);return rid
    try:
        for item in selected:
            kind,value=by_id[item]
            if kind=='C':
                rid=next(r for r,f in state.public_ids.items() if f==value.feature_id)
                store('release_scope.features.done_criteria',rid,value.criteria)
            elif kind=='T':
                path='release_scope.tech_stack';rid=row(path,value.choice)
                for field in ('choice','area','reason'):store(f'{path}.{field}',rid,getattr(value,field))
            elif kind=='E':
                path='policies.exceptions';rid=row(path,value.condition)
                for field in ('condition','system_response','user_guidance','recovery','related_features'):store(f'{path}.{field}',rid,getattr(value,field))
            elif kind=='F': store('user_experience.flow',None,value)
            elif kind=='P': store('release_scope.project_type',None,value)
            elif kind=='D':
                rid=next(r for r,f in state.public_ids.items() if f==value.feature_id)
                target=Target(path=BEHAVIOR,item=rid)
                old=state.cells[target.key()]
                revised = next(update.value for update in batch.delivery_updates
                               if update.feature_id == value.feature_id)
                save_value(state,target,old,revised,old.evidence+evidence,keep_history=True)
                update = next(update for update in batch.delivery_updates if update.feature_id == value.feature_id)
                for patch in update.dependents:
                    previous = state.cells[patch.target.key()]
                    save_value(state,patch.target,previous,patch.value,previous.evidence+evidence,keep_history=True)
            elif kind=='S':
                old = state.cells.get(value.target.key())
                if (old.value if old else None) != value.previous_value:
                    raise ValueError('제안의 기존 값이 바뀌었어')
                save_value(state,value.target,old,value.value,(old.evidence if old else [])+evidence,keep_history=True)
                for dependent in value.dependents:
                    previous = state.cells.get(dependent.target.key())
                    if (previous.value if previous else None) != dependent.previous_value:
                        raise ValueError('종속 설명의 기존 값이 바뀌었어')
                    save_value(state,dependent.target,previous,dependent.value,(previous.evidence if previous else [])+evidence,keep_history=True)
            elif kind=='L':
                rid, = [r for r, row_data in state.rows.items() if row_data.path=='policies.exceptions' and
                        (condition := state.cells.get(Target(path='policies.exceptions.condition',item=r).key()))
                        and condition.value==value.condition]
                target=Target(path='policies.exceptions.related_features',item=rid)
                old=state.cells.get(target.key())
                previous=old.value if old and old.value else []
                save_value(state,target,old,[*previous,value.feature_id],(old.evidence if old else [])+evidence,keep_history=True)
        batch.status='approved';batch.selected=selected;batch.decision_text=message.strip()
        validate_state(processor.template,state)
    except Exception:
        return TurnResult(state=original.model_copy(deep=True),reviews=['제안 반영 검증에 실패했어. 모든 기존 값을 유지했어.'])
    result.review_carries_forward = True
    result.saved=1;result.updates.append(f'{batch.id} 승인 항목 {len(selected)}개를 요구사항 JSON에 반영했어. 미승인 항목은 확정하지 않았어.')
    return result


def revise_proposal(processor, original, revision, source, start, end, *, trace=None, budget=None, request_decisions=()):
    """Create a reviewed successor; never mutate the proposal's original draft."""
    from core.planning_logic import TurnResult, source_evidence
    from planning_graph import run_model_stage
    result = TurnResult(state=original.model_copy(deep=True))
    old = pending_batch(result.state)
    if (old is None or old.id != revision.batch_id
            or old.base_digest != digest(processor.template.export(original))):
        result.reviews.append('현재 요구사항에 맞는 미확정 제안 ID를 지정해 줘.')
        return result
    try:
        revision=revision.model_copy(deep=True)
        revision.draft=normalize_patch_targets(revision.draft,original,processor.template)
        old.draft=normalize_patch_targets(old.draft,original,processor.template)
    except Exception:
        result.reviews.append('제안 수정 대상을 유일하게 확인하지 못했어.')
        return result
    before = {key:(kind,value) for key,kind,value in suggestion_items(old.draft)}
    after = {key:(kind,value) for key,kind,value in suggestion_items(revision.draft)}
    chosen = revision.item_ids
    if (not chosen or len(chosen) != len(set(chosen)) or set(chosen) - before.keys()
            or before.keys() != after.keys() or not revision.reason.strip()
            or any(before[key] != after[key] for key in before if key not in chosen)
            or all(before[key] == after[key] for key in chosen)):
        result.reviews.append('지정한 제안 항목의 내용만 수정해 줘. 항목 추가·삭제는 새 제안으로 처리해 줘.')
        return result
    for key in chosen:
        kind,left = before[key]; _,right = after[key]
        if ((kind in ('C','D') and left.feature_id != right.feature_id)
                or (kind == 'S' and left.target != right.target)):
            result.reviews.append('제안의 수정 대상을 다른 기능이나 필드로 바꿀 수 없어.')
            return result
    document = processor.template.export(original)
    requests = {r.id:r for r in result.state.input_requests if r.status == 'pending'}
    decision_ids = [d.request_id for d in request_decisions]
    if len(decision_ids) != len(set(decision_ids)) or any(d.request_id not in requests or not d.reason.strip() for d in request_decisions):
        result.reviews.append('수정 요청의 미반영 입력 결정을 확인해 줘.')
        return result
    issues = check_draft(revision.draft, document, original, processor.template)
    if issues or processor.review_completion is None:
        result.reviews.extend(issues or ['proposal_semantic_review_missing'])
        return result
    try:
        evidence = source_evidence(source,start,end,revision.quotes)
        if not evidence:
            raise ValueError('제안 수정 요청의 원문이 필요해')
        payload = review_payload(document,revision.draft,original)
        payload.update(previous_draft=old.draft.model_dump(mode='json'),
                       revision_item_ids=chosen, revision_request=source.text[start:end],
                       pending_inputs=[r.model_dump(mode='json') for r in requests.values()],
                       request_decisions=[d.model_dump(mode='json') for d in request_decisions])
        review = run_model_stage(processor.review_completion,payload,CompletionReview,
            'review_proposal_revision',trace=trace,budget=budget,
            check=lambda value:proposal_review_issues(value,revision.draft,original))
    except Exception:
        result.reviews.append('제안 수정의 근거 또는 의미 검토를 통과하지 못했어.')
        return result
    for decision in request_decisions:
        request = requests[decision.request_id]
        request.status, request.resolved_by = decision.status, source.text
    from storage.request_state import resolve_retried_input
    resolve_retried_input(result.state,source.text)
    old.status = 'superseded'
    successor = AdvisoryBatch(id=f'P{len(result.state.advisory_batches)+1:04d}',
        base_digest=old.base_digest,draft=revision.draft.model_copy(deep=True),
        review_digest=review_key(document,revision.draft),delivery_updates=review.delivery_updates,
        reviewed_patch_ids=review.reviewed_patch_ids,revision_of=old.id,
        revision_reason=revision.reason,revision_evidence=evidence)
    result.state.advisory_batches.append(successor)
    result.saved = 1
    result.request_verified = True
    result.review_carries_forward = True
    result.updates.append(f'{old.id}의 수정안을 {successor.id}로 만들었어. 승인 전까지 요구사항 JSON에는 반영하지 않았어.')
    return result
