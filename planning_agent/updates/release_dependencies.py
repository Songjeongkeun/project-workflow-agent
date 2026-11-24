"""출시 범위 변경과 연관 기능의 의존성을 처리한다.

Conservative, evidence-bound cleanup for explicit release feature exclusions.

This module never rewrites business policy, creates a feature, or infers a replacement
user flow. All changes are pending until the normal full transition and independent
semantic review accept the whole turn.
"""
from core.planning_schema import Target, FEATURE_LINK_PATHS
from core.planning_logic import save_value, remove_item, source_evidence

FEATURE_INCLUDED = 'release_scope.features.included_in_release'
SCREEN_PARENT = 'user_experience.screens'


def reconcile_feature_exclusions(before, result, plan, source, start, end, trace=None):
    """feature exclusions 관련 작업을 조정한다"""
    state = result.state
    window = source.text[start:end]
    explicit = {}
    for op in plan.operations:
        if (op.path != FEATURE_INCLUDED or op.value is not False
                or op.action not in ('replace', 'record', 'answer') or not op.item
                or not any(token in window for token in ('제외', '빼', '삭제', '포함하지', '범위에서'))):
            continue
        rid = op.item
        if rid not in before.rows:
            continue
        old = before.cells.get(f'{FEATURE_INCLUDED}@{rid}')
        new = state.cells.get(f'{FEATURE_INCLUDED}@{rid}')
        if (old is None or old.value is not True or new is None or new.value is not False):
            continue
        feature_id = before.public_ids.get(rid)
        feature_name = before.rows[rid].label
        excluded = state.cells.get('release_scope.out_of_scope')
        if (not feature_id or not excluded or not isinstance(excluded.value, list)
                or feature_name not in excluded.value):
            continue
        explicit[feature_id] = (rid, feature_name)
    if not explicit:
        return []
    proofs = source_evidence(source,start,end,[window])
    actions = []
    for key, cell in list(state.cells.items()):
        path, sep, rid = key.partition('@')
        if path not in FEATURE_LINK_PATHS or not sep or not isinstance(cell.value,list):
            continue
        excluded_refs = [ref for ref in cell.value if ref in explicit]
        if not excluded_refs:
            continue
        retained = [ref for ref in cell.value if ref not in explicit]
        row = state.rows.get(rid)
        if not row:
            continue
        if row.path == SCREEN_PARENT and not retained:
            name_cell = state.cells.get(f'{SCREEN_PARENT}.screen_or_step@{rid}')
            if not name_cell or not isinstance(name_cell.value,str):
                continue
            remove_item(state,Target(path=f'{SCREEN_PARENT}.screen_or_step',item=rid),proofs)
            actions.append({'action':'retire_exclusive_screen','row_id':rid,'excluded_refs':excluded_refs})
            continue
        target = Target(path=path,item=rid)
        merged = list(cell.evidence)
        merged.extend(proof for proof in proofs if proof not in merged)
        save_value(state,target,cell,retained,merged,True)
        actions.append({'action':'unlink_excluded_feature','row_id':rid,'path':path,
                        'excluded_refs':excluded_refs,'retained_refs':retained})
    if trace and actions:
        trace('release_dependency_reconciled',{'features':list(explicit),'actions':actions})
    return actions


def stale_excluded_feature_summary(before, candidate):
    """Flag an *unchanged*, concretely overlapping summary after feature removal.

    This is not a keyword-based rewrite. It only asks the existing semantic
    reviewer to repair the draft; no policy or saved cell is modified here.
    Require a multi-word excerpt from the old feature behavior to avoid
    considering generic shared nouns sufficient evidence of a contradiction.
    """
    import re
    original = before.cells.get('overview.summary')
    current = candidate.cells.get('overview.summary')
    if not original or not current or original.value != current.value or not isinstance(current.value, str):
        return []
    failures = []
    summary = ' '.join(current.value.split())
    for rid, row in before.rows.items():
        if row.path != 'release_scope.features':
            continue
        key = f'{FEATURE_INCLUDED}@{rid}'
        old = before.cells.get(key)
        new = candidate.cells.get(key)
        if not old or not new or old.value is not True or new.value is not False:
            continue
        behavior = before.cells.get(f'release_scope.features.behavior_and_value@{rid}')
        if not behavior or not isinstance(behavior.value, str):
            continue
        words = re.findall(r'[^\s,.!?;:()]+', behavior.value)
        overlap = any(len(' '.join(words[i:i+2])) >= 6 and ' '.join(words[i:i+2]) in summary
                      for i in range(len(words)-1))
        if overlap:
            failures.append(f'excluded_feature_summary_stale:{before.public_ids.get(rid, rid)}')
    return failures
