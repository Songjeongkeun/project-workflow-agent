"""V18.42 요구사항 JSON을 지정된 기획서 양식의 Word로 작성하고 PDF로 변환한다."""

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
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Mm, Pt, RGBColor


# 원본 8페이지 PDF의 목차와 하위 항목 순서를 유지한다.
SECTIONS = (
    ("user_problem", "01 대상 사용자 및 문제", (
        ("대상 사용자", ("target_users",)),
        ("이용 상황", ("usage_context",)),
        ("현재 방식", ("current_method",)),
        ("문제점", ("pain_points",)),
        ("문제의 근거", ("evidence",)),
    )),
    ("goals", "02 목표 및 성공 지표", (
        ("기대하는 변화", ("expected_change",)),
        ("성공 지표", ("metrics",)),
    )),
    ("release_scope", "03 주요 기능 및 출시 범위", (
        ("핵심 기능과 우선순위", ("features",)),
        ("기능별 완료 기준", ("done_criteria",)),
        ("이번 출시에서 제외할 범위", ("out_of_scope",)),
        ("지원 플랫폼", ("supported_platforms",)),
        ("사용 기술 및 선택 근거", ("tech_stack",)),
        ("서비스 구성 요소 및 외부 연동", ("service_components_and_integrations",)),
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

# (열 제목, JSON 키, 열 너비 mm). 페이지의 표 너비는 170mm이다.
TABLES = {
    "metrics": (
        ("지표", ("name",), 36), ("현재값", ("current_value",), 22),
        ("목표값", ("target_value",), 22), ("측정 방법", ("measurement_method",), 52),
        ("확인 시점", ("measurement_timing",), 38),
    ),
    "features": (
        ("기능 ID", ("id",), 22), ("기능명", ("name",), 31),
        ("핵심 동작 / 제공 가치", ("behavior_and_value",), 73),
        ("우선순위", ("priority",), 22), ("출시 포함", ("included_in_release",), 22),
    ),
    "done_criteria": (
        ("기능 ID", ("id",), 35), ("완료 기준", ("done_criteria",), 135),
    ),
    "tech_stack": (
        ("기술 영역", ("area",), 35), ("사용 기술", ("choice",), 37),
        ("선택 이유", ("reason",), 64), ("출처", ("origin",), 34),
    ),
    "screens": (
        ("화면 ID / 화면·단계", ("id", "screen_or_step"), 37),
        ("표시 정보", ("displayed_information",), 32),
        ("입력 항목", ("input_fields",), 30),
        ("사용자 행동", ("user_action",), 26),
        ("다음 화면", ("next_screen",), 22),
        ("관련 기능 ID", ("related_features",), 23),
    ),
    "exceptions": (
        ("발생 상황 / 조건", ("condition",), 39),
        ("시스템 처리", ("system_response",), 40),
        ("사용자 안내", ("user_guidance",), 36),
        ("복구 방법", ("recovery",), 32),
        ("관련 기능 ID", ("related_features",), 23),
    ),
    "fields": (
        ("데이터 항목", ("name",), 29), ("용도", ("purpose",), 33),
        ("출처", ("source",), 29), ("형식·구조", ("format_and_structure",), 39),
        ("필수 여부", ("required",), 19), ("관련 기능 ID", ("related_features",), 21),
    ),
    "milestones": (
        ("단계", ("stage",), 27), ("일정", ("schedule",), 29),
        ("담당", ("owner",), 27), ("산출물", ("deliverable",), 43),
        ("완료 기준", ("completion_criteria",), 44),
    ),
    "glossary_and_references": (
        ("용어 / 자료명", ("name",), 43),
        ("정의", ("definition",), 73), ("참고 링크", ("url",), 54),
    ),
}

COVER = {"project_name": "프로젝트명", "team_name": "팀명", "team_members": "팀원",
         "written_on": "작성일", "summary": "기획 개요 / 요약"}
LIST_FIELDS = {
    "team_members", "evidence", "out_of_scope", "supported_platforms", "input_fields",
    "design_links", "open_considerations", "pre_launch_checks", "post_launch_checks",
    "done_criteria", "related_features",
}
BOOLEAN_LABELS = {"included_in_release": ("포함", "제외"), "required": ("필수", "선택")}
PART_LABELS = {"id": "ID", "screen_or_step": "화면", "related_features": "관련 기능"}
INK = RGBColor(34, 57, 75)
TEAL = RGBColor(29, 108, 112)
MUTED = RGBColor(117, 133, 143)


class PlanningDocumentError(ValueError):
    """기획서 입력 검증 및 변환 실패를 나타낸다."""


def _check_object(record, keys, path):
    """객체의 자료형과 허용된 JSON 키를 검사한다."""
    if not isinstance(record, Mapping):
        raise PlanningDocumentError(f"{path}: JSON 객체가 필요합니다.")
    unknown = record.keys() - set(keys)
    if unknown:
        names = ", ".join(sorted(map(str, unknown)))
        raise PlanningDocumentError(f"{path}: 지원하지 않는 키 ({names})")


def _value(record, key, path):
    """문서 필드의 자료형을 확인하고 표시 문자열로 변환한다."""
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
        separator = ", " if key in {"team_members", "input_fields", "related_features"} else "\n"
        return separator.join(item.strip() for item in value if item.strip())
    if not isinstance(value, str):
        raise PlanningDocumentError(f"{path}.{key}: 문자열 또는 null이 필요합니다.")
    return value.strip()


def _combine(record, keys, path):
    """여러 JSON 값을 라벨과 줄바꿈으로 결합한다."""
    parts = []
    for key in keys:
        value = _value(record, key, path)
        if value:
            label = PART_LABELS.get(key) if len(keys) > 1 else None
            parts.append(f"{label}: {value}" if label else value)
    return "\n".join(parts)


def _shade(cell, color="E8F1F1"):
    """표 셀 배경색을 설정한다."""
    fill = OxmlElement("w:shd")
    fill.set(qn("w:fill"), color)
    cell._tc.get_or_add_tcPr().append(fill)


def _set_cell_border(cell):
    """표 셀에 원본 양식과 유사한 얇은 테두리를 넣는다."""
    tc_pr = cell._tc.get_or_add_tcPr()
    borders = tc_pr.first_child_found_in("w:tcBorders")
    if borders is None:
        borders = OxmlElement("w:tcBorders")
        tc_pr.append(borders)
    for edge in ("top", "left", "bottom", "right"):
        line = OxmlElement(f"w:{edge}")
        line.set(qn("w:val"), "single")
        line.set(qn("w:sz"), "4")
        line.set(qn("w:color"), "D3DEDE")
        borders.append(line)


def _format_document(doc, font_name):
    """A4, 한글 글꼴, 원본 양식의 제목·머리말·꼬리말 스타일을 설정한다."""
    page = doc.sections[0]
    page.page_width, page.page_height = Mm(210), Mm(297)
    page.left_margin = page.right_margin = Mm(20)
    page.top_margin, page.bottom_margin = Mm(17), Mm(16)
    page.header_distance, page.footer_distance = Mm(7), Mm(9)
    for name, size in (("Normal", 9), ("Title", 20), ("Heading 1", 16), ("Heading 2", 10)):
        style = doc.styles[name]
        style.font.name = font_name
        style.font.size = Pt(size)
        style.font.color.rgb = INK
        fonts = style.element.get_or_add_rPr().rFonts
        for attribute in ("asciiTheme", "hAnsiTheme", "eastAsiaTheme", "cstheme"):
            fonts.attrib.pop(qn(f"w:{attribute}"), None)
        fonts.set(qn("w:eastAsia"), font_name)
    doc.styles["Normal"].paragraph_format.space_after = Pt(3)
    doc.styles["Heading 1"].paragraph_format.space_before = Pt(10)
    doc.styles["Heading 1"].paragraph_format.space_after = Pt(7)
    doc.styles["Heading 2"].paragraph_format.space_before = Pt(10)
    doc.styles["Heading 2"].paragraph_format.space_after = Pt(4)
    header = page.header.paragraphs[0]
    header.add_run("서비스·기능 기획서").font.color.rgb = INK
    header.style = doc.styles["Normal"]
    footer = page.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    footer.add_run("프로젝트 기획서  |  ").font.color.rgb = MUTED
    field = OxmlElement("w:fldSimple")
    field.set(qn("w:instr"), "PAGE")
    footer._p.append(field)


def _add_table(doc, records, columns, path):
    """JSON 객체 배열을 머리글 반복이 가능한 Word 표로 작성한다."""
    if records is None:
        records = []
    if not isinstance(records, list):
        raise PlanningDocumentError(f"{path}: 행 객체의 배열 또는 null이 필요합니다.")
    keys = [key for _, fields, _ in columns for key in fields]
    if path.endswith("features") or path.endswith(".features"):
        keys = list(set(keys) | {"id", "name", "behavior_and_value", "priority", "included_in_release", "done_criteria"})
    rows = []
    for index, record in enumerate(records):
        row_path = f"{path}[{index}]"
        _check_object(record, keys, row_path)
        values = [_combine(record, fields, row_path) for _, fields, _ in columns]
        if any(values):
            rows.append(values)

    table = doc.add_table(rows=1, cols=len(columns))
    table.style = "Table Grid"
    table.autofit = False
    table.rows[0]._tr.get_or_add_trPr().append(OxmlElement("w:tblHeader"))
    for column, (_, _, width) in zip(table.columns, columns):
        column.width = Mm(width)
    for index, values in enumerate([[label for label, _, _ in columns], *(rows or [[""] * len(columns)])]):
        row = table.rows[0] if index == 0 else table.add_row()
        for cell, value, (_, _, width) in zip(row.cells, values, columns):
            cell.width = Mm(width)
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            _set_cell_border(cell)
            paragraph = cell.paragraphs[0]
            paragraph.paragraph_format.space_before = Pt(3)
            paragraph.paragraph_format.space_after = Pt(3)
            paragraph.paragraph_format.keep_together = True
            run = paragraph.add_run(value)
            run.font.size = Pt(7.5 if len(columns) >= 5 else 8.5)
            if index == 0:
                run.bold = True
                _shade(cell)
                paragraph.paragraph_format.keep_with_next = True


def _add_field(doc, label, text):
    """양식의 소제목과 해당 기획 내용을 출력한다."""
    doc.add_heading(label, level=2)
    paragraph = doc.add_paragraph(text or "—")
    paragraph.paragraph_format.keep_together = True
    paragraph.paragraph_format.space_after = Pt(5)


def _add_section(doc, title, records, fields):
    """한 영역의 제목과 문단·표를 JSON 양식 순서대로 출력한다."""
    doc.add_heading(title, level=1)
    for label, keys in fields:
        field = keys[0]
        if field == "done_criteria":
            doc.add_heading(label, level=2)
            _add_table(doc, records.get("features", []), TABLES[field], "release_scope.features")
        elif field in TABLES:
            doc.add_heading(label, level=2)
            _add_table(doc, records.get(field, []), TABLES[field], f"{title}.{field}")
        else:
            _add_field(doc, label, _combine(records, keys, title))


def _page_break(doc):
    """다음 양식 영역을 새로운 쪽에서 시작한다."""
    doc.add_page_break()


def render_planning_docx(data: Mapping, *, font_name: str = "Malgun Gothic") -> bytes:
    """V18.42 JSON으로 양식 순서가 보존된 편집 가능한 Word 바이트를 만든다."""
    _check_object(data, ("overview", *(key for key, _, _ in SECTIONS)), "$")
    overview = data.get("overview") or {}
    _check_object(overview, COVER, "overview")
    for key, _, blocks in SECTIONS:
        record = data.get(key) or {}
        _check_object(record, tuple(field for _, fields in blocks for field in fields if field != "done_criteria") + (("project_type",) if key == "release_scope" else ()), key)
        for label, fields in blocks:
            field = fields[0]
            if field in TABLES:
                source = record.get("features", []) if field == "done_criteria" else record.get(field, [])
                if source is not None and not isinstance(source, list):
                    raise PlanningDocumentError(f"{key}.{field}: 배열 또는 null이 필요합니다.")
            else:
                _combine(record, fields, key)

    doc = Document()
    _format_document(doc, font_name)
    project_name = _value(overview, "project_name", "overview")
    doc.core_properties.title = (project_name or "서비스·기능 기획서")[:255]
    doc.add_heading(project_name or "[ 프로젝트명 ]", level=0)
    paragraph = doc.add_paragraph()
    paragraph.add_run("프로젝트 유형  ").bold = True
    paragraph.add_run(_value(data.get("release_scope") or {}, "project_type", "release_scope") or "—")
    for key in ("team_name", "team_members", "written_on"):
        paragraph = doc.add_paragraph()
        paragraph.add_run(f"{COVER[key]}  ").bold = True
        paragraph.add_run(_value(overview, key, "overview") or "—")
    _add_field(doc, COVER["summary"], _value(overview, "summary", "overview"))
    doc.add_heading("목차", level=2)
    for key, section_title, _ in SECTIONS:
        doc.add_paragraph(section_title)
    doc.add_paragraph("작성 기준: 확정 JSON 값만 반영하며, 미정·제외·미승인 상태를 혼동하지 않습니다.")

    _page_break(doc)
    _add_section(doc, SECTIONS[0][1], data.get("user_problem") or {}, SECTIONS[0][2])
    _add_section(doc, SECTIONS[1][1], data.get("goals") or {}, SECTIONS[1][2])

    _page_break(doc)
    scope = data.get("release_scope") or {}
    _add_section(doc, SECTIONS[2][1], scope, SECTIONS[2][2][:3])

    _page_break(doc)
    _add_section(doc, SECTIONS[2][1] + " (계속)", scope, SECTIONS[2][2][3:])

    for key, title, fields in SECTIONS[3:]:
        _page_break(doc)
        _add_section(doc, title, data.get(key) or {}, fields)

    output = BytesIO()
    doc.save(output)
    return output.getvalue()


def convert_docx_to_pdf(docx_bytes: bytes, *, soffice: str | Path | None = None) -> bytes:
    """LibreOffice를 사용해 편집 가능한 Word 파일을 PDF로 변환한다."""
    if soffice is None:
        soffice = shutil.which("soffice.com") or shutil.which("soffice")
        if not soffice and os.name == "nt":
            candidate = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "LibreOffice/program/soffice.com"
            soffice = candidate if candidate.is_file() else None
    if not soffice:
        raise PlanningDocumentError("LibreOffice를 설치하거나 soffice 실행 파일 경로를 지정하세요.")
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
        if result.returncode or not pdf.is_file():
            detail = (result.stderr or result.stdout).decode("utf-8", errors="replace").strip()
            raise PlanningDocumentError(f"Word를 PDF로 변환하지 못했습니다. {detail}")
        content = pdf.read_bytes()
        if not content.startswith(b"%PDF-"):
            raise PlanningDocumentError("변환 결과가 올바른 PDF 파일이 아닙니다.")
        return content


def main() -> None:
    """명령줄 인수의 요구사항 JSON을 읽어 DOCX와 PDF를 함께 저장한다."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("json_file", type=Path)
    parser.add_argument("output_stem", type=Path, help="확장자를 제외한 출력 파일 경로")
    parser.add_argument("--soffice", help="LibreOffice 실행 파일 경로")
    parser.add_argument("--font", default="Malgun Gothic", help="설치된 한글 글꼴 이름")
    args = parser.parse_args()
    try:
        docx_path = Path(f"{args.output_stem}.docx")
        pdf_path = Path(f"{args.output_stem}.pdf")
        if args.json_file.resolve() in {docx_path.resolve(), pdf_path.resolve()}:
            raise PlanningDocumentError("출력 경로는 입력 파일과 달라야 합니다.")
        data = json.loads(args.json_file.read_text(encoding="utf-8-sig"))
        docx = render_planning_docx(data, font_name=args.font)
        pdf = convert_docx_to_pdf(docx, soffice=args.soffice)
        docx_path.parent.mkdir(parents=True, exist_ok=True)
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
