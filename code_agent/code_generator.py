"""A03 코드 작성 단계: 기술 명세 → 작업 계획(사람 확인) → 확정한 범위 안에서 코드 작성."""
from __future__ import annotations

from typing import Any

from agent_loop import Tool, run_with_tools
from code_prompts import CODE_SYSTEM_PROMPT, PLAN_SYSTEM_PROMPT, build_code_message, build_plan_message
from code_schema import CodeResult, WorkPlan
from extractor import ExtractionError, run_structured
from spec_generator import assign_feature_ids
from workspace import Workspace, normalize_path

MAX_PLANNED_FILES = 40


# ---------- 1단계: 작업 계획 ----------

def validate_work_plan(wp: dict[str, Any], feature_ids: list[str], ac_ids: list[str] | None = None) -> list[str]:
    problems: list[str] = []
    tasks = wp.get("tasks", [])
    if not tasks:
        return ["작업이 하나도 없습니다."]
    known, known_ac = set(feature_ids), set(ac_ids or [])
    seen_ids: set[str] = set()
    covered_f: set[str] = set()
    covered_ac: set[str] = set()
    all_paths: set[str] = set()
    for t in tasks:
        if t["id"] in seen_ids:
            problems.append(f"작업 ID {t['id']}가 중복되었습니다.")
        seen_ids.add(t["id"])
        if not t["feature_ids"]:
            problems.append(f"{t['id']}에 기능 ID가 없습니다.")
        for f in t["feature_ids"]:
            if f not in known:
                problems.append(f"{t['id']}가 존재하지 않는 기능 ID {f}를 참조합니다.")
            covered_f.add(f)
        for a in t["ac_ids"]:
            if known_ac and a not in known_ac:
                problems.append(f"{t['id']}가 존재하지 않는 완료 조건 ID {a}를 참조합니다.")
            covered_ac.add(a)
        if not t["files"]:
            problems.append(f"{t['id']}에 파일이 없습니다.")
        for fl in t["files"]:
            try:
                all_paths.add(normalize_path(fl["path"]))
            except ValueError as e:
                problems.append(f"{t['id']}: {e}")
        for d in t["depends_on"]:
            if d not in {x["id"] for x in tasks}:
                problems.append(f"{t['id']}가 존재하지 않는 작업 {d}에 의존합니다.")
    for f in sorted(known - covered_f):
        problems.append(f"기능 {f}가 어떤 작업에도 연결되지 않았습니다.")
    for a in sorted(known_ac - covered_ac):
        problems.append(f"완료 조건 {a}가 어떤 작업에도 연결되지 않았습니다.")
    if len(all_paths) > MAX_PLANNED_FILES:
        problems.append(f"파일이 {len(all_paths)}개로 너무 많습니다 (최대 {MAX_PLANNED_FILES}개). 범위를 줄이세요.")
    return problems


def plan_work(
    spec: dict[str, Any],
    plan: dict[str, Any],
    *,
    acceptance_criteria: list[dict[str, Any]] | None = None,
    screen_plan: dict[str, Any] | str | None = None,
    tech_stack: str | None = None,
    execution_contract: str | None = None,
    client: Any = None,
    model: str | None = None,
    max_repairs: int = 1,
) -> tuple[dict[str, Any], list[str]]:
    """작업 계획을 만든다. 사람이 이 계획(특히 files)을 확인한 뒤 write_code에 넘긴다."""
    features = assign_feature_ids(plan)
    if not features:
        raise ExtractionError("이번 출시에 포함된 기능이 없습니다.")
    feature_ids = [f["id"] for f in features]
    ac_ids = [a["id"] for a in (acceptance_criteria or []) if a.get("id")]
    ctx = dict(acceptance_criteria=acceptance_criteria, screen_plan=screen_plan, tech_stack=tech_stack, execution_contract=execution_contract)

    problems: list[str] = []
    wp: dict[str, Any] = {}
    for _ in range(max_repairs + 1):
        user = build_plan_message(features, plan, spec, problems=problems or None, **ctx)
        wp = run_structured(PLAN_SYSTEM_PROMPT, user, WorkPlan, "work_plan", client=client, model=model)
        problems = validate_work_plan(wp, feature_ids, ac_ids)
        if not problems:
            break
    return wp, problems


# ---------- 2단계: 코드 작성 ----------

def make_workspace_tools(ws: Workspace, with_tests: bool) -> list[Tool]:
    path_param = {"type": "object", "properties": {"path": {"type": "string", "description": "프로젝트 루트 기준 상대 경로"}}, "required": ["path"]}
    tools = [
        Tool("list_files", "작업 공간에 있는 파일 목록을 본다.", {"type": "object", "properties": {}},
            lambda a: "\n".join(ws.list_files()) or "(비어 있음)"),
        Tool("read_file", "작업 공간의 파일 내용을 읽는다.", path_param, lambda a: ws.read_file(a.get("path"))),
        Tool(
            "write_file",
            "작업 계획에 있는 파일을 쓴다(전체 내용으로 덮어씀). 계획 밖 파일은 거부된다.",
            {"type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string", "description": "파일 전체 내용"}},
            "required": ["path", "content"]},
            lambda a: ws.write_file(a.get("path"), a.get("content", "")),
        ),
    ]
    if with_tests:
        def _run(_: dict) -> str:
            r = ws.run_tests()
            head = "시간 초과" if r["timed_out"] else f"종료 코드 {r['exit_code']}"
            return f"{head}\n{r['output']}"

        tools.append(Tool("run_tests", "정해진 테스트 명령을 실행하고 결과를 본다.", {"type": "object", "properties": {}}, _run))
    return tools


def write_code(
    work_plan: dict[str, Any],
    spec: dict[str, Any],
    plan: dict[str, Any],
    workspace_dir: str,
    *,
    acceptance_criteria: list[dict[str, Any]] | None = None,
    screen_plan: dict[str, Any] | str | None = None,
    tech_stack: str | None = None,
    execution_contract: str | None = None,
    test_command: list[str] | None = None,
    client: Any = None,
    model: str | None = None,
    max_rounds: int = 40,
    trace: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """확정한 작업 계획의 파일 범위 안에서 코드를 쓴다. 반환: (모델의 요약, 코드가 확인한 보고서).

    보고서는 모델의 말이 아니라 코드가 직접 확인한 사실이다 (파일 존재, 범위 위반, 완료 조건 참조, 테스트 결과).
    """
    planned = sorted({normalize_path(f["path"]) for t in work_plan["tasks"] for f in t["files"]})
    ws = Workspace(workspace_dir, allowed_files=planned, test_command=test_command)
    features = assign_feature_ids(plan)
    user = build_code_message(
        work_plan, features, plan, spec,
        acceptance_criteria=acceptance_criteria, screen_plan=screen_plan, tech_stack=tech_stack, execution_contract=execution_contract,
    )
    result = run_with_tools(
        CODE_SYSTEM_PROMPT, user, CodeResult, "code_result", make_workspace_tools(ws, bool(test_command)),
        max_rounds=max_rounds, client=client, model=model, trace=trace,
    )

    written = sorted({e["path"] for e in ws.log if e["action"] == "write" and e["ok"]})
    ac_ids = sorted({a for t in work_plan["tasks"] for a in t["ac_ids"]})
    report = {
        "planned_files": planned,
        "written_files": written,
        "missing_files": [p for p in planned if not (ws.root / p).is_file()],
        "scope_violations": sorted(set(ws.scope_violations)),
        # 함수 이름에는 하이픈을 못 쓰므로 AC-001-01 과 AC_001_01 형태를 모두 인정한다
        "ac_not_referenced": [a for a in ac_ids if not ws.files_containing(a, a.replace("-", "_"))],
        "tests": ws.run_tests() if test_command else None,
    }
    return result, report
