"""Notion MCP로 페이지를 만들고 내용을 바꾼다 (02 4.6, 04 7.4).

Notion MCP 서버(@notionhq/notion-mcp-server)는 Notion API를 도구로 그대로 옮긴 것이다.
여기서 쓰는 도구는 세 개뿐이다.
- API-post-page            : 상위 페이지 아래에 새 페이지 만들기
- API-update-page-markdown : 페이지 내용을 마크다운으로 통째로 바꾸기
- API-retrieve-a-page      : 페이지 읽기 (연결 확인 스크립트가 쓴다)

어떤 페이지를 언제 게시할지는 services/notify.py가 정한다. 이 파일은 DB를 모른다.
"""

import re

import config
from integrations.mcp_client import McpError, call_tool

PACKAGE = "@notionhq/notion-mcp-server"


def env() -> dict:
    """Notion MCP 서버에 넘길 값. 토큰은 이 서버 프로세스의 환경 변수로만 전달된다."""
    if not config.NOTION_TOKEN:
        raise McpError(".env에 NOTION_TOKEN을 넣어 주세요.")
    return {"NOTION_TOKEN": config.NOTION_TOKEN}


def page_id_of(value: str) -> str:
    """페이지 주소나 ID에서 ID(32자리 16진수)를 꺼낸다.

    예) https://www.notion.so/Orbit-1a2b...9f?pvs=4 → 1a2b...9f
    """
    found = re.findall(r"[0-9a-f]{32}", value.replace("-", "").lower())
    if not found:
        raise McpError("NOTION_PARENT_PAGE에서 페이지 ID를 찾지 못했습니다. 페이지 주소를 그대로 넣어 주세요.")
    return found[-1]


def _call(tool: str, args: dict):
    out = call_tool(PACKAGE, tool, args, env=env())
    # Notion API는 실패하면 {"object": "error", "code": ..., "message": ...}를 돌려준다
    if isinstance(out, dict) and out.get("object") == "error":
        raise McpError(f"Notion 거절: {out.get('code')}: {str(out.get('message'))[:200]}")
    return out


def create_page(parent_id: str, title: str, intro: str | None = None, icon: str | None = None) -> dict:
    """parent_id 페이지 아래에 새 페이지를 만든다. {"id", "url"}을 돌려준다. icon은 이모지 하나."""
    args = {
        "parent": {"page_id": parent_id},
        "properties": {"title": [{"text": {"content": title}}]},
    }
    if icon:
        args["icon"] = {"emoji": icon}
    if intro:
        args["children"] = [{"type": "paragraph", "paragraph": {"rich_text": [{"type": "text", "text": {"content": intro}}]}}]
    out = _call("API-post-page", args)
    return {"id": out["id"], "url": out.get("url")}


def replace_markdown(page_id: str, markdown: str) -> None:
    """페이지 내용을 통째로 바꾼다 (이전 내용은 Notion 페이지 기록에 남는다)."""
    _call("API-update-page-markdown", {
        "page_id": page_id,
        "type": "replace_content",
        "replace_content": {"new_str": markdown},
    })


def get_page(page_id: str) -> dict:
    return _call("API-retrieve-a-page", {"page_id": page_id})


def is_missing(error: Exception) -> bool:
    """페이지가 지워졌거나 통합에 공유되지 않아 못 찾는 오류인지 (그러면 새로 만든다)."""
    text = str(error).lower()
    return "object_not_found" in text or "archived" in text or "in_trash" in text
