"""대화, A01 실행, 기획서 확정(G1), 변경 요청(C01~C04).

- 메시지를 저장한 뒤 A01을 부른다. A01 결과는 기획 묶음 초안 버전이 되고, 그 버전에 대한 G1 승인 요청이 만들어진다.
- 확정은 기획 담당만, 필수 질문이 모두 답해졌고 최신 메시지까지 반영된 초안만 할 수 있다.
- 확정 뒤의 변경은 메시지로 받지 않고 변경 요청으로만 받는다 (07 7장).
"""

import logging

from fastapi import HTTPException

from agents import AGENTS
from db import fetch_all, fetch_one, get_connection, insert, update, update_if, write_lock
from services import approvals, artifacts, controller, members, notify

log = logging.getLogger(__name__)
USER_KINDS = ("description", "answer", "revision")


# ── 대화 ──────────────────────────────────────────────────


def list_messages(conn, project_id: int) -> list[dict]:
    return fetch_all(conn, "select * from messages where project_id = ? order by id", (project_id,))


def list_questions(conn, project_id: int) -> list[dict]:
    return fetch_all(conn, "select * from questions where project_id = ? order by id", (project_id,))


def open_questions(conn, project_id: int, required_only: bool = False) -> list[dict]:
    questions = [q for q in list_questions(conn, project_id) if q["status"] == "open"]
    return [q for q in questions if q["required"]] if required_only else questions


def _last_a01_step(conn, project_id: int) -> dict | None:
    return fetch_one(
        conn,
        "select * from run_steps where project_id = ? and agent = 'A01' order by id desc limit 1",
        (project_id,),
    )


def processing(conn, project_id: int) -> bool:
    """A01이 일하는 중인지: 마지막 A01 작업보다 나중에 온 사용자 메시지가 있으면 처리 중이다."""
    last_user = fetch_one(
        conn,
        "select * from messages where project_id = ? and sender = 'user' order by id desc limit 1",
        (project_id,),
    )
    if last_user is None:
        return False
    step = _last_a01_step(conn, project_id)
    return step is None or last_user["created_at"] > step["created_at"]


def last_a01_error(conn, project_id: int) -> str | None:
    step = _last_a01_step(conn, project_id)
    return step["error"] if step is not None and step["status"] == "failed" else None


def receive_message(conn, project: dict, user: dict, kind: str, body: str, answers: list[dict]) -> int:
    """사용자 메시지를 저장한다. answers: [{"question_id": 1, "body": "..."}]"""
    if kind not in USER_KINDS:
        raise HTTPException(status_code=400, detail=f"kind는 {', '.join(USER_KINDS)} 중 하나입니다.")
    latest = artifacts.latest(conn, project["id"], "planning")
    if latest is not None and latest["status"] == "confirmed":
        raise HTTPException(status_code=409, detail="기획서를 확정한 뒤에는 변경 요청 화면에서 변경을 등록해 주세요.")
    if kind == "revision" and (latest is None or latest["status"] != "draft"):
        raise HTTPException(status_code=409, detail="수정 요청은 기획서 초안이 있을 때 보낼 수 있습니다.")
    if kind == "answer" and not answers:
        raise HTTPException(status_code=400, detail="어떤 질문에 대한 답인지 알려 주세요.")
    if kind != "answer" and answers:
        raise HTTPException(status_code=400, detail="질문에 답하려면 kind를 answer로 보내세요.")

    questions = []
    for a in answers:
        q = fetch_one(conn, "select * from questions where id = ?", (a["question_id"],))
        if q is None or q["project_id"] != project["id"]:
            raise HTTPException(status_code=404, detail=f"없는 질문입니다: {a['question_id']}")
        if q["status"] != "open":
            raise HTTPException(status_code=409, detail="이미 답한 질문입니다.")
        questions.append(q)
    if len({q["id"] for q in questions}) != len(questions):
        raise HTTPException(status_code=400, detail="같은 질문에 두 번 답했습니다.")

    # 질문마다 따로 적은 답이 있으면 본문 뒤에 붙인다
    text = body.strip()
    extra = [
        f"- {q['body']} → {a['body'].strip()}"
        for q, a in zip(questions, answers)
        if a.get("body") and a["body"].strip() not in text
    ]
    text = "\n".join([text, *extra]).strip()
    if not text:
        raise HTTPException(status_code=400, detail="내용을 입력해 주세요.")

    message_id = insert(conn, "messages", project_id=project["id"], sender="user", user_id=user["id"], kind=kind, body=text)
    for q in questions:
        update(conn, "questions", q["id"], status="answered", answer_message_id=message_id)
    return message_id


# ── A01 실행 ──────────────────────────────────────────────


def _source_messages(conn, project_id: int) -> list[dict]:
    """A01에 넘길 대화 전체. 답변 메시지에는 어떤 질문에 대한 답인지 붙인다."""
    answered = {}
    for q in list_questions(conn, project_id):
        if q["answer_message_id"]:
            answered.setdefault(q["answer_message_id"], []).append(q["body"])
    return [
        {
            "id": m["id"], "sender": m["sender"], "kind": m["kind"], "body": m["body"],
            "answers_question": " / ".join(answered[m["id"]]) if m["id"] in answered else None,
        }
        for m in list_messages(conn, project_id)
    ]


def run_a01(project_id: int, change_request_id: int | None = None) -> None:
    """대화 전체로 기획 묶음 초안을 새로 만든다. 메시지를 커밋한 뒤에 부른다."""
    with get_connection() as conn:
        if change_request_id is None:
            # 인자로 못 받았어도(요청이 겹쳐 덮어써짐, 서버 재시작) 분석 중인 변경 요청이 있으면 그것을 처리한다
            pending_cr = fetch_one(
                conn,
                "select id from change_requests where project_id = ? and status = 'analyzing' order by id desc limit 1",
                (project_id,),
            )
            change_request_id = pending_cr["id"] if pending_cr else None
        prev = artifacts.latest(conn, project_id, "planning")
        cr = fetch_one(conn, "select * from change_requests where id = ?", (change_request_id,)) if change_request_id else None
        inp = {
            "project_id": project_id,
            "messages": _source_messages(conn, project_id),
            "previous": artifacts.planning_bundle(prev) if prev else None,
            "change_request": cr["request_text"] if cr else None,
        }

    try:
        out = AGENTS["plan"](inp)  # 에이전트는 DB 연결 밖에서 부른다
    except Exception as e:
        log.exception("A01 실패: project=%s", project_id)
        with get_connection() as conn:
            insert(conn, "run_steps", project_id=project_id, agent="A01", status="failed", summary="요구사항 정리 실패", error=str(e))
            body = f"요구사항을 정리하지 못했어요.\n- {e}"
            # 확정 뒤에는 대화를 막으므로, 변경 요청을 '분석 중'으로 두면 다시 등록할 길이 없다 → 닫는다
            if change_request_id and update_if(conn, "change_requests", change_request_id, {"status": "analyzing"}, status="rejected"):
                body += "\n- 이 변경 요청은 닫았어요. 변경 요청을 다시 등록해 주세요."
            insert(conn, "messages", project_id=project_id, sender="agent", kind="agent", body=body)
        return

    with get_connection() as conn:
        version = artifacts.write_planning(conn, project_id, out)
        g1 = approvals.create_request(conn, project_id, "G1", version["content_hash"], target_version_id=version["id"])
        update(conn, "projects", project_id, has_ui=out.get("has_ui", True))
        _sync_questions(conn, project_id, out.get("questions", []))
        notify.a01_done(conn, project_id, version, artifacts.planning_bundle(version),
                        len(open_questions(conn, project_id)), g1)
        insert(conn, "messages", project_id=project_id, sender="agent", kind="agent", body=out["reply"])
        usage = out.get("usage") or {}
        insert(
            conn, "run_steps",
            project_id=project_id, agent="A01", status="completed", summary=f"기획 묶음 v{version['version']} 작성",
            input_tokens=usage.get("input_tokens", 0), output_tokens=usage.get("output_tokens", 0),
        )
        if change_request_id:
            _analyze_change(conn, change_request_id, version)


def _sync_questions(conn, project_id: int, new: list[dict]) -> None:
    """새 질문만 기록한다. 이번 결과에 없는 열린 질문은 닫는다 (다른 메시지에서 답이 나왔거나 표현이 바뀌었다)."""
    wanted = {(q.get("field_path"), q["body"]) for q in new}
    for q in open_questions(conn, project_id):
        if (q["field_path"], q["body"]) not in wanted:
            update(conn, "questions", q["id"], status="dropped")
    have = {(q["field_path"], q["body"]) for q in open_questions(conn, project_id)}
    for q in new:
        if (q.get("field_path"), q["body"]) not in have:
            insert(
                conn, "questions",
                project_id=project_id, body=q["body"], field_path=q.get("field_path"), required=int(q.get("required", False)), status="open",
            )


# ── 기획서 확정 (G1) ──────────────────────────────────────


def confirm_blockers(conn, project_id: int) -> list[str]:
    """확정을 막는 이유 목록. 비어 있으면 확정할 수 있다."""
    version = artifacts.latest(conn, project_id, "planning")
    if version is None:
        return ["아직 기획서 초안이 없습니다."]
    if version["status"] == "confirmed":
        return ["이미 확정한 기획서입니다."]
    if version["status"] != "draft":
        return ["최신 기획서가 초안이 아닙니다."]
    if processing(conn, project_id):
        return ["새 메시지를 기획서에 반영하는 중입니다."]

    reasons = []
    n = len(open_questions(conn, project_id, required_only=True))
    if n:
        reasons.append(f"답하지 않은 필수 질문이 {n}개 있습니다.")
    bundle = artifacts.planning_bundle(version)
    scope = bundle["planning"].get("release_scope", {})
    features = scope.get("features", [])
    if not features:
        reasons.append("정리된 기능이 없습니다.")
    for f in features:
        if f.get("included_in_release") is None:
            reasons.append(f"{f['id']}의 출시 포함 여부가 정해지지 않았습니다.")
        elif f.get("included_in_release") and not any(c["feature_id"] == f["id"] and c["kind"] == "정상" for c in bundle["criteria"]):
            reasons.append(f"{f['id']}에 정상 완료 조건이 없습니다.")
    if scope.get("container_supported") is False:
        reasons.append("컨테이너에서 실행·검사할 수 없는 유형은 지원하지 않습니다.")
    return reasons


def confirm(conn, project: dict, user: dict, version_number: int, expected_hash: str) -> dict:
    """G1 확정. {"approval_id", "run_id"}를 돌려준다 (run_id가 있으면 커밋 뒤 실행을 진행한다)."""
    members.require_role(conn, project["id"], user["id"], "planner")
    version = artifacts.latest(conn, project["id"], "planning")
    if version is None:
        raise HTTPException(status_code=404, detail="기획서가 없습니다.")
    if version["version"] != version_number or version["content_hash"] != expected_hash:
        raise HTTPException(status_code=409, detail="그사이에 기획서가 바뀌었습니다. 새 버전을 다시 불러오세요.")
    blockers = confirm_blockers(conn, project["id"])
    if blockers:
        raise HTTPException(status_code=400, detail=" ".join(blockers))

    req = fetch_one(
        conn,
        "select * from approval_requests where project_id = ? and gate = 'G1' and target_version_id = ? and status = 'pending'",
        (project["id"], version["id"]),
    )
    if req is None:
        req = approvals.create_request(conn, project["id"], "G1", version["content_hash"], target_version_id=version["id"])
    approval_id = approvals.decide(conn, req, user["id"], "approve", expected_hash, None)
    artifacts.confirm(conn, version, expected_hash)
    notify.planning_confirmed(conn, project["id"], version)  # Notion 기획서 페이지를 확정으로

    # 변경 요청을 다시 승인한 것이면 영향받은 단계만 다시 실행한다
    cr = fetch_one(
        conn,
        "select * from change_requests where project_id = ? and status = 'awaiting_approval' order by id desc limit 1",
        (project["id"],),
    )
    if cr is None:
        run_id = controller.start_run(conn, project, version)
    else:
        steps = (cr["impact"] or {}).get("rerun_steps", [])
        if not steps:
            update(conn, "change_requests", cr["id"], status="done")
            return {"approval_id": approval_id, "run_id": None}
        update(conn, "change_requests", cr["id"], status="rerunning")
        run_id = controller.start_run(conn, project, version, steps=steps, change_request_id=cr["id"])
        _carry_over(conn, cr, run_id)
    return {"approval_id": approval_id, "run_id": run_id}


def reject_g1(conn, req: dict, user: dict, comment: str) -> None:
    """승인함에서 G1을 반려하면 수정 요청과 같다: 의견을 revision 메시지로 남긴다 (부르는 쪽이 A01을 다시 부른다)."""
    approvals.decide(conn, req, user["id"], "reject", req["target_hash"], comment)
    insert(conn, "messages", project_id=req["project_id"], sender="user", user_id=user["id"], kind="revision", body=comment.strip())


# ── 변경 요청 (C01~C04) ───────────────────────────────────


def register_change(conn, project: dict, user: dict, text: str) -> int:
    text = text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="바꿀 내용을 입력해 주세요.")
    write_lock(conn)  # 두 번 눌러도 '처리 중인 변경 요청' 검사와 등록이 겹치지 않게
    confirmed = artifacts.latest(conn, project["id"], "planning", "confirmed")
    latest = artifacts.latest(conn, project["id"], "planning")
    if confirmed is None or latest is None or latest["id"] != confirmed["id"]:
        raise HTTPException(status_code=409, detail="확정한 기획서가 없습니다. 확정 전에는 대화로 수정해 주세요.")
    busy = fetch_one(
        conn,
        "select id from change_requests where project_id = ? and status in ('analyzing', 'awaiting_approval')",
        (project["id"],),
    )
    if busy is not None:
        raise HTTPException(status_code=409, detail="처리 중인 변경 요청이 있습니다. 먼저 끝내 주세요.")

    message_id = insert(conn, "messages", project_id=project["id"], sender="user", user_id=user["id"], kind="revision", body=text)
    cr_id = insert(
        conn, "change_requests",
        project_id=project["id"], requested_by=user["id"], request_text=text,
        from_version_id=confirmed["id"], source_message_id=message_id, status="analyzing",
    )
    controller.void_active_run(conn, project["id"])
    return cr_id


def _by_id(items: list[dict]) -> dict:
    """[{id: ...}] → {id: 항목}. 비교할 때 _로 시작하는 내부 키는 뺀다."""
    return {i["id"]: {k: v for k, v in i.items() if not k.startswith("_")} for i in items}


def _analyze_change(conn, cr_id: int, new: dict) -> None:
    """C02~C03: 이전 확정본과 새 초안을 비교해 바뀐 ID, 영향, 다시 승인할 지점을 계산한다."""
    cr = fetch_one(conn, "select * from change_requests where id = ?", (cr_id,))
    old_b = artifacts.planning_bundle(artifacts.get_version(conn, cr["from_version_id"]))
    new_b = artifacts.planning_bundle(new)

    added, modified, removed = [], [], []
    for key in ("features", "criteria"):
        if key == "features":
            old, cur = _by_id(old_b["planning"]["release_scope"]["features"]), _by_id(new_b["planning"]["release_scope"]["features"])
        else:
            old, cur = _by_id(old_b["criteria"]), _by_id(new_b["criteria"])
        added += sorted(set(cur) - set(old))
        removed += sorted(set(old) - set(cur))
        modified += sorted(i for i in set(old) & set(cur) if old[i] != cur[i])
    changed = added + modified + removed
    changed_features = {i for i in changed if i.startswith("FEAT-")}
    changed_features |= {f"FEAT-{i.split('-')[1]}" for i in changed if i.startswith("AC-")}

    # 바뀐 기능과 연결된 화면
    project = members.get_project(conn, cr["project_id"])
    sp = artifacts.latest(conn, cr["project_id"], "screen_plan", "confirmed")
    screens = []
    if sp is not None:
        for sc in artifacts.read_json(sp, "screen_plan.json").get("screens", []):
            if changed_features & set(sc.get("related_features", [])):
                screens.append(sc["screen_id"])

    new_feature = any(i.startswith("FEAT-") for i in added)
    needs_screens = bool(project["has_ui"]) and (bool(screens) or new_feature or sp is None)
    tests = len([i for i in added + modified if i.startswith("AC-")])
    anything = bool(changed)

    rerun = (["A02"] if needs_screens and anything else []) + (["A03", "A04", "A05"] if anything else [])
    gates = ["G1"] + (["G2"] if "A02" in rerun else []) + (["G4"] if rerun else [])
    carried = [] if "A02" in rerun or not project["has_ui"] else [{"gate": "G2", "reason": "화면이 바뀌지 않는다."}]
    update(
        conn, "change_requests", cr_id,
        to_version_id=new["id"],
        changed_ids={"added": added, "modified": modified, "removed": removed},
        impact={"screens": screens, "apis": [], "tests": tests, "code": ["backend", "frontend"] if anything else [],
                "rerun_steps": rerun, "carried_over": carried},
        reapproval_gates=gates,
        status="awaiting_approval",
    )
    notify.change_registered(conn, fetch_one(conn, "select * from change_requests where id = ?", (cr_id,)))


def _carry_over(conn, cr: dict, run_id: int) -> None:
    """영향이 없는 G2는 이전 승인을 이어받는다 (C04)."""
    for c in (cr["impact"] or {}).get("carried_over", []):
        if c["gate"] != "G2":
            continue
        sp = artifacts.latest(conn, cr["project_id"], "screen_plan", "confirmed")
        if sp is None:
            continue
        req = approvals.create_request(
            conn, cr["project_id"], "G2", sp["content_hash"], run_id=run_id, target_version_id=sp["id"],
            detail={"carried_over": c["reason"]},
        )
        approvals.carry_over(conn, req, cr["id"])
