"""초기 요구사항의 추출 품질을 검사한다.

Conservative source-to-JSON semantic invariants for initial ingestion.

Only check when evidence is explicit. These checks *reject* an incomplete draft;
they never infer facts or alter user requirements. Domain-independent.
"""
import re

PLATFORM_MARKERS = (
    re.compile(r"(?:웹\s*서비스|웹\s*앱|웹으로\s*제공|웹\s*기반|web\s*(?:service|app))", re.I),
    re.compile(r"(?:모바일\s*앱|스마트폰\s*앱|mobile\s*app)", re.I),
)


def audit_initial_quality(source_text, document):
    """Return actionable, source-grounded draft problems; no automatic filling.

    This gate is for new-project ingestion only. Existing project edits and
    user-specified empty values are not silently overwritten.
    """
    issues=[]
    overview=document.get('overview') or {}
    scope=document.get('release_scope') or {}
    plan=document.get('release_plan') or {}
    features=scope.get('features') or []
    if (len(source_text.strip()) >= 150 and len(features)>=2
            and not (overview.get('summary') or '').strip()):
        issues.append('initial_summary_missing:overview.summary:원문에 명시된 서비스 목적과 기능으로 개요를 요약하고 원문 구절을 인용')
    if (not scope.get('supported_platforms') and
            any(rx.search(source_text) for rx in PLATFORM_MARKERS)):
        issues.append('initial_explicit_platform_missing:release_scope.supported_platforms:원문의 명시적 제공 플랫폼을 확인하되 부정/제외 표현이면 추측하지 않기')
    checks=plan.get('pre_launch_checks') or []
    names={str(f.get('name','')).strip() for f in features if isinstance(f,dict) and f.get('name')}
    if (len(checks)>=2 and all(isinstance(c,str) and c.strip() in names for c in checks)
            and not re.search(r'(출시\s*전\s*(?:검증|테스트|점검)|배포\s*전\s*(?:검증|테스트)|pre.?launch\s*(?:check|test))',source_text,re.I)):
        issues.append('initial_feature_names_not_tests:release_plan.pre_launch_checks:기능명 복사는 출시 전 테스트 기준이 아님; 원문에 테스트가 없으면 목록을 지우고 후속 제안으로 남기기')
    return issues


def review_focus(document):
    """Provide independent LLM reviewer with exact candidate fields to inspect."""
    return {
        'overview_summary':(document.get('overview') or {}).get('summary'),
        'declared_platforms':(document.get('release_scope') or {}).get('supported_platforms'),
        'project_type':(document.get('release_scope') or {}).get('project_type'),
        'pre_launch_checks':(document.get('release_plan') or {}).get('pre_launch_checks'),
        'feature_names':[f.get('name') for f in ((document.get('release_scope') or {}).get('features') or []) if isinstance(f,dict)],
        'review_rules':[
            '명시된 서비스 개요/플랫폼은 서로 다른 대상 필드에도 일관되게 기록되었는가?',
            '출시 전 테스트는 원문에 명시된 검증 절차인가? 단순 기능 이름 복제/추측한 테스트는 제거할 것',
            '기능·예외·권한·데이터 필드의 의미적 역할과 연결이 맞는가?',
            '원문에 없는 기술·테스트·정책을 만들어낸 것은 없는가?',
        ],
    }
