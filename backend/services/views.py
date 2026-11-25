"""화면에 돌려줄 모양을 만든다. 여러 API가 같이 쓰는 것만 둔다 (실행 상태, 프로젝트 상태)."""

from db import fetch_all, fetch_one
from services import artifacts, controller, members, planning

AGENTS = ["A01", "A02", "A03", "A04", "A05"]
AGENT_LABEL = {"A01": "기획", "A02": "화면 설계", "A03": "개발", "A04": "테스트", "A05": "배포"}


def latest_run(conn, project_id: int) -> dict | None:
    return fetch_one(conn, "select * from runs where project_id = ? order by id desc limit 1", (project_id,))


def project_state(conn, project: dict) -> dict:
    """프로젝트 카드와 위쪽 표시줄의 '현재 단계'와 '실행 상태'."""
    run = latest_run(conn, project["id"])
    if run is not None:
        step = controller.STEP_AGENT.get(run["current_step"] or "", "A05" if run["status"] == "done" else None)
        return {"run_id": run["id"], "status": run["status"], "current_step": step}

    if planning.processing(conn, project["id"]):
        status = "running"
    elif planning.open_questions(conn, project["id"], required_only=True):
        status = "waiting_input"
    elif artifacts.latest(conn, project["id"], "planning") is not None:
        status = "waiting_approval"  # G1 확정 대기
    else:
        status = "waiting_input"  # 아직 설명이 없다
    return {"run_id": None, "status": status, "current_step": "A01"}


def pending_for(conn, project_id: int, user_id: int) -> int:
    """이 프로젝트에서 내가 결정할 대기 중 승인 수."""
    roles = members.roles_of(conn, project_id, user_id)
    if not roles:
        return 0
    marks = ", ".join("?" for _ in roles)
    row = fetch_one(
        conn,
        f"select count(*) as n from approval_requests where project_id = ? and status = 'pending' and role in ({marks})",
        (project_id, *roles),
    )
    return row["n"]


def run_view(conn, project: dict, user_id: int) -> dict:
    """GET /api/projects/{pid}/run (07 4.5)."""
    roles = members.roles_of(conn, project["id"], user_id)
    run = latest_run(conn, project["id"])
    if run is None:
        state = project_state(conn, project)
        return {
            "run_id": None,
            "status": state["status"],
            "current_step": "A01",
            "steps": [{"agent": a, "label": AGENT_LABEL[a], "status": "running" if a == "A01" else "pending"} for a in AGENTS],
            "waiting": None, "input": None, "retry": None, "failure": None, "deployment_url": None,
            "post_results": None, "pre_results": None, "deployment": None, "plan_version": None,
            "change_request_id": None, "replaced_by": None,
        }

    plan = controller.get_plan(conn, run["plan_id"])
    current = controller.STEP_AGENT.get(run["current_step"] or "")
    step_rows = fetch_all(conn, "select * from run_steps where run_id = ? order by id", (run["id"],))
    last_by_agent = {st["agent"]: st for st in step_rows}
    order = controller.sequence(plan["steps"])
    cur_idx = order.index(run["current_step"]) if run["current_step"] in order else len(order)

    steps = [{
        "agent": "A01", "label": AGENT_LABEL["A01"], "status": "completed",
        "summary": f"기획 묶음 v{plan['input_versions']['planning']['version']} 확정",
        "links": [{"label": "기획서", "url": f"/projects/{project['id']}/planning"}],
    }]
    for a in AGENTS[1:]:
        st = last_by_agent.get(a)
        if a == "A02" and "A02" not in plan["steps"]:
            status = "skipped"
        elif run["status"] == "done":
            status = "completed"
        elif a == current:
            status = {"running": "running", "waiting_input": "waiting", "waiting_approval": "waiting",
                      "failed": "failed", "void": "void"}.get(run["status"], "running")
        else:
            first = next((i for i, step in enumerate(order) if controller.STEP_AGENT.get(step) == a), len(order))
            status = "completed" if first < cur_idx and st is not None else "pending"
        item = {"agent": a, "label": AGENT_LABEL[a], "status": status}
        if st is not None:
            item["summary"], item["links"] = st["summary"], st["links"] or []
        steps.append(item)

    waiting = None
    if run["status"] == "waiting_approval":
        req = fetch_one(
            conn,
            "select * from approval_requests where run_id = ? and status = 'pending' order by id desc limit 1",
            (run["id"],),
        )
        if req is not None:
            waiting = {"gate": req["gate"], "role": req["role"], "approval_id": req["id"],
                       "requested_at": req["created_at"], "can_decide": req["role"] in roles}

    inp = None
    if run["status"] == "waiting_input" and run["waiting_input"]:
        inp = {**run["waiting_input"], "can_confirm": "designer" in roles}

    failure = None
    if run["status"] == "failed" and run["failure"]:
        failure = {**run["failure"], "can_retry": run["failure"].get("role") in roles}

    post, pre, deployment = None, None, None
    if run["status"] == "done":
        post = fetch_all(conn, "select ac_id, result from test_results where run_id = ? and phase = 'post'", (run["id"],))
        pre = fetch_all(conn, "select ac_id, result from test_results where run_id = ? and phase = 'pre'", (run["id"],))
        # 화면의 "배포 ID · 커밋 · 완료 시각" 표
        deployment = {"id": run["deployment_id"], "sha": run["pushed_sha"], "finished_at": run["finished_at"]}

    replaced_by = None
    if run["status"] == "void":
        newer = fetch_one(conn, "select id from runs where project_id = ? and id > ? order by id desc limit 1", (project["id"], run["id"]))
        replaced_by = newer["id"] if newer else None

    return {
        "run_id": run["id"],
        "status": run["status"],
        "current_step": current,
        "steps": steps,
        "waiting": waiting,
        "input": inp,
        "retry": {"count": run["retry_count"], "limit": plan["limits"].get("fix_retry", 5)},
        "failure": failure,
        "deployment_url": run["deployment_url"] if run["status"] == "done" else None,
        "post_results": post,
        "pre_results": pre,
        "deployment": deployment,
        "plan_version": plan["input_versions"]["planning"]["version"],
        "change_request_id": run["change_request_id"],
        "replaced_by": replaced_by,
    }
