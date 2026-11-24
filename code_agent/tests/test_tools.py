"""Tool 연결 테스트 (가짜 클라이언트). 실행: python tests/test_tools.py"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from domain_tools import NO_DOMAIN_MESSAGE, TOOL_NAME, make_domain_tools  # noqa: E402
from spec_generator import generate_tech_spec  # noqa: E402
from test_spec import PLAN, good_spec  # noqa: E402


def tool_call(domains, call_id="call_1", name=TOOL_NAME):
    fn = SimpleNamespace(name=name, arguments=json.dumps({"domains": domains}, ensure_ascii=False))
    return SimpleNamespace(id=call_id, type="function", function=fn)


class StepClient:
    """호출마다 steps를 순서대로 돌려준다. step은 최종 JSON(dict)이거나 tool_call 리스트."""

    def __init__(self, steps):
        self.calls, self._steps = [], list(steps)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.calls.append(json.loads(json.dumps(kw, ensure_ascii=False, default=str)))  # 시점 복사
        step = self._steps.pop(0)
        if isinstance(step, list):
            msg = SimpleNamespace(content=None, refusal=None, tool_calls=step)
            return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason="tool_calls")])
        msg = SimpleNamespace(content=json.dumps(step, ensure_ascii=False), refusal=None, tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason="stop")])


def test_tool_is_forced_then_result_returned_to_model():
    client = StepClient([[tool_call(["업무 운영", "협업·문서"])], good_spec()])
    trace = []
    spec, problems = generate_tech_spec(PLAN, client=client, model="m", trace=trace)
    assert problems == [] and len(client.calls) == 2
    first, second = client.calls
    assert first["tool_choice"] == {"type": "function", "function": {"name": TOOL_NAME}}  # 첫 호출은 도구 강제
    assert first["tools"][0]["function"]["name"] == TOOL_NAME
    roles = [m["role"] for m in second["messages"]]
    assert roles == ["system", "user", "assistant", "tool"]  # 도구 결과가 모델에게 돌아감
    assert "예약·대여" in second["messages"][3]["content"] and "알림·메시지" in second["messages"][3]["content"]
    assert second["tool_choice"] == "auto"
    assert trace and trace[0]["ok"] and trace[0]["arguments"] == {"domains": ["업무 운영", "협업·문서"]}


def test_repair_reuses_fetched_rules_without_calling_tool_again():
    client = StepClient([[tool_call(["업무 운영"])], good_spec(drop=("FEAT-001", "limit")), good_spec()])
    spec, problems = generate_tech_spec(PLAN, client=client, model="m")
    assert problems == [] and len(client.calls) == 3
    third = client.calls[2]
    assert "tools" not in third  # 다시 요청할 때는 도구를 주지 않음
    user = third["messages"][1]["content"]
    assert "<domain_rules>" in user and "예약·대여" in user and "<validation_problems>" in user


def test_empty_domains_and_unknown_domain():
    tool = make_domain_tools()[0]
    assert tool.fn({"domains": []}) == NO_DOMAIN_MESSAGE
    try:
        tool.fn({"domains": ["없는영역"]})
    except ValueError as e:
        assert "없는영역" in str(e)
    else:
        raise AssertionError
    assert tool.fn({"domains": ["재무", "재무"]}).count("[업무 영역: 재무]") == 1  # 중복 제거


def test_unknown_domain_error_goes_back_to_model_not_crash():
    client = StepClient([[tool_call(["없는영역"])], [tool_call(["재무"], "call_2")], good_spec()])
    trace = []
    spec, problems = generate_tech_spec(PLAN, client=client, model="m", trace=trace)
    assert problems == [] and [t["ok"] for t in trace] == [False, True]
    assert "알 수 없는 업무 영역" in client.calls[1]["messages"][3]["content"]  # 오류 문장을 모델이 받음


def test_round_limit_forces_final_answer():
    from agent_loop import run_with_tools
    from spec_schema import TechSpec

    steps = [[tool_call(["재무"], f"c{i}")] for i in range(3)] + [good_spec()]
    client = StepClient(steps)
    out = run_with_tools("s", "u", TechSpec, "tech_spec", make_domain_tools(), client=client, model="m", max_rounds=3)
    assert out["checklist_results"] and len(client.calls) == 4
    assert client.calls[-1]["tool_choice"] == "none"  # 상한에 도달하면 도구 없이 최종 응답


def test_no_tools_mode_keeps_old_behavior():
    client = StepClient([good_spec()])
    generate_tech_spec(PLAN, client=client, model="m", use_domain_tools=False)
    assert "tools" not in client.calls[0]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
