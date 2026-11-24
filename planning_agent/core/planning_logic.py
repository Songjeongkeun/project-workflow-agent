"""요구사항 변경과 내부 값·근거 처리를 담당한다."""

from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from functools import cached_property
from hashlib import sha256
import re

from core.planning_schema import (
    Cell, Change, DraftCorrection, Evidence, IDENTITIES, LABELS, ProjectState, Proposal, Question,
    ReviewedRecord, Row, SourceReview, Target, Template, Value, compact, identity,
    FEATURE_LINK_PATHS, SYSTEM_OWNED_PATHS, ensure_public_ids, validate_public_ids, row_matches,
)

METRIC_VALUES = {'goals.metrics.current_value': 'current', 'goals.metrics.target_value': 'target'}
NUMBER = r'[+-]?\d+(?:[.,]\d+)*(?:[ \t]*[%‰]|[건회명초분일주월년개])?'
SENTENCE_END = re.compile(r'(?<=[.?!。！？])(?:\s+|$)|\n')
FEATURES = 'release_scope.features'
FEATURE_INCLUDED = f'{FEATURES}.included_in_release'
FEATURE_PRIORITY = f'{FEATURES}.priority'
FEATURE_BEHAVIOR = f'{FEATURES}.behavior_and_value'
FEATURE_NAME = f'{FEATURES}.name'
DATA_FIELDS = 'data_rules.fields'
DATA_NAME = f'{DATA_FIELDS}.name'
FEATURE_DONE = f'{FEATURES}.done_criteria'
DATA_PURPOSE = f'{DATA_FIELDS}.purpose'
POST_LAUNCH_CHECKS = 'release_plan.post_launch_checks'
EXCEPTIONS = 'policies.exceptions'
EXCEPTION_CONDITION = f'{EXCEPTIONS}.condition'
EXCEPTION_DETAILS = {f'{EXCEPTIONS}.{field}' for field in ('system_response', 'user_guidance', 'recovery')}


MILESTONES = 'release_plan.milestones'
MILESTONE_SCHEDULE = f'{MILESTONES}.schedule'
DATE_FIELDS = {'overview.written_on', MILESTONE_SCHEDULE, 'goals.metrics.measurement_timing'}
STORAGE_MANAGEMENT = 'data_rules.storage_and_management'
ROLE_REVIEW_FIELDS = (
    {f'{EXCEPTIONS}.system_response', f'{EXCEPTIONS}.user_guidance'},
    {DATA_PURPOSE, STORAGE_MANAGEMENT},
)
PRIORITY_CODE = re.compile(r'(?<!\w)P\d+(?![\dA-Za-z])', re.IGNORECASE)


@dataclass(frozen=True)
class Source:
    text: str
    name: str | None = None
    allow_question_reply: bool = True

    @cached_property
    def digest(self) -> str:
        """digest 관련 작업을 수행한다"""
        return sha256(self.text.encode('utf-8')).hexdigest()

@dataclass
class TurnResult:
    state: ProjectState
    updates: list[str] = field(default_factory=list)
    reviews: list[str] = field(default_factory=list)
    saved: int = 0
    replay: bool = False
    retry_links: list[dict] = field(default_factory=list)
    row_aliases: dict[tuple[str, str], str] = field(default_factory=dict)
    semantic_actions: list[dict] = field(default_factory=list)
    input_rejected: bool = False
    request_verified: bool = False
    source_review_verified: bool = False
    review_carries_forward: bool = False
    reviewed_project: bool = False
    review_blocked: bool = False
    advisory_reviews: list[str] = field(default_factory=list)
    source_reviews: list[str] | None = None
    advisory_review_indices: set[int] = field(default_factory=set)
    validation_issues: list = field(default_factory=list)
    recovery_context: dict = field(default_factory=dict)

    @property
    def blocking_reviews(self):
        """reviews 관련 작업을 수행한다"""
        return [message for index,message in enumerate(self.reviews) if index not in self.advisory_review_indices]

    def add_advisory_review(self,message):
        """advisory review 관련 작업을 수행한다"""
        self.advisory_review_indices.add(len(self.reviews))
        self.reviews.append(message)
        self.advisory_reviews.append(message)

    def response(self) -> str:
        """처리 결과와 질문을 사용자용 문장으로 만든다."""
        lines = list(dict.fromkeys(self.updates))
        if self.review_blocked:
            lines.append('원문 재검토를 완료하지 못했어. 아래 검토 항목을 확인해 줘.')
        elif self.state.questions:
            q = self.state.questions[0]
            lines.append(question_text(self.state, q))
        elif any(b.status == 'pending' for b in self.state.advisory_batches):
            from proposals.proposal_flow import proposal_text
            lines.append(proposal_text(self.state))
        elif not lines:
            if self.saved:
                lines.append('요구사항을 저장했어.')
            elif self.replay:
                lines.append('이미 읽은 파일이야.')
            elif not self.reviews:
                lines.append('이번 입력에서 새로 반영할 요구사항을 찾지 못했어.')
        reviews = list(dict.fromkeys([*self.reviews,*self.advisory_reviews]))
        lines.extend(f'검토할 항목: {note}' for note in reviews[:3])
        if len(reviews) > 3:
            lines.append(f'검토할 항목: 그 외 {len(reviews) - 3}개')
        return '\n'.join(lines)


class Rejected(ValueError):
    pass


class UnresolvedTarget(Rejected):
    pass


def same(left: Value, right: Value) -> bool:
    """두 요구사항 값의 동일 여부를 확인한다."""
    if isinstance(left, str) and isinstance(right, str):
        return left == right
    if isinstance(left, list) and isinstance(right, list):
        return left == right
    return left == right


def locate(text: str, quote: str):
    """원문에서 인용 구절의 위치를 찾는다."""
    parts = quote.split()
    if not parts:
        return None
    prefix = r'(?<!\d)(?<!\d[.,])' if quote[0].isdigit() else ''
    suffix = r'(?!\d|[.,]\d)' if quote[-1].isdigit() else ''
    pattern = r'\s+'.join(re.escape(part) for part in parts)
    match = re.search(prefix + pattern + suffix, text)
    return match.span() if match else None


def source_evidence(source: Source, start: int, end: int, quotes: list[str]) -> list[Evidence]:
    """원문 인용의 위치와 출처를 기록한다."""
    result = []
    window = source.text[start:end]
    digest = source.digest
    for quote in dict.fromkeys(quotes):
        quote = quote.strip()
        location = locate(window, quote) if quote else None
        if location is None:
            raise Rejected('원문 인용 확인 필요')
        left, right = location
        result.append(Evidence(source=source.name or '자연어 입력', digest=digest,
                               start=start + left, end=start + right, quote=window[left:right]))
    return result


def named_evidence(refs: list[Evidence], name: str) -> Evidence | None:
    """이름이 포함된 원문 근거를 찾는다."""
    for ref in refs:
        location = locate(ref.quote, name)
        if location:
            start, end = location
            return ref.model_copy(update={'start': ref.start + start, 'end': ref.start + end,
                                          'quote': ref.quote[start:end]})
    return None


def review_segments(window: str) -> list[str]:
    """원문을 재검토할 문장 단위로 나눈다."""
    return [part.strip() for part in SENTENCE_END.split(window) if part.strip()]


def review_feature_labels(state: ProjectState, operations: list[Proposal], window: str,
                          candidate: ProjectState | None = None) -> dict[str, str]:
    """검토 대상 기능의 ID와 이름을 대응시킨다."""
    labels = {item: row.label for item, row in state.rows.items() if row.path == FEATURES}
    for op in operations:
        if (op.action == 'record' and op.path == f'{FEATURES}.name' and op.item
                and len(op.quotes) == 1 and locate(window, op.quotes[0].strip())):
            labels.setdefault(op.item, compact(op.quotes[0]))


    if candidate is not None:
        for item, row in candidate.rows.items():
            if row.path == FEATURES and item not in labels:
                labels[item] = row.label
    return labels


def preview_batch(state: ProjectState, template: Template, operations: list[Proposal],
                  source: Source, start: int, end: int, *, capture_links: bool = False) -> TurnResult:
    """기존 상태를 복제하여 변경안을 미리 적용한다."""
    trial = TurnResult(state=state.model_copy(deep=True))
    apply_batch(trial, template, operations, source, start, end, repair_links=capture_links)
    return trial


def review_target(op: Proposal | ReviewedRecord) -> tuple[str, str]:
    """재검토 항목의 필드와 대상을 반환한다."""
    return op.path or '', compact(op.item or '')


def correction_scope_issue(correction, operations, baseline):
    """A replacement must not implicitly erase another row or scalar field."""
    indices = correction.operation_indices
    if not correction.records or any(not 0 <= i < len(operations) for i in indices):
        return None
    originals = [operations[i] for i in indices]
    paths = {op.path for op in [*originals,*correction.records]}
    if paths in ROLE_REVIEW_FIELDS:
        return None
    def address(op):
        """address 관련 작업을 수행한다"""
        parent = (op.path or '').rpartition('.')[0]
        if parent in IDENTITIES:
            matches = row_matches(baseline.state,parent,op.item or '',baseline.row_aliases)
            return parent,next(iter(matches)) if len(matches)==1 else compact(op.item or '')
        return op.path,None
    covered = {address(rec) for rec in correction.records}
    original_addresses = {address(op) for op in originals}
    missing = original_addresses-covered
    if len(original_addresses)>1 and missing:
        return '재검토 반영 실패 · 교체 결과에서 다른 항목이 빠졌어. 각 대상을 보존하거나 삭제 작업을 별도로 명시해야 해: '+str(sorted(missing,key=str))
    return None


def coverage_issues(review, text, template, state, aliases=None, *, required=False, previous_state=None, removed_targets=()):
    """Validate the model's coverage ledger, never infer claims from keywords."""
    if not review.coverage:
        return ['coverage_missing'] if required and review_segments(text) else []
    expected = set(range(len(review_segments(text))))
    removed_targets=set(removed_targets)
    seen = set()
    issues = []
    for entry in review.coverage:
        if entry.source_ref not in expected or entry.source_ref in seen or not entry.facts:
            issues.append('coverage_invalid_source_ref_or_empty_facts')
        seen.add(entry.source_ref)
        for fact in entry.facts:
            if not fact.description.strip():
                issues.append('coverage_empty_description')
            if fact.disposition != 'represented':
                if not fact.reason.strip():
                    issues.append('coverage_missing_disposition_reason')
                if fact.disposition == 'unresolved':
                    issues.append('coverage_unresolved:'+fact.description)
                if fact.disposition=='undecided' and fact.targets:
                    pending=[*state.questions,*state.deferred_questions]
                    for target in fact.targets:
                        if target.path=='candidate_questions':
                            valid=any(q.id==target.item for q in pending)
                        else:
                            parent=target.path.rpartition('.')[0]
                            valid=(target.path in template.fields and target.path not in SYSTEM_OWNED_PATHS
                                   and (target.item is None or parent in template.rows and
                                        len(row_matches(state,parent,target.item,aliases))==1))
                        if not valid:issues.append('coverage_invalid_undecided_target')
                elif fact.targets:
                    issues.append('coverage_nonrepresented_has_targets')
                continue
            if not fact.targets:
                issues.append('coverage_missing_targets:'+fact.description)
            for target in fact.targets:
                parent = target.path.rpartition('.')[0]
                item = target.item
                if parent in template.rows:
                    matches = row_matches(state,parent,item or '',aliases)
                    if not matches and previous_state is not None:
                        matches = {rid for rid in row_matches(previous_state,parent,item or '')
                                   if Target(path=target.path,item=rid).key() in removed_targets}
                    if len(matches)!=1:
                        issues.append('coverage_unknown_target:'+target.path+'@'+str(item))
                        continue
                    item = next(iter(matches))
                elif item is not None:
                    issues.append('coverage_invalid_scalar_target:'+target.path)
                    continue
                target_key=Target(path=target.path,item=item).key()
                cell = state.cells.get(target_key)
                if target_key in removed_targets and (cell is None or cell.value in (None,[],'')):
                    continue
                if (target.path not in template.fields or cell is None or not cell.evidence
                        or cell.value is None or cell.value=='' or cell.value==[]):
                    issues.append('coverage_unrepresented_target:'+Target(path=target.path,item=item).key()+':'+fact.description)
    if seen != expected:
        issues.append('coverage_source_segments_missing:'+str(sorted(expected-seen)))
    return list(dict.fromkeys(issues))


def data_review_inventory(template, before, candidate, aliases=None, *, full=False):
    """Enumerate concrete data rows to audit; no text/keyword classification."""
    aliases=aliases or {}
    fields=list(template.rows[DATA_FIELDS])
    touched={rid for (parent,_),rid in aliases.items() if parent==DATA_FIELDS}
    inventory=[]
    for rid,row in candidate.rows.items():
        if row.path!=DATA_FIELDS:
            continue
        changed=(rid not in before.rows or any(
            before.cells.get(f'{DATA_FIELDS}.{field}@{rid}')!=candidate.cells.get(f'{DATA_FIELDS}.{field}@{rid}')
            for field in fields))
        if not full and not changed and rid not in touched:
            continue
        keys=[key for (parent,key),value in aliases.items() if parent==DATA_FIELDS and value==rid
              and row_matches(candidate,DATA_FIELDS,key,aliases)=={rid}]
        item=rid if rid in before.rows or not keys else keys[0]
        inventory.append({'item':item,'name':row.label,'fields':fields,'values':{
            field:cell.value if (cell:=candidate.cells.get(f'{DATA_FIELDS}.{field}@{rid}')) else None
            for field in fields}})
    return inventory


def data_review_issues(review, text, template, before, candidate, aliases=None, *, full=False, required=False, sparse=False, allow_deleted_rows=False):
    """Check every requested field verdict against the actual candidate and evidence."""
    audits=review.data_reviews
    if not required and not audits:
        return []
    inventory=data_review_inventory(template,before,candidate,aliases,full=full)
    expected={next(iter(row_matches(candidate,DATA_FIELDS,row['item'],aliases))) for row in inventory}
    deleted={rid for rid,row in before.rows.items() if row.path==DATA_FIELDS and rid not in candidate.rows} if allow_deleted_rows else set()
    seen=set();issues=[];segments=review_segments(text)
    fields=set(template.rows[DATA_FIELDS])
    for audit in audits:
        matches=row_matches(candidate,DATA_FIELDS,audit.item,aliases)
        if not matches and deleted:
            matches=row_matches(before,DATA_FIELDS,audit.item)&deleted
        if len(matches)!=1:
            issues.append('data_review_unknown_item:'+audit.item);continue
        rid=next(iter(matches))
        if rid in seen or rid not in expected|deleted:
            issues.append('data_review_duplicate_or_unrequested_item:'+audit.item)
        seen.add(rid);checked=set()
        for check in audit.checks:
            key=f'{DATA_FIELDS}.{check.field}@{rid}'
            if check.field not in fields or check.field in checked:
                issues.append('data_review_invalid_field:'+key)
            checked.add(check.field)
            if not check.reason.strip():
                issues.append('data_review_missing_reason:'+key)
            cell=candidate.cells.get(key)
            if rid in deleted and check.status != ('cleared' if key in before.cells else 'not_stated'):
                issues.append('data_review_deleted_row_status:'+key)
            if check.status=='uncertain':
                valid_refs=all(0<=ref<len(segments) for ref in check.source_refs)
                quotes=[*check.quotes,*(segments[ref] for ref in check.source_refs if 0<=ref<len(segments))]
                if (required or not sparse or check.field=='name' or check.value is not None
                        or (cell is not None and cell.value is not None) or not valid_refs
                        or not quotes or any(not q.strip() or locate(text,q) is None for q in quotes)):
                    issues.append('data_review_uncertain:'+key)
            elif check.status=='cleared':
                old=before.cells.get(key)
                if check.value is not None or cell is not None or old is None:
                    issues.append('data_review_invalid_clear:'+key)
                if any(not 0<=ref<len(segments) for ref in check.source_refs):
                    issues.append('data_review_invalid_source_ref:'+key);continue
                quotes=[*check.quotes,*(segments[ref] for ref in check.source_refs)]
                if not quotes or any(not q.strip() or locate(text,q) is None for q in quotes):
                    issues.append('data_review_clear_requires_current_evidence:'+key)
            elif check.status=='not_stated':
                if check.value is not None or check.quotes or check.source_refs or (cell is not None and cell.value is not None):
                    issues.append('data_review_absence_conflicts_with_value:'+key)
            else:
                value=check.value
                if check.field=='related_features' and isinstance(value,list):
                    normalized=[]
                    for reference in value:
                        matches=row_matches(candidate,FEATURES,reference,aliases)
                        if len(matches)!=1 or next(iter(matches)) not in candidate.public_ids:
                            issues.append('data_review_unknown_feature_reference:'+key)
                            continue
                        normalized.append(candidate.public_ids[next(iter(matches))])
                    value=list(dict.fromkeys(normalized))
                if (cell is None or not cell.evidence or value is None or type(value) is not type(cell.value)
                        or value!=cell.value):
                    issues.append('data_review_value_mismatch:'+key)
                if any(not 0<=ref<len(segments) for ref in check.source_refs):
                    issues.append('data_review_invalid_source_ref:'+key);continue
                quotes=[*check.quotes,*(segments[ref] for ref in check.source_refs)]
                if not quotes:
                    issues.append('data_review_missing_evidence:'+key)
                for quote in quotes:
                    existing=cell.evidence if cell else []
                    if (not quote.strip() or (locate(text,quote) is None and not any(
                            ref.start>=0 and ref.end-ref.start==len(ref.quote) and locate(ref.quote,quote)
                            for ref in existing))):
                        issues.append('data_review_invalid_quote:'+key)
        if (required or not sparse) and checked!=fields:
            issues.append('data_review_fields_missing:'+audit.item+':'+','.join(sorted(fields-checked)))
    if (required or not sparse) and not expected.issubset(seen):
        missing=[row['item'] for row in inventory if not row_matches(candidate,DATA_FIELDS,row['item'],aliases)&seen]
        issues.append('data_review_items_missing:'+','.join(missing))
    return list(dict.fromkeys(issues))


def correction_parts(correction: DraftCorrection, operations: list[Proposal]):
    """재검토 변경안을 대상별로 나눈다."""
    indices = sorted(set(correction.operation_indices))
    records = correction.records
    if len(records) < 2 or any(not 0 <= index < len(operations) for index in indices):
        return [(indices, records)]
    if {op.path for op in [*records, *(operations[index] for index in indices)]} in ROLE_REVIEW_FIELDS:
        return [(indices, records)]


    declared_rows = set()
    for op in [*records, *(operations[index] for index in indices)]:
        path, item = review_target(op)
        row_path, _, field = path.rpartition('.')
        if item and IDENTITIES.get(row_path) == field:
            declared_rows.add((row_path, item))

    def group_key(op):
        """재검토 항목을 묶을 기준 키를 반환한다."""
        path, item = review_target(op)
        row_key = path.rpartition('.')[0], item
        return row_key if row_key in declared_rows else (path, item)

    groups: dict[tuple[str, str], tuple[list[int], list[ReviewedRecord]]] = {}
    for record in records:
        groups.setdefault(group_key(record), ([], []))[1].append(record)
    for index in indices:
        key = group_key(operations[index])
        if key not in groups:

            return [(indices, records)]
        groups[key][0].append(index)
    return list(groups.values())


def link_correction_targets(correction: DraftCorrection, operations: list[Proposal]
                            ) -> list[tuple[list[int], list[ReviewedRecord]]]:
    """초안 번호를 기준으로 관련 기능의 대상을 식별한다."""
    records = correction.records
    if not records or not all(rec.path in FEATURE_LINK_PATHS for rec in records):
        return correction_parts(correction, operations)
    indices = sorted(set(correction.operation_indices))
    if any(index < 0 or index >= len(operations) for index in indices):
        return correction_parts(correction, operations)


    targets = {(rec.path.rpartition('.')[0], compact(rec.item or '')) for rec in records}
    if len(targets) != len(records) or any(not item for _, item in targets):
        return correction_parts(correction, operations)
    cited = {(operations[index].path.rpartition('.')[0], compact(operations[index].item or ''))
             for index in indices}
    if cited and not cited <= targets:
        return correction_parts(correction, operations)
    if any(operations[index].action not in ('record', 'extend') or operations[index].reply_to
           for index in indices):
        return correction_parts(correction, operations)
    if any((operations[index].path not in FEATURE_LINK_PATHS
            and operations[index].path not in {f'{row_path}.{IDENTITIES[row_path]}'
                for row_path, _ in targets if row_path in IDENTITIES}
            and operations[index].path.rpartition('.')[0] not in {row_path for row_path, _ in targets})
           for index in indices):
        return correction_parts(correction, operations)
    return [([index for index in indices if operations[index].path == rec.path
              and compact(operations[index].item or '') == compact(rec.item or '')], [rec])
            for rec in records]


def check_role_review(originals: list[Proposal], proposals: list[Proposal], window: str):
    """분류 위치가 바뀌더라도 원래 근거와 예외 대상을 보존하는지 확인한다."""
    paths = {op.path for op in [*originals, *proposals]}
    if not originals or paths not in ROLE_REVIEW_FIELDS:
        return
    if paths <= EXCEPTION_DETAILS and len({op.item for op in [*originals, *proposals]}) != 1:
        raise Rejected('재검토 대상 확인 필요 · 동일한 예외의 대응만 재분류할 수 있어')
    for original in originals:
        compatible = [op for op in proposals
                      if op.path != DATA_PURPOSE or op.item == original.item or original.path == STORAGE_MANAGEMENT]
        candidate = [quote for op in compatible for quote in op.quotes]
        for quote in original.quotes:
            if locate(window, quote) and not any(locate(ref, quote.rstrip('.。')) for ref in candidate):
                raise Rejected('재검토 반영 실패 · 재분류할 때 원래 원문 근거를 보존해야 해')


def review_record_refs(record: ReviewedRecord, segments: list[str]) -> list[int]:
    """목표의 검증된 인용 또는 문장 번호를 반환한다."""
    refs = record.source_refs
    if all(0 <= index < len(segments) for index in refs):
        return refs
    raise Rejected('재검토할 원문 문장 번호 확인 필요')


def review_proposal(record: ReviewedRecord, template: Template, source: Source, start: int, end: int,
                    segments: list[str]) -> Proposal:
    """재검토 결과를 검증 가능한 변경안으로 변환한다."""
    refs = review_record_refs(record, segments)
    quotes = record.quotes or [segments[index] for index in refs]
    if record.path not in template.fields:
        raise Rejected(f'재검토 필드 경로 확인 필요 · {short(record.path or "(없음)", 70)}')
    expected = template.fields[record.path]
    value = record.value


    if expected == 'list' and isinstance(value, str):
        if not compact(value):
            raise Rejected(f'재검토 결과 형식 확인 필요 · {short(record.path, 70)} (빈 목록 원소)')
        value = [value]
    elif expected == 'text' and isinstance(value, list) and len(value) == 1:
        value = value[0]
    elif (isinstance(value, list) and expected != 'list') or (type(value) is bool and expected != 'bool'):
        raise Rejected(f'재검토 결과 형식 확인 필요 · {short(record.path, 70)} (필요: {expected})')


    if not quotes:


        if isinstance(value, str) and locate(source.text[start:end], value):
            quotes = [value]
        elif (isinstance(value, list) and value
              and all(locate(source.text[start:end], entry) for entry in value)):
            quotes = list(value)
        else:
            raise Rejected(f'재검토 원문 근거 누락 · {short(record.path, 70)}')
    evidence = source_evidence(source, start, end, quotes)
    op = Proposal(action='record', path=record.path, item=record.item, quotes=quotes,
                  value=None if isinstance(value, str) else value, evidence_refs=record.evidence_refs)
    if isinstance(value, str):
        text = compact(value)
        if not text or expected != 'text':
            raise Rejected(f'재검토 결과 형식 확인 필요 · {short(record.path, 70)} (필요: {expected})')
        if not quoted(text, evidence):
            if record.path in DATE_FIELDS | METRIC_VALUES.keys() | {FEATURE_PRIORITY}:
                raise Rejected('수치·일정의 원문 값 확인 필요')
            original = {compact(number) for ref in evidence for number in re.findall(NUMBER, ref.quote)}
            if any(compact(number) not in original for number in re.findall(NUMBER, text)):
                raise Rejected('재검토 결과의 수치 근거 확인 필요')
        op._reviewed_text = text
    if isinstance(value, list):
        op._reviewed_list = True
    if record.path == FEATURE_INCLUDED and type(value) is bool:
        op._semantic_binding = True
    return op


def resolve_review_item(record: ReviewedRecord, state: ProjectState,
                        baseline: TurnResult, operations: list[Proposal]) -> ReviewedRecord:
    """같은 행의 임시 키, 내부 행 ID, 이름 및 공개 ID를 초안 키에 대응시킨다."""
    row_path = record.path.rpartition('.')[0]
    if row_path not in IDENTITIES:
        return record
    raw = compact(record.item or '')
    if not raw:
        return record
    matches = row_matches(baseline.state, row_path, raw, baseline.row_aliases)
    if not matches:
        return record
    if len(matches) != 1:
        raise Rejected('검토 대상이 여러 항목과 충돌해')
    row_id, = matches
    if row_id in state.rows and state.rows[row_id].path == row_path:
        return record.model_copy(update={'item': row_id})
    name_path = f'{row_path}.{IDENTITIES[row_path]}'
    matching = {compact(op.item) for op in operations
                if op.path == name_path and op.action == 'record' and op.item
                and op.reply_to is None
                and row_matches(baseline.state,row_path,op.item,baseline.row_aliases)=={row_id}}
    if raw in matching:
        return record
    if len(matching) != 1:
        raise Rejected('검토 대상에 대응하는 초안 항목이 모호해')
    return record.model_copy(update={'item': next(iter(matching))})


def apply_source_review(result: TurnResult, template: Template, operations: list[Proposal], review: SourceReview,
                        labels: dict[str, str], source: Source, start: int, end: int,
                        baseline: TurnResult, *,strict_review=False) -> list[Proposal]:
    """원문 재검토 결과를 검증해 초안에 반영한다."""
    state = result.state
    operations = [op.model_copy(deep=True) for op in operations]
    if not review.issues or strict_review:
        segments = review_segments(source.text[start:end])
        for correction in review.corrections:
            if (any(not 0 <= index < len(operations) for index in correction.operation_indices)
                    or correction_scope_issue(correction, operations, baseline)):
                continue
            for record in correction.records:
                if record.path != FEATURE_INCLUDED or type(record.value) is not bool:
                    continue
                try:
                    approved = review_proposal(record,template,source,start,end,segments)
                    for index in correction.operation_indices:
                        if not 0 <= index < len(operations):
                            continue
                        original = operations[index]
                        if (original.action == 'record' and original.reply_to is None
                                and review_target(original)==review_target(record) and original.value == record.value):
                            source_evidence(source,start,end,original.quotes)
                            original._semantic_binding = True
                            original.quotes = list(dict.fromkeys([*original.quotes,*approved.quotes]))
                except Rejected:
                    continue
        baseline = preview_batch(state,template,operations,source,start,end)
    def report_review(message,code='review_application',target=None):
        """Isolate bad receipts; valid decisions remain provisional until the full audit passes."""
        from core.planning_schema import RecoveryIssue
        result.reviews.append(str(message))
        result.validation_issues.append(RecoveryIssue(stage='source_review',code=code,target=target,detail=str(message)))
    if review.semantic_bindings:
        seen=set()
        duplicate_indices={index for index,count in Counter(b.operation_index for b in review.semantic_bindings).items() if count>1}
        segments=review_segments(source.text[start:end])
        for binding in review.semantic_bindings:
            try:
                i=binding.operation_index
                if (review.issues and not strict_review) or i in duplicate_indices or i in seen or not 0<=i<len(operations):
                    raise Rejected('의미 연결 검토의 작업 번호 확인 필요')
                seen.add(i);op=operations[i]
                if op.action!='record' or op.reply_to is not None or op.path not in FEATURE_LINK_PATHS|{FEATURE_INCLUDED} or not binding.reason.strip():
                    raise Rejected('의미 연결 검토의 대상 확인 필요')
                if any(not 0<=ref<len(segments) for ref in binding.source_refs):
                    raise Rejected('의미 연결 검토의 문장 번호 확인 필요')
                quotes=list(dict.fromkeys([*binding.quotes,*(segments[ref] for ref in binding.source_refs)]))
                if not quotes:raise Rejected('의미 연결 검토의 원문 근거 확인 필요')
                source_evidence(source,start,end,[*op.quotes,*quotes])
                op.quotes=list(dict.fromkeys([*op.quotes,*quotes]));op._semantic_binding=True
            except Rejected as exc:
                report_review('재검토 반영 실패 · '+str(exc),'binding_target',str(binding.operation_index))
        baseline=preview_batch(state,template,operations,source,start,end)
    edited: dict[int, Proposal | None] = {}
    additions: list[Proposal] = []
    protected = {change.target.key() for change in state.changes}


    stable_refs: dict[str, set[str]] = {}
    original_names: dict[str, str] = {}
    for op in operations:
        if (op.path == FEATURE_NAME and op.action == 'record' and op.item
                and op.reply_to is None and len(op.quotes) == 1
                and locate(source.text[start:end], op.quotes[0].strip())):
            item = compact(op.item)
            name = compact(op._reviewed_text or op.quotes[0])
            original_names[item] = name
            stable_refs.setdefault(identity(name), set()).add(item)
            stable_refs.setdefault(identity(item), set()).add(item)
    for row_id, row in state.rows.items():
        if row.path == FEATURES:
            stable_refs.setdefault(identity(row_id), set()).add(row_id)
            stable_refs.setdefault(identity(row.label), set()).add(row_id)
            if row_id in state.public_ids:
                stable_refs.setdefault(identity(state.public_ids[row_id]), set()).add(row_id)


    for row_id, row in baseline.state.rows.items():
        if row.path != FEATURES or row_id not in baseline.state.public_ids:
            continue
        public_id = baseline.state.public_ids[row_id]
        candidates = {item for item, name in original_names.items()
                      if identity(name) == identity(row.label)}
        if row_id in state.rows and state.rows[row_id].path == FEATURES:
            candidates.add(row_id)
        if len(candidates) == 1:
            stable_refs.setdefault(identity(public_id), set()).update(candidates)

    def raw_draft(edits, appended):
        """기존 초안에 교체 및 추가 작업을 조합한다."""
        revised = [edits.get(index, op) for index, op in enumerate(operations)]
        combined = [op for op in revised if op is not None] + appended
        renamed: set[str] = set()
        for index, replacement in edits.items():
            if replacement is None or index >= len(operations):
                continue
            original = operations[index]
            if original.path != FEATURE_NAME or replacement.path != FEATURE_NAME:
                continue
            if original.item != replacement.item:
                continue
            before = compact(original._reviewed_text or ' '.join(original.quotes))
            after = compact(replacement._reviewed_text or ' '.join(replacement.quotes))
            if identity(before) != identity(after):
                renamed.add(compact(original.item or ''))
        if not renamed:
            return combined
        normalized = []
        for op in combined:
            if (op.path not in FEATURE_LINK_PATHS or not isinstance(op.value, list)
                    or op.action not in ('record', 'replace', 'answer')):
                normalized.append(op)
                continue
            refs = []
            for ref in op.value:
                candidates = stable_refs.get(identity(ref), set()) if isinstance(ref, str) else set()
                if len(candidates) == 1 and next(iter(candidates)) in renamed:
                    refs.append(next(iter(candidates)))
                else:
                    refs.append(ref)
            if refs != op.value:
                op = op.model_copy(update={'value': refs})
            normalized.append(op)
        return normalized

    def conflicts(turn):
        """미리 적용한 결과의 충돌을 확인한다."""
        return {(q.target.path, label(turn.state, q.target)) for q in turn.state.questions if q.kind == 'conflict'}

    def accept(edits, appended):
        """검토 변경안을 미리 실행해 적용 가능 여부를 판단한다."""
        nonlocal edited, additions, baseline
        trial = preview_batch(state, template, raw_draft(edits, appended), source, start, end)
        if new_reviews := Counter(trial.reviews) - Counter(baseline.reviews):
            raise Rejected(f'재검토 반영 실패 · {next(iter(new_reviews))}')
        if new_conflicts := conflicts(trial) - conflicts(baseline):
            _, field_name = min(new_conflicts)
            raise Rejected(f'재검토 값 충돌 · {field_name}')
        if (trial.state.changes != baseline.state.changes
                or any(trial.state.cells.get(key) != baseline.state.cells.get(key) for key in protected)):
            raise Rejected('사용자가 확정한 값의 변경 확인 필요')
        edited, additions, baseline = edits, appended, trial


    original_feature_names: dict[str, set[str]] = {}
    declared_feature_items = {compact(op.item) for op in operations
                              if op.path == FEATURE_NAME and op.item
                              and op.action == 'record' and op.reply_to is None}
    for item, label_name in labels.items():
        if (item in declared_feature_items or
                (item in state.rows and state.rows[item].path == FEATURES)):
            original_feature_names.setdefault(identity(label_name), set()).add(item)

    occupied = {identity(labels[op.item]) for op in operations
                if op.path == FEATURE_BEHAVIOR and op.item in labels and op.action != 'question'}
    occupied.update(identity(name) for item, name in labels.items()
                    if f'{FEATURE_BEHAVIOR}@{item}' in state.cells)


    normalized_corrections = []
    for correction in review.corrections:
        if issue := correction_scope_issue(correction,operations,baseline):
            report_review(issue)
            continue
        normalized_records = []
        invalid_target = False
        for record in correction.records:
            if record.path.rpartition('.')[0] in IDENTITIES:
                try:
                    record = resolve_review_item(record, state, baseline, operations)
                except Rejected as exc:
                    message = f'{LABELS[record.path]} · {exc}'
                    report_review(message)
                    invalid_target = True
                    continue
            normalized_records.append(record)
        if invalid_target:
            continue
        indices=list(correction.operation_indices)
        if not indices and strict_review:
            for record in normalized_records:
                if record.path==f"{record.path.rpartition('.')[0]}.{IDENTITIES.get(record.path.rpartition('.')[0],'')}":continue
                matches=[i for i,op in enumerate(operations) if op.action in ('record','extend') and op.reply_to is None
                         and review_target(op)==review_target(record)]
                if len(matches)==1:
                    index=matches[0]
                    parent=record.path.rpartition('.')[0]
                    ids=row_matches(state,parent,record.item or '') if parent in IDENTITIES else set()
                    key=Target(path=record.path,item=next(iter(ids)) if len(ids)==1 else record.item).key()
                    if key not in state.cells:indices.append(index)
        normalized_corrections.append(correction.model_copy(update={'records':normalized_records,'operation_indices':indices}))
    review = review.model_copy(update={'corrections': normalized_corrections})

    for change in review.feature_changes:


        raw_key = identity(labels.get(change.item, change.item))
        aliases_for_name = {item for item, label_name in labels.items()
                            if identity(label_name) == raw_key}
        distinct_labels = {identity(label_name) for label_name in labels.values()
                           if identity(label_name) == raw_key}
        if not aliases_for_name or len(distinct_labels) != 1:
            report_review(f'{short(change.item, 40)} · 기능 동작 재검토 대상 확인 필요')
            continue


        existing_matches = [item for item in aliases_for_name
                            if item in state.rows and state.rows[item].path == FEATURES]
        if len(existing_matches) > 1:
            report_review(f'{short(change.item, 40)} · 기능 동작 재검토 대상 확인 필요')
            continue
        name = labels[sorted(aliases_for_name)[0]]
        key = identity(name)
        index = change.operation_index
        matching = [i for i, op in enumerate(operations)
                    if i not in edited and op.path == FEATURE_BEHAVIOR
                    and op.item in aliases_for_name and op.action in ('record', 'extend')
                    and op.reply_to is None]
        if index is None and key in occupied:
            if len(matching) != 1 or not change.quotes:
                report_review(f'{short(name)} · 기능 동작 재검토 대상 확인 필요')
                continue
            index = matching[0]
        if index is None:
            if not change.quotes:
                continue
            if len(matching) > 1:
                report_review(f'{short(name)} · 기능 동작 재검토 대상 확인 필요')
                continue


            real_item = next((item for item in aliases_for_name if item in state.rows), None)
            original = Proposal(action='record', path=FEATURE_BEHAVIOR, item=real_item or name)
        else:
            if index not in matching:
                report_review(f'{short(name)} · 기능 동작 재검토 대상 확인 필요')
                continue
            original = operations[index]
        if not change.quotes:

            if index is None:
                report_review(f'{short(name)} · 기능 동작 재검토 대상 확인 필요')
                continue
            try:
                accept({**edited, index: None}, additions)
            except Rejected as exc:
                report_review(str(exc))
                continue
            report_review(f'{short(name)} · 기능 동작 근거 연결 확인 필요')
            continue
        try:
            source_evidence(source, start, end, change.quotes)
        except Rejected:
            report_review(f'{short(name)} · 기능 동작 재검토 인용 확인 필요')
            continue
        proposal = original.model_copy(update={'quotes': change.quotes})
        try:
            if index is None:
                accept(edited, additions + [proposal])
                occupied.add(key)
            else:
                accept({**edited, index: proposal}, additions)
        except Rejected as exc:
            report_review(str(exc))

    conditions = {identity(row.label) for row in state.rows.values() if row.path == EXCEPTIONS}
    used_items = set(state.rows) | {compact(op.item) for op in operations if op.item}
    draft_conditions: dict[str, set[str]] = {}
    window = source.text[start:end]
    for op in operations:
        if (op.path == EXCEPTION_CONDITION and op.action == 'record' and op.item and len(op.quotes) == 1
                and locate(window, op.quotes[0].strip())):
            draft_conditions.setdefault(identity(op.quotes[0]), set()).add(compact(op.item))
    for exception in review.exceptions:
        exception_edits = dict(edited)
        values = {name: quote.strip() for name, quote in exception.model_dump().items() if quote and quote.strip()}
        if 'condition' not in values or len(values) < 2:
            report_review('예외 조건과 대응 근거 확인 필요')
            continue
        try:
            source_evidence(source, start, end, list(values.values()))
        except Rejected:
            report_review('예외 처리 재검토 인용 확인 필요')
            continue
        key = identity(exception.condition)
        if key in conditions:
            continue
        items = draft_conditions.get(key)
        if items:
            if len(items) != 1:
                continue
            item, = items
            if item in state.rows:
                continue
            details = {index: op for index, op in enumerate(operations)
                       if op.path in EXCEPTION_DETAILS
                       and (compact(op.item or '') == item or identity(op.item or '') == key)}
            if any(op.action not in ('record', 'extend') or op.reply_to is not None for op in details.values()):
                continue
            if len(details) != len({op.path for op in details.values()}):
                continue

            verified_quotes = [quote for op in details.values() for quote in map(str.strip, op.quotes)
                               if locate(window, quote)]
            combined = ' '.join(values.values())
            if not all(locate(combined, quote) for quote in verified_quotes):
                continue
            exception_edits.update(dict.fromkeys(details))
            del values['condition']
        else:
            item = f'__review_exception_{len(conditions)}'
            while item in used_items:
                item += '_'
            used_items.add(item)
        exception_records = [
            Proposal(action='record', path=f'{EXCEPTIONS}.{name}', item=item, quotes=[quote])
            for name, quote in values.items()
        ]
        try:
            accept(exception_edits, additions + exception_records)
        except Rejected as exc:
            report_review(str(exc))
        else:
            conditions.add(key)

    segments = review_segments(window)
    groups = (group for correction in review.corrections for group in link_correction_targets(correction, operations))
    for indices, records in groups:
        try:


            if not indices and len(records) == 1 and records[0].path == POST_LAUNCH_CHECKS:
                current = baseline.state.cells.get(POST_LAUNCH_CHECKS)
                candidate = records[0].value
                if (current and isinstance(current.value, list) and isinstance(candidate, list)
                        and candidate and all(any(same(v, old) for old in current.value)
                                              for v in candidate)):
                    continue
            if any(not 0 <= index < len(operations) or index in edited
                   or operations[index].action not in ('record', 'extend')
                   or operations[index].reply_to is not None for index in indices):
                raise Rejected('재검토할 초안 항목 확인 필요')
            proposals = []
            for record in records:
                op = review_proposal(record, template, source, start, end, segments)
                if strict_review and op.path in FEATURE_LINK_PATHS and any(
                        operations[index].path==op.path and operations[index].value==op.value
                        and review_target(operations[index])==review_target(op) for index in indices):
                    op._semantic_binding=True
                if op.path in FEATURE_LINK_PATHS and isinstance(op.value, list):


                    rewritten = []
                    for reference in op.value:


                        matches = stable_refs.get(identity(reference), set())
                        rewritten.append(next(iter(matches)) if len(matches) == 1 else reference)
                    if rewritten != op.value:
                        op = op.model_copy(update={'value': rewritten})
                proposals.append(op)
            for index in indices:
                original=operations[index]
                parent=(original.path or '').rpartition('.')[0]
                if (parent in IDENTITIES and original.path==f'{parent}.{IDENTITIES[parent]}'
                        and original.action=='record' and original.reply_to is None and proposals
                        and all(p.path.rpartition('.')[0]==parent and compact(p.item or '')==compact(original.item or '')
                                for p in proposals)
                        and not any(p.path==original.path for p in proposals)):
                    source_evidence(source,start,end,original.quotes)
                    proposals.insert(0,original.model_copy(deep=True))
            check_role_review([operations[index] for index in indices], proposals, window)

            replaced = {}
            candidate_edits = dict(edited)
            for index in indices:
                key = review_target(operations[index])
                replaced.setdefault(key, []).append(index)
                candidate_edits[index] = None
            candidate_additions = list(additions)
            counts = Counter(review_target(op) for op in proposals)
            for op in proposals:
                key = review_target(op)
                same_target = replaced.get(key, [])
                if same_target and counts[key] == 1:
                    approved = [operations[index] for index in same_target
                                if operations[index]._semantic_binding and operations[index].value == op.value]
                    if approved:
                        op._semantic_binding = True
                        op.quotes = list(dict.fromkeys([*op.quotes,*(quote for original in approved for quote in original.quotes)]))
                    if all(operations[index].action == 'extend' for index in same_target):
                        op = op.model_copy(update={'action': 'extend'})
                    candidate_edits[same_target[0]] = op
                else:
                    candidate_additions.append(op)
            accept(candidate_edits, candidate_additions)
        except Rejected as exc:
            report_review(str(exc))


            if any(record.path in FEATURE_LINK_PATHS for record in records):
                result.retry_links.append({
                    'records': [record.model_dump(mode='json') for record in records
                                if record.path in FEATURE_LINK_PATHS],
                    'error': str(exc), 'window_start': start, 'window_end': end,
                })


    return raw_draft(edited, additions)


PERMISSION_PATH = 'policies.eligibility_and_permissions'


def priority_code(value: Value) -> Value:
    """우선순위 값을 표준 코드로 정리한다."""
    codes = {token.upper() for token in PRIORITY_CODE.findall(value)} if isinstance(value, str) else set()
    return codes.pop() if len(codes) == 1 else value


def quoted(value: str, evidence: list[Evidence]) -> bool:
    """값을 뒷받침하는 원문 인용이 있는지 확인한다."""
    value = compact(value)
    return bool(value) and any(locate(compact(ref.quote), value) is not None for ref in evidence)


def describe(value: Value) -> str:
    """요구사항 값을 표시용 문자열로 변환한다."""
    if isinstance(value, bool):
        return '예' if value else '아니요'
    if isinstance(value, list):
        return ', '.join(value) or '없음'
    return value or '없음'


def short(text: str, limit: int = 86) -> str:
    """긴 문장을 표시용 길이로 제한한다."""
    text = compact(text)
    return text if len(text) <= limit else text[:limit - 1] + '…'


def label(state: ProjectState, target: Target) -> str:
    """저장 대상의 표시 이름을 생성한다."""
    field_name = LABELS[target.path]
    if target.item:
        row = state.rows[target.item]
        return short(f'{row.label} · {field_name}')
    return field_name


def question_text(state: ProjectState, question: Question) -> str:
    """질문의 사용자 표시 문장을 구성한다."""
    if question.kind == 'conflict':
        title = label(state, question.target)
        choices = ' / '.join(f'{i}. {short(describe(option.value), 58)}'
                             for i, option in enumerate(question.options, 1))
        return f'{title}에 어떤 값을 사용할까요? ({choices})'
    if question.text:
        visible = re.sub(r'^\[필수확인:[^\]]+\]\s*', '', question.text)
        return visible
    return f'{label(state, question.target)}을 어떻게 정할까요?'


def context(state: ProjectState) -> dict:
    """모델에 전달할 기존 요구사항과 질문을 구성한다."""
    fields = {}
    rows = {item: {**row.model_dump(), 'values': {}} for item, row in state.rows.items()}
    for key, cell in state.cells.items():
        path, separator, item = key.partition('@')
        if separator:
            rows[item]['values'][path.rsplit('.', 1)[1]] = cell.value
        else:
            fields[path] = cell.value
    from storage.evidence_store import evidence_catalog
    from proposals.proposal_flow import suggestion_items
    return {
        'evidence_catalog': evidence_catalog(state),
        'approved_proposals': [dict(id=batch.id,decision_text=batch.decision_text,
            items={key:(value.model_dump(mode='json') if hasattr(value,'model_dump') else value)
                   for key,kind,value in suggestion_items(batch.draft) if key in batch.selected})
            for batch in state.advisory_batches if batch.status == 'approved'],
        'existing': {'rows': rows, 'fields': fields},
        'questions': [
            {'id': q.id, 'kind': q.kind, 'target': q.target.model_dump() if q.target else None,
             'answer_hint': hint.model_dump() if (hint := answer_hint(state, q)) else None,
             'text': question_text(state, q),
             'reason': state.dynamic_question_reasons.get(q.id),
             'options': [cell.value for cell in q.options],
             'original_request': short(' '.join(e.quote for e in q.evidence), 600) if q.kind == 'clarify' else None}
            for q in state.questions
        ],
        'active_question': state.questions[0].id if state.questions else None,
        'focus': state.focus.model_dump() if state.focus else None,
    }


def canonical_operation(op: Proposal, template: Template) -> Proposal:
    """행 삭제 요청의 경로를 이름 필드로 정규화한다."""
    if op.action == 'remove_item' and op.path in template.rows:
        return op.model_copy(update={'path': f'{op.path}.{IDENTITIES[op.path]}'})
    return op


def resolve_target(state: ProjectState, template: Template, op: Proposal,
                   evidence: list[Evidence], aliases: dict[tuple[str, str], str]) -> tuple[Target, Row | None]:
    """변경안이 가리키는 필드와 행을 식별한다."""
    if op.path is None and op.action in ('replace', 'clear') and op.item is None and state.focus:
        op = op.model_copy(update={'path': state.focus.path, 'item': state.focus.item})
    if op.path not in template.fields:
        raise UnresolvedTarget('요구사항 필드 확인 필요')
    row_path = op.path.rsplit('.', 1)[0]
    if row_path not in template.rows:
        if op.item is not None:
            raise Rejected('수정 대상 확인 필요')
        return Target(path=op.path), None

    name = compact(op.item or '')
    key = (row_path, name)
    matches = row_matches(state, row_path, name, aliases)
    if len(matches) > 1:
        raise Rejected('수정 대상이 여러 항목과 일치해')
    if matches:
        rid = next(iter(matches))
        if (key in aliases and op.action == 'record' and op.path == f'{row_path}.{IDENTITIES[row_path]}'
                and len(evidence) == 1 and identity(op._reviewed_text or evidence[0].quote) != identity(state.rows[rid].label)):
            raise Rejected('같은 임시 ID에 다른 항목 이름을 지정했어')
        return Target(path=op.path, item=rid), None
    if name in state.rows or re.fullmatch(r'(?:FEAT|SCR)-[0-9]+|R[0-9]+', name, re.I):
        raise Rejected('존재하지 않거나 종류가 다른 ID야')

    declares_row = op.action == 'record' and op.path == f'{row_path}.{IDENTITIES[row_path]}'
    if declares_row and (not name or op.value is not None or (op._reviewed_text is None and len(evidence) != 1)):
        raise Rejected('항목 이름은 원문 구절 하나로 지정해 줘')
    row_name = (op._reviewed_text or compact(evidence[0].quote)) if declares_row else name
    matches = [item for item, row in state.rows.items()
               if row.path == row_path and row_name and identity(row.label) == identity(row_name)]
    if declares_row and not matches and op._reviewed_text is None:
        matches = [item for item, row in state.rows.items() if row.path == row_path
                   and (cell := state.cells.get(Target(path=op.path, item=item).key()))
                   and cell.evidence == evidence]
    if len(matches) == 1:
        if declares_row:
            aliases[key] = matches[0]
        return Target(path=op.path, item=matches[0]), None
    if matches or not declares_row:
        raise UnresolvedTarget('항목 이름 또는 수정 대상 확인 필요')
    while f'R{state.next_row:04d}' in state.rows:
        state.next_row += 1
    row = Row(path=row_path, label=row_name)
    return Target(path=op.path, item=f'R{state.next_row:04d}'), row


def checked_value(op: Proposal, expected: str, old: Cell | None, evidence: list[Evidence]) -> Value:
    """자료형과 원문 근거를 검사해 저장값을 만든다."""
    if op._reviewed_text is not None and expected == 'text':
        return op._reviewed_text
    value = op.value
    if expected == 'bool' and type(value) is bool:
        return value
    if value is None:
        excerpts = list(dict.fromkeys(compact(ref.quote) for ref in evidence))
        if expected == 'text':
            return '\n'.join(excerpts)
        if expected == 'list':
            return excerpts
    if expected == 'list' and isinstance(value, list):
        if op._reviewed_list:
            return list(dict.fromkeys(value))
        retained = old.value if old and isinstance(old.value, list) and op.action in ('replace', 'answer') else []
        if all(quoted(v, evidence) or any(same(v, member) for member in retained) for v in value):
            return list(dict.fromkeys(compact(v) for v in value))
    raise Rejected('원문에 있는 값 확인 필요')


def question_hint(state: ProjectState, template: Template, op: Proposal, evidence: list[Evidence],
                  aliases: dict[tuple[str, str], str]) -> Target | None:
    """질문의 잠재적 저장 대상을 확인한다."""
    if op.path not in template.fields:
        return None
    candidate = op.model_copy(update={'action': 'question'})
    try:
        return resolve_target(state, template, candidate, evidence, aliases)[0]
    except Rejected:
        name = compact(op.item or '')
        row_path = op.path.rsplit('.', 1)[0]
        if row_path in template.rows and name not in state.rows and named_evidence(evidence, name):
            return Target(path=op.path, item=name)
        return Target(path=op.path)


def queue(state: ProjectState, kind, target=None, text=None, evidence=None, hint=None) -> Question:
    """새 질문을 대기 목록에 등록한다."""
    for q in state.questions:
        if q.target == target and (kind == 'conflict' or (q.text == text and q.answer_hint == hint)):
            if kind == 'conflict':
                q.kind = kind
            return q
    q = Question(id=f'Q{state.next_question:04d}', kind=kind, target=target, text=text, answer_hint=hint,
                 evidence=evidence or [])
    state.next_question += 1
    state.questions.append(q)
    return q


def drop_question(state: ProjectState, target: Target, reply: Question | None):
    """해결된 질문을 대기 목록에서 제거한다."""
    if reply is not None:
        state.questions = [q for q in state.questions if q.id != reply.id]




def save_value(state: ProjectState, target: Target, previous: Cell | None, value: Value,
               evidence: list[Evidence], keep_history: bool) -> bool:
    """검증된 요구사항 값과 변경 이력을 저장한다."""
    if previous is not None and same(previous.value, value):
        return False
    if previous is None and value is None:
        return False
    if value is None:
        state.cells.pop(target.key(), None)
    else:
        state.cells[target.key()] = Cell(value=value, evidence=evidence)
        if target.item:
            row = state.rows[target.item]
            if isinstance(value, str) and target.path == f'{row.path}.{IDENTITIES[row.path]}':
                row.label = value
    if keep_history:
        state.changes.append(Change(target=target, before=previous.value if previous else None,
                                    after=value, evidence=evidence))
    return True


def choose(op: Proposal, reply: Question) -> Cell | None:
    """질문 답변에 대응하는 기존 선택지를 가져온다."""
    if op.choice is None:
        return None
    if op.value is not None or not 1 <= op.choice <= len(reply.options):
        raise Rejected('선택한 값을 확인해 줘')
    return reply.options[op.choice - 1]


def update_exclusion(state: ProjectState, old_name: str, new_name: str | None,
                     evidence: list[Evidence]):
    """기능명 변경에 맞춰 제외 범위를 정리한다."""
    target = Target(path='release_scope.out_of_scope')
    previous = state.cells.get(target.key())
    values = previous.value if previous and isinstance(previous.value, list) else []
    remaining = [name for name in values if identity(name) != identity(old_name)]
    if new_name and not any(identity(name) == identity(new_name) for name in remaining):
        remaining.append(new_name)
    if previous or remaining:
        sources = (previous.evidence if previous else []) + evidence
        save_value(state, target, previous, remaining, sources, keep_history=True)


def remove_item(state: ProjectState, target: Target, evidence: list[Evidence]):
    """대상 행과 관련 데이터를 제거한다."""
    if target.item is None:
        raise Rejected('삭제할 항목 확인 필요')
    row = state.rows[target.item]
    for key, cell in list(state.cells.items()):
        if key.endswith(f'@{target.item}'):
            path = key.split('@', 1)[0]
            state.changes.append(Change(target=Target(path=path, item=target.item),
                                        before=cell.value, after=None, evidence=evidence))
            del state.cells[key]
    del state.rows[target.item]
    public_id = state.public_ids.pop(target.item, None)
    if public_id and public_id not in state.retired_public_ids:
        state.retired_public_ids.append(public_id)
    if public_id and row.path == 'release_scope.features':

        for key, cell in list(state.cells.items()):
            if key.partition('@')[0] not in FEATURE_LINK_PATHS or not isinstance(cell.value, list):
                continue
            if public_id in cell.value:
                remaining = [ref for ref in cell.value if ref != public_id]
                state.changes.append(Change(target=Target(path=key.partition('@')[0], item=key.partition('@')[2]),
                                            before=cell.value, after=remaining, evidence=evidence))
                if remaining:
                    state.cells[key] = Cell(value=remaining, evidence=cell.evidence + evidence)
                else:
                    del state.cells[key]
    if row.path == 'release_scope.features':
        update_exclusion(state, row.label, None, evidence)
    state.questions = [q for q in state.questions
                       if all(address is None or address.item != target.item for address in (q.target, q.answer_hint))]
    if state.focus and state.focus.item == target.item:
        state.focus = None
    return f'{short(row.label)} 항목을 삭제했어.'


def check_explicit_row(state: ProjectState, target: Target, evidence: list[Evidence]):
    """명시된 ID가 실제 수정 대상과 다른지 확인한다."""
    row = state.rows.get(target.item or '')
    if row is None:
        return
    prefix = {'release_scope.features': 'FEAT', 'user_experience.screens': 'SCR'}.get(row.path)
    if not prefix:
        return
    references = {ref.upper() for proof in evidence for ref in
                  re.findall(rf'(?<![A-Za-z0-9]){prefix}-[0-9]+(?![A-Za-z0-9])', proof.quote, re.I)}
    known = state.public_ids.get(target.item)
    if references and known not in references:
        raise Rejected('원문에 지정된 ID와 수정 대상이 일치하지 않아')


def check_edit_scope(state: ProjectState, target: Target, op: Proposal, old: Cell | None,
                     evidence: list[Evidence], text: str, reply: Question | None):
    """삭제나 기존 값의 교체에 필요한 명시적인 변경 근거를 검사한다."""
    if op.action not in ('replace', 'clear', 'remove_item'):
        return
    check_explicit_row(state, target, evidence)
    row = state.rows.get(target.item or '')
    if row is not None and reply is None:
        prefix = {'release_scope.features':'FEAT', 'user_experience.screens':'SCR'}.get(row.path)
        if prefix:
            references = {value.upper() for value in re.findall(
                rf'(?<![A-Za-z0-9]){prefix}-[0-9]+(?![A-Za-z0-9])', text, re.I)}
            if references and state.public_ids.get(target.item) not in references:
                raise Rejected('사용자가 지정한 ID의 범위를 벗어났어')


def explicit_list_replacement(old, new, evidence, text):
    """기존 목록 원소의 변경 구절과 새 값이 현재 수정 지시에 있는지 확인한다."""
    numbers = {compact(number) for part in [old,*[ref.quote for ref in evidence]] for number in re.findall(NUMBER,part)}
    return (old != new and any(ref.quote in text for ref in evidence)
            and all(compact(number) in numbers for number in re.findall(NUMBER,new)))


def replace_list_fragment(op, previous, evidence, text):
    """목록의 지정된 기존 구절만 교체하고 다른 원소를 유지한다."""
    old_part = (op.previous_text or '').strip()
    old_list = previous.value
    if not old_part or sum(item.count(old_part) for item in old_list)!=1:
        raise Rejected('목록 부분 변경의 기존 구절을 유일하게 확인할 수 없어')
    index = next(i for i,item in enumerate(old_list) if old_part in item)
    old = old_list[index]
    position = old.index(old_part)
    if locate(old,old_part) != (position,position+len(old_part)):
        raise Rejected('목록 부분 변경의 기존 수치 경계가 일치하지 않아')
    prefix,suffix = old.split(old_part,1)
    proposed = op.value
    candidates = []
    if isinstance(proposed,list):
        if len(proposed)==len(old_list) and proposed[:index]==old_list[:index] and proposed[index+1:]==old_list[index+1:]:
            candidates.append(proposed[index])
        if len(proposed)==1:
            candidates.append(prefix+proposed[0]+suffix)
            if proposed[0].startswith(prefix) and proposed[0].endswith(suffix):
                candidates.append(proposed[0])
    elif proposed is None and len(evidence)==1:
        candidates.append(prefix+compact(evidence[0].quote)+suffix)
    for new in dict.fromkeys(candidates):
        if not new.startswith(prefix) or not new.endswith(suffix) or len(new)<len(prefix)+len(suffix):
            continue
        if new == old or new in old_list[:index]+old_list[index+1:]:
            continue
        if explicit_list_replacement(old,new,evidence,text):
            return old_list[:index]+[new]+old_list[index+1:]
    raise Rejected('목록 부분 변경의 이전 값과 새 값 또는 변경 방향을 확인할 수 없어')


def replace_text_fragment(previous: str, fragment: str, proposed: str) -> str:
    """Unambiguous text edit: accept either a new fragment or a full candidate.

    Never paste a full-field replacement into a substring location: it would
    duplicate the untouched prefix/suffix. An explicit full candidate must
    contain the exact unaffected prefix and suffix exactly once in position.
    The caller independently verifies source evidence, numeric changes and
    semantic consistency. No best-effort matching or fuzzy rewriting.
    """
    if not fragment or previous.count(fragment) != 1 or not isinstance(proposed, str):
        raise Rejected('수정할 원래 구절 확인 필요')
    position = previous.index(fragment)
    if locate(previous, fragment) != (position, position + len(fragment)):
        raise Rejected('수정할 원래 수치의 경계가 일치하지 않아')
    prefix = previous[:position]
    suffix = previous[position + len(fragment):]
    full_candidate = bool(prefix or suffix) and proposed.startswith(prefix) and proposed.endswith(suffix)
    if full_candidate:
        replacement = proposed[len(prefix):len(proposed)-len(suffix) if suffix else len(proposed)]
        if suffix and replacement.endswith(suffix):
            raise Rejected('부분 교체의 기존 나머지 문장 중복')
        if prefix and replacement.startswith(prefix):
            raise Rejected('부분 교체의 기존 앞 문장 중복')
        return proposed
    if suffix and suffix.strip() and suffix.strip() in proposed:
        raise Rejected('부분 교체에 전체 필드 값이 혼합됐어')
    if prefix and prefix.strip() and prefix.strip() in proposed:
        raise Rejected('부분 교체에 전체 필드 값이 혼합됐어')
    return prefix + proposed + suffix


def apply_operation(result: TurnResult, template: Template, op: Proposal,
                    evidence: list[Evidence], aliases: dict[tuple[str, str], str],
                    answer_text: str = '') -> bool:
    """변경안 하나를 검증하고 상태에 반영한다."""
    state = result.state
    reply = next((q for q in state.questions if q.id == op.reply_to), None)
    if op.reply_to is not None and reply is None:
        raise Rejected('답변할 질문 확인 필요')
    answer_header = re.match(r'^\s*' + re.escape(reply.id) + r'\s*[:：]\s*', answer_text, re.I) if reply else None
    if answer_header and op.action not in ('question', 'clarify'):
        answer_digest = sha256(answer_text.encode('utf-8')).hexdigest()
        clipped = []
        for ref in evidence:
            if ref.start < answer_header.end() and ref.digest == answer_digest:
                if ref.end <= answer_header.end():
                    continue
                ref = ref.model_copy(update={'start': answer_header.end(), 'quote': answer_text[answer_header.end():ref.end]})
            clipped.append(ref)
        if not clipped and evidence:
            raise Rejected('질문 번호 뒤의 답변 원문을 확인해 줘')
        evidence = clipped
        if op._reviewed_text:
            header = re.match(r'^\s*' + re.escape(reply.id) + r'\s*[:：]\s*', op._reviewed_text, re.I)
            if header:
                cleaned = op._reviewed_text[header.end():].strip()
                if not cleaned or not any(locate(ref.quote, cleaned) for ref in evidence):
                    raise Rejected('질문 번호를 제외한 답변 원문을 확인해 줘')
                op = op.model_copy()
                op._reviewed_text = cleaned
    if op.action == 'clarify':
        if not evidence or not op.question or not op.question.strip():
            raise Rejected('확인할 질문의 근거가 필요해')
        text = short(op.question, 135)
        hint = question_hint(state, template, op, evidence, aliases)
        if reply is not None:
            known = reply.target or answer_hint(state, reply)
            if known and hint and (known.path != hint.path or (known.item and hint.item not in (None, known.item))):
                raise Rejected('질문과 답변의 저장 대상 확인 필요')
            if reply.target is None:
                reply.answer_hint = known if known and known.item else hint or known
            reply.text = text
            reply.evidence.extend(ref for ref in evidence if ref not in reply.evidence)
        else:
            queue(state, 'clarify', text=text, evidence=evidence, hint=hint)
        return False

    if op.path in SYSTEM_OWNED_PATHS:
        raise Rejected('시스템이 관리하는 필드이므로 직접 변경할 수 없어')
    if op.path in FEATURE_LINK_PATHS:

        raise Rejected('관련 기능 연결 처리 순서 확인 필요')
    if op.action == 'answer' and reply is None:
        raise Rejected('답변할 질문 확인 필요')
    row_proof = None
    if reply is not None:
        if op.action == 'question':
            raise Rejected('질문과 답변 대상 확인 필요')
        target, new_row, row_proof = resolve_answer(state, template, reply, op, evidence, aliases, answer_text)
    else:
        try:
            target, new_row = resolve_target(state, template, op, evidence, aliases)
        except UnresolvedTarget:
            if op.action != 'question' or not evidence or not (op.question or '').strip():
                raise
            hint = question_hint(state, template, op, evidence, aliases)
            queue(state, 'clarify', text=short(op.question, 135), evidence=evidence, hint=hint)
            return False

    if target.path in SYSTEM_OWNED_PATHS or target.path in FEATURE_LINK_PATHS:
        raise Rejected('직접 변경할 수 없는 필드야')
    previous = state.cells.get(target.key())
    if op.action == 'question':
        if not op.question or not op.question.strip():
            raise Rejected('질문 내용 확인 필요')
        if previous is None:
            queue(state, 'missing', target, short(op.question, 135), evidence)
        else:
            queue(state, 'clarify', text=short(op.question,135),evidence=evidence,hint=target)
        return False

    if not evidence:
        raise Rejected('원문 인용 확인 필요')
    if op.choice is not None and op.action != 'answer':
        raise Rejected('선택한 질문 확인 필요')
    if op.previous_text is not None and op.action != 'replace':
        raise Rejected('수정할 원래 구절 확인 필요')
    check_edit_scope(state, target, op, previous, evidence, answer_text, reply)
    if op.action == 'remove_item':
        if target.item is None or target.path != f'{state.rows[target.item].path}.{IDENTITIES[state.rows[target.item].path]}':
            raise Rejected('항목 전체 삭제와 세부 필드 초기화를 구분해 줘')
        result.updates.append(remove_item(state, target, evidence))
        if reply is not None and reply in state.questions:
            state.questions.remove(reply)
        return False

    if op.action == 'clear':
        if op.value is not None:
            raise Rejected('비울 값 확인 필요')
        if target.item and target.path == f'{state.rows[target.item].path}.{IDENTITIES[state.rows[target.item].path]}':
            raise Rejected('항목 이름을 비울지 항목을 삭제할지 확인 필요')
        value = None
    else:
        option = choose(op, reply) if op.action == 'answer' else None
        list_fragment = (op.previous_text is not None and previous is not None and isinstance(previous.value,list)
                         and template.fields[target.path]=='list')
        if list_fragment:
            value = replace_list_fragment(op,previous,evidence,answer_text)
        else:
            value = option.value if option else checked_value(op, template.fields[target.path], previous, evidence)
        if option is None:
            if target.path == FEATURE_INCLUDED and op.action == 'record' and not reply:
                row = new_row or state.rows[target.item]
                public_id = state.public_ids.get(target.item)
                name = state.cells.get(Target(path=FEATURE_NAME, item=target.item).key())
                if not op._semantic_binding and not any(locate(ref.quote, row.label) or (public_id and locate(ref.quote, public_id))
                           or (name and any(contains_evidence(ref, anchor) for anchor in name.evidence))
                           for ref in evidence):
                    raise Rejected('출시 포함 여부를 결정한 기능 이름 또는 ID의 원문 근거 확인 필요')
        if option:
            evidence = option.evidence + evidence
        if op.action == 'extend' and not (isinstance(value, str) or (op._reviewed_list and isinstance(value, list) and value)):
            raise Rejected('추가할 문장 또는 비어 있지 않은 목록 확인 필요')
        if target.item and isinstance(value, str):
            row = new_row or state.rows[target.item]
            if target.path == f'{row.path}.{IDENTITIES[row.path]}':
                if option is None and op._reviewed_text is None and len(evidence) != 1:
                    raise Rejected('항목 이름은 원문 구절 하나로 지정해 줘')
                if op.action == 'record' and previous and identity(row.label) == identity(value) and reply is None:
                    return False
                if any(item != target.item and other.path == row.path and identity(other.label) == identity(value)
                       for item, other in state.rows.items()):
                    raise Rejected('이미 있는 항목 이름이야')
        if list_fragment:
            evidence = previous.evidence + evidence
        elif op.previous_text is not None:
            old_part = op.previous_text.strip()
            if not previous or not isinstance(previous.value, str):
                raise Rejected('수정할 원래 구절 확인 필요')
            revised = replace_text_fragment(previous.value, old_part, value)
            if revised != previous.value and not explicit_list_replacement(previous.value,revised,evidence,answer_text):
                raise Rejected('부분 수정 결과의 새 수치 근거를 확인할 수 없어')
            value = revised
            evidence = previous.evidence + evidence
        elif previous and isinstance(value, list) and op.action in ('replace', 'answer'):
            evidence = previous.evidence + evidence

    if (previous and op.action in ('record', 'extend') and reply is None and isinstance(value, str)
            and previous.evidence == evidence):
        return False


    if new_row:
        state.rows[target.item] = new_row
        state.next_row += 1
        if op.item is not None:
            aliases[(new_row.path, compact(op.item))] = target.item
        if row_proof is not None:
            identity_target = Target(path=f'{new_row.path}.{IDENTITIES[new_row.path]}', item=target.item)
            save_value(state, identity_target, None, new_row.label, [row_proof], reply is not None)

    if previous and op._reviewed_text is not None:
        evidence = previous.evidence + [ref for ref in evidence if ref not in previous.evidence]

    old_value = previous.value if previous else None
    if target.path == FEATURE_PRIORITY:
        value = priority_code(value)
        old_value = priority_code(old_value)
    authoritative = op.action in ('answer', 'replace', 'clear')
    pending_conflict = next((q for q in state.questions
                             if q.target == target and q.kind == 'conflict'), None)
    if not authoritative:
        if op.action in ('record','extend') and isinstance(value, list) and previous and isinstance(previous.value, list):
            if not value or (not previous.value and op.action == 'record'):
                if same(value, previous.value):
                    return False
                pending_conflict = queue(state, 'conflict', target)
            else:
                value = previous.value + [
                    v for v in value if not any(same(v, existing) for existing in previous.value)
                ]
                evidence = previous.evidence + evidence
        if op.action == 'extend' and previous and isinstance(previous.value, str) and not pending_conflict:
            if any(same(value, line) for line in previous.value.splitlines()):
                if reply is None:
                    return False
                value = previous.value
            else:
                value = f'{previous.value}\n{value}'
            evidence = previous.evidence + evidence
        if pending_conflict or (previous and not same(old_value, value)
                                and op.action == 'record' and not isinstance(value, list)):
            conflict = queue(state, 'conflict', target)
            for option in [previous, Cell(value=value, evidence=evidence)]:
                if option and not any(same(option.value, known.value) for known in conflict.options):
                    conflict.options.append(option)
            return False

    confirmed = authoritative or reply is not None
    changed = save_value(state, target, previous, value, evidence, confirmed)
    drop_question(state, target, reply)
    if confirmed:
        if target.path == FEATURE_INCLUDED and type(value) is bool:
            feature = state.rows[target.item].label
            update_exclusion(state, feature, None if value else feature, evidence)
        if (target.path == 'release_scope.features.name' and changed and previous
                and isinstance(previous.value, str) and isinstance(value, str)):
            exclusions = state.cells.get('release_scope.out_of_scope')
            if exclusions and isinstance(exclusions.value, list) and any(
                identity(name) == identity(previous.value) for name in exclusions.value
            ):
                update_exclusion(state, previous.value, value, evidence)
        state.focus = target
        name = label(state, target)
        if value is None:
            result.updates.append(f'{name}을 비웠어.')
        else:
            suffix = '저장했어' if changed or pending_conflict else '이미 저장돼 있어'
            shown = short(describe(value))
            if changed and previous and op.action == 'replace':
                shown = f'{short(describe(previous.value))} → {shown}'
            result.updates.append(f'{name} {suffix}. ({shown})')
    elif changed:
        result.saved += 1
    return confirmed




def answer_hint(state: ProjectState, question: Question) -> Target | None:
    """질문의 원문에서 답변 대상 힌트를 구한다."""
    return question.answer_hint


def resolve_answer(state: ProjectState, template: Template, reply: Question, op: Proposal,
                   evidence: list[Evidence], aliases: dict[tuple[str, str], str],
                   answer_text: str) -> tuple[Target, Row | None, Evidence | None]:
    """질문에 고정된 답변 저장 대상을 검증한다."""
    hint = reply.target or answer_hint(state, reply)
    if hint and op.path not in (None, hint.path):
        raise Rejected('질문과 답변의 저장 위치 확인 필요')
    path = hint.path if hint else op.path
    if path not in template.fields:
        raise Rejected('질문과 답변의 저장 위치 확인 필요')

    row_path, field = path.rsplit('.', 1)
    if hint and row_path not in template.rows:

        return Target(path=path), None, None

    if reply.target is not None or (hint and hint.item in state.rows):
        if op.item is not None:
            given, _ = resolve_target(state, template, op.model_copy(update={'path': path}), evidence, aliases)
            if given != hint:
                raise Rejected('질문과 답변의 저장 대상 확인 필요')
        return hint, None, None

    if IDENTITIES.get(row_path) == field and op.action in ('answer', 'record'):
        declaration = op.model_copy(update={'action': 'record', 'path': path, 'item': op.item or reply.id})
        target, row = resolve_target(state, template, declaration, evidence, aliases)
        return target, row, None

    if row_path not in template.rows:
        target, row = resolve_target(state, template, op.model_copy(update={'path': path}), evidence, aliases)
        return target, row, None

    name = hint.item if hint and hint.item else None
    given = compact(op.item or '')
    given = aliases.get((row_path, given), given)
    if given in state.rows:
        row = state.rows[given]
        if row.path != row_path:
            raise Rejected('질문과 답변의 저장 대상 확인 필요')
        given = row.label
    if name and given and identity(name) != identity(given):
        raise Rejected('질문과 답변의 저장 대상 확인 필요')

    rows = {item: row for item, row in state.rows.items() if row.path == row_path}
    if not name:

        matches = [row.label for row in rows.values() if given and locate(answer_text, row.label)]
        if not matches:
            matches = [row.label for row in rows.values()
                       if locate(reply.text or '', row.label) and named_evidence(reply.evidence, row.label)]
        if len(matches) == 1 and (not given or identity(given) == identity(matches[0])):
            name = matches[0]
        elif not matches and given and locate(reply.text or '', given) and named_evidence(reply.evidence, given):
            name = given

    row_name = LABELS[f'{row_path}.{IDENTITIES[row_path]}']
    if not name:
        raise UnresolvedTarget(f'어느 {row_name}에 해당하는 답변인지 알려줘')
    matching_ids = [item for item, row in rows.items() if identity(row.label) == identity(name)]
    if len(matching_ids) == 1:
        return Target(path=path, item=matching_ids[0]), None, None
    if not matching_ids and op.action in ('answer', 'record') and (proof := named_evidence(reply.evidence, name)):
        target = Target(path=path, item=f'R{state.next_row:04d}')
        return target, Row(path=row_path, label=compact(proof.quote)), proof
    raise UnresolvedTarget(f'어느 {row_name}에 해당하는 답변인지 알려줘')




def contains_evidence(outer: Evidence, inner: Evidence) -> bool:
    """두 인용이 같은 원문에서 포함 관계인지 확인한다."""
    return (outer.digest == inner.digest and outer.source == inner.source
            and outer.start <= inner.start < inner.end <= outer.end)


def apply_feature_links(result: TurnResult, operations: list[Proposal],
                        aliases: dict[tuple[str, str], str], source: Source, start: int, end: int,
                        *, queue_repair: bool = False):
    """관련 기능 참조를 검증하고 공개 ID로 저장한다."""
    state = result.state
    ensure_public_ids(state)
    for op in operations:
        if op.path not in FEATURE_LINK_PATHS or op.action == 'question':
            continue
        try:
            row_path = op.path.rsplit('.', 1)[0]
            raw_item = compact(op.item or '')
            matches = row_matches(state, row_path, raw_item, aliases)
            if len(matches) != 1:
                raise Rejected('관련 항목을 정확하게 지정해 줘')
            item, = matches
            from storage.evidence_store import bind_evidence, operation_evidence
            bind_evidence(op, state)
            evidence = operation_evidence(op, source, start, end)
            if not evidence:
                raise Rejected('원문 근거 확인 필요')
            target = Target(path=op.path, item=item)
            reply = next((q for q in state.questions if q.id == op.reply_to), None)
            if op.reply_to is not None or op.action == 'answer':
                if reply is None or (reply.target or reply.answer_hint) != target:
                    raise Rejected('질문과 관련 기능의 저장 대상이 일치하지 않아')
            check_explicit_row(state, target, evidence)
            if op.previous_text is not None:
                raise Rejected('기능 연결은 문자열 부분 교체를 지원하지 않아')
            if op.action in ('clear', 'remove_item'):
                if op.action == 'remove_item':
                    raise Rejected('기능 연결을 수정하려면 clear를 사용해 줘')
                check_edit_scope(state, target, op, state.cells.get(target.key()), evidence, source.text[start:end], reply)
                mapped = []
            elif op.action in ('record','replace','answer','extend'):
                if op.action == 'extend' and not op._semantic_binding:
                    raise Rejected('기능 연결 추가는 의미 검토가 있는 명시적 추가 작업이어야 해')
                if not isinstance(op.value, list) or not op.value:
                    raise Rejected('기능 이름을 문자열 배열로 알려줘')
                mapped = []
                for ref in op.value:
                    choices = row_matches(state, FEATURES, ref, aliases)
                    if len(choices) != 1:
                        raise Rejected(f'존재하지 않거나 모호한 관련 기능: {short(ref, 42)}')
                    feat_id = state.public_ids[next(iter(choices))]
                    if feat_id not in mapped:
                        mapped.append(feat_id)
                references = {value.upper() for proof in evidence for value in
                              re.findall(r'(?<![A-Za-z0-9])FEAT-[0-9]+(?![A-Za-z0-9])', proof.quote, re.I)}
                if references and references != set(mapped):
                    raise Rejected('원문에 명시된 기능 ID와 연결 대상이 일치하지 않아')

                destination = state.rows[item].label
                named_destinations = {rid for rid, row in state.rows.items() if row.path == row_path
                                      and len(identity(row.label)) >= 2
                                      and any(identity(row.label) in identity(proof.quote) for proof in evidence)}
                row_fields = [cell for key, cell in state.cells.items()
                              if key.endswith('@' + item) and key.partition('@')[0] not in FEATURE_LINK_PATHS]
                destination_grounded = item in named_destinations or any(
                    contains_evidence(proof, ref) for cell in row_fields for ref in cell.evidence for proof in evidence
                )
                if named_destinations and not destination_grounded:
                    raise Rejected('관련 기능을 연결할 항목이 원문과 일치하지 않아')
                feature_ids = {state.public_ids[rid]: rid for rid, row in state.rows.items()
                               if row.path == FEATURES}
                ungrounded = {}
                for public in mapped:
                    rid = feature_ids[public]
                    behavior = state.cells.get(Target(path=FEATURE_BEHAVIOR, item=rid).key())
                    if public in references or any(
                        locate(proof.quote, state.rows[rid].label)
                        or (behavior and any(contains_evidence(proof, ref) for ref in behavior.evidence))
                        for proof in evidence
                    ):
                        continue
                    ungrounded[public] = list(dict.fromkeys(
                        ref.quote for ref in (behavior.evidence if behavior else []) if ref.digest == source.digest
                    ))
                if (ungrounded or not destination_grounded) and not op._semantic_binding:
                    reason = '기능 연결 관계의 원문 근거 확인 필요'
                    if queue_repair and op.action == 'record' and reply is None and target.key() not in state.cells:
                        warning = f'{LABELS[op.path]} · {destination} · {reason}'
                        if not any(issue.get('target_key') == target.key() for issue in result.retry_links):
                            result.retry_links.append({'target_key': target.key(), 'error': warning,
                                'window_start': start, 'window_end': end,
                                'feature_evidence': ungrounded, 'missing_target_evidence': not destination_grounded,
                                'records': [{'path': op.path, 'item': item, 'value': mapped, 'quotes': op.quotes}]})
                            result.reviews.append(warning)
                        continue
                    raise Rejected(reason)
            else:
                raise Rejected('기능 연결은 record, replace, answer, extend, clear만 지원해')
            old = state.cells.get(target.key())
            if op.action == 'replace' and old and not same(old.value, mapped):
                check_edit_scope(state, target, op, old, evidence, source.text[start:end], reply)
            if op.action in ('record','extend') and old and isinstance(old.value, list):
                mapped = list(dict.fromkeys([*old.value, *mapped]))
                if op.action == 'record' and any(change.target == target for change in state.changes) and mapped != old.value:
                    raise Rejected('사용자가 확정한 관련 기능은 명시적 변경 요청으로만 수정할 수 있어')
            if old and isinstance(old.value,list) and set(old.value)&set(mapped):
                evidence=[*old.evidence,*(ref for ref in evidence if ref not in old.evidence)]
            if save_value(state, target, old, mapped, evidence, op.action != 'record'):
                result.saved += 1
            if reply is not None:
                drop_question(state, target, reply)
        except (Rejected, ValueError) as exc:
            result.reviews.append(f'{LABELS[op.path]} · {exc}')


def apply_batch(result: TurnResult, template: Template, operations: list[Proposal],
                source: Source, start: int, end: int, *, repair_links: bool = False):
    """검증된 작업을 선언, 일반 필드, 관계 순서로 반영한다."""
    operations = [canonical_operation(op, template) for op in operations]
    state = result.state
    previous_focus, previous_saved = state.focus, result.saved
    aliases: dict[tuple[str, str], str] = {}
    result.row_aliases = aliases
    window = source.text[start:end]
    replies = {q.id:q for q in state.questions}
    positions = {q.id:index for index,q in enumerate(state.questions)}
    def order(op: Proposal):
        """새 행을 먼저 선언하고 답변 전 기존 질문의 순서를 보존한다."""
        row_path, _, field = (op.path or '').rpartition('.')
        if op.action == 'record' and IDENTITIES.get(row_path) == field:
            return 0
        permission_scope = op.action == 'clarify' and op.path == PERMISSION_PATH and not op.reply_to
        return 2 if op.action == 'question' or permission_scope else 1

    focused = False
    for op in sorted(operations, key=order):
        if op.path in FEATURE_LINK_PATHS:
            continue
        if op.reply_to in replies and not any(q.id == op.reply_to for q in state.questions):
            state.questions.insert(min(positions[op.reply_to],len(state.questions)),replies[op.reply_to])
        row_path = (op.path or '').rsplit('.', 1)[0]
        raw_item = compact(op.item or '')
        item = aliases.get((row_path, raw_item), raw_item)
        try:
            from storage.evidence_store import bind_evidence, operation_evidence
            bind_evidence(op, state)
            evidence = operation_evidence(op, source, start, end)
            if op.path == FEATURE_INCLUDED and op.action == 'record' and op.reply_to is None:
                row = state.rows.get(item)
                if row and not any(locate(ref.quote, row.label) for ref in evidence):
                    for sentence in review_segments(window):
                        if not locate(sentence, row.label):
                            continue
                        proof = source_evidence(source, start, end, [sentence])[0]
                        if any(contains_evidence(proof, ref) for ref in evidence):
                            evidence.append(proof)
            focused = apply_operation(result, template, op, evidence, aliases, window) or focused
        except (Rejected, ValueError) as exc:
            field_name = LABELS.get(op.path, '이번 입력')
            row = state.rows.get(item)
            prefix = f'{short(row.label, 32)} · ' if row else ''
            result.reviews.append(f'{prefix}{field_name} · {exc}')
    apply_feature_links(result, operations, aliases, source, start, end, queue_repair=repair_links)
    ensure_public_ids(state)
    try:
        validate_public_ids(state)
    except ValueError as exc:
        result.reviews.append(str(exc))
    if not focused and previous_focus == state.focus and result.saved > previous_saved:
        state.focus = None
