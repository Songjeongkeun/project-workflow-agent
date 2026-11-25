"""
python -m pip install anthropic python-dotenv
anthropic: Claude API 호출


"""
import os
from dotenv import load_dotenv
# from openai import OpenAI
# from anthropic import Anthropic
import anthropic

from schemas import PlanningInput, ScreenPlan

load_dotenv() 

def generate_text(prompt: str) -> str:
    """
    Claude API에 프롬프트를 전달하고 텍스트 응답 반환
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")

    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY 환경 변수가 설정되지 않았습니다.")

    model = os.getenv(
        "ANTHROPIC_MODEL", 
        "claude-sonnet-5-5"
    )

    # Claude 클라이언트 생성
    client = anthropic.Anthropic(api_key=api_key)
    # client = anthropic.Anthropic(
    #     api_key=api_key
    #     default_headers={
    #         "anthropic-workspace-id": os.environ["ANTHROPIC_WORKSPACE_ID"]
    #     }
    # )

    # API 요청
    response = client.messages.create(
        model=model,
        max_tokens=8192,
        messages=[{
            "role": "user",
            "content": prompt
        }]
    )

    # 응답 완료 검사
    if response.stop_reason != "end_turn":
        raise RuntimeError(f"응답이 정상적으로 완료되지 않았습니다.\n{response.stop_reason}")

    # 텍스트 추출
    result = "\n".join(
        block.text for block in response.content if block.type == "text"
    )

    if not result:
        raise RuntimeError("Claude의 텍스트 응답이 비어있습니다.")

    # 토큰 사용량 출력
    print(
        f"[토큰 사용량] "
        f"입력: {response.usage.input_tokens}, "
        f"출력: {response.usage.output_tokens}"
    )

    return result


def generate_screen_plan_with_ai(planning: PlanningInput, project_id: str) -> ScreenPlan:
    """
    A01 기획서를 분석하고, Claude API를 사용하여 
    화면 계획(screen_plan.json) 생성

    """
    api_key = os.getenv("ANTHROPIC_API_KEY")

    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY가 없습니다.")

    client = anthropic.Anthropic(api_key=api_key)

    model = os.getenv(
        "ANTHROPIC_MODEL", 
        "claude-sonnet-5-5"
    )

    # 기획서 JSON을 model에 전달
    planning_json = planning.model_dump_json(indent=2, ensure_ascii=False)

    prompt = f"""
        당신은 UX/UI 전문가입니다. 아래는 기획서(요구사항 JSON)입니다.

        [프로젝트 ID]
        {project_id}
        
        [입력 요구사항] 
        {planning_json} 
        A01에서 확정한 요구사항을 분석하여 Figma 화면 생성을 위한 화면 계획을 JSON 형식으로 작성하세요.

        [규칙]
        1. 요구사항 준수
        - 입력 JSON을 화면 설계의 유일한 요구사항 기준으로 사용합니다.
        - A01에서 확정하지 않은 서비스 기능, 정책, 권한을 임의로 추가하지 않습니다.
        - release_scope에서 included_in_release가 true인 기능만 구현 범위로 고려합니다.
        - false인 기능은 화면이나 동작으로 구현하지 않습니다.
        - 값이 null이거나 누락된 정보는 사실처럼 추측하지 않습니다.

        2. 화면 구성
        - user_experience.screens에 정의된 화면을 우선 반영합니다.
        - user_experience.flow를 참고하여 전체 사용자 흐름을 설계합니다.
        - 기존 화면만으로 필수 기능을 구현하기 어려운 경우, 요구사항에 근거한 보조 화면을 추가할 수 있습니다.
        - 중복되거나 불필요한 화면을 생성하지 않습니다.
        - 화면이 필요하지 않은 서버 내부 기능은 별도의 UI 화면으로 생성하지 않아도 됩니다.

        3. 기능 및 화면 ID
        - related_features에는 release_scope에 실제 존재하는 출시 포함 기능 ID만 사용합니다.
        - 입력된 기능 ID를 변경하거나 새로 생성하지 않습니다.
        - 입력된 화면 ID가 있다면 가능한 한 유지합니다.
        - 새 화면에만 기존 ID와 중복되지 않는 SCR-NNN 형식의 화면 ID를 부여합니다.
        - 입력된 화면 ID가 null이면 SCR-001부터 순서대로 중복되지 않는 ID를 부여한다.
        - 하나의 화면에 여러 기능을 연결할 수 있습니다.
        - 하나의 기능을 여러 화면에 연결할 수도 있습니다.

        4. 화면 요소(elements)
        - 각 화면에 필요한 표시 정보, 입력 항목, 버튼 및 사용자 행동을 구체적으로 정의합니다.
        - 화면 요소는 실제 구현 가능한 UI 단위로 표현합니다.
        - 요소의 type과 label을 명확하게 작성합니다.
        - 버튼처럼 동작이 있는 요소에는 action을 작성합니다.
        - 사용자 입력 항목은 data_rules 및 user_experience.input_fields를 참고합니다.
        - 기획서에 명시된 필수 입력 항목을 누락하지 않습니다.
        - 입력 데이터의 출처가 로그인 세션 등 시스템 내부라면 사용자 입력창을 임의로 만들지 않습니다.
        - 화면 요소는 사용자가 정보를 인식하고 작업을 수행하는 순서에 맞춰 배치할 수 있도록 정렬합니다.
        - 화면 상단에는 제목과 주요 정보를, 주요 작업 영역에는 핵심 콘텐츠와 사용자 입력을 배치하도록 설계합니다.
        - 주요 행동과 보조 행동을 구분하고, 주요 행동이 시각적으로 강조되도록 구성합니다.
        - 반복되는 UI 요소는 일관된 컴포넌트 유형을 사용합니다.

        5. 화면 상태(states) 및 예외 처리
        - 각 화면에는 기본 상태(default)를 포함합니다.
        - policies.exceptions를 분석하여 관련 화면에 필요한 오류 및 예외 상태를 정의합니다.
        - 예외는 해당 동작이 발생하는 화면에 연결합니다.
        - 예외 상태에는 필요한 안내 메시지를 elements에 포함합니다.
        - 사용자에게 보여줄 안내 문구는 간결하고 이해하기 쉽게 작성합니다.
        - 메시지 작성 시 원래 정책의 의미를 변경하지 않습니다.
        - 재시도나 복구 동작은 기획서에서 허용한 경우에만 추가합니다. 동일한 예외 상태를 복사하지 않습니다.

        6. 화면 이동(next_screens)
        - 사용자 행동과 user_experience.flow를 기준으로 화면 간 이동 관계를 정의합니다.
        - next_screens에는 실제 생성한 화면 ID만 사용합니다.
        - 존재하지 않는 화면을 참조하지 않습니다.
        - 이동이 없는 화면은 빈 배열을 사용합니다.
        - 이동 대상이 불명확하면 임의의 흐름을 만들지 않습니다.

        7. Figma 연동
        - 결과는 편집 가능한 Figma 프레임을 만들기 위한 화면 계획으로 활용됩니다.
        - 각 화면의 요소는 Figma에 구현할 수 있도록 명확하게 구분합니다.
        - 아직 Figma 프레임을 생성하지 않았으므로 design_ref는 null로 설정합니다.
        - Figma 링크와 노드 ID를 임의로 생성하지 않습니다.
        - 화면 계획은 Figma 디자인을 위한 구조적 설계 자료입니다.
        - 화면별 주요 정보와 사용자 행동의 우선순위를 명확히 합니다.
        - UI 요소는 Figma에서 구현 가능한 컴포넌트 단위로 정의합니다.
        - 색상, 폰트, 상세 간격 등은 별도의 디자인 시스템이나 Figma 생성 단계에서 결정하며, 근거 없이 브랜드 규칙을 만들지 않습니다.
        - D02가 새로운 서비스 기능을 추측하지 않고 화면을 생성할 수 있도록 필수 UI 요소를 구체화합니다.

        8. 출력 검증
        - 모든 화면 ID는 고유해야 합니다.
        - 모든 related_features는 실제 입력 기능 ID와 일치해야 합니다.
        - 모든 next_screens는 존재하는 화면 ID를 참조해야 합니다.
        - 기획서에 명시된 필수 화면 요소가 누락되지 않아야 합니다.
        - 기획서에 없는 기능이 추가되지 않아야 합니다.
        - 각 화면의 elements, states, next_screens는 ScreenPlan 모델의 타입과 구조를 따라야 합니다.
        - 정보가 부족하더라도 임의로 사실을 만들어 검증을 통과시키지 않습니다.

        [출력 규칙]
        - ScreenPlan Pydantic 모델에 맞는 JSON을 생성합니다.
        - project_id는 전달받은 프로젝트 식별자를 사용합니다.
        - 설명문, Markdown 코드블록 등 JSON 외 텍스트는 출력하지 않습니다.
    """

    response = client.messages.parse(
        model=model,
        max_tokens=16384,
        messages=[{
            "role": "user",
            "content": prompt
        }], output_format=ScreenPlan
    )

    if response.stop_reason != "end_turn":
        raise RuntimeError(f"응답 중단: {response.stop_reason}")

    screen_plan = response.parsed_output

    if screen_plan is None:
        raise RuntimeError("화면 계획 파싱 실패")

    screen_plan.project_id = project_id
    screen_plan.supported_platforms = planning.release_scope.supported_platforms

    print(
        f"[Claude 토큰 사용량] "
        f"입력 {response.usage.input_tokens}, "
        f"출력 {response.usage.output_tokens}"
    )

    return screen_plan
    
    # # OpenAI API를 사용하여 화면 계획 JSON 생성
    # response = client.responses.parse(
    #     model=model,
    #     input=[
    #         {
    #             "role": "user",
    #             "content": prompt
    #         }
    #     ],
    #     text_format=ScreenPlan
    # )

    # # 응답 완료 여부 확인
    # if response.status != "completed":
    #     raise RuntimeError(
    #         f"OpenAI 응답이 완료되지 않았습니다: {response.status}"
    #     )

    # # Pydantic ScreenPlan 객체 추출
    # screen_plan = response.output_parsed

    # if screen_plan is None:
    #     raise RuntimeError("화면 계획 JSON 파싱 실패")

    # screen_plan.project_id = project_id

    # # 토큰 사용량 출력
    # if response.usage:
    #     print(
    #         f"[OpenAI 토큰 사용량] "
    #         f"입력 {response.usage.input_tokens}, "
    #         f"출력 {response.usage.output_tokens}"
    #     )

    # return screen_plan
