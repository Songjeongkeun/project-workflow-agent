"""사용 예:
    # 새로 추출
    python main.py -f idea.txt -o output/planning.json
    python main.py "우리 팀은 ... 앱을 만들려고 해"
    python main.py -f proposal.pdf -o output/planning.json      # 글자가 있는 PDF도 가능

    # 이미 추출한 JSON 수정 (바뀐 항목만 반영, 이전 버전은 output/history/에 백업)
    python main.py --update output/planning.json "재예약률 목표를 40%로 바꿔줘"
    python main.py --update output/planning.json -f changes.txt
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

from extractor import ExtractionError, extract_planning_doc, missing_fields, update_planning_doc
from readers import read_input
from schema import diff_docs


def backup(path: Path) -> Path | None:
    """덮어쓰기 전에 기존 파일을 <폴더>/history/에 시각을 붙여 복사한다."""
    if not path.exists():
        return None
    hist = path.parent / "history"
    hist.mkdir(parents=True, exist_ok=True)
    dest = hist / f"{path.stem}_{datetime.now():%Y%m%d_%H%M%S}{path.suffix}"
    shutil.copy2(path, dest)
    return dest


def _short(v, n: int = 50) -> str:
    t = json.dumps(v, ensure_ascii=False)
    return t if len(t) <= n else t[: n - 1] + "…"


def main() -> int:
    p = argparse.ArgumentParser(description="프롬프트에서 기획서 JSON 추출 / 수정")
    p.add_argument("text", nargs="?", help="설명 또는 수정 요청 텍스트 (생략 시 -f 또는 stdin)")
    p.add_argument("-f", "--file", help="텍스트가 담긴 파일 (.txt, .md, .pdf)")
    p.add_argument("-o", "--output", help="결과 JSON 경로 (기본: 새로 추출=output/planning.json, 수정=--update 파일)")
    p.add_argument("--update", metavar="JSON", help="수정할 기존 기획서 JSON. 텍스트는 수정 요청으로 처리")
    p.add_argument("--model", help="모델 이름 (기본: .env의 OPENAI_MODEL)")
    args = p.parse_args()

    if args.text:
        text = args.text
    elif args.file:
        try:
            text = read_input(args.file)
        except ExtractionError as e:
            print(f"오류: {e}", file=sys.stderr)
            return 1
    elif not sys.stdin.isatty():
        text = sys.stdin.read()
    else:
        p.error("텍스트를 인자, -f, 또는 stdin으로 주세요.")

    previous = None
    if args.update:
        src = Path(args.update)
        if not src.is_file():
            p.error(f"--update 파일을 찾을 수 없습니다: {src}")
        previous = json.loads(src.read_text(encoding="utf-8"))
    out = Path(args.output or args.update or "output/planning.json")

    try:
        kwargs = {"model": args.model} if args.model else {}
        doc = update_planning_doc(previous, text, **kwargs) if previous else extract_planning_doc(text, **kwargs)
    except ExtractionError as e:
        print(f"오류: {e}", file=sys.stderr)
        return 1

    changes = diff_docs(previous, doc) if previous else []
    if previous and not changes:
        print("변경 사항이 없어 파일을 그대로 두었습니다.")
        return 0

    saved_backup = backup(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"저장: {out}")
    if previous:
        print(f"변경된 항목 {len(changes)}개:")
        for path, old, new in changes:
            print(f"  ~ {path}: {_short(old)} → {_short(new)}")
        if saved_backup:
            print(f"이전 버전 백업: {saved_backup}")
    missing = missing_fields(doc)
    print(f"비어 있는 항목 {len(missing)}개")
    for m in missing:
        print(f"  - {m}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
