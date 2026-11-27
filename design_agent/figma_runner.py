"""
실행 : python figma_runner.py 기획서명_UI.json    
python figma_runner.py EQUIPFLOW_TEST02_requirements_UI.json

Figma MCP (Codex)
- Figma 디자인 생성
- Figma 결과 검증

Codex CLI 확인 : codex --version
"""
import argparse
import json
import os
import re
import subprocess
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from schemas import ScreenPlan
from figma_prompt import build_figma_prompt
from codex_app_server import run_codex_with_approvals


BASE_DIR = Path(__file__).resolve().parent
REGISTRY_DIR = BASE_DIR / "figma_projects"

"""A02 Figma MCP 실행기.

최초 실행: Codex의 Figma MCP에서 새 Design 파일 생성 -> URL 저장 -> 화면 생성
이후 실행: 저장된 같은 파일 URL + node_id로 기존 화면 수정

실행 예시 (design_agent 폴더에서):
    python figma_runner.py EQUIPFLOW_TEST02_requirements_UI.json

이미 만들어 둔 파일을 최초 등록할 때:
    python figma_runner.py EQUIPFLOW_TEST02_requirements_UI.json --figma-url https://www.figma.com/design/FILE_KEY/FileName

주의: Codex에 연결된 MCP에 '새 파일 생성' 도구가 없으면 자동 생성은 실패합니다.
그 경우 임의 URL을 만들지 않고 오류를 반환합니다.
"""

import argparse
import json
import re
import subprocess
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from schemas import ScreenPlan
from figma_prompt import build_figma_prompt
from codex_app_server import run_codex_with_approvals

BASE_DIR = Path(__file__).resolve().parent
REGISTRY_DIR = BASE_DIR / "figma_projects"

CREATE_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["success", "failed"]},
        "error": {"type": "string"},
        "figma_file_url": {"type": "string"},
    },
    "required": ["status", "error", "figma_file_url"],
    "additionalProperties": False,
}

# Codex 구조화 출력용 JSON Schema
OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {
            "type": "string",
            "enum": ["success", "failed"]
        },
        "error": {"type": "string"},
        "screens": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "screen_id": {"type": "string"},
                    "figma_url": {"type": "string"},
                    "node_id": {"type": "string"}
                },
                "required": [
                    "screen_id",
                    "figma_url",
                    "node_id"
                ],
                "additionalProperties": False
            }
        }
    },
    "required": ["status", "error", "screens"],
    "additionalProperties": False
}


def file_key_from_url(url: str) -> str:
    """
    Figma URL에서 file key 추출
    """
    parsed = urlparse(url)
    parts = parsed.path.strip("/").split("/")
    if (
        parsed.scheme != "https"
        or parsed.hostname not in {"www.figma.com", "figma.com"}
        or len(parts) < 2
        or parts[0] != "design"
        or not re.fullmatch(r"[a-zA-Z0-9]+", parts[1])
    ):
        raise ValueError(f"잘못된 Figma URL 형식: {url}")
    return parts[1]


def canonical_file_url(url: str) -> str:
    """
    Figma 파일 URL 정규화
    """
    parsed = urlparse(url)
    parts = parsed.path.strip("/").split("/")
    file_key = file_key_from_url(url)
    return f"https://www.figma.com/design/{file_key}"


def resolve_input_path(value: str) -> Path:
    """
    화면 계획 JSON 파일 경로 확인
    """
    path = Path(value)
    if path.is_file():
        return path.resolve()
    candidate = REGISTRY_DIR / path
    if candidate.is_file():
        return candidate.resolve()
    raise FileNotFoundError(f"화면 계획 JSON 파일을 찾을 수 없습니다.: {value}")


def write_json(path: Path, data: dict) -> None:
    """
    임시 파일 작성 후 교체하여 중간 저장 실패를 줄임
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def load_registry(path: Path, project_id: str) -> dict | None:
    """
    Figma 프로젝트 기록 로드
    """
    if not path.exists():
        return None
    record = json.loads(path.read_text(encoding="utf-8"))
    if record.get("project_id") != project_id:
        raise ValueError(
            f"기존 Figma 기록의 project_id가 다릅니다: "
            f"{record.get('project_id')} != {project_id}"
        )
    record["figma_file_url"] = canonical_file_url(record["figma_file_url"])
    if record.get("figma_file_key") != file_key_from_url(record["figma_file_url"]):
        raise ValueError("Figma 기록의 file key와 URL이 일치하지 않습니다.")
    if not isinstance(record.get("screens", {}), dict):
        raise ValueError("Figma 기록의 screens 형식이 올바르지 않습니다.")
    return record


def create_figma_file(project_name: str) -> str:
    """
    Codex에 실제 MCP 파일 생성 도구 사용 요청
    """
    prompt = f"""
Figma MCP를 통해 새 빈 Figma Design 파일을 실제로 생성하세요.
파일명: {project_name}
파일 유형: Figma Design (FigJam, Figma Make 아님)

1. 연결된 Figma MCP 도구 목록을 확인하세요.
2. 새 Design 파일을 생성할 수 있는 도구가 있을 때만 그 도구를 호출하세요.
3. 계정/플랜 선택이 필요하고 확정할 수 없다면 실패로 보고하세요.
4. 파일 생성 후 실제 반환된 URL을 확인하세요.
5. 생성 도구가 없거나 인증/권한이 부족하면 status='failed'로 보고하세요.
6. 절대 임의 URL이나 파일 키를 만들어 성공이라고 보고하지 마세요.

구조화 출력 스키마를 따르세요.
성공: status='success', error='', figma_file_url='실제 URL'
실패: status='failed', error='원인', figma_file_url=''
""".strip()
    data = call_codex(prompt, CREATE_SCHEMA, BASE_DIR / "figma_create_result.json")
    if data.get("status") != "success":
        raise RuntimeError(f"Figma 파일 생성 실패: {data.get('error') or '원인 불명'}")
    url = data.get("figma_file_url", "")
    if not isinstance(url, str) or not url:
        raise ValueError("Figma 생성 결과에 실제 파일 URL이 없습니다.")
    return canonical_file_url(url)


def call_codex(prompt: str, schema: dict, result_path: Path) -> dict:
    """
    Codex App Server 구조화 출력 실행
    실패 결과도 디버깅용으로 기록
    """
    result_path.unlink(missing_ok=True)
    data = run_codex_with_approvals(
        prompt=prompt,
        output_schema=schema,
        cwd=BASE_DIR,
        timeout=900,
    )
    if not isinstance(data, dict):
        raise ValueError("Codex가 JSON 객체를 반환하지 않았습니다.")
    write_json(result_path, data)
    return data


def restore_refs(screen_plan: ScreenPlan, record: dict) -> None:
    """D01이 화면 계획을 재생성하여 design_ref가 null이 되어도 기존 연결 복원."""
    key = record["figma_file_key"]
    known = record.get("screens", {})
    for screen in screen_plan.screens:
        old_ref = known.get(screen.screen_id)
        if screen.design_ref is None and old_ref:
            screen.design_ref = old_ref
        if screen.design_ref is not None:
            ref_url = screen.design_ref.get("figma_url", "")
            if file_key_from_url(ref_url) != key:
                raise ValueError(
                    f"{screen.screen_id}의 design_ref가 다른 Figma 파일을 가리킵니다."
                )


def validate_screen_results(screen_plan: ScreenPlan, data: dict, file_key: str) -> dict:
    if data.get("status") != "success":
        raise RuntimeError(f"Figma 작업 실패: {data.get('error') or '원인 불명'}")

    result_screens = data.get("screens")
    if not isinstance(result_screens, list):
        raise ValueError("Codex 결과의 screens가 리스트가 아닙니다.")

    found = {}
    for item in result_screens:
        screen_id = item.get("screen_id")
        figma_url = item.get("figma_url", "")
        node_id = item.get("node_id", "")
        if not isinstance(screen_id, str) or screen_id in found:
            raise ValueError(f"중복 또는 잘못된 screen_id: {screen_id}")
        if not isinstance(node_id, str) or not re.fullmatch(r"\d+:\d+", node_id):
            raise ValueError(f"잘못된 node_id: {screen_id} / {node_id}")
        if not isinstance(figma_url, str) or file_key_from_url(figma_url) != file_key:
            raise ValueError(f"대상 Figma 파일 불일치: {screen_id}")
        query_node = parse_qs(urlparse(figma_url).query).get("node-id", [""])[0]
        if query_node.replace("-", ":") != node_id:
            raise ValueError(f"URL의 node-id와 node_id 불일치: {screen_id}")
        found[screen_id] = {"figma_url": figma_url, "node_id": node_id}

    expected = {s.screen_id for s in screen_plan.screens}
    if set(found) != expected:
        raise ValueError(
            f"화면 ID 불일치. 누락={sorted(expected - set(found))}, "
            f"추가={sorted(set(found) - expected)}"
        )
    return found


def run_codex(
        input_file: str,
        existing_url: str | None = None
) -> int:
    """
    Codex App Server를 실행하여 Figma 디자인을 생성 및 수정하고 결과를 저장
    이미 생성된 화면 계획 JSON을 기반으로 작업을 수행
    """
    # 화면 계획 JSON 파일 경로
    input_path = resolve_input_path(input_file)
    screen_plan = ScreenPlan.model_validate_json(input_path.read_text(encoding="utf-8"))

    # 같은 화면 계획 파일명 -> 같은 Figma 프로젝트 기록
    record_path = REGISTRY_DIR / f"{input_path.stem}.json"
    record = load_registry(record_path, screen_plan.project_id)

    if record:
        figma_url = record["figma_file_url"]
        if existing_url and file_key_from_url(existing_url) != record["figma_file_key"]:
            raise ValueError("--figma-url과 기존 기록의 Figma 파일이 다릅니다.")
    else:
        # 예전 실행 결과에 design_ref가 남아 있다면 새 파일을 만들지 않고 기존 파일을 복구
        old_refs = {
            s.screen_id: s.design_ref for s in screen_plan.screens if s.design_ref
        }
        old_keys = {file_key_from_url(ref["figma_url"]) for ref in old_refs.values()}
        if len(old_keys) > 1:
            raise ValueError("화면별 design_ref가 서로 다른 Figma 파일을 가리킵니다.")

        if existing_url:
            figma_url = canonical_file_url(existing_url)
            print(f"[기존 파일 최초 등록] {figma_url}")
        elif old_refs:
            figma_url = canonical_file_url(next(iter(old_refs.values()))["figma_url"])
            print(f"[기존 design_ref에서 파일 복구] {figma_url}")
        else:
            print("[최초 실행] Figma Design 파일 생성 요청")
            figma_url = create_figma_file(input_path.stem.removesuffix("_UI"))
            print(f"[파일 생성 완료] {figma_url}")

        record = {
            "project_id": screen_plan.project_id,
            "figma_file_url": figma_url,
            "figma_file_key": file_key_from_url(figma_url),
            "screens": old_refs,
        }
        # 프레임 생성에 실패해도 새 파일을 중복 생성하지 않도록 즉시 저장
        write_json(record_path, record)

    restore_refs(screen_plan, record)
    mode = "update" if record["screens"] or any(s.design_ref for s in screen_plan.screens) else "create"
    print(f"[Figma 작업] mode={mode}, screens={len(screen_plan.screens)}")

    prompt = build_figma_prompt(screen_plan, figma_url, mode=mode)
    data = call_codex(prompt, OUTPUT_SCHEMA, BASE_DIR / "figma_codex_result.json")
    found = validate_screen_results(screen_plan, data, record["figma_file_key"])

    # 모든 화면 검증이 끝난 후에만 화면별 참조 갱신
    for screen in screen_plan.screens:
        screen.design_ref = found[screen.screen_id]

    write_json(input_path, screen_plan.model_dump(mode="json"))
    # 화면 계획에서 일시적으로 제외된 화면의 참조도 기록에는 보존
    record["screens"].update(found)
    write_json(record_path, record)

    print(f"[완료] {len(found)}개 design_ref 저장: {input_path}")
    print(f"[Figma 파일] {figma_url}")
    print(f"[프로젝트 기록] {record_path}")
    return 0



def main() -> int:
    parser = argparse.ArgumentParser(description="Figma 파일 최초 생성 및 기존 화면 수정")
    parser.add_argument("input_file", help="화면 계획 JSON 파일 경로 (기획서명_UI.json)")
    parser.add_argument("--figma-url", help="기존 Figma 파일을 최초 등록할 때만 사용")
    args = parser.parse_args()

    try:
        return run_codex(args.input_file, args.figma_url)
    except (OSError, RuntimeError, ValueError, TimeoutError, subprocess.TimeoutExpired) as exc:
        print(f"[실패] {exc}")
        print("Figma에 일부 변경이 반영됐을 수 있습니다. 재실행 전 파일을 확인하세요.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

