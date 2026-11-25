"""원문 근거와 요구사항의 연결 정보를 관리한다.

Stable references to existing, validated source spans. No language classification."""
import json
from hashlib import sha256


def evidence_id(ref):
    """id 관련 작업을 수행한다"""
    identity = json.dumps(ref.model_dump(), sort_keys=True, ensure_ascii=False)
    return 'EV-' + sha256(identity.encode()).hexdigest()[:24]


def evidence_index(state):
    """index 관련 작업을 수행한다"""
    from core.planning_schema import state_evidence
    result = {}
    for ref in state_evidence(state):
        key = evidence_id(ref)
        if key in result and result[key] != ref:
            raise ValueError('근거 참조 충돌')
        result[key] = ref
    return result


def evidence_catalog(state):
    """Only send the stable reference and quote; full provenance stays in Python."""
    return {key: {'quote': ref.quote} for key, ref in evidence_index(state).items()}


def bind_evidence(operation, state):
    """evidence 관련 작업을 수행한다"""
    references = operation.evidence_refs
    index = evidence_index(state) if references else {}
    if len(references) != len(set(references)) or any(key not in index for key in references):
        raise ValueError('기존 근거 참조가 없거나 중복됐어')
    operation._prior_evidence = [index[key].model_copy(deep=True) for key in references]
    return operation


def operation_evidence(operation, source, start, end):
    """evidence 관련 작업을 수행한다"""
    from core.planning_logic import source_evidence
    current = source_evidence(source, start, end, operation.quotes)
    if not current:
        raise ValueError('현재 입력의 근거가 필요해')
    return current + [ref for ref in operation._prior_evidence if ref not in current]


def turn_operation_grounding(operations, before, source, start, end):
    """Verify quotes and historical references before any candidate mutation.

    New evidence is generated in Python. Historical references support context,
    never substitute for a current quote. Return only verified provenance.
    """
    if not operations:
        return [], []
    index = evidence_index(before)
    from core.planning_logic import source_evidence, Rejected
    records, issues = [], []
    for i, op in enumerate(operations):
        prefix = f'operation[{i}]'
        current = []
        try:
            current = source_evidence(source, start, end, op.quotes)
            if not current:
                issues.append(prefix + ':current_evidence_required')
        except (Rejected, ValueError):
            issues.append(prefix + ':invalid_current_quote')
        unknown = [key for key in op.evidence_refs if key not in index]
        if unknown:
            issues.append(prefix + ':unknown_prior_evidence:' + ','.join(unknown))
        records.append({'operation_index': i,
                        'current': [review_evidence(ref) for ref in current],
                        'historical_context': [review_evidence(index[key]) for key in op.evidence_refs if key in index]})
    return records, issues


def review_evidence(ref):
    """evidence 관련 작업을 수행한다"""
    return {'id': evidence_id(ref), 'quote': ref.quote, 'source': ref.source,
            'digest': ref.digest, 'start': ref.start, 'end': ref.end}


def turn_candidate_evidence(before, after, targets, source):
    """Expose the actual validated before/after proofs, including deleted values.

    Change history holds deletion/replace proofs even if a cell no longer exists.
    Keep current-input and historical evidence in separate arrays; do not invent
    proof for a value absent from the candidate.
    """
    changes = {}
    for change in after.changes[len(before.changes):]:
        changes.setdefault(change.target.key(), []).extend(change.evidence)
    def pack(refs):
        """pack 관련 작업을 수행한다"""
        return [review_evidence(ref) for ref in dict((evidence_id(r),r) for r in refs).values()]
    def is_current(ref):
        """current 관련 작업을 판별한다"""
        return (ref.digest == source.digest and ref.source == (source.name or '자연어 입력')
                and 0 <= ref.start < ref.end <= len(source.text)
                and source.text[ref.start:ref.end] == ref.quote)
    result = {}
    for key in sorted(targets):
        old, new = before.cells.get(key), after.cells.get(key)
        refs = (new.evidence if new else [])
        result[key] = {
            'before_value': old.value if old else None,
            'after_value': new.value if new else None,
            'before_evidence': pack(old.evidence if old else []),
            'after_current_evidence': pack([ref for ref in refs if is_current(ref)]),
            'after_historical_evidence': pack([ref for ref in refs if not is_current(ref)]),
            'change_evidence': pack(changes.get(key, [])),
        }
    return result
