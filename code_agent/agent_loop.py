"""도구(Tool)를 쓸 수 있는 GPT 호출 루프 (OpenAI function calling).

흐름: 모델이 도구 호출을 요청 → 코드가 도구를 실행해 결과를 돌려줌 → 모델이 최종 JSON 응답
      (도구를 부르지 않고 바로 최종 응답해도 됨)
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Callable

from pydantic import BaseModel

from extractor import parse_final, prepare, response_format


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema
    fn: Callable[[dict[str, Any]], str]  # 인자 dict → 모델에게 돌려줄 문자열

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": self.parameters},
        }


def _run_tool(tool: Tool | None, raw_args: str) -> tuple[dict[str, Any], str, bool]:
    """도구 하나를 실행한다. 실패해도 예외를 던지지 않고 오류 문장을 돌려줘서 모델이 고칠 기회를 갖게 한다."""
    if tool is None:
        return {}, "알 수 없는 도구입니다.", False
    try:
        args = json.loads(raw_args or "{}")
    except json.JSONDecodeError:
        return {}, "도구 인자가 올바른 JSON이 아닙니다.", False
    try:
        return args, tool.fn(args), True
    except Exception as e:  # noqa: BLE001 - 도구 오류는 모델에게 알려 준다
        return args, f"도구 실행 오류: {e}", False


def run_with_tools(
    system: str,
    user: str,
    model_cls: type[BaseModel],
    schema_name: str,
    tools: list[Tool],
    *,
    force_first: str | None = None,
    max_rounds: int = 3,
    client: Any = None,
    model: str | None = None,
    trace: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """도구를 쓰는 호출 루프. 최종 응답은 model_cls 구조로 검증해 돌려준다.

    force_first: 첫 호출에서 반드시 부를 도구 이름 (모델이 도구 사용을 건너뛰지 못하게 함)
    max_rounds : 도구 호출 라운드 상한. 넘으면 도구 없이 최종 응답을 요구한다.
    trace      : 넘기면 도구 호출 기록({tool, arguments, result, ok})을 여기에 채운다.
    """
    client, model = prepare(client, model)
    by_name = {t.name: t for t in tools}
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    rf = response_format(model_cls, schema_name)

    for round_no in range(max_rounds + 1):
        if round_no == 0 and force_first:
            tool_choice: Any = {"type": "function", "function": {"name": force_first}}
        elif round_no < max_rounds:
            tool_choice = "auto"
        else:
            tool_choice = "none"  # 상한: 이제는 최종 응답만

        response = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=[t.schema() for t in tools],
            tool_choice=tool_choice,
            response_format=rf,
            # 일부 추론 모델은 Chat Completions에서 함수 도구와 추론을 같이 못 쓴다.
            # 기본 "none", .env의 OPENAI_REASONING_EFFORT로 변경 (low/medium/high)
            reasoning_effort=os.environ.get("OPENAI_REASONING_EFFORT", "none"),
        )
        choice = response.choices[0]
        calls = getattr(choice.message, "tool_calls", None)
        if not calls:
            return parse_final(choice, model_cls)

        messages.append(
            {
                "role": "assistant",
                "content": choice.message.content,
                "tool_calls": [
                    {"id": c.id, "type": "function",
                     "function": {"name": c.function.name, "arguments": c.function.arguments}}
                    for c in calls
                ],
            }
        )
        for c in calls:
            args, result, ok = _run_tool(by_name.get(c.function.name), c.function.arguments)
            if trace is not None:
                trace.append({"tool": c.function.name, "arguments": args, "result": result, "ok": ok})
            messages.append({"role": "tool", "tool_call_id": c.id, "content": result})

    raise AssertionError("도달할 수 없음")  # tool_choice="none"이면 항상 최종 응답이 온다
