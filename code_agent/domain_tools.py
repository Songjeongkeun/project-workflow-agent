"""기술 명세 생성기가 쓰는 도구: 업무 영역 규칙 가져오기."""
from __future__ import annotations

from typing import Any

from agent_loop import Tool
from domain_rules import DOMAIN_NAMES, DOMAINS, get_domain_prompt

TOOL_NAME = "get_domain_rules"
NO_DOMAIN_MESSAGE = "해당하는 업무 영역이 없습니다. 공통 점검 축만 사용하세요."


def _get_domain_rules(args: dict[str, Any]) -> str:
    domains = args.get("domains") or []
    if not domains:
        return NO_DOMAIN_MESSAGE
    unknown = [d for d in domains if d not in DOMAINS]
    if unknown:
        raise ValueError(f"알 수 없는 업무 영역 {unknown}. 가능한 값: {DOMAIN_NAMES}")
    return get_domain_prompt(list(dict.fromkeys(domains)))  # 중복 제거, 순서 유지


def make_domain_tools() -> list[Tool]:
    overview = "; ".join(f"{name}({', '.join(t['name'] for t in d['types'])})" for name, d in DOMAINS.items())
    return [
        Tool(
            name=TOOL_NAME,
            description=(
                "서비스가 속한 업무 영역의 점검 규칙(점검 항목, 기획 때 확인할 질문 후보)을 가져온다. "
                "서비스가 여러 영역에 걸치면 모두 고르고, 어느 영역에도 맞지 않으면 빈 배열을 넣는다. "
                f"영역(유형): {overview}"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "domains": {
                        "type": "array",
                        "items": {"type": "string", "enum": DOMAIN_NAMES},
                        "description": "해당하는 업무 영역 이름 목록",
                    }
                },
                "required": ["domains"],
            },
            fn=_get_domain_rules,
        )
    ]
