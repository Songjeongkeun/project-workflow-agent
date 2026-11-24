"""확정 기획서 JSON → 기술 명세 JSON.

    python spec_main.py output/planning.json -o output/tech_spec.json
    python spec_main.py output/planning.json --stack "FastAPI + SQLite" --criteria criteria.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from extractor import ExtractionError
from readers import read_input
from spec_generator import assign_feature_ids, derive_acceptance_criteria, generate_tech_spec


def main() -> int:
    p = argparse.ArgumentParser(description="기획서 JSON에서 기술 명세 생성")
    p.add_argument("plan", help="기획서 JSON 경로")
    p.add_argument("-o", "--output", default="output/tech_spec.json")
    p.add_argument("--stack", help="확정한 기술 스택 (예: 'FastAPI + SQLite')")
    p.add_argument("--criteria", help="완료 조건 JSON 파일 (생략하면 기획서의 기능별 완료 기준에서 자동 생성)")
    p.add_argument("--ui-doc", help="UI 기획서/화면 계획 파일 (.pdf .docx .md .txt)")
    p.add_argument("--update", metavar="JSON", help="이전 기술 명세 JSON (수정 모드)")
    p.add_argument("--no-tools", action="store_true", help="업무 영역 도구를 쓰지 않고 공통 점검 축만 사용 (비교용)")
    p.add_argument("--model", help="모델 이름 (기본: .env의 OPENAI_MODEL)")
    args = p.parse_args()

    load = lambda path: json.loads(Path(path).read_text(encoding="utf-8"))  # noqa: E731
    plan = load(args.plan)
    trace: list = []
    try:
        spec, problems = generate_tech_spec(
            plan,
            acceptance_criteria=load(args.criteria) if args.criteria else derive_acceptance_criteria(plan) or None,
            screen_plan=read_input(args.ui_doc) if args.ui_doc else None,
            tech_stack=args.stack,
            previous=load(args.update) if args.update else None,
            use_domain_tools=not args.no_tools,
            trace=trace,
            **({"model": args.model} if args.model else {}),
        )
    except ExtractionError as e:
        print(f"오류: {e}", file=sys.stderr)
        return 1

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"저장: {out}")
    print("기능 ID:", ", ".join(f"{f['id']}={f['name']}" for f in assign_feature_ids(plan)))
    for t in trace:
        shown = ", ".join(t["arguments"].get("domains") or []) or "(해당 영역 없음)" if t["ok"] else f"오류: {t['result']}"
        print(f"도구 호출 {t['tool']}: {shown}")
    print(f"API {len(spec['apis'])}개, 데이터 {len(spec['data_entities'])}개, 점검 결과 {len(spec['checklist_results'])}줄")
    if spec["open_questions"]:
        print(f"사람이 답해야 할 질문 {len(spec['open_questions'])}개:")
        for q in spec["open_questions"]:
            print(f"  ? [{q['feature_id']}/{q['axis']}] {q['question']}" + (f" (제안: {q['proposal']})" if q["proposal"] else ""))
    if problems:
        print(f"⚠ 검증 문제 {len(problems)}개가 남았습니다:", file=sys.stderr)
        for m in problems:
            print(f"  - {m}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
