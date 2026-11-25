"""요구사항 필드별 근거와 변경 누락을 점검한다.

Deterministic review inventories/receipts. No language or domain classification."""
from core.planning_schema import Target, FEATURE_LINK_PATHS, row_matches

DELIVERY_FIELDS=('release_scope.project_type','release_scope.supported_platforms','release_scope.access_install_and_run')


def assess_platform(processor,text,before,budget,trace):
    """Independent source-only meaning judgment; never infer platforms in Python."""
    if not processor.require_quality_audits or (processor.review_platform is None and not processor.require_detailed_receipts):
        return None
    from core.planning_schema import PlatformAssessment
    from planning_graph import run_model_stage
    from planning_agent import ModelError
    from core.planning_logic import locate
    if processor.review_platform is None:
        raise ModelError('플랫폼 의미 검토 모델이 없어.',kind='candidate_validation',issues=['platform_reviewer_missing'])
    evidence=[ref.quote for c in before.cells.values() for ref in c.evidence]
    def check(verdict):
        """check 관련 작업을 확인한다"""
        issues=[]
        if not verdict.reason.strip():issues.append('platform_missing_reason')
        if len(verdict.platforms)!=len(set(verdict.platforms)) or any(not p.strip() for p in verdict.platforms):
            issues.append('platform_invalid_values')
        if any(not q.strip() or not (locate(text,q) is not None or any(locate(ref,q) is not None for ref in evidence)) for q in verdict.quotes):
            issues.append('platform_unverified_quote')
        if verdict.status=='stated' and (not verdict.platforms or not verdict.quotes):issues.append('platform_stated_without_value')
        if verdict.status!='stated' and verdict.platforms:issues.append('platform_unknown_has_value')
        if verdict.status=='uncertain' and not verdict.quotes:issues.append('platform_uncertainty_without_basis')
        return issues
    result=run_model_stage(processor.review_platform,{'source_text':text,'existing_source_quotes':list(dict.fromkeys(evidence))},
        PlatformAssessment,'source_platform',trace=trace,budget=budget,check=check)
    if trace:trace('source_platform_assessment',{'response':result.model_dump(mode='json')})
    return result


def platform_consistency(verdict,candidate):
    """consistency 관련 작업을 수행한다"""
    if verdict is None:return []
    cell=candidate.cells.get('release_scope.supported_platforms')
    value=cell.value if cell else None
    expected=verdict.platforms if verdict.status=='stated' else None
    if isinstance(value,list) and isinstance(expected,list):
        if len(value)!=len(set(value)) or len(expected)!=len(set(expected)):
            return ['platform_source_candidate_mismatch']
        return [] if set(value)==set(expected) else ['platform_source_candidate_mismatch']
    return [] if value==expected or expected is None and value==[] else ['platform_source_candidate_mismatch']


def field_review_inventory(template,before,baseline,*,full=False,include_delivery=False):
    """review inventory 관련 작업을 수행한다"""
    selected={}
    if include_delivery:
        for path in DELIVERY_FIELDS:selected[Target(path=path).key()]=Target(path=path)
    for key in set(before.cells)|set(baseline.cells):
        path,sep,item=key.partition('@')
        if template.fields.get(path)!='list' or path in FEATURE_LINK_PATHS:continue
        if full or before.cells.get(key)!=baseline.cells.get(key):
            selected[key]=Target(path=path,item=item if sep else None)
    return [dict(target=t.model_dump(),kind=template.fields[t.path],
        input_value=baseline.cells[key].value if key in baseline.cells else None,
        evidence_quotes=list(dict.fromkeys(ref.quote for ref in baseline.cells[key].evidence)) if key in baseline.cells else [])
        for key,t in sorted(selected.items())]


def field_review_request(template,before,baseline,candidate=None,*,full=False,include_delivery=False,include_proposed=False):
    """review request 관련 작업을 수행한다"""
    inventory=field_review_inventory(template,before,baseline,full=full,include_delivery=include_delivery)
    known={Target(**row['target']).key() for row in inventory}
    if candidate is not None:
        for row in field_review_inventory(template,before,candidate,full=full,include_delivery=include_delivery):
            key=Target(**row['target']).key()
            if key not in known:
                inventory.append({**row,'input_value':baseline.cells[key].value if key in baseline.cells else None})
                known.add(key)
    if include_proposed and candidate is not None:
        for row in inventory:
            key=Target(**row['target']).key();proposed=candidate.cells[key].value if key in candidate.cells else None
            row['candidate_value']=proposed
            if row['kind']=='list':
                old=row['input_value'] if isinstance(row['input_value'],list) else []
                row['input_value']=list(dict.fromkeys([*old,*(proposed if isinstance(proposed,list) else [])]))
    return inventory


def optional_turn_field_inventory(template,before,candidate,targets):
    """Additional actual changed targets are permissible optional receipts.
    The standard required inventory is not expanded by this helper.
    """
    rows=[]
    for key in sorted(set(targets)):
        path,sep,item=key.partition('@')
        if path not in template.fields or before.cells.get(key)==candidate.cells.get(key):
            continue
        old,new=before.cells.get(key),candidate.cells.get(key)
        initial=old.value if old else None
        proposed=new.value if new else None
        if template.fields[path]=='list':
            initial=list(dict.fromkeys([*(initial if isinstance(initial,list) else []),
                                        *(proposed if isinstance(proposed,list) else [])]))
        rows.append({'target':Target(path=path,item=item if sep else None).model_dump(),
                     'kind':template.fields[path],'input_value':initial,'candidate_value':proposed,
                     'evidence_quotes':list(dict.fromkeys(ref.quote for ref in (old.evidence if old else [])))})
    return rows


def field_review_issues(review,text,template,before,baseline,candidate,aliases=None,*,full=False,include_delivery=False,include_proposed=False,required=False,sparse=False,optional_turn_targets=()):
    """Check submitted verdicts, indices and quotes against real preview values."""
    if not required and not review.field_reviews:return []
    from core.planning_logic import locate,review_segments
    inventory=field_review_request(template,before,baseline,candidate,full=full,include_delivery=include_delivery,include_proposed=include_proposed)
    expected={Target(**row['target']).key():row for row in inventory}
    required_keys=set(expected)
    for row in optional_turn_field_inventory(template,before,candidate,optional_turn_targets):
        expected.setdefault(Target(**row['target']).key(),row)
    segments=review_segments(text);seen=set();issues=[]
    evidence=[ref for state in (before,baseline,candidate) for c in state.cells.values() for ref in c.evidence]
    for receipt in review.field_reviews:
        target=Target(path=receipt.target.path,item=receipt.target.item)
        parent=target.path.rpartition('.')[0]
        if parent in template.rows:
            matches=row_matches(candidate,parent,target.item or '',aliases)
            if not matches:matches=row_matches(baseline,parent,target.item or '',aliases)
            if len(matches)!=1:
                issues.append('field_review_unknown_row:'+target.key());continue
            target=Target(path=target.path,item=next(iter(matches)))
        elif target.item is not None:
            issues.append('field_review_scalar_item:'+target.key());continue
        key=target.key()
        if key not in expected or key in seen:
            issues.append('field_review_unrequested_or_duplicate:'+key);continue
        seen.add(key);cell=candidate.cells.get(key);actual=cell.value if cell else None
        if not receipt.reason.strip():issues.append('field_review_missing_reason:'+key)
        if any(not 0<=i<len(segments) for i in receipt.source_refs):
            issues.append('field_review_invalid_source_ref:'+key);continue
        quotes=[*receipt.quotes,*(segments[i] for i in receipt.source_refs)]
        allowed_evidence=evidence
        if key not in required_keys:
            allowed_evidence=[ref for state in (before,baseline,candidate)
                              for c in [state.cells.get(key)] if c for ref in c.evidence]
        valid_quotes=all(q.strip() and (locate(text,q) is not None or any(
            ref.start>=0 and ref.end-ref.start==len(ref.quote) and locate(ref.quote,q) is not None for ref in allowed_evidence)) for q in quotes)
        if not valid_quotes:issues.append('field_review_invalid_quote:'+key)
        if type(receipt.value) is not type(actual) or receipt.value!=actual:
            issues.append('field_review_value_mismatch:'+key)
        if receipt.status=='stated':
            if actual is None or not cell.evidence or not quotes:issues.append('field_review_stated_without_evidence:'+key)
        elif receipt.status=='not_stated':
            if actual is not None or receipt.value is not None or quotes:issues.append('field_review_absence_conflict:'+key)
        elif receipt.status=='uncertain':
            if actual is not None or receipt.value is not None or not quotes:issues.append('field_review_uncertain_has_value:'+key)
        elif receipt.status=='cleared':
            if actual is not None or key not in before.cells or not quotes or any(locate(text,q) is None for q in quotes):
                issues.append('field_review_invalid_clear:'+key)
        inputs=expected[key]['input_value'];inputs=inputs if isinstance(inputs,list) else []
        output=actual if isinstance(actual,list) else []
        indices=set()
        if expected[key]['kind']!='list' and receipt.items:issues.append('field_review_nonlist_items:'+key)
        for item in receipt.items:
            if item.input_index in indices or not 0<=item.input_index<len(inputs):issues.append('field_review_input_index:'+key)
            indices.add(item.input_index)
            if not item.reason.strip() or len(item.output_indices)!=len(set(item.output_indices)) or any(not 0<=i<len(output) for i in item.output_indices):
                issues.append('field_review_item_mapping:'+key)
            if item.status=='preserved' and not item.output_indices:issues.append('field_review_lost_item:'+key)
            if item.status!='preserved' and item.output_indices:issues.append('field_review_removed_has_outputs:'+key)
            if item.status=='unsupported' and key in before.cells and isinstance(before.cells[key].value,list) and 0<=item.input_index<len(inputs) and inputs[item.input_index] in before.cells[key].value:
                issues.append('field_review_protected_item:'+key)
            if item.status=='removed' and (not quotes or any(locate(text,q) is None for q in quotes)):
                issues.append('field_review_removal_needs_current_evidence:'+key)
        if indices!=set(range(len(inputs))):issues.append('field_review_missing_input_items:'+key)
        if include_proposed and output and {i for item in receipt.items for i in item.output_indices}!=set(range(len(output))):
            issues.append('field_review_unaccounted_outputs:'+key)
    if (required or not sparse) and not required_keys.issubset(seen):issues.append('field_review_targets_missing:'+str(sorted(required_keys-seen)))
    return list(dict.fromkeys(issues))
