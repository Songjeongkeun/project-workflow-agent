"""코드 작성 단계 테스트 (가짜 클라이언트, 실제 파일/명령 실행은 임시 폴더에서). 실행: python tests/test_code.py"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from code_generator import plan_work, validate_work_plan, write_code  # noqa: E402
from spec_generator import assign_feature_ids  # noqa: E402
from test_spec import PLAN, good_spec  # noqa: E402
from test_tools import StepClient  # noqa: E402
from types import SimpleNamespace  # noqa: E402
from workspace import ScopeError, Workspace, normalize_path  # noqa: E402

FIDS = ["FEAT-001", "FEAT-002"]


def work_plan(extra_ac=None):
    return {"tasks": [
        {"id": "TASK-001", "title": "알림 신청", "feature_ids": ["FEAT-001"], "ac_ids": ["AC-001-01"], "depends_on": [],
         "files": [{"path": "app/main.py", "action": "create", "purpose": "API"},
                   {"path": "tests/test_sub.py", "action": "create", "purpose": "AC-001-01 검사"}]},
        {"id": "TASK-002", "title": "취소 알림", "feature_ids": ["FEAT-002"], "ac_ids": extra_ac or [], "depends_on": ["TASK-001"],
         "files": [{"path": "app/notify.py", "action": "create", "purpose": "발송"}]},
    ]}


def call(name, args, cid):
    return SimpleNamespace(id=cid, type="function", function=SimpleNamespace(name=name, arguments=json.dumps(args, ensure_ascii=False)))


def final(summary="완료", unresolved=None):
    return {"summary": summary, "task_status": [{"task_id": "TASK-001", "done": True, "note": None}], "unresolved": unresolved or []}


def test_normalize_path():
    assert normalize_path("app\\main.py") == "app/main.py"
    for bad in ["../x", "a/../../b", "/etc/passwd", "C:/x", "~/x", ".env", "app/.git/x", "", "  "]:
        try:
            normalize_path(bad)
        except ValueError:
            continue
        raise AssertionError(bad)


def test_workspace_scope_and_limits():
    ws = Workspace(tempfile.mkdtemp(), allowed_files=["app/main.py"])
    assert "저장" in ws.write_file("app/main.py", "print(1)")
    assert ws.read_file("app/main.py") == "print(1)"
    try:
        ws.write_file("app/other.py", "x")
    except ScopeError:
        pass
    else:
        raise AssertionError
    assert ws.scope_violations == ["app/other.py"] and not (ws.root / "app/other.py").exists()
    try:
        ws.write_file("app/main.py", "a" * 300_000)
    except ValueError:
        pass
    else:
        raise AssertionError
    assert ws.list_files() == ["app/main.py"] and ws.files_containing("print") == ["app/main.py"]


def test_symlink_escape_blocked():
    outside = Path(tempfile.mkdtemp())
    ws = Workspace(tempfile.mkdtemp(), allowed_files=["link/x.py"])
    try:
        (ws.root / "link").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        return  # 심볼릭 링크를 못 만드는 환경
    try:
        ws.write_file("link/x.py", "x")
    except ValueError:
        assert not (outside / "x.py").exists()
        return
    raise AssertionError("작업 공간 밖에 파일이 써짐")


def test_run_tests_real_subprocess():
    ws = Workspace(tempfile.mkdtemp(), test_command=[sys.executable, "-c", "print('통과')"])
    r = ws.run_tests()
    assert r["exit_code"] == 0 and "통과" in r["output"]
    ws = Workspace(tempfile.mkdtemp(), test_command=[sys.executable, "-c", "import sys; sys.exit(3)"])
    assert ws.run_tests()["exit_code"] == 3
    ws = Workspace(tempfile.mkdtemp(), test_command=[sys.executable, "-c", "import time; time.sleep(5)"], test_timeout=1)
    assert ws.run_tests()["timed_out"] is True
    ws = Workspace(tempfile.mkdtemp(), test_command=["명령없음-xyz"])
    assert ws.run_tests()["exit_code"] is None
    try:
        Workspace(tempfile.mkdtemp()).run_tests()
    except ValueError:
        return
    raise AssertionError


def test_validate_work_plan():
    assert validate_work_plan(work_plan(), FIDS, ["AC-001-01"]) == []
    wp = work_plan()
    wp["tasks"][1]["feature_ids"] = []
    wp["tasks"][0]["files"][0]["path"] = "../밖.py"
    wp["tasks"][0]["ac_ids"] = []
    probs = validate_work_plan(wp, FIDS, ["AC-001-01"])
    assert any("기능 ID가 없습니다" in p for p in probs)
    assert any("상위 폴더" in p for p in probs)
    assert any("FEAT-002" in p and "연결되지 않았습니다" in p for p in probs)
    assert any("AC-001-01" in p for p in probs)
    assert validate_work_plan({"tasks": []}, FIDS) == ["작업이 하나도 없습니다."]


def test_plan_work_repairs():
    bad = work_plan()
    bad["tasks"].pop()  # FEAT-002 누락
    client = StepClient([bad, work_plan()])
    wp, problems = plan_work(good_spec(), PLAN, client=client, model="m")
    assert problems == [] and len(client.calls) == 2
    assert "FEAT-002" in client.calls[1]["messages"][1]["content"]  # 문제 목록이 모델에게 전달됨


def test_write_code_end_to_end():
    tmp = tempfile.mkdtemp()
    client = StepClient([
        [call("write_file", {"path": "app/main.py", "content": "def sub(): ...\n"}, "c1"),
         call("write_file", {"path": "app/evil.py", "content": "x"}, "c2"),      # 범위 밖
         call("write_file", {"path": "../escape.py", "content": "x"}, "c3")],    # 경로 탈출
        [call("write_file", {"path": "tests/test_sub.py", "content": "def test_AC_001_01(): assert True\n"}, "c4")],
        final(unresolved=["재시도 횟수 미정"]),
    ])
    trace = []
    result, report = write_code(work_plan(), good_spec(), PLAN, tmp, client=client, model="m", trace=trace)
    assert report["written_files"] == ["app/main.py", "tests/test_sub.py"]
    assert report["missing_files"] == ["app/notify.py"]          # 쓰지 않은 계획 파일을 코드가 찾아냄
    assert report["scope_violations"] == ["app/evil.py"]
    assert report["ac_not_referenced"] == []                      # AC-001-01이 테스트 파일에 있음
    assert report["tests"] is None                                # --test-cmd 없으면 실행 안 함
    assert not (Path(tmp) / "app/evil.py").exists() and not (Path(tmp).parent / "escape.py").exists()
    assert [t["ok"] for t in trace] == [True, False, False, True]
    tools_offered = {t["function"]["name"] for t in client.calls[0]["tools"]}
    assert tools_offered == {"list_files", "read_file", "write_file"}  # 테스트 명령이 없으면 run_tests는 주지 않음
    assert result["unresolved"] == ["재시도 횟수 미정"]


def test_write_code_runs_tests_only_when_configured():
    tmp = tempfile.mkdtemp()
    client = StepClient([
        [call("write_file", {"path": "tests/test_sub.py", "content": "# AC-001-01\n"}, "c1"), call("run_tests", {}, "c2")],
        final(),
    ])
    result, report = write_code(
        work_plan(), good_spec(), PLAN, tmp, client=client, model="m",
        test_command=[sys.executable, "-c", "print('ok')"],
    )
    assert "run_tests" in {t["function"]["name"] for t in client.calls[0]["tools"]}
    assert report["tests"]["exit_code"] == 0                      # 모델 주장과 별개로 코드가 직접 실행
    tool_msg = [m for m in client.calls[1]["messages"] if m["role"] == "tool"][-1]["content"]
    assert "종료 코드 0" in tool_msg


def test_ac_hyphen_form_in_comment_also_counts():
    tmp = tempfile.mkdtemp()
    client = StepClient([[call("write_file", {"path": "tests/test_sub.py", "content": "# AC-001-01 검사\n"}, "c1")], final()])
    _, report = write_code(work_plan(), good_spec(), PLAN, tmp, client=client, model="m")
    assert report["ac_not_referenced"] == []


def test_ac_not_referenced_detected():
    tmp = tempfile.mkdtemp()
    client = StepClient([[call("write_file", {"path": "tests/test_sub.py", "content": "def test_x(): pass\n"}, "c1")], final()])
    _, report = write_code(work_plan(), good_spec(), PLAN, tmp, client=client, model="m")
    assert report["ac_not_referenced"] == ["AC-001-01"]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
