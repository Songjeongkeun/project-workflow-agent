"""입력 파일(txt/md/pdf)에서 텍스트를 읽는다."""
from __future__ import annotations

from collections import Counter
from pathlib import Path

from extractor import ExtractionError

MIN_PDF_CHARS = 30  # 이보다 적으면 글자가 없는 PDF(스캔본 등)로 본다


def strip_repeated_lines(lines_by_page: list[list[str]]) -> list[list[str]]:
    """모든 쪽에 반복되는 머리글·바닥글을 지운다 (쪽 번호만 다른 줄 포함). 3쪽 이상일 때만 적용.

    '쪽'을 세는 것이지 줄을 세는 것이 아니다: 한 쪽 안에 FEAT-001, FEAT-002처럼 숫자만 다른 줄이
    여러 개 있어도 그 쪽은 한 번만 센다. (예전에는 줄 수로 세서 기능 ID 줄이 통째로 지워졌다.)
    """
    n = len(lines_by_page)
    if n < 3:
        return lines_by_page

    def norm(ln: str) -> str:
        return "".join(ch for ch in ln if not ch.isdigit())

    counts = Counter(k for lines in lines_by_page for k in {norm(ln) for ln in lines})
    repeated = {k for k, c in counts.items() if c >= n}
    return [[ln for ln in lines if norm(ln) not in repeated] for lines in lines_by_page]


def read_pdf(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as e:  # pragma: no cover
        raise ExtractionError("PDF를 읽으려면 `pip install -r requirements.txt` 를 먼저 실행하세요.") from e

    try:
        reader = PdfReader(str(path))
        pages = [(p.extract_text() or "") for p in reader.pages]
    except Exception as e:
        raise ExtractionError(f"PDF를 읽지 못했습니다: {e}") from e

    lines_by_page = [[ln.strip() for ln in t.splitlines() if ln.strip()] for t in pages]

    lines_by_page = strip_repeated_lines(lines_by_page)

    text = "\n\n".join("\n".join(lines) for lines in lines_by_page).strip()
    if len(text) < MIN_PDF_CHARS:
        raise ExtractionError(
            "PDF에서 글자를 찾지 못했습니다. 스캔본/이미지 PDF는 지원하지 않습니다 (텍스트가 선택되는 PDF만 가능)."
        )
    return text


def read_docx(path: Path) -> str:
    try:
        import docx
        from docx.table import Table
        from docx.text.paragraph import Paragraph
    except ImportError as e:  # pragma: no cover
        raise ExtractionError("Word 파일을 읽으려면 `pip install -r requirements.txt` 를 먼저 실행하세요.") from e
    try:
        d = docx.Document(str(path))
        parts: list[str] = []
        for el in d.element.body.iterchildren():  # 본문 순서 그대로 (문단과 표가 섞여 있어도)
            if el.tag.endswith("}p"):
                t = Paragraph(el, d).text.strip()
                if t:
                    parts.append(t)
            elif el.tag.endswith("}tbl"):
                for row in Table(el, d).rows:
                    cells = [c.text.strip().replace("\n", " / ") for c in row.cells]
                    parts.append(" | ".join(cells))
    except Exception as e:
        raise ExtractionError(f"Word 파일을 읽지 못했습니다: {e}") from e
    text = "\n".join(parts).strip()
    if not text:
        raise ExtractionError("Word 파일에서 글자를 찾지 못했습니다.")
    return text


def read_input(path: str | Path) -> str:
    p = Path(path)
    if not p.is_file():
        raise ExtractionError(f"파일을 찾을 수 없습니다: {p}")
    suffix = p.suffix.lower()
    if suffix == ".pdf":
        return read_pdf(p)
    if suffix == ".docx":
        return read_docx(p)
    if suffix in {".txt", ".md", ".json", ""}:
        return p.read_text(encoding="utf-8")
    raise ExtractionError(f"지원하지 않는 파일 형식입니다: {suffix} (txt, md, json, pdf, docx만 가능)")
