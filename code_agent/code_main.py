"""기술 명세 → 작업 계획 → 코드 작성 (두 단계, 사이에 사람이 계획을 확인한다).

    # 1) 작업 계획 만들기 → output/work_plan.json 을 열어 files(쓸 수 있는 파일 범위)를 확인·수정
    python code_main.py plan output/tech_spec.json --plan output/planning.json --stack "FastAPI + SQLite"

    # 2) 확인한 계획의 파일 범위 안에서 코드 쓰기
    python code_main.py write output/work_plan.json --spec output/tech_spec.json --plan output/planning.json \\
        --workspace output/project --test-cmd "python -m pytest -q"

주의: --test-cmd를 주면 모델이 쓴 코드가 이 컴퓨터에서 실행됩니다. Docker 컨테이너에서 도는 명령을 권장합니다
(예: --test-cmd "docker compose run --rm test"). 주지 않으면 파일만 쓰고 아무것도 실행하지 않습니다.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
from pathlib import Path

from extractor import ExtractionError
from code_generator import plan_work, validate_work_plan, write_code
from readers import read_input
from spec_generator import assign_feature_ids, derive_acceptance_criteria


def _load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def print_write_result(result, report, trace, workspace) -> int:
    """코드 작성 결과를 보여 준다. 문제가 있으면 2, 없으면 0을 돌려준다."""
    print(f"\n[모델 요약] {result['summary']}")
    for s in result["task_status"]:
        print(f"  {'완료' if s['done'] else '미완'} {s['task_id']}" + (f" — {s['note']}" if s["note"] else ""))
    print(f"\n[코드가 확인한 결과] (작업 공간: {workspace})")
    print(f"  쓴 파일 {len(report['written_files'])}/{len(report['planned_files'])}개, 도구 호출 {len(trace)}번")
    bad = False
    if report["missing_files"]:
        bad = True
        print("  ✗ 계획했지만 만들어지지 않은 파일:", ", ".join(report["missing_files"]))
    if report["scope_violations"]:
        bad = True
        print("  ✗ 범위 밖 파일 쓰기 시도(거부됨, 승인 필요):", ", ".join(report["scope_violations"]))
    if report["ac_not_referenced"]:
        bad = True
        print("  ✗ 어떤 파일에도 나오지 않는 완료 조건:", ", ".join(report["ac_not_referenced"]))
    t = report["tests"]
    if t is None:
        print("  - 테스트는 실행하지 않았습니다 (--test-cmd 없음)")
    else:
        ok = t["exit_code"] == 0
        bad = bad or not ok
        print(f"  {'✓' if ok else '✗'} 테스트: " + ("시간 초과" if t["timed_out"] else f"종료 코드 {t['exit_code']}"))
        if not ok:
            print("    " + t["output"][-800:].replace("\n", "\n    "))
    if result["unresolved"]:
        print("  사람이 확인할 것:")
        for u in result["unresolved"]:
            print(f"    ? {u}")
    return 2 if bad else 0


def cmd_plan(a) -> int:
    spec, plan = _load(a.spec), _load(a.plan)
    criteria = _load(a.criteria) if a.criteria else derive_acceptance_criteria(plan) or None
    ui = read_input(a.ui_doc) if a.ui_doc else None
    wp, problems = plan_work(
        spec, plan, acceptance_criteria=criteria, screen_plan=ui, tech_stack=a.stack,
        **({"model": a.model} if a.model else {}),
    )
    out = Path(a.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(wp, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"저장: {out}")
    for t in wp["tasks"]:
        print(f"  {t['id']} {t['title']}  (기능 {', '.join(t['feature_ids'])})")
        for f in t["files"]:
            print(f"      [{f['action']}] {f['path']} — {f['purpose']}")
    print("\n위 파일 목록이 코드를 쓸 수 있는 범위입니다. 확인·수정한 뒤 write 단계를 실행하세요.")
    if problems:
        print(f"⚠ 검증 문제 {len(problems)}개:", file=sys.stderr)
        for m in problems:
            print(f"  - {m}", file=sys.stderr)
        return 2
    return 0


def cmd_write(a) -> int:
    wp, spec, plan = _load(a.work_plan), _load(a.spec), _load(a.plan)
    criteria = _load(a.criteria) if a.criteria else derive_acceptance_criteria(plan) or None
    ui = read_input(a.ui_doc) if a.ui_doc else None
    problems = validate_work_plan(
        wp, [f["id"] for f in assign_feature_ids(plan)], [c["id"] for c in criteria or [] if c.get("id")]
    )
    if problems:  # 사람이 계획을 고쳤다면 다시 확인
        print("작업 계획에 문제가 있어 코드를 쓰지 않았습니다:", file=sys.stderr)
        for m in problems:
            print(f"  - {m}", file=sys.stderr)
        return 2

    test_cmd = shlex.split(a.test_cmd, posix=(os.name != "nt")) if a.test_cmd else None
    if test_cmd:
        print(f"⚠ 모델이 쓴 코드를 이 컴퓨터에서 실행합니다: {a.test_cmd}\n  (Docker 컨테이너에서 실행하는 명령을 권장합니다)")
    trace: list = []
    result, report = write_code(
        wp, spec, plan, a.workspace, acceptance_criteria=criteria, screen_plan=ui, tech_stack=a.stack,
        test_command=test_cmd, trace=trace, **({"model": a.model} if a.model else {}),
    )

    return print_write_result(result, report, trace, a.workspace)


def main() -> int:
    p = argparse.ArgumentParser(description="기술 명세 → 작업 계획 → 코드")
    sub = p.add_subparsers(dest="cmd", required=True)

    pl = sub.add_parser("plan", help="작업 계획 만들기")
    pl.add_argument("spec", help="기술 명세 JSON")
    pl.add_argument("--plan", required=True, help="기획서 JSON")
    pl.add_argument("-o", "--output", default="output/work_plan.json")
    pl.add_argument("--stack")
    pl.add_argument("--criteria", help="완료 조건 JSON (생략하면 기획서의 기능별 완료 기준에서 자동 생성)")
    pl.add_argument("--ui-doc", help="UI 기획서/화면 계획 파일 (.pdf .docx .md .txt)")
    pl.add_argument("--model")
    pl.set_defaults(fn=cmd_plan)

    wr = sub.add_parser("write", help="확정한 계획의 범위 안에서 코드 쓰기")
    wr.add_argument("work_plan", help="확인한 작업 계획 JSON")
    wr.add_argument("--spec", required=True)
    wr.add_argument("--plan", required=True)
    wr.add_argument("--workspace", default="output/project")
    wr.add_argument("--stack")
    wr.add_argument("--criteria")
    wr.add_argument("--ui-doc", help="UI 기획서/화면 계획 파일 (.pdf .docx .md .txt)")
    wr.add_argument("--test-cmd", help="테스트 명령 (생략하면 테스트를 실행하지 않음)")
    wr.add_argument("--model")
    wr.set_defaults(fn=cmd_write)

    a = p.parse_args()
    try:
        return a.fn(a)
    except ExtractionError as e:
        print(f"오류: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
