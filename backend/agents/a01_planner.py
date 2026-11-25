"""A01 기획: 저장소 최상위의 planning_agent를 Orbit의 plan() 약속(agents/schemas.py)에 맞춰 부른다 (최소판).

planning_agent는 입력을 하나씩 받고 자기 상태를 파일에 저장하는 에이전트다.
Orbit은 매번 대화 전체를 넘기므로, 지난번까지 넘긴 메시지 다음 것만 turn()으로 넘긴다.
상태는 data/planning_agent/projects/p{프로젝트 id}.json 에 쌓인다.
"""

import importlib.util
import json
import sys
from pathlib import Path

import config

AGENT_DIR = Path(__file__).resolve().parents[2] / "planning_agent"
STATE_ROOT = config.DATA_DIR / "planning_agent"

# 에이전트 코드는 planning_agent 폴더를 import 시작점으로 쓴다 (from core... import).
# 뒤에 붙여서 이름이 같으면 Orbit 모듈이 먼저 잡히게 한다.
sys.path.append(str(AGENT_DIR))
# 에이전트 main.py는 Orbit main.py와 이름이 같아 다른 이름으로 불러온다
_spec = importlib.util.spec_from_file_location("planning_agent_main", AGENT_DIR / "main.py")
agent = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(agent)
TEMPLATE = agent.Template(AGENT_DIR / "templates" / "planning-template.json")


def plan(inp: dict) -> dict:
    pid = inp["project_id"]
    project = agent.Project(STATE_ROOT, f"p{pid}", TEMPLATE, agent.build_processor(TEMPLATE))

    # 지난번까지 넘긴 메시지 다음 것만 넘긴다
    cursor = STATE_ROOT / "projects" / f"p{pid}.orbit.json"
    last = json.loads(cursor.read_text(encoding="utf-8"))["last"] if cursor.exists() else 0
    new = [m for m in inp["messages"] if m["sender"] == "user" and m["id"] > last]
    texts = [m["body"] for m in new] + ([inp["change_request"]] if inp.get("change_request") else [])
    if not texts:
        raise Exception("새로 반영할 메시지가 없습니다.")

    reply = ""
    for text in texts:
        reply = project.turn(text)  # 실패하면 예외 → Orbit이 실패로 기록
    if new:
        cursor.write_text(json.dumps({"last": new[-1]["id"]}), encoding="utf-8")

    state = project.read()
    planning = TEMPLATE.export(state)
    return {
        "planning": planning,
        # 기능 안의 done_criteria(문장 목록이라고 가정) → Orbit 완료 조건. ID는 AC-<기능번호>-<순번>
        "criteria": [
            {"id": f"AC-{f['id'].split('-')[-1]}-{j:02d}", "feature_id": f["id"], "kind": "정상",
             "condition": str(c), "source": f"release_scope.features[{i}].done_criteria",
             "origin": "명시", "required": True}
            for i, f in enumerate(planning["release_scope"]["features"]) if f.get("id")
            for j, c in enumerate(f.get("done_criteria") or [], 1)
        ],
        # '[필수확인:'으로 시작하거나 값이 충돌한 질문은 필수 (답하기 전에는 G1 확정 불가)
        "questions": [
            {"body": agent.question_text(state, q), "field_path": q.target.path if q.target else None,
             "required": q.kind == "conflict" or (q.text or "").startswith("[필수확인:")}
            for q in state.questions
        ],
        "evidence": [],  # 나중에: 근거 → 메시지 번호 연결
        "brief_markdown": "```json\n" + json.dumps(planning, ensure_ascii=False, indent=2) + "\n```",  # 나중에: 기획서 본문
        "reply": reply,
        "has_ui": bool(planning.get("user_experience", {}).get("screens")),
    }
