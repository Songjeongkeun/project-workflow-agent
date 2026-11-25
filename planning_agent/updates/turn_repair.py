"""변경 계획의 대상 참조와 오류를 보정한다.

Fail-closed repair of an LLM turn plan. Independently review the patched result."""
from core.planning_schema import TurnPlan, TurnRepair



def align_repair_indices(plan: TurnPlan, patch: TurnRepair, allowed_targets=None):
    """Correct only a wrong numeric index pointing to a *unique existing operation*.

    An LLM may copy the correct path/item/action/reply_to from an existing
    operation but accidentally assign the index of an adjacent operation.
    Matching is exact, never inferred from semantics or a partial path.  The
    original patch guard still checks scope, quotes and prior evidence.
    """
    out = patch.model_copy(deep=True)
    corrections = []
    for replacement in out.replacements:
        index = replacement.operation_index
        new = replacement.operation
        identity = (new.path, new.item, new.reply_to, new.action)
        if 0 <= index < len(plan.operations):
            old = plan.operations[index]
            if (old.path, old.item, old.reply_to, old.action) == identity:
                continue
        if allowed_targets is not None and (new.path, new.item) not in allowed_targets:
            continue
        matches = [i for i,op in enumerate(plan.operations)
                   if (op.path,op.item,op.reply_to,op.action) == identity]
        if len(matches) != 1:
            continue
        corrected = matches[0]
        replacement.operation_index = corrected
        corrections.append({'supplied_index': index, 'resolved_index': corrected,
                            'path': new.path, 'item': new.item})
    return out, corrections


def patch_turn_plan(plan: TurnPlan, patch: TurnRepair, source_text: str, allowed_targets=None, existing_cells=None):
    """Only targeted corrections and additional non-destructive operations.

    The planner cannot silently change question decisions, intent, existing
    unrelated operations or source quotes. Semantic validation still follows.
    """
    if not patch.replacements and not patch.additions:
        return None, ['empty_turn_repair']
    if len(patch.replacements) > 8 or len(patch.additions) > 8:
        return None, ['turn_repair_too_many_changes']
    revised = plan.model_copy(deep=True)
    if allowed_targets is not None and not allowed_targets:
        return None, ['turn_repair_no_reviewed_error_targets']
    changed = set()
    for item in patch.replacements:
        index = item.operation_index
        if index >= len(plan.operations) or index in changed:
            return None, ['turn_repair_invalid_or_duplicate_index']
        old, new = plan.operations[index], item.operation
        if allowed_targets is not None and (old.path, old.item) not in allowed_targets:
            return None, ['turn_repair_unrelated_replacement']
        if ((new.path,new.item,new.reply_to,new.action) !=
                (old.path,old.item,old.reply_to,old.action)):
            return None, ['turn_repair_retargeted_operation']
        if new.value == old.value and new.quotes == old.quotes and new.previous_text == old.previous_text:
            return None, ['turn_repair_noop_replacement']
        if new.previous_text != old.previous_text or new.evidence_refs != old.evidence_refs:
            return None, ['turn_repair_modified_prior_evidence']
        if not new.quotes or any(not q.strip() or q not in source_text for q in new.quotes):
            return None, ['turn_repair_invalid_quote']
        revised.operations[index] = new.model_copy(deep=True)
        changed.add(index)
    existing = {(o.path,o.item,o.reply_to) for o in revised.operations}
    for op in patch.additions:
        if allowed_targets is not None and (op.path, op.item) not in allowed_targets:
            return None, ['turn_repair_unrelated_addition']
        if op.action == 'replace':
            if not existing_cells or (op.path,op.item) not in existing_cells:
                return None, ['turn_repair_replacement_without_existing_target']
            old_value = existing_cells[(op.path,op.item)]
            from core.planning_logic import replace_text_fragment, Rejected
            if (not isinstance(old_value,str) or not op.previous_text
                    or op.previous_text not in old_value or not isinstance(op.value,str)):
                return None, ['turn_repair_replacement_requires_exact_fragment']
            try:
                changed_value=replace_text_fragment(old_value,op.previous_text,op.value)
            except Rejected:
                return None, ['turn_repair_replacement_invalid_fragment']
            if changed_value == old_value:
                return None, ['turn_repair_noop_addition']
            import re
            from core.planning_logic import NUMBER
            if not set(re.findall(NUMBER,old_value)) <= set(re.findall(NUMBER,changed_value)):
                return None, ['turn_repair_removed_existing_numeric_condition']
        elif op.action not in ('record','extend') or op.previous_text is not None:
            return None, ['turn_repair_destructive_addition']
        if not op.quotes or any(not q.strip() or q not in source_text for q in op.quotes):
            return None, ['turn_repair_invalid_quote']
        if op.reply_to and op.reply_to not in {d.question_id for d in plan.question_decisions}:
            return None, ['turn_repair_unbound_reply']
        key = (op.path,op.item,op.reply_to)
        if key in existing:
            return None, ['turn_repair_duplicate_target']
        revised.operations.append(op.model_copy(deep=True))
        existing.add(key)
    return revised, []
