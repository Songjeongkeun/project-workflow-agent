"""문서(기획서 PDF/Word/텍스트 + UI 기획서) → 기획서 JSON → 기술 명세 → 작업 계획 → 코드를 한 번에 실행한다.

    python build.py --doc 점검안내_기획서.pdf --ui-doc ui_plan.md --workspace output/project
    python build.py --doc 기획서.pdf --stack "HTML, CSS" --test-cmd "python -m pytest -q" --yes

각 단계의 결과는 --out 폴더(기본 output/run)에 JSON으로 남는다: planning.json, criteria.json, tech_spec.json, work_plan.json
작업 계획(코드를 쓸 수 있는 파일 범위)을 보여 주고 사람이 확인한 뒤에 코드를 쓴다 (--yes면 확인을 건너뜀).

주의: --test-cmd를 주면 모델이 쓴 코드가 이 컴퓨터에서 실행됩니다 (Docker 컨테이너 실행을 권장).
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
from pathlib import Path
from typing import Any, Callable

from code_generator import plan_work, write_code
from code_main import print_write_result
from extractor import ExtractionError, extract_planning_doc, missing_fields
from readers import read_input
from spec_generator import assign_feature_ids, derive_acceptance_criteria, generate_tech_spec


def stack_text(plan: dict[str, Any]) -> str | None:
    """기획서의 '사용 기술 및 선택 근거' 표를 모델에게 줄 문장으로 바꾼다."""
    rows = (plan.get("release_scope") or {}).get("tech_stack") or []
    lines = []
    for r in rows:
        if r.get("technology"):
            why = f" — {r['reason']}" if r.get("reason") else ""
            lines.append(f"{r.get('area') or '기술'}: {r['technology']}{why}")
    return "\n".join(lines) or None


def _save(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_pipeline(
    doc: str,
    *,
    ui_doc: str | None = None,
    stack: str | None = None,
    workspace: str = "output/project",
    out_dir: str = "output/run",
    test_command: list[str] | None = None,
    approve: Callable[[dict[str, Any]], bool] = lambda wp: True,
    client: Any = None,
    model: str | None = None,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    """전체 파이프라인. 단계마다 결과를 파일로 남기고, 작업 계획 확인(approve)을 통과해야 코드를 쓴다.

    반환: {"ok": bool, "stage": 멈춘/끝난 단계, "problems": [...], 그리고 단계별 결과}
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    kw = {"client": client, "model": model}
    result: dict[str, Any] = {"ok": False, "problems": []}

    # 1) 문서 → 기획서 JSON
    log(f"[1/4] 문서 읽기와 요구사항 추출: {doc}")
    plan = extract_planning_doc(read_input(doc), **kw)
    _save(out / "planning.json", plan)
    features = assign_feature_ids(plan)
    if not features:
        result["stage"] = "extract"
        result["problems"] = ["이번 출시에 포함된 기능을 찾지 못했습니다. 기획서의 기능 목록을 확인하세요."]
        return result
    criteria = derive_acceptance_criteria(plan)
    _save(out / "criteria.json", criteria)
    log(f"      기능 {len(features)}개 ({', '.join(f['id'] for f in features)}), 완료 조건 {len(criteria)}개, 비어 있는 항목 {len(missing_fields(plan))}개")

    stack = stack or stack_text(plan)
    if not stack:
        result["stage"] = "extract"
        result["problems"] = ["기술 스택을 알 수 없습니다. 기획서에 '사용 기술' 표가 없으면 --stack으로 알려 주세요."]
        return result
    contract = (plan.get("release_scope") or {}).get("access_install_and_run")
    ui = read_input(ui_doc) if ui_doc else None

    # 2) 기술 명세
    log("[2/4] 기술 명세 생성 (업무 영역 규칙 도구 사용)")
    trace: list = []
    spec, problems = generate_tech_spec(
        plan, acceptance_criteria=criteria or None, screen_plan=ui, tech_stack=stack,
        execution_contract=contract, trace=trace, **kw,
    )
    _save(out / "tech_spec.json", spec)
    for t in trace:
        log("      도구 호출 " + t["tool"] + ": " + (", ".join(t["arguments"].get("domains") or []) or "(해당 영역 없음)"))
    log(f"      API {len(spec['apis'])}개, 데이터 {len(spec['data_entities'])}개, 사람이 답할 질문 {len(spec['open_questions'])}개")
    result.update(spec=spec, open_questions=spec["open_questions"], spec_problems=problems)
    for m in problems:  # 명세의 빈틈은 경고로 남기고 계속한다 (코드 범위와 직접 관련된 작업 계획 문제만 막는다)
        log(f"      ⚠ 명세 검증: {m}")

    # 3) 작업 계획 + 사람 확인
    log("[3/4] 작업 계획 (코드를 쓸 수 있는 파일 범위)")
    wp, problems = plan_work(
        spec, plan, acceptance_criteria=criteria or None, screen_plan=ui, tech_stack=stack,
        execution_contract=contract, **kw,
    )
    _save(out / "work_plan.json", wp)
    for t in wp["tasks"]:
        log(f"      {t['id']} {t['title']} (기능 {', '.join(t['feature_ids'])})")
        for f in t["files"]:
            log(f"          [{f['action']}] {f['path']} — {f['purpose']}")
    if problems:
        result.update(stage="plan", problems=problems)
        return result
    if not approve(wp):
        result.update(stage="plan", problems=["사용자가 작업 계획을 승인하지 않아 코드를 쓰지 않았습니다."], work_plan=wp)
        return result

    # 4) 코드 작성
    log("[4/4] 코드 작성")
    trace = []
    code_result, report = write_code(
        wp, spec, plan, workspace, acceptance_criteria=criteria or None, screen_plan=ui,
        tech_stack=stack, execution_contract=contract, test_command=test_command, trace=trace, **kw,
    )
    result.update(stage="write", work_plan=wp, code_result=code_result, report=report, trace=trace)
    t = report["tests"]
    result["ok"] = not (
        report["missing_files"] or report["scope_violations"] or report["ac_not_referenced"] or (t is not None and t["exit_code"] != 0)
    )
    return result


def main() -> int:
    p = argparse.ArgumentParser(description="문서(+UI 기획서) → 코드 한 번에 만들기")
    p.add_argument("--doc", required=True, help="기획서 파일 (.pdf .docx .md .txt)")
    p.add_argument("--ui-doc", help="UI 기획서/화면 계획 파일 (.pdf .docx .md .txt)")
    p.add_argument("--stack", help="기술 스택 (생략하면 기획서의 '사용 기술' 표를 사용)")
    p.add_argument("--workspace", default="output/project", help="코드를 쓸 폴더")
    p.add_argument("--out", default="output/run", help="단계별 JSON을 남길 폴더")
    p.add_argument("--test-cmd", help="테스트 명령 (생략하면 테스트를 실행하지 않음)")
    p.add_argument("--yes", action="store_true", help="작업 계획 확인을 건너뛰고 바로 코드를 쓴다")
    p.add_argument("--model")
    a = p.parse_args()

    def approve(wp: dict[str, Any]) -> bool:
        if a.yes:
            return True
        if not sys.stdin.isatty():
            print("확인할 수 없는 환경입니다. 위 작업 계획을 확인한 뒤 --yes로 다시 실행하세요.", file=sys.stderr)
            return False
        return input("\n위 파일 범위로 코드를 작성할까요? [y/N] ").strip().lower() in {"y", "yes"}

    test_cmd = shlex.split(a.test_cmd, posix=(os.name != "nt")) if a.test_cmd else None
    if test_cmd:
        print(f"⚠ 모델이 쓴 코드를 이 컴퓨터에서 실행합니다: {a.test_cmd}\n  (Docker 컨테이너에서 실행하는 명령을 권장합니다)")
    try:
        r = run_pipeline(
            a.doc, ui_doc=a.ui_doc, stack=a.stack, workspace=a.workspace, out_dir=a.out,
            test_command=test_cmd, approve=approve, **({"model": a.model} if a.model else {}),
        )
    except ExtractionError as e:
        print(f"오류: {e}", file=sys.stderr)
        return 1

    if r.get("open_questions"):
        print(f"\n사람이 답해야 할 질문 {len(r['open_questions'])}개 (기술 명세 기준):")
        for q in r["open_questions"]:
            print(f"  ? [{q['feature_id']}/{q['axis']}] {q['question']}")
    if r.get("spec_problems"):
        print(f"\n⚠ 기술 명세 검증 경고 {len(r['spec_problems'])}개 (코드는 계속 진행됨)")
    if "report" in r:
        return print_write_result(r["code_result"], r["report"], r["trace"], a.workspace)
    print(f"\n[{r.get('stage')} 단계에서 멈춤]", file=sys.stderr)
    for m in r["problems"]:
        print(f"  - {m}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
