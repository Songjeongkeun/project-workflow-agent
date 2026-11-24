"""기술 명세 생성기 테스트 (가짜 클라이언트). 실행: python tests/test_spec.py"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from spec_generator import assign_feature_ids, generate_tech_spec, validate_spec  # noqa: E402
from spec_schema import AXES, AXIS_IDS, TechSpec  # noqa: E402
from schema import strict_json_schema  # noqa: E402
from typing import get_args  # noqa: E402

PLAN = {
    "release_scope": {
        "features": [
            {"name": "알림 신청", "included_in_release": True, "priority": "P0", "behavior_and_value": None},
            {"name": "취소 알림 발송", "included_in_release": True, "priority": "P0", "behavior_and_value": None},
            {"name": "자동 예약", "included_in_release": False, "priority": "P1", "behavior_and_value": None},
        ]
    }
}


def good_spec(fids=("FEAT-001", "FEAT-002"), drop=None):
    return {
        "components": [{"name": "알림 서비스", "role": "취소 이벤트 수신", "feature_ids": ["FEAT-002"]}],
        "apis": [{"method": "POST", "path": "/subscriptions", "purpose": "알림 신청", "request": None,
                  "response": None, "errors": [], "feature_ids": ["FEAT-001"], "linked_ac_ids": ["AC-001-01"]}],
        "checklist_results": [
            {"feature_id": f, "axis": a, "applies": True, "decision": "ok", "linked_ac_ids": []}
            for f in fids for a in AXIS_IDS if (f, a) != drop
        ],
    }


class SeqClient:
    """호출할 때마다 outputs를 순서대로 돌려주는 가짜 OpenAI 클라이언트."""

    def __init__(self, outputs):
        self.calls, self._outs = [], list(outputs)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.calls.append(kw)
        msg = SimpleNamespace(content=json.dumps(self._outs.pop(0), ensure_ascii=False), refusal=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason="stop")])


def test_axes_in_sync_with_literal():
    assert list(get_args(TechSpec.model_fields["checklist_results"].annotation.__args__[0].model_fields["axis"].annotation)) == AXIS_IDS
    assert len(AXES) == 9


def test_assign_ids_skips_excluded():
    assert [(f["id"], f["name"]) for f in assign_feature_ids(PLAN)] == [("FEAT-001", "알림 신청"), ("FEAT-002", "취소 알림 발송")]


def test_validate_ok_and_problems():
    ids = ["FEAT-001", "FEAT-002"]
    assert validate_spec(TechSpec.model_validate(good_spec()).model_dump(), ids, ["AC-001-01"]) == []
    bad = TechSpec.model_validate(good_spec(drop=("FEAT-002", "failure"))).model_dump()
    assert any("FEAT-002" in p and "failure" in p for p in validate_spec(bad, ids))
    bad["apis"][0]["feature_ids"] = ["FEAT-999"]
    assert any("FEAT-999" in p for p in validate_spec(bad, ids))
    bad["apis"][0]["feature_ids"] = []
    assert any("(feature_ids)가 없습니다" in p for p in validate_spec(bad, ids))
    assert any("AC-002-01" in p for p in validate_spec(TechSpec.model_validate(good_spec()).model_dump(), ids, ["AC-001-01", "AC-002-01"]))


def test_repair_loop_sends_problems_then_succeeds():
    client = SeqClient([good_spec(drop=("FEAT-001", "limit")), good_spec()])
    spec, problems = generate_tech_spec(PLAN, client=client, model="m")
    assert problems == [] and len(client.calls) == 2
    assert "<validation_problems>" not in client.calls[0]["messages"][1]["content"]
    assert "limit" in client.calls[1]["messages"][1]["content"]  # 문제가 모델에게 전달됨


def test_gives_up_after_max_repairs():
    client = SeqClient([good_spec(drop=("FEAT-001", "limit"))] * 2)
    spec, problems = generate_tech_spec(PLAN, client=client, model="m", max_repairs=1)
    assert len(client.calls) == 2 and problems  # 무한 반복하지 않고 문제를 돌려줌


def test_prompt_contains_all_axes_and_strict_schema_ok():
    client = SeqClient([good_spec()])
    generate_tech_spec(PLAN, client=client, model="m")
    sys_prompt = client.calls[0]["messages"][0]["content"]
    assert all(a in sys_prompt for a in AXIS_IDS)
    schema = strict_json_schema(TechSpec)

    def walk(n):
        if isinstance(n, dict):
            assert "default" not in n and "title" not in n
            if "properties" in n:
                assert n["required"] == list(n["properties"]) and n["additionalProperties"] is False
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)

    walk(schema)


def test_no_features_error():
    from extractor import ExtractionError

    try:
        generate_tech_spec({"release_scope": {"features": []}}, client=SeqClient([]), model="m")
    except ExtractionError:
        return
    raise AssertionError


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
