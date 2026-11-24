"""첫 입력의 구조화 추출·검토·보정 흐름을 처리한다.

Domain-independent first-ingestion contract.

LLM: interpret source into typed cells, review meaning and omissions.
Python: verify supported paths/types, verbatim evidence, stable row bindings,
public feature IDs and transitions. No industry-specific keyword rules.
"""
from __future__ import annotations


from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr, StrictInt
from typing import Literal
from dataclasses import dataclass
import re

from core.planning_schema import (
    Cell, Evidence, FEATURE_LINK_PATHS, IDENTITIES, ProjectState, Row,
    SYSTEM_OWNED_PATHS, Target, Template, ensure_public_ids, validate_public_ids,
    validate_state, identity,
)
from core.planning_logic import Source, TurnResult, locate


class StructuredField(BaseModel):
    model_config = ConfigDict(extra='forbid')
    path: StrictStr
    mode: Literal['set', 'append'] = 'set'
    local_key: StrictStr | None = None
    value: StrictStr | StrictBool | list[StrictStr] | None = None
    quotes: list[StrictStr] = Field(default_factory=list)
    feature_keys: list[StrictStr] = Field(default_factory=list)


class StructuredDraft(BaseModel):
    model_config = ConfigDict(extra='forbid')
    fields: list[StructuredField]


class StructuredPatchTarget(BaseModel):
    model_config = ConfigDict(extra='forbid')
    path: StrictStr
    local_key: StrictStr | None = None


class StructuredPatch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    remove: list[StructuredPatchTarget] = Field(default_factory=list)
    upsert: list[StructuredField] = Field(default_factory=list)


def apply_patch(draft: StructuredDraft, patch: StructuredPatch) -> StructuredDraft:
    """Deterministic upsert/removal; untouched fields survive every correction."""
    from extraction.response_normalization import unique
    records = {(f.path, f.local_key):f.model_copy(deep=True) for f in draft.fields}
    if len(records)!=len(draft.fields):
        raise ValueError('원본 초안에 중복 필드가 있어.')
    removals = unique((r.path,r.local_key) for r in patch.remove)
    updates={}
    for raw in patch.upsert:
        item=raw.model_copy(deep=True)
        target=(item.path,item.local_key)
        if item.path in FEATURE_LINK_PATHS:
            refs=normalized_feature_keys(item)
            if refs is None:raise ValueError('invalid_feature_link_format:'+str(target))
            item.value=None;item.feature_keys=refs
        item.quotes=unique(item.quotes)
        empty=(item.value is None or isinstance(item.value,str) and not item.value.strip()
               or isinstance(item.value,list) and not item.value) and not item.feature_keys
        if empty and target in records:
            raise ValueError('explicit_removal_required:'+str(target))
        previous=updates.get(target)
        if previous is not None:
            old=previous.model_dump(exclude={'quotes'})
            new=item.model_dump(exclude={'quotes'})
            if old!=new or type(previous.value) is not type(item.value):
                raise ValueError('conflicting_patch_updates:'+str(target))
            previous.quotes=unique([*previous.quotes,*item.quotes])
        else:updates[target]=item
    if set(removals)&set(updates):
        raise ValueError('교정 대상의 제거와 수정을 동시에 요청했어.')
    for target in removals:
        if target not in records:
            raise ValueError('존재하지 않는 교정 삭제 대상이야: '+str(target))
        del records[target]
    records.update(updates)
    return StructuredDraft(fields=list(records.values()))


def semantic_repair_targets(issues: list[str], template: Template) -> set[str]:
    """an actual schema path. Keep the permitted scope in Python, not just a prompt."""
    paths = set(template.fields) - SYSTEM_OWNED_PATHS
    found = set()
    for issue in issues:
        for match in re.finditer(r"(?<![\w.])(?:[a-z][a-z_]*\.)+[a-z][a-z_]*", issue):
            if match.group(0) in paths:
                found.add(match.group(0))
    return found



def referenced_repair_rows(issues: list[str], draft: StructuredDraft,
                           allowed_paths: set[str]) -> set[tuple[str, str]]:
    """Allow only explicitly named existing rows in the same section as a reviewed field."""
    permitted_sections = {path.split('.', 1)[0] for path in allowed_paths}
    targets = set()
    for field in draft.fields:
        key = field.local_key
        if not key or field.path.split('.', 1)[0] not in permitted_sections:
            continue
        section = field.path.split('.', 1)[0]
        if any((f"'{key}'" in issue or f'"{key}"' in issue) and
               any(path.startswith(section + '.') and path in issue for path in allowed_paths)
               for issue in issues):
            targets.add((field.path, key))
    return targets


def canonical_semantic_patch(draft: StructuredDraft, patch: StructuredPatch) -> StructuredPatch:
    """A same-field remove+upsert is a replace, not two operations.

    Only a *field-level* redundant removal may be normalized; row deletion or
    a removal with no replacement is never silently discarded.
    """
    updated = {(f.path, f.local_key) for f in patch.upsert}
    removed = {(f.path, f.local_key) for f in patch.remove}
    existing = {(f.path, f.local_key) for f in draft.fields}
    overlapping = updated & removed
    if not overlapping or not overlapping <= existing:
        return patch
    return StructuredPatch(remove=[r for r in patch.remove if (r.path, r.local_key) not in overlapping],
                           upsert=[f.model_copy(deep=True) for f in patch.upsert])


def inspect_semantic_patch(draft: StructuredDraft, patch: StructuredPatch, allowed_paths: set[str],
                           allowed_rows: set[tuple[str, str]] | None = None) -> list[str]:
    """semantic patch 관련 작업을 수행한다"""
    touched = {(r.path, r.local_key) for r in patch.remove} | {(f.path, f.local_key) for f in patch.upsert}
    if not touched:
        return []
    permitted_rows = allowed_rows or set()
    if allowed_paths:
        unexpected = {path for path, key in touched if path not in allowed_paths and
                      (path, key) not in permitted_rows}
        if unexpected:
            return ['semantic_repair_scope_violation:' + ','.join(sorted(unexpected))]
    try:
        updated = apply_patch(draft, patch)
    except ValueError as exc:
        return [f'invalid_patch:{exc}']
    if updated == draft:
        return ['semantic_repair_no_effect']
    return []


class StructuredVerdict(BaseModel):
    model_config = ConfigDict(extra='forbid')
    valid: StrictBool
    issues: list[StrictStr]
    missing_segment_indices: list[StrictInt] = Field(default_factory=list)
    field_audit_confirmed: StrictBool = False


from core.object_contract import EXTRACT_PROMPT, CORRECT_PROMPT

REVIEW_PROMPT = """새 프로젝트 요구사항의 독립 의미 검토자다. 원문과 candidate_json을 직접 비교한다.
- 원문에서 JSON으로: 명시된 핵심 동작, 주체, 조건, 수치·단위·시점, 권한, 차단·복구, 개인정보 목적·보관, 제외 기능, 지표, 플랫폼을 빠뜨리지 않았는지 확인한다.
- JSON에서 원문으로: 근거 없는 사실, 동작이 아닌 제한 규칙을 독립 기능으로 중복 추출, 기능/정책/예외/데이터 분류 오류, 원문 단편만 복사해서 의미를 잃은 값, 잘못된 관계를 확인한다.
- 명시된 기능과 직접 연결되는 화면·예외·데이터의 관계 누락, 원문에서 명확한 데이터 용도 누락, 이용 목적의 한정(오직/~에만), 권한과 시간 조건의 소실도 실제 오류로 검토한다. 시스템 차단의 조건과 실제 대응, 사용자의 행동 지침은 독립적으로 확인한다. 원문에서 명확한 사용 상황과 예외 대응을 빠뜨리지 않는다. 불명확한 관계나 용도는 추측하지 않는다. 확정되지 않은 기술·입력 형식·선택 필드가 null/[]인 것은 오류가 아니다. 필요한 사용자 결정은 이후 질문 단계에서 판단한다. 현재 단계에서 질문을 생성하지 않는다.
- 개요(summary)는 원문이 서비스의 목적/대상/주요 기능을 충분히 설명한 경우 요약되어야 한다. 명시된 플랫폼은 project_type에 있더라도 supported_platforms에도 독립적으로 기록되어야 한다. 단 부정/제외된 플랫폼은 추가하지 않는다. pre_launch_checks는 원문에 명시된 실제 테스트·점검만 기록하며 단순 기능 이름의 나열이나 모델이 새로 제안한 검증을 확정 사항으로 채우지 않는다. 원문에 테스트 지시가 없다면 비워둔다.
- field_review_focus의 개요·플랫폼·출시 전 검증 역할을 실제 원문과 반드시 대조하고 field_audit_confirmed=true로 검증 수행 사실을 표시한다. 누락 또는 부적절한 테스트 목록이 있으면 valid=false로 오류를 보고하고 교정을 요청한다. 원문 증거가 불충분하면 확정값을 추측하지 않는다.
- 검토 결과에 임의의 선택적 구현 세부사항을 필수 결함으로 취급하지 않는다. 필수 질문이나 미정 항목은 다음 질문 단계로 넘긴다.
- missing_segment_indices는 원문 구간 중 실제 요구사항인데 후보에 반영되지 않은 문장의 0부터 시작하는 인덱스다. 불필요한 문장에는 적지 않는다.
- 문제없으면 valid=true, issues=[], missing_segment_indices=[]. 문제 있으면 valid=false와 오류 이유를 구체적으로 쓴다. 실제 내용 누락·왜곡·근거 없는 추가가 없는데 단순 문체·배열 순서만으로 오류 처리하지 않는다.
"""


Key = tuple[str, str | None]


@dataclass(frozen=True)
class DraftProblem:
    message: str
    targets: tuple[Key, ...]


@dataclass
class DraftInspection:
    normalized: StructuredDraft
    problems: list[DraftProblem]
    conversions: list[dict]

    @property
    def issues(self):
        """issues 관련 작업을 수행한다"""
        return list(dict.fromkeys(p.message for p in self.problems))

    @property
    def targets(self):
        """targets 관련 작업을 수행한다"""
        return set(k for p in self.problems for k in p.targets)

    def add(self, message, targets=()):
        """add 관련 작업을 수행한다"""
        problem = DraftProblem(message, tuple(dict.fromkeys(targets)))
        if problem not in self.problems:
            self.problems.append(problem)


def normalized_feature_keys(field):
    """Accept equivalent carriers; never select one side of a conflict."""
    from extraction.response_normalization import normalize_initial_value, unique
    value, _, error = normalize_initial_value(field.path, 'list', field.value)
    if error:
        return None
    keys = field.feature_keys
    if keys and value and set(keys) != set(value):
        return None
    return unique(keys or value or [])


def _normalize_report(template, base, draft, source, start, end):
    """Aggregate independent local errors without altering the received draft.

    Valid fields can be inspected further even if other fields fail. Invalid
    fields are *not* silently dropped/accepted: their original values remain
    available for a scoped correction, and any problem prevents committing.
    """
    from extraction.response_normalization import unique, normalize_initial_value
    fields = {}
    groups = {}
    report = DraftInspection(StructuredDraft(fields=[]), [], [])
    window = source.text[start:end]
    known_local = {f.local_key for f in draft.fields if f.path == 'release_scope.features.name' and f.local_key}
    known_public = {v for v in base.public_ids.values() if v.startswith('FEAT-')}
    ambiguous = known_local & known_public
    for key in sorted(ambiguous):
        affected = [(f.path, f.local_key) for f in draft.fields
                    if f.path.rpartition('.')[0] == 'release_scope.features' and f.local_key == key]
        report.add('ambiguous_local_and_public_feature_key:' + key, affected)
    for i, raw in enumerate(draft.fields):
        f = raw.model_copy(deep=True)
        path = f.path
        parent, _, leaf = path.rpartition('.')
        marker = (path, f.local_key)
        where = f'field[{i}]:{path}'
        kind = template.fields.get(path)
        is_identity = parent in template.rows and leaf == IDENTITIES[parent]
        errors = []
        if kind is None or path in SYSTEM_OWNED_PATHS:
            errors.append('unknown_or_system_path')
        if parent in template.rows:
            if not f.local_key or not f.local_key.strip():
                errors.append('missing_row_key')
        elif f.local_key is not None:
            errors.append('scalar_has_row_key')

        if any(not q.strip() or locate(window, q.strip()) is None for q in f.quotes):
            errors.append('invalid_quote')
        f.quotes = unique(f.quotes)
        f.feature_keys = unique(f.feature_keys)
        empty = f.value is None or isinstance(f.value, str) and not f.value.strip() or isinstance(f.value, list) and not f.value
        if kind is not None:
            if f.mode == 'append' and (kind == 'bool' or path in FEATURE_LINK_PATHS or is_identity):
                errors.append('invalid_append_type')
            if empty and path not in FEATURE_LINK_PATHS:
                if is_identity:
                    errors.append('empty_identity')
                if f.mode != 'set' or f.value is not None and not (kind == 'text' and isinstance(f.value, str) or kind == 'list' and isinstance(f.value, list)):
                    errors.append('empty_value_type_or_mode')
            else:
                f.value, conversion, error = normalize_initial_value(path, kind, f.value)
                if error:
                    errors.append(error)
                elif conversion:
                    report.conversions.append({'path': path, 'key': f.local_key, 'kind': conversion})
        if path in FEATURE_LINK_PATHS:
            refs = normalized_feature_keys(f)
            if refs is None:
                errors.append('invalid_feature_link_format')
            else:
                f.value = None
                f.feature_keys = refs
        if f.feature_keys:
            invalid = [key for key in f.feature_keys if key not in known_local and key not in known_public]
            errors.extend('unknown_feature_reference:' + key for key in invalid)
            errors.extend('ambiguous_feature_reference:' + key for key in f.feature_keys if key in ambiguous)
        if (not empty or f.feature_keys) and not f.quotes:
            errors.append('missing_evidence')
        if errors:
            for error in unique(errors):
                report.add(where + ':' + error, [marker])
            continue

        if f.feature_keys:
            relation = parent + '.related_features'
            if relation in FEATURE_LINK_PATHS and relation in template.fields:
                groups.setdefault((relation, f.local_key), []).append((f.feature_keys, f.quotes, marker))
                if path != relation:
                    f.feature_keys = []
            elif path not in FEATURE_LINK_PATHS:
                f.feature_keys = []
        if path not in FEATURE_LINK_PATHS and empty:
            continue
        if path in FEATURE_LINK_PATHS and not f.feature_keys:
            continue
        old = fields.get(marker)
        if old is None:
            fields[marker] = f
            continue
        same = (old.mode == f.mode and type(old.value) is type(f.value)
                and (old.value == f.value or path == 'release_scope.supported_platforms'
                     and isinstance(old.value, list) and set(old.value) == set(f.value))
                and set(old.feature_keys) == set(f.feature_keys))
        if not same:
            report.add(where + ':duplicate_field_conflict', [marker])
        else:
            old.quotes = unique([*old.quotes, *f.quotes])
    for (path, key), carriers in groups.items():
        sets = [set(refs) for refs, _, _ in carriers]
        if any(refs != sets[0] for refs in sets[1:]):
            report.add(f'{path}@{key}:conflicting_feature_carriers', [(path, key), *(marker for _, _, marker in carriers)])
            continue
        refs = carriers[0][0]
        quotes = unique(q for _, qs, _ in carriers for q in qs)
        fields[(path, key)] = StructuredField(path=path, local_key=key, quotes=quotes, feature_keys=refs)
    report.normalized = StructuredDraft(fields=list(fields.values()))
    return report


def normalize_draft(template, base, draft, source, start, end):
    """Public compatibility wrapper. Reject a candidate if any issue is present."""
    report = _normalize_report(template, base, draft, source, start, end)
    return report.normalized, report.issues


def inspect_candidate(template: Template, base: ProjectState, draft: StructuredDraft,
                      source: Source, start: int, end: int):
    """Inspect all safely checkable fields and cross-references, then commit none
    unless the complete candidate is valid. The copied partial state is private.
    """
    report = _normalize_report(template, base, draft, source, start, end)
    normalized = report.normalized
    state = base.model_copy(deep=True)
    result = TurnResult(state=state)
    window = source.text[start:end]
    row_ids = {}
    row_labels = {}
    raw_rows = {}
    for f in draft.fields:
        parent = f.path.rpartition('.')[0]
        if parent in template.rows and f.local_key is not None:
            raw_rows.setdefault((parent, f.local_key), []).append((f.path, f.local_key))
    for f in normalized.fields:
        parent, _, leaf = f.path.rpartition('.')
        if parent in template.rows and leaf == IDENTITIES[parent]:
            row_labels[(parent, f.local_key)] = f.value.strip()
    label_to_keys = {}
    for (parent, key), label in row_labels.items():
        label_to_keys.setdefault((parent, identity(label)), []).append(key)
    invalid_rows = set()
    for (parent, label), keys in label_to_keys.items():
        if len(keys) > 1:
            affected = [(parent, key) for key in keys]
            invalid_rows.update(affected)
            report.add(f'{parent}:duplicate_identity_across_rows:{label}',
                       [target for row in affected for target in raw_rows.get(row, [])])
    for (parent, key), members in raw_rows.items():
        if (parent, key) in row_labels:
            continue
        identity_key = (parent + '.' + IDENTITIES[parent], key)
        targets = [identity_key] if identity_key in members else [identity_key, *members]
        if any(f.path == identity_key[0] and f.local_key == key for f in draft.fields) or any(
                f.local_key == key and f.path.rpartition('.')[0] == parent for f in normalized.fields):
            report.add(f'{parent}@{key}:missing_identity_field', targets)
    for (parent, key), label in row_labels.items():
        if (parent, key) in invalid_rows:
            continue
        matches = [rid for rid, row in state.rows.items() if row.path == parent and identity(row.label) == identity(label)]
        if len(matches) > 1:
            report.add(f'{parent}:{key}:ambiguous_existing_row', raw_rows.get((parent, key), []))
            continue
        if matches:
            row_ids[(parent, key)] = matches[0]
        else:
            rid = f'R{state.next_row:04d}'
            while rid in state.rows:
                state.next_row += 1
                rid = f'R{state.next_row:04d}'
            state.rows[rid] = Row(path=parent, label=label)
            row_ids[(parent, key)] = rid
            state.next_row += 1
    ensure_public_ids(state)
    for f in normalized.fields:
        parent = f.path.rpartition('.')[0]
        marker = (f.path, f.local_key)
        rid = row_ids.get((parent, f.local_key)) if parent in template.rows else None
        if parent in template.rows and rid is None:
            continue
        value = f.value
        if f.path in FEATURE_LINK_PATHS:
            keys = normalized_feature_keys(f)
            ids = []
            unresolved = False
            for key in keys:
                feature_row = row_ids.get(('release_scope.features', key))
                if feature_row is not None:
                    ids.append(state.public_ids[feature_row])
                elif ('release_scope.features', key) in raw_rows:
                    unresolved = True
                elif key in base.public_ids.values() and key.startswith('FEAT-'):
                    ids.append(key)
                else:
                    report.add(f'{f.path}:unknown_feature_reference:{key}', [marker])
                    unresolved = True
            if unresolved or not ids:
                continue
            value = list(dict.fromkeys(ids))
        evidence = []
        for quote in f.quotes:
            left, right = locate(window, quote.strip())
            evidence.append(Evidence(source=source.name or '자연어 입력', digest=source.digest,
                                     start=start+left, end=start+right, quote=window[left:right]))
        target = Target(path=f.path, item=rid)
        previous = state.cells.get(target.key())
        if (previous and f.path == 'release_scope.supported_platforms'
                and isinstance(previous.value, list) and isinstance(value, list)
                and len(previous.value) == len(value) and set(previous.value) == set(value)):
            value = previous.value
        if previous and previous.value != value:
            if f.mode == 'append' and isinstance(previous.value, str) and isinstance(value, str):
                value = previous.value if value in previous.value else previous.value.rstrip() + '\n' + value
            elif f.mode == 'append' and isinstance(previous.value, list) and isinstance(value, list):
                value = list(dict.fromkeys([*previous.value, *value]))
            else:
                report.add(f'{target.key()}:conflicting_existing_value', [marker])
                continue
        if previous:
            if previous.value != value:
                previous.value = value
                result.saved += 1
            previous.evidence = list({(e.digest, e.start, e.end): e for e in [*previous.evidence, *evidence]}.values())
        else:
            state.cells[target.key()] = Cell(value=value, evidence=evidence)
            result.saved += 1
    if not report.problems:
        ensure_public_ids(state)
        try:
            validate_public_ids(state)
            validate_state(template, state)
        except ValueError as exc:
            report.add(f'state_validation:{exc}')
    if report.problems:
        result = TurnResult(state=base.model_copy(deep=True))
    return result, report


def build_candidate(template: Template, base: ProjectState, draft: StructuredDraft,
                    source: Source, start: int, end: int) -> tuple[TurnResult, list[str]]:
    """candidate 관련 작업을 구성한다"""
    trial, report = inspect_candidate(template, base, draft, source, start, end)
    return trial, report.issues


def apply_structural_patch(draft, patch, allowed_targets):
    """Scope-limited repair of invalid raw records, including conflicting duplicates.

    Unlike a semantic patch, the base may contain duplicate records. Replace
    *all* occurrences of the explicitly identified bad target. Untouched raw
    records, values, quotes, modes and relative order survive exactly.
    """
    allowed = set(allowed_targets)
    touched = {(r.path, r.local_key) for r in patch.remove} | {(f.path, f.local_key) for f in patch.upsert}
    if not touched:
        raise ValueError('empty_structural_patch')
    if not touched <= allowed:
        raise ValueError('structural_repair_scope_violation:' + repr(sorted(touched-allowed, key=repr)))
    representatives = {}
    for f in draft.fields:
        representatives.setdefault((f.path, f.local_key), f)
    replacement = apply_patch(StructuredDraft(fields=list(representatives.values())), patch)
    replacements = {(f.path, f.local_key): f for f in replacement.fields if (f.path, f.local_key) in touched}
    out = []
    emitted = set()
    for f in draft.fields:
        marker = (f.path, f.local_key)
        if marker not in touched:
            out.append(f.model_copy(deep=True))
        elif marker in replacements and marker not in emitted:
            out.append(replacements[marker])
            emitted.add(marker)
    out.extend(f for marker, f in replacements.items() if marker not in emitted)
    return StructuredDraft(fields=out)


def ingest_initial(processor, state: ProjectState, text: str, source_name=None, *, trace=None, budget=None):
    """One draft per window; bounded structural patches, then semantic review.

    Only an unparseable/transport-failed response is a reason to retry the full
    extraction call. A parsed draft is retained even if fields fail validation.
    No provisional candidate is written to the durable project state.
    """
    from planning_graph import run_model_stage, RecoveryBudget
    from planning_agent import ModelError, windows
    from core.planning_logic import review_segments
    from storage.request_state import resolve_retried_input
    from core.object_contract import STRUCTURAL_CORRECT_PROMPT
    source = Source(text=text, name=source_name)
    result = TurnResult(state=state.model_copy(deep=True), replay=bool(source_name and source.digest in state.documents))
    if result.replay:
        return result
    if budget is None:
        budget = RecoveryBudget()
    if trace:
        from hashlib import sha256
        trace('structured_initial_start', {
            'source_sha256': source.digest,
            'length': len(text),
            'extract_prompt_sha256': sha256(EXTRACT_PROMPT.encode('utf-8')).hexdigest(),
            'correct_prompt_sha256': sha256(CORRECT_PROMPT.encode('utf-8')).hexdigest(),
            'structural_correct_prompt_sha256': sha256(STRUCTURAL_CORRECT_PROMPT.encode('utf-8')).hexdigest(),
            'review_prompt_sha256': sha256(REVIEW_PROMPT.encode('utf-8')).hexdigest(),
        })

    def failure(message, stage, errors):
        """failure 관련 작업을 수행한다"""
        error = ModelError(message, kind='candidate_validation', issues=errors)
        error.failed_stage = stage
        return error

    for start, end in windows(text):
        window = text[start:end]
        payload = {'source_text': window, 'previous_context': text[max(0, start-800):start],
                   'fields': processor.fields, 'identities': {k: v for k, v in IDENTITIES.items() if k in processor.template.rows},
                   'existing': processor.template.export(result.state)}
        revision = 0
        structural_repairs = 0
        semantic_repairs = 0
        seen_semantic_cycles = set()
        last_errors = []
        draft = run_model_stage(processor.model.structured_extract,
                                {**payload, 'revision': 0, 'correction_feedback': [], 'previous_draft': None},
                                StructuredDraft, 'structured_extract', trace=trace, budget=budget)
        while True:
            if trace:
                trace('structured_initial_cycle', {'window_start': start, 'revision': revision, 'prior_errors': last_errors})
            trial, report = inspect_candidate(processor.template, result.state, draft, source, start, end)
            if trace and report.conversions:
                trace('structured_initial_normalized', {'window_start': start, 'revision': revision, 'conversions': report.conversions})
            if report.problems:
                last_errors = report.issues
                targets = [{'path': p, 'key': k} for p, k in sorted(report.targets, key=repr)]
                if trace:
                    trace('structured_initial_structure_rejected', {'window_start': start, 'revision': revision,
                        'issues': last_errors, 'repair_targets': targets, 'draft': draft.model_dump(mode='json')})
                if not report.targets or any(not p.targets for p in report.problems):
                    raise failure('구조화 후보의 상태 검증을 복구할 수 없어.', 'structured_validation', last_errors)
                if structural_repairs >= 2 or budget.remaining <= 0:
                    raise failure('구조화 후보의 부분 복구 후에도 검증 오류가 있어.', 'structured_validation', last_errors)
                budget.remaining -= 1
                structural_repairs += 1
                revision += 1
                request = {**payload, 'revision': revision, 'repair_kind': 'structure', 'repair_targets': targets,
                           'correction_feedback': last_errors, 'previous_draft': draft.model_dump(mode='json')}

                def check_repair(patch):
                    """repair 관련 작업을 확인한다"""
                    try:
                        apply_structural_patch(draft, patch, report.targets)
                    except ValueError as exc:
                        return [f'invalid_structural_patch:{exc}']
                    return []

                patch = run_model_stage(processor.model.structured_correct, request, StructuredPatch,
                                        'structured_correct', trace=trace, budget=budget, check=check_repair)
                draft = apply_structural_patch(draft, patch, report.targets)
                if trace:
                    trace('structured_initial_structure_patch', {'window_start': start, 'revision': revision,
                        'upsert': len(patch.upsert), 'remove': len(patch.remove)})
                continue

            draft = report.normalized
            segments = review_segments(window)
            from extraction.initial_quality import audit_initial_quality, review_focus
            candidate_document=processor.template.export(trial.state)
            quality_issues=audit_initial_quality(window,candidate_document)
            review_payload = {'source_text': window, 'source_segments': segments, 'field_review_focus':review_focus(candidate_document),
                              'candidate_json': candidate_document,
                              'candidate_evidence': {key: [e.quote for e in cell.evidence] for key, cell in trial.state.cells.items()},
                              'revision': revision}
            verdict = run_model_stage(processor.model.structured_review, review_payload, StructuredVerdict,
                                      'structured_review', trace=trace, budget=budget,
                check=lambda v: ['invalid_review_issue_contract'] if (v.valid and (v.issues or v.missing_segment_indices))
                or (not v.valid and not v.issues and not v.missing_segment_indices)
                or any(i < 0 or i >= len(segments) for i in v.missing_segment_indices) else [])
            if quality_issues and trace:
                trace('initial_field_quality_rejected',{'issues':quality_issues,'window_start':start,'revision':revision})
            if verdict.valid and not (verdict.field_audit_confirmed and quality_issues):
                if trace:
                    trace('structured_initial_accepted', {'window_start': start, 'revision': revision, 'saved': trial.saved})
                result.state = trial.state
                result.saved += trial.saved
                break
            last_errors = list(verdict.issues) + [f'missing source segment {i}: {segments[i]}' for i in verdict.missing_segment_indices] + (quality_issues if verdict.field_audit_confirmed else [])
            if trace:
                trace('structured_initial_semantic_rejected', {'window_start': start, 'revision': revision, 'issues': last_errors})
            from hashlib import sha256
            import json
            signature = (sha256(json.dumps(draft.model_dump(mode='json'),sort_keys=True,ensure_ascii=False).encode()).hexdigest(),
                         tuple(sorted(last_errors)))
            if signature in seen_semantic_cycles:
                raise failure('동일한 의미 검토 오류가 반복되어 복구를 중단했어.', 'structured_review', last_errors)
            seen_semantic_cycles.add(signature)
            if semantic_repairs >= 4 or budget.remaining <= 0:
                raise failure('원문 의미 검토 후에도 수정되지 않은 요구사항이 있어.', 'structured_review', last_errors)
            budget.remaining -= 1
            semantic_repairs += 1
            revision += 1
            allowed_paths = semantic_repair_targets(last_errors, processor.template)
            allowed_rows = referenced_repair_rows(last_errors, draft, allowed_paths)
            request = {**payload, 'revision': revision, 'repair_kind': 'semantic',
                       'repair_targets': [{'path':path} for path in sorted(allowed_paths)],
                       'allowed_repair_paths': sorted(allowed_paths),
                       'allowed_repair_rows': [{'path': path, 'local_key': key} for path, key in sorted(allowed_rows)],
                       'correction_feedback': last_errors,
                       'previous_draft': draft.model_dump(mode='json')}

            def check_patch(patch):
                """patch 관련 작업을 확인한다"""
                normalized = canonical_semantic_patch(draft, patch)
                return inspect_semantic_patch(draft, normalized, allowed_paths, allowed_rows)

            patch = run_model_stage(processor.model.structured_correct, request, StructuredPatch,
                                    'structured_correct', trace=trace, budget=budget, check=check_patch)
            patch = canonical_semantic_patch(draft, patch)
            next_draft = apply_patch(draft, patch)
            if trace:
                trace('structured_initial_semantic_patch', {
                    'window_start':start, 'revision':revision, 'allowed_paths':sorted(allowed_paths),
                    'updated_paths':sorted({f.path for f in patch.upsert}),
                    'removed_paths':sorted({r.path for r in patch.remove})})
            draft = next_draft
    result.source_review_verified = True
    result.reviewed_project = True
    if source_name and source.digest not in result.state.documents:
        result.state.documents.append(source.digest)
    resolve_retried_input(result.state, text)
    ensure_public_ids(result.state)
    validate_state(processor.template, result.state)
    if trace:
        trace('finish', {'json': processor.template.export(result.state), 'saved': result.saved, 'structured_initial': True})
    return result
