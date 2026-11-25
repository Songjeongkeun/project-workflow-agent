"""출시 범위 및 원문 의미의 모순을 검사한다.

Grounded release-scope contradiction guard for question reviews.

A feature being absent from a short, enumerated launch list is not itself an
explicit exclusion. The reviewer may ask a blocking scope question only when
there is source evidence explicitly excluding that very feature. This module
never changes the agreed release scope, feature IDs, or user facts.
"""
import re

_SCOPE_PATH = 'release_scope.features.included_in_release'
_EXCLUSION = re.compile(
    r'제외|포함\s*하지|포함\s*안\s*|범위\s*밖|지원\s*하지|\b(?:exclude|excluded|not\s+included|out\s+of\s+scope|not\s+in\s+scope)\b',
    re.I,
)
_FEATURE = re.compile(r'FEAT-[0-9]+', re.I)


def scope_target(path):
    """target 관련 작업을 수행한다"""
    return path == _SCOPE_PATH


def explicitly_excludes(feature, source_text):
    """Conservative lexical floor, not a substitute for semantic judgment.

    Explicitly excluded feature name/ID must be present in the same grounded
    source span as the exclusion wording. Merely missing from a list fails.
    """
    if not isinstance(feature, dict) or not isinstance(source_text, str):
        return False
    name = (feature.get('name') or '').strip()
    fid = (feature.get('id') or '').strip()
    text = ' '.join(source_text.split())
    for clause in re.split(r'[.!?。;\n]+', text):
        if not _EXCLUSION.search(clause):
            continue
        if (len(name) >= 3 and name.casefold() in clause.casefold()):
            return True
        if fid and re.search(r'(?<![A-Za-z0-9])'+re.escape(fid)+r'(?!\d)', clause,re.I):
            return True
    return False


def source_has_explicit_exclusion(feature, source_spans):
    """has explicit exclusion 관련 작업을 수행한다"""
    return any(explicitly_excludes(feature, span) for span in source_spans)


def review_omission_claim(review, requirements, source_spans):
    """Return a feature ID only for an unsubstantiated *list-omission* claim.

    This is narrow by design. A vague or genuinely contradictory review is
    left unresolved (fail closed). No release values are silently toggled.
    """
    if review.complete or not review.unresolved_reason:
        return None
    text = review.unresolved_reason
    if not (('included_in_release' in text or '출시' in text or 'release scope' in text.lower())
            and (re.search(r'목록.{0,80}(?:없|누락|명시되지|포함되지)', text)
                 or re.search(r'(?:not\s+(?:listed|mentioned|named)|absent\s+from\s+(?:the\s+)?list)',text,re.I))):
        return None
    features = (requirements.get('release_scope') or {}).get('features') or []
    ids = set(_FEATURE.findall(text.upper()))
    if len(ids) != 1:
        return None
    feature = next((f for f in features if str(f.get('id','')).upper() in ids),None)
    if not feature or feature.get('included_in_release') is not True:
        return None
    if source_has_explicit_exclusion(feature, source_spans):
        return None
    return feature['id']


def verified_scope_question(target, question_evidence, requirements):
    """Only a grounded explicit exclusion may ask to reverse included=True.

    Questions about an *unsettled* scope or ordinary optional target are not
    affected. This protects against spurious questions on confirmed features.
    """
    if not scope_target(target.get('path')):
        return True
    public_id = target.get('public_id')
    feature = next((f for f in (requirements.get('release_scope') or {}).get('features',[])
                    if f.get('id')==public_id),None)
    if not feature or feature.get('included_in_release') is not True:
        return True
    return source_has_explicit_exclusion(feature, question_evidence)
