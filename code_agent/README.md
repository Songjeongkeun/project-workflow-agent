# Project Workflow

프롬프트/기획서 → 기획 JSON → 기술 명세 → 작업 계획 → 코드 생성.

## 폴더 구조
- 루트 `*.py` : 실행 코드 (아래 표 참고)
- `tests/` : API 없이 도는 테스트
- `input/` : 내가 쓰는 입력 파일
- `output/` : 실행 결과 (build.py `--out`, `--workspace`로 지정)
- `.env` : OPENAI_API_KEY, OPENAI_MODEL

| 파일 | 역할 |
|---|---|
| build.py | 한 번에 실행: 기획서 → 코드 |
| main.py | 프롬프트/문서 → 기획 JSON (`--update`로 수정) |
| spec_main.py | 기획 JSON → 기술 명세 |
| code_main.py | `plan`(작업 계획) / `write`(코드 작성) |
| a03 | 기획 JSON 하나로 명세→계획→코드 (`./a03 이름`) |
| compare.py | 기대 JSON과 결과 비교 |

## 실행
```
pip install -r requirements.txt
python build.py --doc input/planning.pdf --ui-doc input/screen_plan.json --out output/run --workspace output/project
```

입력 자료는 로컬에서 `input/`에 준비합니다. 입력·결과·백업과 실제 API 설정은 저장소에 포함하지 않습니다.
