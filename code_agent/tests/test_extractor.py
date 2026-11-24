"""API 없이 동작하는 테스트 (가짜 클라이언트 사용). 실행: python -m pytest tests  또는  python tests/test_extractor.py"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from extractor import SCHEMA_NAME, extract_planning_doc, load_env, missing_fields, update_planning_doc  # noqa: E402
from schema import PlanningDoc, diff_docs, find_missing, strict_json_schema  # noqa: E402


def _template():
    return json.loads((ROOT / "planning_template.json").read_text(encoding="utf-8"))


class FakeClient:
    """openai.OpenAI().chat.completions.create 만 흉내 낸다."""

    def __init__(self, output, finish_reason="stop", refusal=None):
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
        self._output, self._finish, self._refusal = output, finish_reason, refusal

    def _create(self, **kw):
        self.calls.append(kw)
        content = self._output if isinstance(self._output, str) else json.dumps(self._output, ensure_ascii=False)
        msg = SimpleNamespace(content=content, refusal=self._refusal)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason=self._finish)])


def _extract(fake, text="x", **kw):
    return extract_planning_doc(text, client=fake, model="test-model", **kw)


def test_empty_doc_matches_template_structure():
    """스키마 기본값 구조가 제공된 JSON 템플릿과 키 단위로 동일해야 한다."""
    empty = PlanningDoc().model_dump()
    tpl = _template()
    assert set(empty) == set(tpl)
    for section in tpl:  # 스키마는 템플릿의 모든 키를 가진다 (V17.15 항목은 추가로 더 가짐)
        assert set(tpl[section]) <= set(empty[section]), section


def test_list_item_keys_match_template():
    tpl = _template()
    fields = {
        ("goals", "metrics"): "Metric",
        ("release_scope", "features"): "Feature",
        ("user_experience", "screens"): "Screen",
        ("policies", "exceptions"): "ExceptionCase",
        ("data_rules", "fields"): "DataField",
        ("release_plan", "milestones"): "Milestone",
        ("release_plan", "glossary_and_references"): "Reference",
    }
    defs = PlanningDoc.model_json_schema()["$defs"]
    for (sec, key), model in fields.items():
        assert set(tpl[sec][key][0]) <= set(defs[model]["properties"]), (sec, key)


def test_extract_fills_and_reports_missing():
    fake = FakeClient(
        {
            "overview": {"project_name": "프로젝트 진행 지원 AI 에이전트", "team_members": ["A", "B"]},
            "goals": {"metrics": [{"name": "배포 검증 통과율", "target_value": "100%"}]},
            "release_scope": {"features": [{"name": "요구사항 추출", "included_in_release": True}]},
        }
    )
    doc = _extract(fake, "테스트 입력", today="2026-11-10")
    assert doc["overview"]["project_name"].startswith("프로젝트")
    assert doc["overview"]["summary"] is None  # 말하지 않은 값은 null
    assert doc["goals"]["metrics"][0]["current_value"] is None
    miss = missing_fields(doc)
    assert "overview.summary" in miss and "overview.project_name" not in miss
    # strict 스키마 요청 + 시스템 프롬프트 + 날짜 전달 확인
    call = fake.calls[0]
    assert call["response_format"]["json_schema"]["name"] == SCHEMA_NAME
    assert call["response_format"]["json_schema"]["strict"] is True
    assert call["messages"][0]["role"] == "system"
    assert "2026-11-10" in call["messages"][1]["content"]


def test_previous_doc_is_sent():
    fake = FakeClient({})
    _extract(fake, "답변", previous={"overview": {"project_name": "X"}})
    assert "<previous_planning_doc>" in fake.calls[0]["messages"][1]["content"]
    assert "수정 모드" in fake.calls[0]["messages"][1]["content"]


def test_invalid_output_rejected():
    from pydantic import ValidationError

    fake = FakeClient({"overview": {"unknown_key": 1}})
    try:
        _extract(fake)
    except ValidationError:
        return
    raise AssertionError("알 수 없는 키는 거부되어야 함")


def test_strict_schema_rules():
    """OpenAI strict 조건: 모든 object는 전 키 required + additionalProperties false, default/title 없음."""
    schema = strict_json_schema()

    def walk(n):
        if isinstance(n, dict):
            assert "default" not in n and "title" not in n
            if "properties" in n:
                assert n["required"] == list(n["properties"])
                assert n["additionalProperties"] is False
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)

    walk(schema)


def test_strict_schema_keeps_field_named_title():
    """필드 이름이 title이어도(WorkPlan.Task) properties/required에서 지워지면 안 된다 (OpenAI 400 회귀)."""
    from code_schema import WorkPlan

    task = strict_json_schema(WorkPlan)["$defs"]["Task"]
    assert "title" in task["properties"] and "title" in task["required"]


def test_error_cases():
    from extractor import ExtractionError

    for fake in (FakeClient({}, finish_reason="length"), FakeClient("", refusal="no"), FakeClient("not json")):
        try:
            _extract(fake)
        except ExtractionError:
            continue
        raise AssertionError("ExtractionError 기대")


def test_load_env(tmp_path=None):
    import os
    import tempfile

    d = Path(tmp_path or tempfile.mkdtemp())
    f = d / ".env"
    f.write_text('# c\nZZ_TEST_A = hello\nZZ_TEST_B="q v"\nZZ_TEST_C=keep\n', encoding="utf-8")
    os.environ["ZZ_TEST_C"] = "already"
    load_env(f)
    assert os.environ["ZZ_TEST_A"] == "hello"  # = 양옆 공백 허용
    assert os.environ["ZZ_TEST_B"] == "q v"
    assert os.environ["ZZ_TEST_C"] == "already"  # 기존 환경변수 유지


def test_update_applies_only_changes():
    prev = PlanningDoc().model_dump()
    prev["overview"]["project_name"] = "A"
    prev["goals"]["metrics"] = [{"name": "재예약률", "current_value": "20%", "target_value": "35%",
                                  "measurement_method": None, "measurement_timing": None}]
    snapshot = json.loads(json.dumps(prev))
    new = json.loads(json.dumps(prev))
    new["goals"]["metrics"][0]["target_value"] = "40%"
    doc = update_planning_doc(prev, "목표를 40%로", client=FakeClient(new), model="m")
    assert prev == snapshot  # 원본은 바뀌지 않음
    assert diff_docs(prev, doc) == [("goals.metrics[0].target_value", "35%", "40%")]


def test_update_requires_previous():
    from extractor import ExtractionError

    try:
        update_planning_doc({}, "x", client=FakeClient({}), model="m")
    except ExtractionError:
        return
    raise AssertionError


def test_diff_docs_list_add_remove():
    assert diff_docs({"a": ["x"]}, {"a": ["x", "y"]}) == [("a[1]", None, "y")]
    assert diff_docs({"a": ["x", "y"]}, {"a": ["x"]}) == [("a[1]", "y", None)]
    assert diff_docs({"a": 1}, {"a": 1}) == []


def test_backup_and_cli_update(tmp=None):
    import subprocess
    import tempfile

    d = Path(tmp or tempfile.mkdtemp())
    f = d / "planning.json"
    f.write_text("{}", encoding="utf-8")
    import main as m

    b = m.backup(f)
    assert b.parent.name == "history" and b.read_text() == "{}"
    assert m.backup(d / "none.json") is None


def test_read_input():
    import tempfile

    from extractor import ExtractionError
    from pypdf import PdfWriter
    from readers import read_input

    d = Path(tempfile.mkdtemp())
    (d / "a.txt").write_text("안녕 기획", encoding="utf-8")
    assert read_input(d / "a.txt") == "안녕 기획"
    w = PdfWriter()
    w.add_blank_page(width=200, height=200)
    with open(d / "blank.pdf", "wb") as fh:
        w.write(fh)
    (d / "x.docx").write_text("x")
    for bad in (d / "blank.pdf", d / "x.docx", d / "none.txt"):  # 글자 없는 PDF / 미지원 형식 / 없는 파일
        try:
            read_input(bad)
        except ExtractionError:
            continue
        raise AssertionError(bad)


def test_strip_repeated_lines_keeps_id_lists():
    from readers import strip_repeated_lines

    header = "서비스 기획서 01 / 08"
    pages = [
        [header, "머리말", "프로젝트 기획서 | 1"],
        [header.replace("01", "02"), "FEAT-001", "FEAT-002", "FEAT-003", "프로젝트 기획서 | 2"],
        [header.replace("01", "03"), "FEAT-004", "FEAT-005", "프로젝트 기획서 | 3"],
    ]
    out = strip_repeated_lines(pages)
    assert out[1] == ["FEAT-001", "FEAT-002", "FEAT-003"]  # 기능 ID 줄은 유지
    assert out[2] == ["FEAT-004", "FEAT-005"]
    assert out[0] == ["머리말"]                              # 모든 쪽에 있는 머리글·바닥글만 제거
    assert strip_repeated_lines(pages[:2]) == pages[:2]       # 2쪽 이하는 건드리지 않음


def test_find_missing():
    assert find_missing({"a": None, "b": [], "c": [{"d": None}]}) == ["a", "b[]", "c[0].d"]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
