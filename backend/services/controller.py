"""실행 제어기 (LangGraph). G1 확정 뒤 실행 계획에 따라 에이전트를 부르고 승인 지점에서 멈춘다.

단계 순서 (화면이 있는 프로젝트):
    A02_scope(범위 확인 대기) → A02 → G2 → A03 → G3(계획 밖 변경이 있을 때만) → A04 → G4 → A05 → A04_post → 완료

- 단계 하나가 그래프의 노드 하나다. 노드는 일을 마치면 다음 단계(state["step"])를 정하고,
  승인·입력 대기, 실패, 완료면 state["stop"]에 이유를 넣어 그래프를 끝낸다.
- 지금 단계는 DB의 runs.current_step에 저장한다. 그래서 승인 버튼을 누르면 그 단계부터 그래프를 다시 실행하면 된다.
- 에이전트를 부르는 동안에는 DB 연결을 열어 두지 않는다. 결과를 쓰기 전에 실행이 그사이 무효가 됐는지 다시 확인한다.
"""

import json
import logging
from typing import TypedDict

from fastapi import HTTPException
from langgraph.graph import END, START, StateGraph

import config
from agents import AGENTS
from db import fetch_all, fetch_one, get_connection, insert, now, update, update_if, write_lock
from services import approvals, artifacts, members, notify
from workspace_server import gitops
from workspace_server.guards import workspace_dir

log = logging.getLogger(__name__)

# 단계 → 담당 에이전트 (실행 상태 화면에 보일 이름)
STEP_AGENT = {
    "A02_scope": "A02", "A02": "A02", "G2": "A02",
    "A03": "A03", "G3": "A03",
    "A04": "A04", "G4": "A04",
    "A05": "A05", "A04_post": "A05",
}
# 에이전트 → 실패했을 때 [다시 시도]를 누를 역할
STEP_ROLE = {"A01": "planner", "A02": "designer", "A03": "developer", "A04": "developer", "A05": "deployer"}
GATE_STEPS = ("G2", "G3", "G4")
ACTIVE = ("running", "waiting_input", "waiting_approval", "failed")


# ── 조회 ──────────────────────────────────────────────────


def get_run(conn, run_id: int) -> dict | None:
    return fetch_one(conn, "select * from runs where id = ?", (run_id,))


def get_plan(conn, plan_id: int) -> dict:
    return fetch_one(conn, "select * from run_plans where id = ?", (plan_id,))


def active_run(conn, project_id: int) -> dict | None:
    marks = ", ".join("?" for _ in ACTIVE)
    return fetch_one(
        conn,
        f"select * from runs where project_id = ? and status in ({marks}) order by id desc limit 1",
        (project_id, *ACTIVE),
    )


def sequence(steps: list[str]) -> list[str]:
    """실행 계획의 에이전트 목록으로 단계 순서를 만든다."""
    seq = []
    if "A02" in steps:
        seq += ["A02_scope", "A02", "G2"]
    if "A03" in steps:
        seq += ["A03", "G3"]
    if "A04" in steps:
        seq += ["A04"]
    return seq + ["G4", "A05", "A04_post"]


def next_step(plan: dict, step: str) -> str | None:
    seq = sequence(plan["steps"])
    i = seq.index(step)
    return seq[i + 1] if i + 1 < len(seq) else None


# ── 실행 만들기 ───────────────────────────────────────────


def default_target(project_id: int) -> dict:
    """실행 계획에 적을 게시·배포 대상. G4에서 배포 담당이 SHA와 함께 이것을 승인한다."""
    return {
        "repository": config.DEPLOY_REPOSITORY,
        "branch": config.DEPLOY_BRANCH.format(project_id=project_id),
        "environment": config.DEPLOY_ENVIRONMENT,
        "url": f"http://{config.DEPLOY_HOST}:{config.DEPLOY_PORT_BASE + project_id}",
    }


def start_run(conn, project: dict, planning: dict, steps: list[str] | None = None, change_request_id: int | None = None) -> int:
    """실행 계획과 실행을 만든다. 진행 중인 실행이 있으면 409."""
    if active_run(conn, project["id"]) is not None:
        raise HTTPException(status_code=409, detail="이 프로젝트에 진행 중인 실행이 있습니다.")
    if steps is None:
        steps = (["A02"] if project["has_ui"] else []) + ["A03", "A04", "A05"]

    prev = fetch_one(conn, "select version from run_plans where project_id = ? order by version desc limit 1", (project["id"],))
    gates = {"G3": "developer", "G4": "deployer"}
    if "A02" in steps:
        gates = {"G2": "designer", **gates}
    plan_id = insert(
        conn, "run_plans",
        project_id=project["id"], version=prev["version"] + 1 if prev else 1, steps=steps,
        input_versions={"planning": {"id": planning["id"], "version": planning["version"], "hash": planning["content_hash"]}},
        gates=gates, target=default_target(project["id"]), limits={"fix_retry": config.FIX_RETRY_LIMIT}, status="valid",
    )
    return insert(
        conn, "runs",
        project_id=project["id"], plan_id=plan_id, change_request_id=change_request_id,
        status="running", current_step=sequence(steps)[0],
    )


def void_active_run(conn, project_id: int) -> None:
    """변경 요청이 들어오면 진행 중인 실행을 무효로 한다 (C01)."""
    run = active_run(conn, project_id)
    if run is None:
        return
    update(conn, "runs", run["id"], status="void", finished_at=now())
    update(conn, "run_plans", run["plan_id"], status="void")
    approvals.void_run_requests(conn, run["id"])


# ── 에이전트 부르기 ────────────────────────────────────────


def _context(conn, run: dict) -> dict:
    """A02~A05에 넘길 입력 (agents/schemas.py의 RunContext)."""
    plan = get_plan(conn, run["plan_id"])
    planning = artifacts.get_version(conn, plan["input_versions"]["planning"]["id"])
    bundle = artifacts.planning_bundle(planning)
    sp = artifacts.latest(conn, run["project_id"], "screen_plan", "confirmed") or artifacts.latest(conn, run["project_id"], "screen_plan")
    ts = artifacts.latest(conn, run["project_id"], "tech_spec")
    return {
        "project_id": run["project_id"],
        "run_id": run["id"],
        "planning": bundle["planning"],
        "criteria": bundle["criteria"],
        "screen_plan": artifacts.read_json(sp, "screen_plan.json") if sp else None,
        "tech_spec": artifacts.read_json(ts, "tech_spec.json") if ts else None,
        "candidate_sha": run["candidate_sha"],
        "feedback": _feedback(conn, run),
        "target": plan["target"],
        "redeploy": bool(run["redeploy_of_run_id"]),
    }


def _feedback(conn, run: dict) -> list[str]:
    """고쳐야 할 것: 이 실행의 반려 의견과 마지막 배포 전 검사에서 실패한 조건."""
    out = []
    gates = ("G2",) if (run["current_step"] or "").startswith("A02") else ("G3", "G4")
    marks = ", ".join("?" for _ in gates)
    rejected = fetch_all(
        conn,
        f"select * from approval_requests where run_id = ? and status = 'rejected' and gate in ({marks})",
        (run["id"], *gates),
    )
    for req in rejected:
        decision = approvals.decision_of(conn, req["id"])
        if decision and decision["comment"]:
            out.append(f"{req['gate']} 반려: {decision['comment']}")

    if run["current_step"] == "A03":
        last = fetch_one(conn, "select sha from test_results where run_id = ? and phase = 'pre' order by id desc limit 1", (run["id"],))
        if last:
            fails = fetch_all(
                conn,
                "select ac_id from test_results where run_id = ? and sha = ? and result = 'fail'",
                (run["id"], last["sha"]),
            )
            out += [f"{row['ac_id']} 실패" for row in fails]
    return out


def _call_agent(step: str, ctx: dict, run: dict) -> dict:
    if step == "A02_scope":
        return AGENTS["propose_scope"](ctx)
    if step == "A02":
        return AGENTS["design"](ctx)
    if step == "A03":
        return AGENTS["develop"](ctx)
    if step == "A04":
        return AGENTS["test"](ctx, "pre", run["candidate_sha"] or "")
    if step == "A05":
        return AGENTS["deploy"](ctx, run["tested_sha"] or "")
    if step == "A04_post":
        return AGENTS["test"](ctx, "post", run["pushed_sha"] or "", run["deployment_url"])
    raise ValueError(step)


# ── 결과 저장 ─────────────────────────────────────────────


def _add_step(conn, run: dict, agent: str, status: str, summary: str, out: dict | None = None, links: list | None = None, error: str | None = None) -> None:
    usage = (out or {}).get("usage") or {}
    insert(
        conn, "run_steps",
        project_id=run["project_id"], run_id=run["id"], agent=agent, status=status, summary=summary,
        error=error, links=links, finished_at=now(),
        input_tokens=usage.get("input_tokens", 0), output_tokens=usage.get("output_tokens", 0),
    )


def _fail(conn, run: dict, step: str, reason: str) -> None:
    agent = STEP_AGENT.get(step, step)
    failure = {"step": agent, "reason": reason, "role": STEP_ROLE.get(agent, "developer"), "can_retry": True}
    update(conn, "runs", run["id"], status="failed", failure=failure)
    _add_step(conn, run, agent, "failed", f"{agent} 실패", error=reason)
    notify.failed(conn, run, failure)  # 그 단계의 담당에게 Slack 알림


def _move(conn, run: dict, step: str) -> None:
    """다음 단계로 넘어간다."""
    update(conn, "runs", run["id"], current_step=next_step(get_plan(conn, run["plan_id"]), step))


def _record_tests(conn, run: dict, out: dict, phase: str) -> None:
    for r in out["results"]:
        insert(
            conn, "test_results",
            run_id=run["id"], ac_id=r["ac_id"], result=r["result"], sha=out["sha"], phase=phase,
            evidence_path=r.get("evidence_path"),
        )


def _all_required_pass(conn, run: dict, out: dict) -> bool:
    """필수 완료 조건(미정이 아닌 것)이 모두 통과했는지."""
    plan = get_plan(conn, run["plan_id"])
    criteria = artifacts.planning_bundle(artifacts.get_version(conn, plan["input_versions"]["planning"]["id"]))["criteria"]
    required = {c["id"] for c in criteria if c.get("required") and c.get("origin") != "미정"}
    passed = {r["ac_id"] for r in out["results"] if r["result"] == "pass"}
    return required <= passed


def _deploy_problem(conn, run: dict, out: dict) -> str | None:
    """A05의 결과를 에이전트 말만 믿지 않고 승인 기록과 도구 기록으로 확인한다 (07 4.7). 문제가 없으면 None."""
    req = fetch_one(
        conn,
        "select * from approval_requests where run_id = ? and gate = 'G4' and status = 'approved' order by id desc limit 1",
        (run["id"],),
    )
    # 1. 검사한 커밋 = G4에서 승인한 커밋 = 올린 커밋
    if req is None or req["target_hash"] != run["tested_sha"] or out.get("pushed_sha") != run["tested_sha"]:
        return "배포한 커밋이 승인한 커밋과 다릅니다. 새 승인이 필요합니다."

    # 2. G4에서 승인한 대상 = 실행 계획의 대상 (저장소, 브랜치, 테스트 환경, 주소)
    target = get_plan(conn, run["plan_id"])["target"]
    if (req["deploy_target"] or {}) != target:
        return "승인한 배포 대상이 실행 계획과 다릅니다. 새 승인이 필요합니다."

    # 3. 배포 주소 = 승인한 테스트 환경의 주소 (예전 계획처럼 주소가 없으면 건너뛴다)
    if target.get("url") and (out.get("deployment_url") or "").rstrip("/") != target["url"].rstrip("/"):
        return f"배포 주소가 승인한 테스트 환경({target['url']})과 다릅니다."

    # 4. 실제로 workspace 서버로 올리고(git_push) 띄웠는가(deploy_local). 재배포는 올리지 않는다
    if config.USE_WORKSPACE:
        if not run["redeploy_of_run_id"] and not _tool_succeeded(conn, run, "git_push"):
            return "workspace 서버로 게시(git_push)한 기록이 없습니다."
        if not _tool_succeeded(conn, run, "deploy_local"):
            return "workspace 서버로 배포(deploy_local)한 기록이 없습니다."
    return None


def _tool_succeeded(conn, run: dict, tool: str) -> bool:
    """A05가 검사한 커밋으로 그 도구를 성공적으로 불렀는지 (tool_calls 기록).

    record()는 실행 중 오류도 allowed=1로 남기고 reason에 이유를 적으므로, reason이 없는 것만 성공이다.
    target 칸은 db.JSON_COLUMNS에 들어 있어 '"sha"'처럼 JSON 문자열로 저장되므로 같은 모양으로 비교한다.
    """
    row = fetch_one(
        conn,
        "select id from tool_calls where run_id = ? and agent = 'A05' and tool = ? "
        "and target = ? and allowed = 1 and reason is null",
        (run["id"], tool, json.dumps(run["tested_sha"], ensure_ascii=False)),
    )
    return row is not None


def _apply(conn, run: dict, step: str, out: dict) -> None:
    """에이전트 결과를 DB에 저장하고 다음 단계를 정한다."""
    pid = run["project_id"]

    if step == "A02_scope":
        waiting = {"type": "scope_confirm", "role": "designer", **{k: out[k] for k in ("figma_file", "page", "frames", "changes")}}
        update(conn, "runs", run["id"], status="waiting_input", waiting_input=waiting)
        _add_step(conn, run, "A02", "needs_input", "Figma 작업 범위 확인 대기")
        notify.scope_waiting(conn, run, waiting)

    elif step == "A02":
        files = {
            "screen_plan.json": artifacts.dumps(out["screen_plan"]),
            "screens.json": artifacts.dumps({"screens": out["screens"], "check": out["check"]}),
        }
        artifacts.add_version(conn, pid, "screen_plan", files, "A02")
        links = [{"label": "Figma", "url": out["screens"][0]["figma_url"]}] if out["screens"] else []
        _add_step(conn, run, "A02", "completed", out["summary"], out, links)
        update(conn, "runs", run["id"], waiting_input=None)
        _move(conn, run, step)

    elif step == "A03":
        files = {"tech_spec.json": artifacts.dumps(out["tech_spec"]), "out_of_plan.json": artifacts.dumps(out.get("out_of_plan", []))}
        artifacts.add_version(conn, pid, "tech_spec", files, "A03")
        update(conn, "runs", run["id"], candidate_sha=out["candidate_sha"])
        _add_step(conn, run, "A03", "completed", out["summary"], out, [{"label": "기술 명세", "url": f"/projects/{pid}/runs/{run['id']}"}])
        notify.a03_done(conn, run, out)
        _move(conn, run, step)

    elif step == "A04":
        _record_tests(conn, run, out, "pre")
        if _all_required_pass(conn, run, out):
            update(conn, "runs", run["id"], tested_sha=out["sha"])
            _add_step(conn, run, "A04", "completed", out["summary"], out)
            _move(conn, run, step)
        else:
            # 실패 → 다시 개발(A03). 한도에 닿으면 멈추고 사람에게 알린다
            count = run["retry_count"] + 1
            limit = get_plan(conn, run["plan_id"])["limits"].get("fix_retry", config.FIX_RETRY_LIMIT)
            update(conn, "runs", run["id"], retry_count=count)
            _add_step(conn, run, "A04", "failed", f"검사 실패 ({count}/{limit})", out, error=out["summary"])
            if count >= limit:
                failure = {"step": "A04", "reason": f"수정 반복 {limit}회 한도에 닿았습니다.", "role": "developer", "can_retry": True}
                update(conn, "runs", run["id"], status="failed", failure=failure)
                notify.failed(conn, run, failure)
            else:
                update(conn, "runs", run["id"], current_step="A03")

    elif step == "A05":
        problem = _deploy_problem(conn, run, out)
        if problem:
            _fail(conn, run, step, problem)
            return
        update(conn, "runs", run["id"], pushed_sha=out["pushed_sha"], deployment_id=out["deployment_id"], deployment_url=out["deployment_url"])
        _add_step(conn, run, "A05", "completed", out["summary"], out, [{"label": "배포 주소", "url": out["deployment_url"]}])
        _move(conn, run, step)

    elif step == "A04_post":
        _record_tests(conn, run, out, "post")
        if _all_required_pass(conn, run, out):
            _add_step(conn, run, "A04", "completed", out["summary"], out)
            update(conn, "runs", run["id"], status="done", current_step=None, finished_at=now())
            notify.deployed(conn, run, out["results"])
            if run["change_request_id"]:
                update(conn, "change_requests", run["change_request_id"], status="done")
        else:
            _fail(conn, run, step, "배포 후 검사에서 필수 완료 조건이 실패했습니다.")


def _open_gate(conn, run: dict, gate: str) -> None:
    """승인 요청을 만들고 멈춘다. G3는 계획 밖 변경이 없으면 건너뛴다."""
    pid = run["project_id"]
    if gate == "G2":
        sp = artifacts.latest(conn, pid, "screen_plan")
        info = artifacts.read_json(sp, "screens.json")
        req = approvals.create_request(conn, pid, "G2", sp["content_hash"], run_id=run["id"], target_version_id=sp["id"], detail=info)
        notify.gate_opened(conn, run, "G2", req, f"화면 {len(info.get('screens', []))}개를 만들었습니다.")
    elif gate == "G3":
        ts = artifacts.latest(conn, pid, "tech_spec")
        # 1. A03이 만든 기술 명세에서 "바꿀 예정인 파일 폴더 목록을 꺼낸다"
        planned = artifacts.read_json(ts, "tech_spec.json").get("planned_paths", []) if ts else []

        if config.USE_WORKSPACE:
            # 2. 후보 커밋에서 실제로 바뀐 파일 목록 (처음부터 후보 커밋까지)
            changed = gitops.changed_paths(workspace_dir(pid), run["candidate_sha"])

            # 3. 바뀐 파일 중 계획 목록에 없는 것
            out_of_plan = [
                {"path": p, "reason": "기술 명세의 바꿀 파일 목록에 없습니다."}
                for p in changed
                if not any(p == x or p.startswith(x.rstrip("/") + "/") for x in planned)
            ]
        else:
            # workspace 서버를 안 쓸 때(임시 에이전트, 테스트): A03이 돌려준 out_of_plan을 쓴다.
            # 임시 에이전트는 실제 커밋을 만들지 않아서 git으로 비교할 수 없다
            out_of_plan = artifacts.read_json(ts, "out_of_plan.json") if ts else []

        # 4. 계획 밖 변경이 없으면 승인 없이 G3를 건너뜀
        if not out_of_plan:
            _move(conn, run, gate)
            return

        # 5. 있으면 개발 담당에게 승인 요청. 승인은 후보 커밋 SHA에 묶인다.
        req = approvals.create_request(
            conn, pid, "G3", run["candidate_sha"] or "", run_id=run["id"],
            detail={"changes": out_of_plan, "candidate_sha": run["candidate_sha"]},
        )
        notify.gate_opened(conn, run, "G3", req, f"계획 밖에서 바뀐 파일 {len(out_of_plan)}개, 후보 버전 {(run['candidate_sha'] or '')[:12]}")
    elif gate == "G4":
        target = get_plan(conn, run["plan_id"])["target"]
        results = fetch_all(
            conn,
            "select ac_id, result from test_results where run_id = ? and phase = 'pre' and sha = ?",
            (run["id"], run["tested_sha"]),
        )
        req = approvals.create_request(
            conn, pid, "G4", run["tested_sha"] or "", run_id=run["id"], deploy_target=target,
            detail={"sha": run["tested_sha"], "target": target, "results": results},
        )
        passed = sum(1 for r in results if r["result"] == "pass")
        notify.gate_opened(
            conn, run, "G4", req,
            f"검사 {passed}/{len(results)} 통과, 버전 {(run['tested_sha'] or '')[:12]}, 대상 {target.get('environment', '')}",
        )
        notify.checks_ready(conn, run, run["tested_sha"] or "", results)  # Notion 검사 결과 페이지
    update(conn, "runs", run["id"], status="waiting_approval")


# ── LangGraph: 단계 하나 = 노드 하나 ──────────────────────


class RunState(TypedDict):
    run_id: int
    step: str  # 다음에 실행할 단계
    stop: str  # 멈춘 이유. 비어 있으면 계속 진행한다


NODES = ["A02_scope", "A02", "G2", "A03", "G3", "A04", "G4", "A05", "A04_post"]


def _next_state(conn, run_id: int) -> dict:
    """DB의 실행 상태를 보고 다음 노드를 정한다."""
    run = get_run(conn, run_id)
    if run["status"] == "running" and run["current_step"]:
        return {"step": run["current_step"], "stop": ""}
    return {"stop": run["status"]}


def _still_here(run: dict | None, step: str) -> bool:
    """그사이 실행이 무효가 되거나 다른 단계로 넘어가지 않았는지."""
    return run is not None and run["status"] == "running" and run["current_step"] == step


def make_agent_node(step: str):
    def node(state: RunState) -> dict:
        run_id = state["run_id"]
        with get_connection() as conn:
            run = get_run(conn, run_id)
            if not _still_here(run, step):
                return {"stop": "moved"}
            ctx = _context(conn, run)

        try:
            out = _call_agent(step, ctx, run)  # 에이전트는 DB 연결 밖에서 부른다
        except Exception as e:  # 에이전트 오류는 실행 실패로 기록한다
            log.exception("에이전트 실패: run=%s step=%s", run_id, step)
            with get_connection() as conn:
                write_lock(conn)  # 확인과 기록 사이에 변경 요청이 실행을 무효로 만들지 못하게
                run = get_run(conn, run_id)
                if _still_here(run, step):
                    _fail(conn, run, step, str(e) or type(e).__name__)
            return {"stop": "failed"}

        with get_connection() as conn:
            # 에이전트가 도는 동안 실행이 무효가 됐을 수 있다. 확인부터 반영까지 한 덩어리로 한다
            write_lock(conn)
            run = get_run(conn, run_id)
            if not _still_here(run, step):
                return {"stop": "moved"}
            _apply(conn, run, step, out)
            return _next_state(conn, run_id)

    return node


def make_gate_node(gate: str):
    def node(state: RunState) -> dict:
        with get_connection() as conn:
            write_lock(conn)
            run = get_run(conn, state["run_id"])
            if not _still_here(run, gate):
                return {"stop": "moved"}
            _open_gate(conn, run, gate)
            return _next_state(conn, state["run_id"])

    return node


def route_start(state: RunState) -> str:
    return state["step"]


def route_next(state: RunState) -> str:
    return END if state["stop"] else state["step"]


def build_run_graph():
    graph = StateGraph(RunState)
    for name in NODES:
        graph.add_node(name, make_gate_node(name) if name in GATE_STEPS else make_agent_node(name))

    # 시작 단계는 DB에 저장된 current_step으로 정한다 (승인 뒤 그 단계부터 이어서 실행)
    graph.add_conditional_edges(START, route_start, {name: name for name in NODES})
    for name in NODES:
        graph.add_conditional_edges(name, route_next, {**{n: n for n in NODES}, END: END})
    return graph.compile()


run_graph = build_run_graph()


def advance(run_id: int) -> None:
    """실행을 다음 멈출 곳(승인·입력 대기, 실패, 완료)까지 진행한다."""
    with get_connection() as conn:
        run = get_run(conn, run_id)
        if run is None or run["status"] != "running":
            return
        step = run["current_step"]
    run_graph.invoke({"run_id": run_id, "step": step, "stop": ""}, config={"recursion_limit": 100})


# ── 사람의 결정 ───────────────────────────────────────────


def on_decision(conn, req: dict, decision: str) -> int | None:
    """G2~G4 결정 뒤 실행을 움직인다. 진행할 실행 id를 돌려준다 (부르는 쪽이 커밋 뒤 advance를 부른다)."""
    if req["run_id"] is None:
        return None
    run = get_run(conn, req["run_id"])
    if run["status"] != "waiting_approval" or run["current_step"] != req["gate"]:
        return None
    if decision == "approve":
        if req["gate"] == "G2" and req["target_version_id"]:
            version = artifacts.get_version(conn, req["target_version_id"])
            artifacts.confirm(conn, version, version["content_hash"])
        _move(conn, run, req["gate"])
    else:
        # 반려: 화면이면 다시 A02, 코드면 다시 A03
        update(conn, "runs", run["id"], current_step="A02" if req["gate"] == "G2" else "A03")
        decision_row = approvals.decision_of(conn, req["id"])
        notify.rejected(conn, run, req, decision_row["comment"] if decision_row else None)
    update(conn, "runs", run["id"], status="running")
    return run["id"]


def scope_confirm(conn, run: dict, user_id: int) -> None:
    members.require_role(conn, run["project_id"], user_id, "designer")
    if run["status"] != "waiting_input" or run["current_step"] != "A02_scope":
        raise HTTPException(status_code=409, detail="범위 확인을 기다리는 실행이 아닙니다.")
    if not update_if(conn, "runs", run["id"], {"status": "waiting_input", "current_step": "A02_scope"},
                     current_step="A02", status="running"):
        raise HTTPException(status_code=409, detail="이미 확인했거나 무효가 된 실행입니다.")


def retry(conn, run: dict, user_id: int) -> None:
    if run["status"] != "failed" or not run["failure"]:
        raise HTTPException(status_code=409, detail="실패한 실행이 아닙니다.")
    role = run["failure"].get("role", "developer")
    if role not in members.require_member(conn, run["project_id"], user_id):
        raise HTTPException(status_code=403, detail=f"{members.role_label(role)} 담당만 다시 시도할 수 있습니다.")

    step = run["current_step"] or "A04_post"
    retry_count = run["retry_count"]
    if run["failure"].get("step") == "A04" and step == "A04":
        retry_count = 0  # 한도에 닿아 멈춘 수정 반복을 처음부터 다시 센다
        step = "A03"
    if not update_if(conn, "runs", run["id"], {"status": "failed"},
                     status="running", failure=None, current_step=step, retry_count=retry_count):
        raise HTTPException(status_code=409, detail="이미 다시 시도했거나 무효가 된 실행입니다.")


def redeploy(conn, source: dict, user_id: int) -> tuple[int, int]:
    """검사를 통과한 실행의 같은 커밋을 다시 배포한다. 새 실행을 만들고 G4부터 진행한다."""
    members.require_role(conn, source["project_id"], user_id, "deployer")
    write_lock(conn)  # 두 번 눌러도 '진행 중인 실행' 검사와 새 실행 만들기가 겹치지 않게
    source = get_run(conn, source["id"])
    if not source["tested_sha"] or source["status"] != "done":
        raise HTTPException(status_code=409, detail="검사를 통과한 실행만 다시 배포할 수 있습니다.")
    if active_run(conn, source["project_id"]) is not None:
        raise HTTPException(status_code=409, detail="진행 중인 실행이 있어 다시 배포할 수 없습니다.")

    run_id = insert(
        conn, "runs",
        project_id=source["project_id"], plan_id=source["plan_id"], redeploy_of_run_id=source["id"],
        status="running", current_step="G4", candidate_sha=source["candidate_sha"], tested_sha=source["tested_sha"],
    )
    # 이전 실행의 배포 전 검사 결과를 그대로 옮긴다 (같은 커밋)
    rows = fetch_all(
        conn,
        "select * from test_results where run_id = ? and phase = 'pre' and sha = ?",
        (source["id"], source["tested_sha"]),
    )
    for r in rows:
        insert(conn, "test_results", run_id=run_id, ac_id=r["ac_id"], result=r["result"], sha=r["sha"], phase="pre", evidence_path=r["evidence_path"])

    _open_gate(conn, get_run(conn, run_id), "G4")
    req = approvals.pending(conn, source["project_id"], "G4", run_id)[0]
    return run_id, req["id"]
