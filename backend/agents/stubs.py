"""임시 에이전트. LLM과 외부 도구를 부르지 않고 정해진 모양의 결과를 바로 돌려준다.

화면과 서버의 전체 흐름(기획 → G1 → 화면 → G2 → 개발 → 테스트 → G4 → 배포)을 확인하기 위한 것이다.
팀원의 실제 에이전트가 준비되면 agents/__init__.py의 AGENTS에서 하나씩 바꿔 끼운다.
"""

import uuid

from agents.schemas import (
    DeployOutput, DesignOutput, DevelopOutput, FigmaScope, PlanningInput, PlanningOutput, RunContext, TestOutput,
)

TARGET_USER_Q = "이 서비스를 주로 쓰는 사람은 누구인가요?"


def _title(text: str, n: int = 24) -> str:
    line = text.strip().splitlines()[0] if text.strip() else "기능"
    return line if len(line) <= n else line[:n].rstrip() + "…"


# ── A01 기획 ──────────────────────────────────────────────


def plan(inp: PlanningInput) -> PlanningOutput:
    """설명·수정 요청 메시지 하나를 기능 하나로 본다. 처음엔 대상 사용자를 한 번 묻는다."""
    user_msgs = [m for m in inp["messages"] if m["sender"] == "user"]
    sources = [m for m in user_msgs if m["kind"] in ("description", "revision")]
    answer = next((m for m in reversed(user_msgs) if m["answers_question"] == TARGET_USER_Q), None)

    # 이전 초안에 있던 기능은 ID를 그대로 쓴다 (_source: 어느 메시지에서 나온 기능인지)
    prev_features = {}
    if inp["previous"]:
        for f in inp["previous"]["planning"]["release_scope"]["features"]:
            prev_features[f.get("_source", "")] = f
    top = max((int(f["id"].split("-")[1]) for f in prev_features.values()), default=0)

    features, evidence = [], []
    for m in sources:
        key = f"msg-{m['id']}"
        if key in prev_features:
            fid = prev_features[key]["id"]
        else:
            top += 1
            fid = f"FEAT-{top:03d}"
        features.append({
            "id": fid, "name": _title(m["body"]), "behavior": m["body"].strip(),
            "priority": "필수", "included": True, "_source": key,
        })
        evidence.append({
            "field_path": f"release_scope.features[{len(features) - 1}].behavior",
            "message_id": m["id"], "quote": m["body"].strip()[:60],
        })

    target_users = [answer["body"].strip()] if answer else []
    planning = {
        "overview": {"project_name": None, "summary": sources[0]["body"].strip() if sources else None},
        "user_problem": {"target_users": target_users},
        "release_scope": {
            "features": features,
            "out_of_scope": [],
            "project_type": "web_app",
            "container_supported": True,
            "tech_stack": [
                {"area": "frontend", "choice": "React", "reason": "브라우저 화면이 필요하다.", "origin": "제안"},
                {"area": "backend", "choice": "FastAPI", "reason": "API가 단순하다.", "origin": "제안"},
            ],
        },
        "user_experience": {
            "screens": [
                {"screen_id": f"SCR-{i + 1:03d}", "name": f["name"], "related_features": [f["id"]]}
                for i, f in enumerate(features)
            ]
        },
    }
    questions = [] if answer else [{"body": TARGET_USER_Q, "field_path": "user_problem.target_users", "required": True}]

    criteria = []
    for i, f in enumerate(features):
        n = f["id"].split("-")[1]
        criteria.append({
            "id": f"AC-{n}-01", "feature_id": f["id"], "kind": "정상",
            "condition": f"{f['name']}을(를) 하면 결과가 저장되고 화면에 보인다.",
            "source": f"release_scope.features[{i}].behavior", "origin": "명시", "required": True,
        })
        criteria.append({
            "id": f"AC-{n}-02", "feature_id": f["id"], "kind": "실패",
            "condition": "필수 입력을 비우고 저장하면 저장되지 않고 안내가 보인다.",
            "source": f"release_scope.features[{i}]", "origin": "추론", "required": False,
        })

    lines = ["# 기획서 (임시 에이전트 초안)", "", "## 개요", "", planning["overview"]["summary"] or "**미정**", "", "## 대상 사용자", ""]
    lines += [f"- {u}" for u in target_users] or ["- **미정**"]
    lines += ["", "## 출시 범위", "", "| ID | 기능 | 동작 |", "|---|---|---|"]
    lines += [f"| {f['id']} | {f['name']} | {f['behavior'].replace('|', '/').replace(chr(10), ' ')} |" for f in features]
    lines += ["", "## 기술 스택 제안", ""]
    lines += [f"- {t['area']}: {t['choice']} ({t['reason']})" for t in planning["release_scope"]["tech_stack"]]

    reply = f"기능 {len(features)}개로 정리했어요: {', '.join(f['name'] for f in features) or '아직 없음'}."
    if questions:
        reply += f"\n확인할 것이 {len(questions)}가지 있어요.\n" + "\n".join(f"- {q['body']}" for q in questions)
    else:
        reply += "\n기획서 초안을 확인해 주세요."

    return {
        "planning": planning, "evidence": evidence, "brief_markdown": "\n".join(lines) + "\n",
        "criteria": criteria, "questions": questions, "reply": reply, "has_ui": True,
    }


# ── A02 화면 설계 ─────────────────────────────────────────


def propose_scope(ctx: RunContext) -> FigmaScope:
    screens = ctx["planning"].get("user_experience", {}).get("screens", [])
    return {
        "figma_file": "(임시) 작업용 Figma 파일",
        "page": f"p{ctx['project_id']} 화면",
        "frames": [f"{s['screen_id']} {s['name']}" for s in screens],
        "changes": "새 프레임을 만든다. 기존 프레임은 바꾸지 않는다.",
    }


def design(ctx: RunContext) -> DesignOutput:
    screens = ctx["planning"].get("user_experience", {}).get("screens", [])
    out = [{"id": s["screen_id"], "name": s["name"], "figma_url": "https://www.figma.com/"} for s in screens]
    screen_plan = {"screens": [{**s, "design_ref": {"url": "https://www.figma.com/", "node_id": None}} for s in screens]}
    return {"screen_plan": screen_plan, "screens": out, "check": {"passed": True, "missing": []}, "summary": f"화면 {len(out)}개 생성 (임시)"}


# ── A03 개발 ──────────────────────────────────────────────


def develop(ctx: RunContext) -> DevelopOutput:
    features = ctx["planning"].get("release_scope", {}).get("features", [])
    tech_spec = {
        "architecture": "React 화면 + FastAPI 서버 + SQLite (임시)",
        "apis": [
            {"id": f"API-{i + 1:03d}", "method": "POST", "path": f"/api/{f['id'].lower()}", "related_features": [f["id"]]}
            for i, f in enumerate(features)
        ],
        "entities": [],
        "planned_paths": ["frontend/", "backend/"],
    }
    return {"tech_spec": tech_spec, "candidate_sha": uuid.uuid4().hex[:12], "out_of_plan": [], "summary": f"기능 {len(features)}개 구현 (임시)"}


# ── A04 테스트 ────────────────────────────────────────────


def test(ctx: RunContext, phase: str, sha: str, url: str | None = None) -> TestOutput:
    results = [{"ac_id": c["id"], "result": "pass"} for c in ctx["criteria"]]
    label = "배포 전" if phase == "pre" else "배포 후"
    return {"sha": sha, "results": results, "summary": f"{label} 검사 {len(results)}개 통과 (임시)"}


test.__test__ = False  # pytest가 테스트 함수로 보지 않게


# ── A05 배포 ──────────────────────────────────────────────


def deploy(ctx: RunContext, sha: str) -> DeployOutput:
    return {
        "pushed_sha": sha,
        "deployment_id": f"dep-{ctx['run_id']}",
        "deployment_url": ctx["target"].get("url") or f"http://localhost:8080/p{ctx['project_id']}",
        "summary": "테스트 환경에 배포 (임시)",
    }
