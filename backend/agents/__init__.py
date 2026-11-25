"""어떤 에이전트를 쓸지 고르는 곳.

팀원이 에이전트를 만들면 해당 줄만 바꾼다. 예:
    from agents.a01_planner import plan as real_plan
    AGENTS["plan"] = real_plan

입출력 모양은 agents/schemas.py에 있다.
"""

from agents import stubs

AGENTS = {
    "plan": stubs.plan,                    # A01 기획
    "propose_scope": stubs.propose_scope,  # A02 화면 설계 - Figma 작업 범위 제안
    "design": stubs.design,                # A02 화면 설계
    "develop": stubs.develop,              # A03 개발
    "test": stubs.test,                    # A04 테스트
    "deploy": stubs.deploy,                # A05 배포
}
