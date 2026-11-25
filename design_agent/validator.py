"""
화면 계획 JSON 검증
(Ex. 존재하지 않는 화면으로 이동하도록 설정되면 오류 生)
"""
from schemas import PlanningInput, ScreenPlan


def validate_screen_plan(
    planning: PlanningInput,
    screen_plan: ScreenPlan
) -> list[str]:
    """
    A01의 기획서와 A02 화면 계획의 기본 구조 및 참조 관계를 검증
    (UI 요소의 배치나 실제 Figma 레이어는 검증하지 않음)
    검증 항목:
    - 생성된 화면의 존재 여부
    - 중복 화면 ID 존재 여부
    - 화면별 관련 기능 및 UI 요소 존재 여부
    - related_features가 출시 포함 기능 ID에 해당하는지 확인
    - next_screens가 실제 존재하는 화면 ID를 참조하는지 확인
    - 출시 포함 기능에 ID가 부여되어 있는지 확인
    """
    errors = []

    feature_ids = {
        f.id
        for f in planning.release_scope.features
        if f.included_in_release is True and f.id
    }

    screen_ids = [
        s.screen_id for s in screen_plan.screens
    ]

    # 중복 화면 확인
    if len(screen_ids) != len(set(screen_ids)):
        errors.append("중복된 화면 ID가 있습니다.")

    # 화면별 검사
    for screen in screen_plan.screens:
        if not screen.related_features:
            errors.append(f"{screen.screen_id}: 관련 기능이 없습니다.")

        if not screen.elements:
            errors.append(f"{screen.screen_id}: 화면 요소가 없습니다.")

        for feature_id in screen.related_features:
            if feature_id not in feature_ids:
                errors.append(
                    f"{screen.screen_id}: "
                    f"출시 기능에 없는 ID {feature_id}"
                )

        for target in screen.next_screens:
            if target not in screen_ids:
                errors.append(
                    f"{screen.screen_id}: "
                    f"존재하지 않는 화면 {target}"
                )

        # valid_states = set(screen.states)
        # for element in screen.elements:
        #     if element.state is not None and element.state not in valid_states:
        #         errors.append(
        #             f"{screen.screen_id}: "
        #             f"요소 '{element.label}'의 state '{element.state}'가 "
        #             f"states 목록에 없습니다."
        #         )

    # ID가 부여되지 않은 출시 기능 확인
    for feature in planning.release_scope.features:
        if feature.included_in_release is True:
            if not feature.id:
                errors.append(f"출시 기능 '{feature.name}'에 ID가 없습니다.")

    # 기본 상태 존재 여부 확인
    for screen in screen_plan.screens:
        if "default" not in screen.states:
            errors.append(
                f"{screen.screen_id}: 기본 상태(default)가 없습니다."
            )

    return errors