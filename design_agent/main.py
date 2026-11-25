# 실행 : python main.py
# python main.py data/planning.json

import json
import argparse
from pathlib import Path

from schemas import PlanningInput
# from planner import create_screen_plan
from llm_client import generate_screen_plan_with_ai
from validator import validate_screen_plan
from pydantic import ValidationError


BASE_DIR = Path(__file__).resolve().parent


def main() -> int:
    """
    A01 기획서 JSON을 입력 받아 A02 화면 계획을 생성하고 저장한다.
    planning.json -> 기획서명_UI.json 생성
    """
    try: 
        # ArgumentParser() : 명령줄 인자를 처리하는 객체 생성
        parser = argparse.ArgumentParser(
            description="A02 화면 계획 생성"
        )

        parser.add_argument(
            "input_file",
            help="입력할 기획서 JSON 파일 경로"
        )
        # parse_args : 사용자가 입력한 인자 읽기
        args = parser.parse_args()
        # 입력 JSON 파일 경로
        input_path = Path(args.input_file)

        if not input_path.is_absolute():
            # 상대 경로를 절대 경로로 변환
            input_path = BASE_DIR / input_path

        # 실제 파일 존재 여부 검사
        if not input_path.is_file():
            print("입력 파일이 존재하지 않습니다.")
            return 1 # 실패 종료 시 1

        # 입력 데이터 검증
        planning = PlanningInput.model_validate_json(
            input_path.read_text(encoding="utf-8")
        )

        # 화면 계획 생성
        # TODO: 실제 프로젝트 ID 전달 (임시 P001)
        screen_plan = generate_screen_plan_with_ai(planning, project_id="P001")

        # 생성된 화면 계획 확인용
        errors = validate_screen_plan(planning, screen_plan)

        if errors:
            print("[화면 계획 검증 실패]")
            for error in errors:
                print(f"- {error}")
            return 1

        print("[화면 계획 검증 성공]")

        # JSON 파일 저장
        output_filename = f"{input_path.stem}_UI.json"
        output_path = BASE_DIR / output_filename

        output_path.write_text(
            screen_plan.model_dump_json(indent=2),
            encoding="utf-8"
        )

        print(f"JSON 저장 완료: {output_path}")
        return 0 # 성공 종료 시 0
    except FileNotFoundError:
        print("입력 파일을 찾을 수 없습니다.")
        return 1
    except PermissionError:
        print("파일에 접근할 권한이 없습니다.")
        return 1
    except ValidationError as ve:
        print("입력 JSON 데이터가 유효하지 않습니다.")
        print(ve)
        return 1
    except ValueError as e:
        print(f"화면 계획 생성 실패: {e}")
        return 1
    except Exception as e:
        print(f"오류 발생: {e}")
        return 1

if __name__ == "__main__":
    # main()
    raise SystemExit(main())