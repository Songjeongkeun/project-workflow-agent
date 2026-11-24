"""문서 → 코드 파이프라인 테스트 (가짜 클라이언트). 실행: python tests/test_pipeline.py"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from build import run_pipeline, stack_text  # noqa: E402
from schema import PlanningDoc  # noqa: E402
from spec_generator import assign_feature_ids, derive_acceptance_criteria  # noqa: E402
from spec_schema import AXIS_IDS  # noqa: E402
from test_code import call, final  # noqa: E402
from test_tools import StepClient, tool_call  # noqa: E402
from readers import read_input  # noqa: E402


def feat(fid, name, included=True, criteria=None):
    return {"id": fid, "name": name, "behavior_and_value": f"{name} 동작", "priority": "P0",
            "included_in_release": included, "completion_criteria": criteria}


def plan_json():
    d = PlanningDoc().model_dump()
    d["overview"]["project_name"] = "점검 안내"
    d["release_scope"]["features"] = [
        feat("FEAT-001", "점검 시간 표시", criteria="상단에 시작·종료 시각이 보인다."),
        feat("FEAT-002", "이용 제한 안내", criteria="미정"),            # 완료 기준 미정 → 완료 조건 없음
        feat("FEAT-009", "알림 발송", included=False),                  # 제외
    ]
    d["release_scope"]["tech_stack"] = [{"area": "화면", "technology": "HTML, CSS", "reason": "정적 한 장", "source": None},
                                       {"area": "빈칸", "technology": None, "reason": None, "source": None}]
    d["release_scope"]["access_install_and_run"] = "python -m pytest -q 로 테스트한다."
    return d


def spec_json(fids, ac_links):
    return {
        "components": [], "data_entities": [], "state_machines": [], "open_questions": [],
        "apis": [{"method": "GET", "path": "/notice", "purpose": "안내", "request": None, "response": None,
                  "errors": [], "feature_ids": [fids[0]], "linked_ac_ids": ac_links}],
        "checklist_results": [{"feature_id": f, "axis": a, "applies": False, "decision": "해당 없음", "linked_ac_ids": []}
                              for f in fids for a in AXIS_IDS],
    }


def work_plan_json():
    return {"tasks": [
        {"id": "TASK-001", "title": "점검 시간 표시", "feature_ids": ["FEAT-001"], "ac_ids": ["AC-001-01"], "depends_on": [],
         "files": [{"path": "index.html", "action": "create", "purpose": "안내 페이지"},
                   {"path": "tests/test_page.py", "action": "create", "purpose": "AC-001-01 검사"}]},
        {"id": "TASK-002", "title": "이용 제한 안내", "feature_ids": ["FEAT-002"], "ac_ids": [], "depends_on": [],
         "files": [{"path": "restrictions.html", "action": "create", "purpose": "제한 안내"}]}]}


def test_ids_prefer_document_ids_and_skip_excluded():
    ids = [f["id"] for f in assign_feature_ids(plan_json())]
    assert ids == ["FEAT-001", "FEAT-002"]
    p = plan_json()
    p["release_scope"]["features"][1]["id"] = None            # ID 없는 기능은 빈 번호를 받는다
    assert [f["id"] for f in assign_feature_ids(p)] == ["FEAT-001", "FEAT-002"]
    p["release_scope"]["features"][0]["id"] = "FEAT-002"      # 충돌하면 피한다
    assert [f["id"] for f in assign_feature_ids(p)] == ["FEAT-002", "FEAT-001"]


def test_derive_criteria_skips_unset():
    assert derive_acceptance_criteria(plan_json()) == [
        {"id": "AC-001-01", "feature_id": "FEAT-001", "text": "상단에 시작·종료 시각이 보인다."}]


def test_stack_text_skips_empty_rows():
    assert stack_text(plan_json()) == "화면: HTML, CSS — 정적 한 장"
    assert stack_text(PlanningDoc().model_dump()) is None


def test_read_docx_with_table():
    import docx

    d = docx.Document()
    d.add_paragraph("화면 계획")
    t = d.add_table(rows=1, cols=2)
    t.rows[0].cells[0].text, t.rows[0].cells[1].text = "SCR-001", "안내 페이지"
    f = Path(tempfile.mkdtemp()) / "ui.docx"
    d.save(str(f))
    text = read_input(f)
    assert "화면 계획" in text and "SCR-001 | 안내 페이지" in text


def test_read_json_as_text():
    f = Path(tempfile.mkdtemp()) / "screen_plan.json"
    f.write_text('{"screens": [{"screen_id": "SCR-001", "name": "예약 상세"}]}', encoding="utf-8")
    t = read_input(f)
    assert "SCR-001" in t and "예약 상세" in t


def run(client, doc_text="기획서 내용", ui_text=None, **kw):
    tmp = Path(tempfile.mkdtemp())
    doc = tmp / "doc.md"
    doc.write_text(doc_text, encoding="utf-8")
    ui = None
    if ui_text:
        ui = tmp / "ui.md"
        ui.write_text(ui_text, encoding="utf-8")
    logs = []
    r = run_pipeline(str(doc), ui_doc=str(ui) if ui else None, workspace=str(tmp / "proj"), out_dir=str(tmp / "out"),
                     client=client, model="m", log=logs.append, **kw)
    return r, tmp, logs


def test_pipeline_end_to_end():
    client = StepClient([
        plan_json(),                                                   # 1) 추출
        [tool_call(["업무 운영"])], spec_json(["FEAT-001", "FEAT-002"], ["AC-001-01"]),   # 2) 명세 (도구 → 최종)
        work_plan_json(),                                              # 3) 작업 계획
        [call("write_file", {"path": "index.html", "content": "<h1>점검</h1>"}, "w1"),
         call("write_file", {"path": "restrictions.html", "content": "<p>제한</p>"}, "w2"),
         call("write_file", {"path": "tests/test_page.py", "content": "# AC-001-01\n"}, "w3")],
        final(),                                                       # 4) 코드 요약
    ])
    r, tmp, logs = run(client, ui_text="SCR-001: 제목 아래에 점검 시간")
    assert r["ok"] and r["stage"] == "write" and r["spec_problems"] == []
    for name in ["planning.json", "criteria.json", "tech_spec.json", "work_plan.json"]:
        assert (tmp / "out" / name).is_file()
    assert (tmp / "proj" / "index.html").read_text(encoding="utf-8") == "<h1>점검</h1>"
    assert r["report"]["missing_files"] == [] and r["report"]["ac_not_referenced"] == []
    calls = client.calls
    assert "화면: HTML, CSS" in calls[1]["messages"][1]["content"]       # 기획서의 기술 스택이 명세 단계로 전달됨
    assert "python -m pytest -q" in calls[1]["messages"][1]["content"]   # 실행 방법(execution_contract)도 전달됨
    assert "SCR-001: 제목 아래에 점검 시간" in calls[1]["messages"][1]["content"]   # UI 기획서가 화면 계획으로 전달됨
    code_user = [c for c in calls if "work_plan" in c["messages"][1]["content"] and "tools" in c][-1]["messages"][1]["content"]
    assert "<screen_plan>" in code_user and "AC-001-01" in code_user
    assert any("도구 호출" in m and "업무 운영" in m for m in logs)


def test_pipeline_stops_when_not_approved():
    client = StepClient([plan_json(), [tool_call([])], spec_json(["FEAT-001", "FEAT-002"], ["AC-001-01"]), work_plan_json()])
    r, tmp, _ = run(client, approve=lambda wp: False)
    assert not r["ok"] and r["stage"] == "plan" and "승인하지 않아" in r["problems"][0]
    assert not (tmp / "proj").exists() or not any((tmp / "proj").iterdir())    # 코드는 쓰지 않음
    assert len(client.calls) == 4


def test_pipeline_needs_features_and_stack():
    p = plan_json()
    p["release_scope"]["features"] = []
    r, _, _ = run(StepClient([p]))
    assert not r["ok"] and "기능" in r["problems"][0]
    p = plan_json()
    p["release_scope"]["tech_stack"] = []
    r, _, _ = run(StepClient([p]))
    assert not r["ok"] and "기술 스택" in r["problems"][0]
    r, _, _ = run(StepClient([p, [tool_call([])], spec_json(["FEAT-001", "FEAT-002"], ["AC-001-01"]), work_plan_json()]),
                  stack="HTML, CSS", approve=lambda wp: False)      # --stack으로 주면 진행
    assert r["stage"] == "plan"


def test_spec_problems_are_warnings_not_blockers():
    bad = spec_json(["FEAT-001", "FEAT-002"], [])        # AC-001-01 연결 누락 → 검증 문제 (재요청 후에도)
    client = StepClient([plan_json(), [tool_call([])], bad, bad, work_plan_json(), final()])
    r, _, logs = run(client, approve=lambda wp: False)
    assert r["spec_problems"] and any("명세 검증" in m for m in logs) and r["stage"] == "plan"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
