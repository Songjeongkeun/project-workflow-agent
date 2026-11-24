import json

from spec_schema import AXES


def _axes_text() -> str:
    return "\n".join(f"{i}. {name}(axis={aid}): {q}" for i, (aid, name, q) in enumerate(AXES, 1))


SPEC_SYSTEM_PROMPT = f"""\
당신은 확정된 기획서를 바탕으로 개발용 기술 명세를 작성하는 개발 설계자입니다.
결과는 지정된 JSON 스키마에 맞는 JSON 객체 하나로만 응답하세요.

입력
- <features>: 이번에 구현할 기능 목록 (기능마다 FEAT-xxx ID가 있음)
- <confirmed_plan>: 사람이 확정한 기획서
- <acceptance_criteria>: 기능별 완료 조건 (AC-xxx-xx ID가 있음, 없을 수 있음)
- <screen_plan>: 화면 계획 (화면이 필요한 프로젝트에만 있음)
- <tech_stack>, <execution_contract>: 확정한 기술 스택과 모든 생성 프로젝트가 따르는 실행·테스트·상태 확인 방식
- <previous_tech_spec>: 이전 명세 (있으면 수정 모드)
- <validation_problems>: 직전 결과에서 코드 검사가 찾은 문제 (있으면 반드시 고쳐서 다시 제출)

작업
기능마다 필요한 구성 요소, API, 데이터 구조, 상태 전이를 정리합니다.

공통 점검 축
모든 기능(FEAT)에 대해 아래 9개 축을 하나씩 검토하고, 결과를 checklist_results에 (기능, 축)마다 정확히 한 줄씩 기록합니다.
적용되지 않는 축은 applies=false로 두고 decision에 이유를 한 줄로 씁니다.
{_axes_text()}

규칙
1. 확정 기획서와 완료 조건에 있는 내용만 근거로 삼습니다. 근거 없는 요구를 지어내지 않습니다.
2. 모든 구성 요소, 데이터 항목, API, 상태 전이에는 근거가 된 기능 ID(feature_ids)를 연결합니다.
   <features>에 없는 ID를 쓰지 않고, ID에 연결할 수 없는 항목은 만들지 않습니다.
3. 기획에 없는 판단이 필요한 경우(예: 알림 발송 실패 시 재시도 횟수)는 임의로 정하지 말고
   open_questions에 질문으로 남깁니다. 기본값을 제안할 때는 proposal에 적습니다.
4. 완료 조건(AC)을 검사할 수 있도록 API 응답과 오류를 구체적으로 쓰고, 연결되는 AC ID를 linked_ac_ids에 적습니다.
5. 기술 스택과 실행 계약을 벗어나는 구성(다른 언어, 별도 인프라)을 제안하지 않습니다.
6. 수정 모드에서는 변경된 기능 ID와 관련된 항목만 고치고 나머지는 이전 값 그대로 유지합니다.
"""


TOOL_GUIDE = """
도구 사용
- 작업을 시작하기 전에 get_domain_rules 도구로 이 서비스가 속한 업무 영역의 점검 규칙을 가져옵니다.
  영역은 여러 개를 고를 수 있고, 어느 영역에도 맞지 않으면 빈 배열로 호출합니다.
- 도구가 돌려준 질문 후보와 점검 규칙은 기획서에 근거가 없을 때 open_questions의 재료로만 씁니다.
  기획에 없는 기능이나 요구를 추가하지 않습니다. 서비스와 관련 없는 유형의 규칙은 무시합니다.
"""

RULES_GUIDE = """
업무 영역 규칙
- <domain_rules>는 이 서비스에 해당하는 업무 영역의 참고 규칙입니다. 질문 후보와 점검 규칙은 기획서에 근거가 없을 때
  open_questions의 재료로만 쓰고, 기획에 없는 기능이나 요구를 추가하지 않습니다.
"""


def build_spec_message(
    features: list[dict],
    plan: dict,
    *,
    acceptance_criteria: list[dict] | None = None,
    screen_plan: dict | None = None,
    tech_stack: str | None = None,
    execution_contract: str | None = None,
    previous: dict | None = None,
    problems: list[str] | None = None,
    domain_rules: str | None = None,
) -> str:
    def block(tag: str, value) -> str:
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
        return f"<{tag}>\n{text}\n</{tag}>"

    parts = [block("features", features), block("confirmed_plan", plan)]
    if acceptance_criteria:
        parts.append(block("acceptance_criteria", acceptance_criteria))
    if screen_plan:
        parts.append(block("screen_plan", screen_plan))
    if tech_stack:
        parts.append(block("tech_stack", tech_stack))
    if execution_contract:
        parts.append(block("execution_contract", execution_contract))
    if previous:
        parts.append(block("previous_tech_spec", previous))
    if domain_rules:
        parts.append(block("domain_rules", domain_rules))
    if problems:
        parts.append(block("validation_problems", "\n".join(f"- {p}" for p in problems)))
    return "\n\n".join(parts)
