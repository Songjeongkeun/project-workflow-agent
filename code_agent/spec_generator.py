"""A03 기술 명세 생성기: 확정 기획서(JSON) → 기술 명세(JSON) + 코드 검증."""
from __future__ import annotations

import re
from typing import Any

from extractor import ExtractionError, run_structured
from agent_loop import run_with_tools
from domain_tools import TOOL_NAME as DOMAIN_TOOL, make_domain_tools
from spec_prompts import RULES_GUIDE, SPEC_SYSTEM_PROMPT, TOOL_GUIDE, build_spec_message
from spec_schema import AXIS_IDS, TechSpec

SCHEMA_NAME = "tech_spec"


def assign_feature_ids(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """이번 출시에 포함된 기능 목록을 돌려준다 (제외한 기능은 뺀다).

    기획서에 기능 ID가 있으면 그대로 쓰고(FEAT-001 등), 없는 기능에만 FEAT-001부터 빈 번호를 붙인다.
    ID는 코드가 정하고 모델은 정하지 않는다 (같은 입력이면 항상 같은 ID).
    """
    feats = [f for f in (plan.get("release_scope") or {}).get("features") or []
             if f.get("name") and f.get("included_in_release") is not False]
    used = {f["id"] for f in feats if f.get("id")}
    out, n = [], 0
    for f in feats:
        fid = f.get("id")
        if not fid:
            while True:
                n += 1
                fid = f"FEAT-{n:03d}"
                if fid not in used:
                    break
            used.add(fid)
        out.append({**f, "id": fid})
    return out


_EMPTY_MARKS = {"", "미정", "—", "-", "없음", "기록된 항목 없음"}


def derive_acceptance_criteria(plan: dict[str, Any]) -> list[dict[str, str]]:
    """기능별 완료 기준(completion_criteria)에서 완료 조건 목록을 만든다: AC-<기능 번호>-01.

    완료 기준이 비어 있거나 '미정'인 기능은 건너뛴다.
    """
    out = []
    for i, f in enumerate(assign_feature_ids(plan), 1):
        text = (f.get("completion_criteria") or "").strip()
        if text in _EMPTY_MARKS:
            continue
        m = re.search(r"(\d+)\s*$", f["id"])
        num = m.group(1) if m else f"{i:03d}"
        out.append({"id": f"AC-{num}-01", "feature_id": f["id"], "text": text})
    return out


def validate_spec(spec: dict[str, Any], feature_ids: list[str], ac_ids: list[str] | None = None) -> list[str]:
    """모델이 지켰는지 코드로 확인한다. 문제가 없으면 빈 목록."""
    problems: list[str] = []
    known = set(feature_ids)
    known_ac = set(ac_ids or [])

    # 1) 모든 항목이 알려진 기능 ID에 연결되어 있는가
    for group in ("components", "data_entities", "apis", "state_machines"):
        for item in spec.get(group, []):
            label = item.get("name") or item.get("path") or item.get("entity") or "?"
            ids = item.get("feature_ids") or []
            if not ids:
                problems.append(f"{group} '{label}'에 기능 ID(feature_ids)가 없습니다.")
            for i in ids:
                if i not in known:
                    problems.append(f"{group} '{label}'가 존재하지 않는 기능 ID {i}를 참조합니다.")

    # 2) 공통 점검 축: (기능 × 9개 축)이 빠짐없이, 한 번씩 있는가
    seen: dict[tuple[str, str], int] = {}
    for r in spec.get("checklist_results", []):
        key = (r["feature_id"], r["axis"])
        seen[key] = seen.get(key, 0) + 1
        if r["feature_id"] not in known:
            problems.append(f"checklist_results가 존재하지 않는 기능 ID {r['feature_id']}를 참조합니다.")
    for fid in feature_ids:
        for axis in AXIS_IDS:
            n = seen.get((fid, axis), 0)
            if n == 0:
                problems.append(f"{fid}의 점검 축 '{axis}' 결과가 없습니다.")
            elif n > 1:
                problems.append(f"{fid}의 점검 축 '{axis}' 결과가 {n}번 중복되었습니다.")

    # 3) 완료 조건 연결
    if known_ac:
        linked: set[str] = set()
        for group in ("apis", "checklist_results"):
            for item in spec.get(group, []):
                for a in item.get("linked_ac_ids") or []:
                    linked.add(a)
                    if a not in known_ac:
                        problems.append(f"존재하지 않는 완료 조건 ID {a}를 참조합니다.")
        for a in sorted(known_ac - linked):
            problems.append(f"완료 조건 {a}가 어떤 API/점검 결과와도 연결되지 않았습니다.")
    return problems


def generate_tech_spec(
    plan: dict[str, Any],
    *,
    acceptance_criteria: list[dict[str, Any]] | None = None,
    screen_plan: dict[str, Any] | str | None = None,
    tech_stack: str | None = None,
    execution_contract: str | None = None,
    previous: dict[str, Any] | None = None,
    client: Any = None,
    model: str | None = None,
    max_repairs: int = 1,
    use_domain_tools: bool = True,
    trace: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """기술 명세를 만들고, 코드 검증에서 문제가 나오면 문제 목록을 알려 주며 최대 max_repairs번 다시 만든다.

    use_domain_tools=True이면 첫 호출에서 GPT가 get_domain_rules 도구를 불러 업무 영역 규칙을 가져온다.
    다시 요청할 때는 도구를 또 부르지 않고, 이미 가져온 규칙을 입력(<domain_rules>)으로 다시 준다.
    trace를 넘기면 도구 호출 기록이 채워진다.

    반환: (명세, 남은 문제 목록). 문제가 남아 있어도 명세는 돌려주므로 호출한 쪽이 판단한다.
    """
    features = assign_feature_ids(plan)
    if not features:
        raise ExtractionError("이번 출시에 포함된 기능이 없습니다. 기획서의 release_scope.features를 확인하세요.")
    feature_ids = [f["id"] for f in features]
    ac_ids = [a["id"] for a in (acceptance_criteria or []) if a.get("id")]

    problems: list[str] = []
    spec: dict[str, Any] = {}
    domain_rules: str | None = None
    tools = make_domain_tools() if use_domain_tools else []
    for attempt in range(max_repairs + 1):
        user = build_spec_message(
            features,
            plan,
            acceptance_criteria=acceptance_criteria,
            screen_plan=screen_plan,
            tech_stack=tech_stack,
            execution_contract=execution_contract,
            previous=previous,
            problems=problems or None,
            domain_rules=domain_rules,
        )
        if tools and attempt == 0:
            local: list[dict[str, Any]] = []
            spec = run_with_tools(
                SPEC_SYSTEM_PROMPT + TOOL_GUIDE, user, TechSpec, SCHEMA_NAME, tools,
                force_first=DOMAIN_TOOL, client=client, model=model, trace=local,
            )
            if trace is not None:
                trace.extend(local)
            ok = [t["result"] for t in local if t["tool"] == DOMAIN_TOOL and t["ok"]]
            domain_rules = "\n\n".join(ok) or None
        else:
            system = SPEC_SYSTEM_PROMPT + (RULES_GUIDE if domain_rules else "")
            spec = run_structured(system, user, TechSpec, SCHEMA_NAME, client=client, model=model)
        problems = validate_spec(spec, feature_ids, ac_ids)
        if not problems:
            break
    return spec, problems
