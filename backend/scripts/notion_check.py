"""Notion 연결 확인. .env의 토큰으로 Notion MCP 서버에 접속해 게시용 상위 페이지를 읽어 본다.

backend 폴더에서 (가상환경을 켠 상태):
    python scripts/notion_check.py          상위 페이지를 읽기만 한다 (아무것도 만들지 않는다)
    python scripts/notion_check.py write    상위 페이지 아래에 "Orbit 연결 확인" 페이지를 하나 만든다

토큰 값은 화면에 출력하지 않는다.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # backend 폴더를 import 경로에

import config  # noqa: E402
from integrations import notion  # noqa: E402
from integrations.mcp_client import list_tools  # noqa: E402

NEEDED = ("API-post-page", "API-update-page-markdown", "API-retrieve-a-page")


def main() -> None:
    print("NOTION_TOKEN      :", "있음" if config.NOTION_TOKEN else "없음")
    print("NOTION_PARENT_PAGE:", "있음" if config.NOTION_PARENT_PAGE else "없음")
    parent = notion.page_id_of(config.NOTION_PARENT_PAGE)
    print("상위 페이지 ID    :", parent)

    print("\nNotion MCP 서버에 접속하는 중... (처음에는 패키지를 내려받느라 오래 걸릴 수 있습니다)")
    names = {t["name"] for t in list_tools(notion.PACKAGE, notion.env())}
    missing = [n for n in NEEDED if n not in names]
    print(f"도구 {len(names)}개, 쓰는 도구 {len(NEEDED) - len(missing)}/{len(NEEDED)}개 있음", f"(없음: {missing})" if missing else "")

    page = notion.get_page(parent)
    print("상위 페이지 읽기  : 성공", page.get("url", ""))

    if len(sys.argv) > 1 and sys.argv[1] == "write":
        made = notion.create_page(parent, "Orbit 연결 확인", "이 페이지가 보이면 Orbit이 Notion에 게시할 수 있습니다. 지워도 됩니다.")
        notion.replace_markdown(made["id"], "> 이 페이지가 보이면 Orbit이 Notion에 게시할 수 있습니다. 지워도 됩니다.\n\n## 확인\n\n- 페이지 만들기: 성공\n- 내용 바꾸기(마크다운): 성공\n")
        print("시험 페이지 만들기: 성공", made.get("url", ""))


if __name__ == "__main__":
    main()
