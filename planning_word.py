"""기획서 JSON을 편집 가능한 Word 문서로 작성하고 PDF로 변환한다."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from collections.abc import Mapping
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

from docx import Document
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Mm, Pt, RGBColor


# 기존 PDF의 섹션 순서와 표 열을 Word의 제목, 문단, 표에 대응시킨다.
# 섹션은 (JSON 섹션 키, Word 제목, [(Word 소제목, JSON 키들), ...]) 순서다.
# 첫 키가 TABLES에 있으면 표로 작성하고, 나머지는 본문 문단으로 작성한다.
SECTIONS = (
    ("user_problem", "01 대상 사용자 및 문제", (
        ("대상 사용자와 이용 상황", ("target_users", "usage_context")),
        ("현재 방식과 불편", ("current_method", "pain_points")),
        ("문제의 근거", ("evidence",)),
    )),
    ("goals", "02 목표 및 성공 지표", (
        ("기대하는 변화", ("expected_change",)),
        ("성공 지표", ("metrics",)),
    )),
    ("release_scope", "03 주요 기능 및 출시 범위", (
        ("핵심 기능과 우선순위", ("features",)),
        ("이번 출시에서 제외할 범위", ("out_of_scope",)),
        ("작동 환경 및 서비스 구성", ("supported_platforms", "service_components_and_integrations")),
        ("접근·설치 및 실행", ("access_install_and_run",)),
    )),
    ("user_experience", "04 사용자 흐름 및 화면 구성", (
        ("사용 흐름도", ("flow",)),
        ("주요 화면 및 입력 방식", ("screens",)),
        ("화면 설계 참고 링크", ("design_links",)),
    )),
    ("policies", "05 기능 정책 및 예외 처리", (
        ("이용 조건 및 권한", ("eligibility_and_permissions",)),
        ("동작 규칙 및 상태 변화", ("behavior_and_state_changes",)),
        ("예외 상황 및 대응", ("exceptions",)),
        ("추가 고려 사항", ("open_considerations",)),
    )),
    ("data_rules", "06 데이터 요소 및 처리 규칙", (
        ("데이터 항목 및 구조", ("fields",)),
        ("저장 및 관리 규칙", ("storage_and_management",)),
        ("입력·검증 규칙", ("input_validation",)),
        ("오류 및 데이터 무결성 처리", ("error_and_integrity_handling",)),
    )),
    ("release_plan", "07 출시 계획 및 검증", (
        ("일정 및 담당", ("milestones",)),
        ("출시 전 확인", ("pre_launch_checks",)),
        ("출시 후 확인", ("post_launch_checks",)),
        ("용어 및 참고 자료", ("glossary_and_references",)),
    )),
)

# 각 열은 (열 제목, JSON 키들, 열 너비 mm) 순서다. 열 너비 합계는 A4 본문 너비인 170mm다.
# JSON 배열의 객체 하나가 표의 한 행이다. 한 열에 여러 키가 있으면 같은 셀에 넣는다.
TABLES = {
    "metrics": (
        ("지표", ("name",), 49), ("현재값", ("current_value",), 23),
        ("목표값", ("target_value",), 26),
        ("측정 방법·시점", ("measurement_method", "measurement_timing"), 72),
    ),
    "features": (
        ("기능명", ("name",), 44), ("핵심 동작 / 제공 가치", ("behavior_and_value",), 83),
        ("우선순위", ("priority",), 21), ("출시 포함", ("included_in_release",), 22),
    ),
    "screens": (
        ("화면 / 단계", ("screen_or_step",), 36),
        ("표시 정보·입력 항목", ("displayed_information", "input_fields"), 67),
        ("사용자 행동 / 다음 화면", ("user_action", "next_screen"), 67),
    ),
    "exceptions": (
        ("발생 상황 / 조건", ("condition",), 53), ("시스템 처리", ("system_response",), 57),
        ("사용자 안내·복구", ("user_guidance", "recovery"), 60),
    ),
    "fields": (
        ("데이터 항목", ("name",), 37), ("용도 / 출처", ("purpose", "source"), 58),
        ("형식·구조", ("format_and_structure",), 50), ("필수 여부", ("required",), 25),
    ),
    "milestones": (
        ("단계", ("stage",), 28), ("일정", ("schedule",), 37), ("담당", ("owner",), 30),
        ("산출물 / 완료 기준", ("deliverable", "completion_criteria"), 75),
    ),
    "glossary_and_references": (
        ("용어 / 자료명", ("name",), 51), ("정의 / 링크", ("definition", "url"), 119),
    ),
}
COVER = {"project_name": "프로젝트명", "team_name": "팀명", "team_members": "팀원",
         "written_on": "작성일", "summary": "기획 개요 / 요약"}
LIST_FIELDS = {
    "team_members", "evidence", "out_of_scope", "supported_platforms", "input_fields",
    "design_links", "open_considerations", "pre_launch_checks", "post_launch_checks",
}
# 불리언 표시 문구는 (true일 때, false일 때) 순서이며 null은 빈값으로 처리한다.
BOOLEAN_LABELS = {"included_in_release": ("포함", "제외"), "required": ("필수", "선택")}
# 한 셀이나 문단에 값이 여러 개 들어갈 때 각 값의 의미를 구분하는 라벨이다.
PART_LABELS = {
    "target_users": "대상", "usage_context": "이용 상황",
    "current_method": "현재 방식", "pain_points": "불편",
    "supported_platforms": "지원 환경", "service_components_and_integrations": "서비스 구성",
    "measurement_method": "방법", "measurement_timing": "시점",
    "displayed_information": "표시", "input_fields": "입력",
    "user_action": "행동", "next_screen": "다음",
    "user_guidance": "안내", "recovery": "복구", "purpose": "용도", "source": "출처",
    "deliverable": "산출물", "completion_criteria": "완료 기준",
}


class PlanningDocumentError(ValueError):
    """입력 검증과 PDF 변환 오류를 호출자에게 전달한다."""


def _check_object(record, keys, path):
    """앞팀과 합의한 JSON 키를 검증한다. 값이 없는 키는 생략할 수 있다."""
    if not isinstance(record, Mapping):
        raise PlanningDocumentError(f"{path}: JSON 객체가 필요합니다.")
    unknown = record.keys() - set(keys)
    if unknown:
        names = ", ".join(sorted(map(str, unknown)))
        raise PlanningDocumentError(f"{path}: 지원하지 않는 키 ({names})")


def _value(record, key, path):
    """필드 하나의 타입을 검증하고 Word에 넣을 문자열로 바꾼다."""
    value = record.get(key)
    if value is None:
        return ""
    if key in BOOLEAN_LABELS:
        if type(value) is not bool:
            raise PlanningDocumentError(f"{path}.{key}: true, false 또는 null이 필요합니다.")
        return BOOLEAN_LABELS[key][0 if value else 1]
    if key in LIST_FIELDS:
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise PlanningDocumentError(f"{path}.{key}: 문자열 배열 또는 null이 필요합니다.")
        separator = ", " if key in {"team_members", "supported_platforms", "input_fields"} else "\n"
        return separator.join(item.strip() for item in value if item.strip())
    if not isinstance(value, str):
        raise PlanningDocumentError(f"{path}.{key}: 문자열 또는 null이 필요합니다.")
    return value.strip()


def _combine(record, keys, path):
    """같은 문단이나 표 셀에 들어가는 값들을 라벨과 줄바꿈으로 묶는다."""
    parts = []
    for key in keys:
        text = _value(record, key, path)
        if text:
            label = PART_LABELS.get(key) if len(keys) > 1 else None
            parts.append(f"{label}: {text}" if label else text)
    return "\n".join(parts)


def _format_document(doc, font_name):
    """기존 양식과 같은 A4 크기를 적용하고 문서의 기본 글꼴과 제목 스타일을 정한다."""
    page = doc.sections[0]
    page.page_width, page.page_height = Mm(210), Mm(297)
    page.left_margin = page.right_margin = Mm(20)
    page.top_margin = page.bottom_margin = Mm(18)
    for name, size in (("Normal", 11), ("Title", 24), ("Heading 1", 16), ("Heading 2", 12)):
        style = doc.styles[name]
        style.font.name = font_name
        style.font.size = Pt(size)
        style.font.color.rgb = RGBColor(0, 0, 0)
        fonts = style.element.get_or_add_rPr().rFonts
        # Word 테마 글꼴이 한글 글꼴 지정을 덮어쓰지 않도록 테마 속성을 제거한다.
        for attribute in ("asciiTheme", "hAnsiTheme", "eastAsiaTheme", "cstheme"):
            fonts.attrib.pop(qn(f"w:{attribute}"), None)
        fonts.set(qn("w:eastAsia"), font_name)
    title_props = doc.styles["Title"].element.get_or_add_pPr()
    border = title_props.find(qn("w:pBdr"))
    if border is not None:
        title_props.remove(border)


def _add_table(doc, records, columns, path):
    """행 높이를 고정하지 않고 다음 페이지에도 열 제목을 반복한다."""
    if not isinstance(records, list):
        raise PlanningDocumentError(f"{path}: 행 객체의 배열이 필요합니다.")
    keys = [key for _, fields, _ in columns for key in fields]
    rows = []
    for index, record in enumerate(records):
        row_path = f"{path}[{index}]"
        _check_object(record, keys, row_path)
        values = [_combine(record, fields, row_path) for _, fields, _ in columns]
        # 빈 JSON 템플릿에 있는 예시 행은 실제 데이터 행으로 세지 않는다.
        if any(values):
            rows.append(values)

    table = doc.add_table(rows=1, cols=len(columns))
    table.style = "Table Grid"
    table.autofit = False
    # 표가 다음 페이지로 넘어갈 때 첫 행을 머리글로 다시 표시하는 Word 설정이다.
    table.rows[0]._tr.get_or_add_trPr().append(OxmlElement("w:tblHeader"))
    for column, (_, _, width) in zip(table.columns, columns):
        column.width = Mm(width)
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        line = OxmlElement(f"w:{edge}")
        line.set(qn("w:val"), "single")
        line.set(qn("w:sz"), "4")
        line.set(qn("w:color"), "D9D9D9")
        borders.append(line)
    table._tbl.tblPr.append(borders)

    # 데이터가 없어도 Word에서 바로 작성할 수 있도록 편집 가능한 빈 행 하나를 둔다.
    for index, values in enumerate([[label for label, _, _ in columns], *(rows or [[""] * len(columns)])]):
        row = table.rows[0] if index == 0 else table.add_row()
        for cell, value, (_, _, width) in zip(row.cells, values, columns):
            cell.width = Mm(width)
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            paragraph = cell.paragraphs[0]
            paragraph.paragraph_format.space_before = Pt(4)
            paragraph.paragraph_format.space_after = Pt(4)
            run = paragraph.add_run(value)
            run.font.size = Pt(10)
            if index == 0:
                run.bold = True
                paragraph.paragraph_format.keep_with_next = True
                fill = OxmlElement("w:shd")
                fill.set(qn("w:fill"), "E8EFF2")
                cell._tc.get_or_add_tcPr().append(fill)


def render_planning_docx(data: Mapping, *, font_name: str = "Malgun Gothic") -> bytes:
    """앞팀에서 파싱한 JSON 객체를 받아 편집 가능한 Word 파일을 bytes로 반환한다."""
    _check_object(data, ("overview", *(key for key, _, _ in SECTIONS)), "$")
    overview = data.get("overview", {})
    _check_object(overview, COVER, "overview")
    doc = Document()
    _format_document(doc, font_name)
    project_name = _value(overview, "project_name", "overview")
    title = project_name or "서비스·기능 기획서"
    doc.core_properties.title = title[:255]  # Word 파일 속성의 길이 제한이며 본문 제목은 그대로 쓴다.
    doc.add_heading(title, level=0)
    for key in ("team_name", "team_members", "written_on"):
        paragraph = doc.add_paragraph()
        paragraph.add_run(f"{COVER[key]}  ").bold = True
        paragraph.add_run(_value(overview, key, "overview") or "—")
    doc.add_heading(COVER["summary"], level=2)
    doc.add_paragraph(_value(overview, "summary", "overview") or "—")

    # 본문과 표를 양식 순서대로 추가한다. 줄바꿈과 페이지 나눔은 Word가 처리한다.
    for key, title, blocks in SECTIONS:
        record = data.get(key, {})
        _check_object(record, (field for _, fields in blocks for field in fields), key)
        doc.add_heading(title, level=1)
        for label, fields in blocks:
            doc.add_heading(label, level=2)
            field = fields[0]
            if field in TABLES:
                _add_table(doc, record.get(field, []), TABLES[field], f"{key}.{field}")
            else:
                doc.add_paragraph(_combine(record, fields, key) or "—")
    output = BytesIO()
    doc.save(output)
    return output.getvalue()


def convert_docx_to_pdf(docx_bytes: bytes, *, soffice: str | Path | None = None) -> bytes:
    """Word 바이트를 LibreOffice에서 열어 PDF 바이트로 변환한다. soffice로 실행 파일을 지정할 수 있다."""
    if soffice is None:
        soffice = shutil.which("soffice.com") or shutil.which("soffice")
        if not soffice and os.name == "nt":
            candidate = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "LibreOffice/program/soffice.com"
            soffice = candidate if candidate.is_file() else None
    if not soffice:
        raise PlanningDocumentError("LibreOffice를 설치하거나 soffice 실행 파일 경로를 지정하세요.")

    # 변환마다 프로필을 분리해 실행 중인 LibreOffice와 동시 요청의 영향을 피한다.
    with TemporaryDirectory(prefix="planning-") as directory:
        root = Path(directory).resolve()
        source = root / "planning.docx"
        source.write_bytes(docx_bytes)
        try:
            result = subprocess.run(
                [str(soffice), f"-env:UserInstallation={(root / 'profile').as_uri()}",
                 "--headless", "--convert-to", "pdf:writer_pdf_Export", "--outdir", str(root), str(source)],
                capture_output=True, timeout=120,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise PlanningDocumentError(f"LibreOffice 실행 실패: {error}") from error
        pdf = source.with_suffix(".pdf")
        # 종료 코드가 0이어도 변환 파일이 생겼는지 확인해야 한다.
        if result.returncode or not pdf.is_file():
            detail = (result.stderr or result.stdout).decode("utf-8", errors="replace").strip()
            raise PlanningDocumentError(f"Word를 PDF로 변환하지 못했습니다. {detail}")
        content = pdf.read_bytes()
        if not content.startswith(b"%PDF-"):
            raise PlanningDocumentError("변환 결과가 올바른 PDF 파일이 아닙니다.")
        return content


def main() -> None:
    """명령줄에서 JSON을 읽어 같은 이름의 .docx와 .pdf를 저장한다."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("json_file", type=Path)
    parser.add_argument("output_stem", type=Path, help="확장자를 제외한 출력 파일 경로")
    parser.add_argument("--soffice", help="LibreOffice 실행 파일 경로")
    parser.add_argument("--font", default="Malgun Gothic", help="변환 환경에 설치된 한글 글꼴 이름")
    args = parser.parse_args()
    try:
        docx_path = Path(f"{args.output_stem}.docx")
        pdf_path = Path(f"{args.output_stem}.pdf")
        if args.json_file.resolve() in {docx_path.resolve(), pdf_path.resolve()}:
            raise PlanningDocumentError("출력 경로는 입력 파일과 달라야 합니다.")
        data = json.loads(args.json_file.read_text(encoding="utf-8-sig"))
        docx = render_planning_docx(data, font_name=args.font)
        pdf = convert_docx_to_pdf(docx, soffice=args.soffice)
        # 두 파일을 모두 임시 위치에 쓴 뒤 교체해 저장 실패가 기존 파일을 잘라내지 않게 한다.
        with TemporaryDirectory(dir=docx_path.parent) as directory:
            staging = Path(directory)
            (staging / docx_path.name).write_bytes(docx)
            (staging / pdf_path.name).write_bytes(pdf)
            for path in (docx_path, pdf_path):
                (staging / path.name).replace(path)
    except (OSError, ValueError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
