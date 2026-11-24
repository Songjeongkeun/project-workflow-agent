"""프롬프트(자연어) → 기획서 JSON 추출기 (OpenAI API)."""
from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path
from typing import Any

from prompts import SYSTEM_PROMPT, build_user_message
from pydantic import BaseModel

from schema import PlanningDoc, find_missing, strict_json_schema

SCHEMA_NAME = "planning_doc"


class ExtractionError(RuntimeError):
    pass


def load_env(path: str | Path | None = None) -> None:
    """`.env`의 KEY=VALUE를 환경변수로 읽는다 (이미 설정된 환경변수는 덮어쓰지 않음).

    기본 탐색 위치: 현재 작업 폴더, 이 파일이 있는 폴더.
    """
    candidates = [Path(path)] if path else [Path.cwd() / ".env", Path(__file__).resolve().parent / ".env"]
    for p in candidates:
        if not p.is_file():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip().removeprefix("export ").strip()
            value = value.strip().strip('"').strip("'")
            if key:
                os.environ.setdefault(key, value)
        return


def _make_client():
    load_env()
    if not os.environ.get("OPENAI_API_KEY"):
        raise ExtractionError(".env 또는 환경변수에 OPENAI_API_KEY가 없습니다.")
    try:
        from openai import OpenAI
    except ImportError as e:  # pragma: no cover
        raise ExtractionError("`pip install -r requirements.txt` 를 먼저 실행하세요.") from e
    return OpenAI()  # OPENAI_API_KEY 환경변수 사용


def prepare(client: Any, model: str | None) -> tuple[Any, str]:
    """클라이언트와 모델 이름을 확정한다 (없으면 .env에서 읽음)."""
    client = client or _make_client()
    model = model or os.environ.get("OPENAI_MODEL")
    if not model:
        raise ExtractionError(".env 또는 환경변수에 OPENAI_MODEL이 없습니다 (또는 --model 사용).")
    return client, model


def response_format(model_cls: type[BaseModel], schema_name: str) -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {"name": schema_name, "strict": True, "schema": strict_json_schema(model_cls)},
    }


def parse_final(choice: Any, model_cls: type[BaseModel]) -> dict[str, Any]:
    """최종 응답(choice)을 검사하고 model_cls 구조의 dict로 바꾼다."""
    if choice.finish_reason == "length":
        raise ExtractionError("응답이 길이 제한으로 잘렸습니다. 입력을 나누어 다시 시도하세요.")
    if getattr(choice.message, "refusal", None):
        raise ExtractionError(f"모델이 응답을 거부했습니다: {choice.message.refusal}")
    try:
        raw = json.loads(choice.message.content or "")
    except json.JSONDecodeError as e:
        raise ExtractionError("모델 응답이 올바른 JSON이 아닙니다.") from e
    # 구조 검증 + 누락 키는 기본값(null/[])으로 채움
    return model_cls.model_validate(raw).model_dump()


def run_structured(
    system: str,
    user: str,
    model_cls: type[BaseModel],
    schema_name: str,
    *,
    client: Any = None,
    model: str | None = None,
) -> dict[str, Any]:
    """시스템 프롬프트 + 사용자 메시지를 보내고, model_cls 구조의 JSON으로 받아 검증한 dict를 돌려준다."""
    client, model = prepare(client, model)
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        response_format=response_format(model_cls, schema_name),
    )
    return parse_final(response.choices[0], model_cls)


def extract_planning_doc(
    user_text: str,
    previous: dict[str, Any] | None = None,
    *,
    client: Any = None,
    model: str | None = None,
    today: str | None = None,
) -> dict[str, Any]:
    """사용자 설명을 템플릿 구조의 dict로 추출한다.

    previous를 넘기면 이전 기획서에 새 발언을 반영(추가 질문 답변 반영용)한다.
    """
    if not user_text.strip():
        raise ExtractionError("입력이 비어 있습니다.")
    prev_json = json.dumps(previous, ensure_ascii=False, indent=2) if previous else None
    user = build_user_message(user_text, today or date.today().isoformat(), prev_json)
    return run_structured(SYSTEM_PROMPT, user, PlanningDoc, SCHEMA_NAME, client=client, model=model)


def update_planning_doc(previous: dict[str, Any], instruction: str, **kwargs: Any) -> dict[str, Any]:
    """이미 추출한 기획서에 수정 요청을 반영한 새 dict를 돌려준다 (previous는 바뀌지 않음)."""
    if not previous:
        raise ExtractionError("수정할 기존 기획서가 없습니다.")
    return extract_planning_doc(instruction, previous, **kwargs)


def missing_fields(doc: dict[str, Any]) -> list[str]:
    """아직 비어 있는 항목 경로 목록 (에이전트가 후속 질문을 만들 때 사용)."""
    return find_missing(doc)
