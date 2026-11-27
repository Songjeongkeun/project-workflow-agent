"""
Figma 프롬프트 생성
"""
from schemas import ScreenPlan

def build_figma_prompt(screen_plan: ScreenPlan, figma_url: str, mode: str = "create") -> str:
    """
    화면 계획(screen_plan.json)을 기반으로
    Codex에 전달할 Figma 작업 프롬프트 생성
    Codex가 수행해야 할 화면 생성, 수정 및 검증 규칙을 구성
    """
    if mode not in {"create", "update"}:
        raise ValueError(f"지원하지 않는 작업 모드: {mode}")

    plan_json = screen_plan.model_dump_json(indent=2)
    operation = (
        "최초 디자인 생성: 기존에 같은 screen_id의 프레임이 있으면 재사용하고, 없으면 생성합니다."
        if mode == "create"
        else "기존 디자인 수정: design_ref가 있는 화면은 해당 node_id를 수정하고, 없는 화면만 새로 생성합니다."
    )

    return f"""
    당신은 Figma UI 디자인 전문가입니다. 
    당신은 Codex와 연결된 Figma MCP를 사용해
    지정된 Figma 파일에 실제 프레임을 생성해야 합니다.
    설명만 하지 말고 실제로 Figma MCP 도구를 사용하세요.
    아래 화면 계획을 바탕으로 Figma Design에 편집 가능한 UI 프레임을 생성하세요.

    [작업]
    {operation}

    [작업 대상 파일]
    {figma_url}

    [화면 계획 JSON]
    {plan_json}

    [필수 규칙]
    1. MCP 연결, 대상 Figma Design 파일의 존재, 쓰기 권한을 확인합니다.
    2. 지정된 파일 안에서만 작업합니다. 새 Design 파일을 만들지 않습니다.
    3. 각 screen_id에 대해 기존 design_ref의 node_id가 있으면 그 프레임을 찾아 수정합니다.
    4. design_ref가 없더라도 같은 screen_id의 프레임이 이미 있으면 중복 생성하지 않고 재사용합니다.
    5. 존재하지 않는 화면만 새 프레임을 생성합니다. 다른 화면이나 레이어를 임의 삭제하지 않습니다.
    6. 프레임 이름은 'SCR-001 예약 상세'처럼 screen_id와 화면명을 포함합니다.
    7. elements, states, next_screens 및 관련 예외 상태를 반영합니다.
    8. 텍스트와 UI 요소는 편집 가능한 Figma 레이어로 만듭니다.
    9. 화면 상태는 구분되게 표현하고, 가능하다면 프로토타입 연결도 적용합니다.
    10. 모든 화면의 실제 프레임을 조회해 screen_id, 필수 요소, node_id를 검증합니다.
    11. 실제 조회한 Figma 프레임 URL과 node_id만 반환합니다. URL과 ID를 추측하지 않습니다.
    12. 실패했거나 일부 화면만 처리했다면 status='failed'로 반환하고 이유를 설명합니다.
    13. 각 프레임 URL은 대상 파일의 URL이어야 하며 node-id 쿼리와 node_id가 일치해야 합니다.

    [최종 출력]
    Codex에 전달된 구조화 출력 JSON Schema를 정확히 따릅니다.
    status='success'일 때 screens에는 화면 계획의 모든 screen_id가 정확히 한 번씩 포함되어야 합니다.
    """.strip()
