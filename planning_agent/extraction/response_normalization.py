"""LLM 출력과 변경 계획의 형식을 정규화한다.

Lossless deterministic normalization of model carriers, never semantic inference."""
def unique(items):
    """unique 관련 작업을 수행한다"""
    return list(dict.fromkeys(items))


def unique_models(items):
    """models 관련 작업을 수행한다"""
    out=[]
    for item in items:
        if item not in out: out.append(item)
    return out


def normalize_model_response(value):
    """Only set-like metadata is canonicalized here. Preserve operation order,
    proposal IDs, actual values, choices and conflicting decisions. The detailed
    stage validator still rejects unknown references and scope conflicts.
    """
    from core.planning_schema import TurnPlan, TurnReview, CompletionReview, AdvisoryDraft, FEATURE_LINK_PATHS
    if isinstance(value,TurnPlan):
        result=value.model_copy(deep=True)
        for op in result.operations:
            op.quotes=unique(op.quotes)
            op.evidence_refs=unique(op.evidence_refs)
            if op.path in FEATURE_LINK_PATHS and isinstance(op.value,list):
                op.value=unique(op.value)
        result.question_decisions=unique_models(result.question_decisions)
        result.request_decisions=unique_models(result.request_decisions)
        return result
    if isinstance(value,TurnReview):
        result=value.model_copy(deep=True)
        result.reviewed_targets=unique(result.reviewed_targets)
        result.issues=unique(result.issues)
        result.coverage=unique_models(result.coverage)
        result.data_reviews=unique_models(result.data_reviews)
        result.field_reviews=unique_models(result.field_reviews)
        return result
    if isinstance(value,CompletionReview):
        result=value.model_copy(deep=True)
        result.reviewed_patch_ids=unique(result.reviewed_patch_ids)
        result.rejected_delivery_ids=unique(result.rejected_delivery_ids)
        result.conflicts=unique_models(result.conflicts)
        result.issues=unique(result.issues)
        result.delivery_updates=unique_models(result.delivery_updates)
        return result
    if isinstance(value,AdvisoryDraft):
        result=value.model_copy(deep=True)
        for exception in result.exceptions:exception.related_features=unique(exception.related_features)
        for patch in result.patches:patch.related_features=unique(patch.related_features)
        return result
    return value


MULTILINE_TEXT_FIELDS = frozenset({
    'overview.summary',
    'user_problem.target_users', 'user_problem.usage_context',
    'user_problem.current_method', 'user_problem.pain_points',
    'goals.expected_change', 'goals.metrics.measurement_method',
    'release_scope.features.behavior_and_value',
    'release_scope.tech_stack.reason',
    'release_scope.service_components_and_integrations',
    'release_scope.access_install_and_run',
    'user_experience.flow', 'user_experience.screens.displayed_information',
    'user_experience.screens.user_action',
    'policies.eligibility_and_permissions', 'policies.behavior_and_state_changes',
    'policies.exceptions.system_response', 'policies.exceptions.user_guidance',
    'policies.exceptions.recovery',
    'data_rules.fields.purpose', 'data_rules.fields.source',
    'data_rules.fields.format_and_structure',
    'data_rules.storage_and_management', 'data_rules.input_validation',
    'data_rules.error_and_integrity_handling',
    'release_plan.milestones.owner', 'release_plan.milestones.deliverable',
    'release_plan.milestones.completion_criteria',
    'release_plan.glossary_and_references.definition',
})


def normalize_initial_value(path, kind, value):
    """Return (value, conversion, error) for an initial-draft value.

    Exact types stay exact. A singleton carrier can be unwrapped/wrapped
    without guessing; multiple narrative strings are joined in their original
    order with a newline only when boundaries remain unambiguous. No splitting
    commas, numeric/Boolean coercion, approximate quotation or value inference.
    The caller handles optional empties, identity and reference validity.
    """
    if value is None:
        return None, None, None
    if kind == 'bool':
        return (value, None, None) if type(value) is bool else (value, None, 'wrong_value_type')
    if kind == 'text':
        if isinstance(value, str):
            return value, None, None
        if isinstance(value, list) and all(isinstance(s, str) and s.strip() for s in value) and value:
            if len(value) == 1:
                return value[0], 'singleton_list_to_text', None
            if path in MULTILINE_TEXT_FIELDS and all(s.splitlines() == [s] for s in value):
                return '\n'.join(value), 'ordered_lines_to_text', None
        return value, None, 'wrong_value_type'
    if kind == 'list':
        if isinstance(value, str) and value.strip():
            return [value], 'text_to_singleton_list', None
        if isinstance(value, list) and all(isinstance(s, str) and s.strip() for s in value):
            return unique(value), 'duplicate_list_items' if len(set(value)) != len(value) else None, None
        return value, None, 'wrong_value_type'
    return value, None, 'wrong_value_type'


def normalize_turn_plan(plan, template, before=None, known_questions=None):
    """Deterministic carrier normalization for interactive changes only.

    Preserve operation order and semantics. Normalize additive operations
    using the field type and the existing state, never infer new facts.
    """
    from core.planning_schema import IDENTITIES, SYSTEM_OWNED_PATHS, row_matches
    result = plan.model_copy(deep=True)
    issues, conversions = [], []
    question_lookup = {key.casefold(): key for key in (known_questions or {})}
    def normalize_item(path, item):
        """item 관련 작업을 정규화한다"""
        if before is None or item is None or path.rpartition('.')[0] not in template.rows:
            return item
        matches = row_matches(before,path.rpartition('.')[0],item)
        return next(iter(matches)) if len(matches)==1 else item
    def normalize_target(target):
        """target 관련 작업을 정규화한다"""
        if target is None:
            return None
        item=normalize_item(target.path,target.item)
        return target.model_copy(update={'item':item}) if item!=target.item else target
    for decision in result.question_decisions:
        decision.question_id=question_lookup.get(decision.question_id.casefold(),decision.question_id)
        decision.target=normalize_target(decision.target)
    result.question_decisions=unique_models(result.question_decisions)
    result.clarification_target=normalize_target(result.clarification_target)
    for i, op in enumerate(result.operations):
        prefix = f'operation[{i}]'
        op.quotes=unique(op.quotes)
        op.evidence_refs=unique(op.evidence_refs)
        if op.action == 'remove_item' and op.path in template.rows:
            old = op.path
            op.path = f'{old}.{IDENTITIES[old]}'
            conversions.append({'operation_index':i,'conversion':'collection_to_identity_path'})
        if op.reply_to is not None:
            op.reply_to=question_lookup.get(op.reply_to.casefold(),op.reply_to)
        original_item=op.item
        op.item=normalize_item(op.path,op.item)
        if op.item!=original_item:
            conversions.append({'operation_index':i,'conversion':'existing_row_reference'})
        if op.path not in template.fields or op.path in SYSTEM_OWNED_PATHS:
            issues.append(prefix+':invalid_field')
            continue
        if op.previous_text is not None and op.action != 'replace':
            issues.append(prefix+':previous_text_requires_replace')
        if op.action in ('clear','remove_item'):
            if op.value is not None:
                issues.append(prefix+':clear_or_delete_requires_null')
            continue
        kind = template.fields[op.path]
        normalized, conversion, error = normalize_initial_value(op.path,kind,op.value)
        if op.previous_text is not None and conversion == 'ordered_lines_to_text':
            error = 'ambiguous_partial_edit_value'
        if error or normalized is None or (isinstance(normalized,str) and not normalized.strip()):
            issues.append(prefix+':'+(error or 'turn_value_type_mismatch'))
            continue
        strings = [normalized] if isinstance(normalized,str) else normalized if isinstance(normalized,list) else []
        if any(not v.strip() or '\x00' in v for v in strings):
            issues.append(prefix+':invalid_turn_text_value')
            continue
        op.value = normalized
        if op.action == 'extend' and kind == 'list' and not normalized:
            issues.append(prefix+':extend_requires_nonempty_list')
        if conversion:
            conversions.append({'operation_index':i,'conversion':conversion})
        if op.action == 'extend' and op.previous_text is None and before is not None:
            from core.planning_schema import Target
            old = before.cells.get(Target(path=op.path,item=op.item).key())
            if old is None or old.value in (None, ''):
                parent, _, leaf=op.path.rpartition('.')
                if kind == 'text' and parent in template.rows and IDENTITIES.get(parent)==leaf and op.item not in before.rows:
                    op.action='record'
                    conversions.append({'operation_index':i,'conversion':'new_identity_extend_to_record'})
                if kind == 'bool':
                    op.action='record'
                    conversions.append({'operation_index':i,'conversion':'unset_bool_extend_to_record'})
            elif kind == 'bool':
                if old.value == normalized:
                    op.action='record'
                    conversions.append({'operation_index':i,'conversion':'identical_bool_extend_to_record'})
                else:
                    issues.append(prefix+':extend_bool_requires_explicit_replace')
    return result, issues, conversions


def normalize_turn_review(review, template, before, candidate, aliases=None):
    """Canonicalize only provably equivalent review carriers; never clear issues.

    A reviewer may use a public/name reference instead of the internal row key.
    A reference resolving differently in old/new states remains ambiguous.
    """
    from core.planning_schema import row_matches, FEATURE_LINK_PATHS, Target
    result=review.model_copy(deep=True)
    def canonical_item(path,item):
        """item 관련 작업을 수행한다"""
        parent=path.rpartition('.')[0]
        if item is None or parent not in template.rows:
            return item
        if item in candidate.rows and candidate.rows[item].path==parent:
            return item
        if item in before.rows and before.rows[item].path==parent:
            return item
        matches=row_matches(candidate,parent,item,aliases)|row_matches(before,parent,item)
        return next(iter(matches)) if len(matches)==1 else item
    def canonical_value(path,item,value):
        """value 관련 작업을 수행한다"""
        if path not in template.fields or value is None:
            return value
        normalized,_,error=normalize_initial_value(path,template.fields[path],value)
        if error:
            return value
        if path in FEATURE_LINK_PATHS and isinstance(normalized,list):
            mapped=[]
            for ref in normalized:
                matches=row_matches(candidate,'release_scope.features',ref,aliases)
                if len(matches)!=1 or next(iter(matches)) not in candidate.public_ids:
                    return value
                mapped.append(candidate.public_ids[next(iter(matches))])
            normalized=unique(mapped)
        return normalized
    keys=[]
    for key in result.reviewed_targets:
        path,sep,item=key.partition('@')
        if sep and item and path in template.fields and path.rpartition('.')[0] in template.rows:
            key=Target(path=path,item=canonical_item(path,item)).key()
        keys.append(key)
    result.reviewed_targets=unique(keys)
    for receipt in result.field_reviews:
        receipt.target.item=canonical_item(receipt.target.path,receipt.target.item)
        if not receipt.items and not isinstance(receipt.value,list):
            receipt.value=canonical_value(receipt.target.path,receipt.target.item,receipt.value)
        elif template.fields.get(receipt.target.path)=='text' and not receipt.items:
            receipt.value=canonical_value(receipt.target.path,receipt.target.item,receipt.value)
    for audit in result.data_reviews:
        audit.item=canonical_item('data_rules.fields.name',audit.item)
        for check in audit.checks:
            check.value=canonical_value('data_rules.fields.'+check.field,audit.item,check.value)
    result.field_reviews=unique_models(result.field_reviews)
    result.data_reviews=unique_models(result.data_reviews)
    return result


def normalize_supporting_operation(op, template, before):
    """Normalize a secondary answer write on the copied plan, or reject it.

    A secure addition must not be treated as a replacement; an anchored
    narrative paraphrase must still pass independent semantic review. Explicit
    destructive changes must be separate, unbound user-authorized operations.
    Return (issues, conversion). Unknown new references are resolved/validated
    after row declarations, never guessed here.
    """
    from core.planning_schema import Target, FEATURE_LINK_PATHS, row_matches
    from core.planning_logic import NUMBER, locate
    import re
    if op.action in ('record','answer','extend'):
        return [], None
    if op.action != 'replace':
        return ['supporting_answer_cannot_delete_or_replace'], None
    old = before.cells.get(Target(path=op.path,item=op.item).key())
    if old is None or old.value is None:
        if op.previous_text is not None:
            return ['supporting_replacement_requires_existing_value'], None
        op.action='record'
        return [], 'supporting_unset_replace_to_record'
    if op.value == old.value and (op.previous_text is None or op.previous_text == old.value):
        op.action='record'
        op.previous_text=None
        return [], 'supporting_identical_replace_to_record'
    if isinstance(old.value,str) and op.path in MULTILINE_TEXT_FIELDS and isinstance(op.value,str):
        if op.previous_text is None and op.value.startswith(old.value+'\n') and op.value[len(old.value)+1:].strip():
            op.action='extend'
            op.value=op.value[len(old.value)+1:]
            return [], 'supporting_text_prefix_to_extend'
        fragment=op.previous_text
        if not fragment or old.value.count(fragment)!=1:
            return ['supporting_replacement_requires_unique_previous_text'], None
        position=old.value.index(fragment)
        if locate(old.value,fragment)!=(position,position+len(fragment)):
            return ['supporting_replacement_requires_unique_previous_text'], None
        from core.planning_logic import replace_text_fragment, Rejected
        try:
            revised=replace_text_fragment(old.value,fragment,op.value)
        except Rejected:
            return ['supporting_replacement_invalid_fragment_scope'], None
        if not set(re.findall(NUMBER,old.value)) <= set(re.findall(NUMBER,revised)):
            return ['supporting_replacement_removes_existing_numeric_condition'], None
        return [], None
    if isinstance(old.value,list) and isinstance(op.value,list) and op.previous_text is None:
        values=op.value
        if op.path in FEATURE_LINK_PATHS:
            mapped=[]
            for ref in values:
                matches=row_matches(before,'release_scope.features',ref)
                if len(matches)>1:
                    return ['supporting_replacement_ambiguous_feature'], None
                if matches:
                    rid=next(iter(matches))
                    if rid not in before.public_ids:
                        return ['supporting_replacement_unknown_feature'], None
                    mapped.append(before.public_ids[rid])
                else:
                    mapped.append(ref)
            values=mapped
        if set(old.value)<=set(values) and values:
            op.action='extend'
            return [], 'supporting_list_superset_to_extend'
        return ['supporting_replacement_removes_existing_list_member'], None
    return ['supporting_answer_cannot_delete_or_replace'], None
