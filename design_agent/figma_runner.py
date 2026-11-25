"""
실행 : python figma_runner.py      
Figma MCP (Codex)
- Figma 디자인 생성
- Figma 결과 검증

Codex CLI 확인 : codex --version
"""
import json
import os
import re
import subprocess
from pathlib import Path
from dotenv import load_dotenv
from urllib.parse import urlparse, parse_qs
from schemas import ScreenPlan
from figma_prompt import build_figma_prompt
from codex_app_server import run_codex_with_approvals

load_dotenv()  

BASE_DIR = Path(__file__).resolve().parent

figma_url = os.getenv("FIGMA_URL")

if not figma_url:
    raise ValueError("FIGMA_URL이 설정되지 않았습니다.")

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

def update_design_refs(screen_plan: ScreenPlan, data: dict) -> bool:
    """
    Codex의 Figma 작업 결과를 검증하고 화면별 design_ref 저장
    검증된 Figma 결과를 screen_plan.json에 저장
    """
    figma_url = os.getenv("FIGMA_URL")

    if not figma_url:
        print("FIGMA_URL이 설정되지 않았습니다.")
        return False
    

    if data.get("status") != "success":
        print("[Figma 작업 미완료]", data.get("error"))
        return False

    result_screens = data.get("screens", [])
    found_map = {}

    # 현재 Figma 파일 키
    file_key = urlparse(figma_url).path.split("/")[2]

    for item in result_screens:
        screen_id = item["screen_id"]
        figma_url = item["figma_url"]
        node_id = item["node_id"]

        if screen_id in found_map:
            print(f"[실패] 화면 ID 중복: {screen_id}")
            return False

        if not re.fullmatch(r"\d+:\d+", node_id):
            print(f"[실패] 잘못된 노드 ID: {node_id}")
            return False

        parsed = urlparse(figma_url)
        parts = parsed.path.strip("/").split("/")

        if (
            parsed.scheme != "https"
            or parsed.hostname != "www.figma.com"
            or len(parts) < 2
            or parts[0] != "design"
            or parts[1] != file_key
        ):
            print(f"[실패] 대상 Figma 파일 불일치: {screen_id}")
            return False

        url_node = parse_qs(parsed.query).get("node-id", [""])[0]

        if url_node.replace("-", ":") != node_id:
            print(f"[실패] URL과 노드 ID 불일치: {screen_id}")
            return False

        found_map[screen_id] = {
            "figma_url": figma_url,
            "node_id": node_id
        }

    expected_ids = {s.screen_id for s in screen_plan.screens}

    if set(found_map) != expected_ids:
        print("[실패] Figma 화면 ID가 화면 계획과 일치하지 않습니다.")
        print("누락:", expected_ids - set(found_map))
        print("추가:", set(found_map) - expected_ids)
        return False

    # 전체 검증 후 저장
    for screen in screen_plan.screens:
        screen.design_ref = found_map[screen.screen_id]

    output_path = BASE_DIR / "screen_plan.json"

    output_path.write_text(
        screen_plan.model_dump_json(indent=2),
        encoding="utf-8"
    )

    print(f"[완료] design_ref {len(found_map)}개 저장")
    return True


def run_codex() -> int:
    """
    Codex App Server를 실행하여 Figma 디자인을 생성 및 수정하고 결과를 저장
    이미 생성된 screen_plan.json을 기반으로 작업을 수행
    """
    # 화면 계획 JSON 파일 경로
    input_path = BASE_DIR / "screen_plan.json"
    screen_plan = ScreenPlan.model_validate_json(
        input_path.read_text(encoding="utf-8")
    )

    # Figma 프롬프트 생성
    prompt = build_figma_prompt(screen_plan, figma_url)
    
    # Codex 출력 스키마 파일
    schema_path = BASE_DIR / "figma_output_schema.json"
    result_path = BASE_DIR / "figma_codex_result.json"

    schema_path.write_text(
        json.dumps(OUTPUT_SCHEMA, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    # 이전 실행 결과 재사용 방지
    result_path.unlink(missing_ok=True)

    try:
        result_data = run_codex_with_approvals(
            prompt=prompt,
            output_schema=OUTPUT_SCHEMA,
            cwd=BASE_DIR,
            timeout=900
        )

        result_path.write_text(
            json.dumps(result_data, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

    except (TimeoutError, subprocess.TimeoutExpired):
        print("[실패] Codex 실행 시간이 초과되었습니다.")
        print("Figma에 내용이 반영됐을 수 있으므로 재실행 전 확인하세요.")
        return 1

    except (OSError, RuntimeError, ValueError) as e:
        print(f"[실패] Codex App Server 실행 오류: {e}")
        return 1

    if not result_path.is_file():
        print("[실패] Codex 결과 JSON 파일이 없습니다.")
        # print(result.stdout)
        return 1

    try:
        data = json.loads(result_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeError) as e:
        print(f"[실패] Codex 결과 JSON 파싱 오류: {e}")
        return 1

    if not update_design_refs(screen_plan, data):
        return 1

    print("[완료] Figma 작업 결과 저장 성공")
    return 0


if __name__ == "__main__":
    raise SystemExit(run_codex())
