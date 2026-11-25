"""어떤 에이전트를 쓸지 고르는 곳.

팀원이 에이전트를 만들면 해당 줄만 바꾼다. A01은 아래처럼 설정으로 고른다 (맨 아래 참고).

입출력 모양은 agents/schemas.py에 있다.
"""

import config
from agents import stubs

AGENTS = {
    "plan": stubs.plan,                    # A01 기획
    "propose_scope": stubs.propose_scope,  # A02 화면 설계 - Figma 작업 범위 제안
    "design": stubs.design,                # A02 화면 설계
    "develop": stubs.develop,              # A03 개발
    "test": stubs.test,                    # A04 테스트
    "deploy": stubs.deploy,                # A05 배포
}

# A01을 실제 planning_agent로 (.env: ORBIT_A01_AGENT=planning_agent, OPENAI_API_KEY 필요)
# 설정하지 않으면 임시 에이전트를 쓴다. 테스트와 개발 PC가 모르게 OpenAI를 부르지 않게 하려는 것.
if config.A01_AGENT == "planning_agent":
    from agents import a01_planner

    AGENTS["plan"] = a01_planner.plan
