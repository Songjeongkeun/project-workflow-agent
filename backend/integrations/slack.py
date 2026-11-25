"""Slack MCP로 메시지 한 건을 보낸다 (02 4.7, 04 7.5).

받는 곳은 Slack 사용자 ID(U...) 또는 채널 ID(C...)다.
사용자 ID로 보내면 봇과의 개인 메시지로 가므로 chat:write 권한 하나로 충분하다.
"""

import config
from integrations.mcp_client import McpError, call_tool

PACKAGE = "@modelcontextprotocol/server-slack"
SEND_TOOL = "slack_post_message"  # scripts/slack_check.py로 실제 도구 이름을 확인할 수 있다


def env() -> dict:
    """Slack MCP 서버에 넘길 값. 토큰은 이 서버 프로세스의 환경 변수로만 전달된다."""
    if not config.SLACK_BOT_TOKEN or not config.SLACK_TEAM_ID:
        raise McpError(".env에 SLACK_BOT_TOKEN과 SLACK_TEAM_ID를 넣어 주세요.")
    return {"SLACK_BOT_TOKEN": config.SLACK_BOT_TOKEN, "SLACK_TEAM_ID": config.SLACK_TEAM_ID}


def send(recipient: str, text: str) -> None:
    """메시지를 보낸다. Slack이 거절하면(ok=false) McpError."""
    out = call_tool(PACKAGE, SEND_TOOL, {"channel_id": recipient, "text": text}, env=env())
    # Slack API는 실패해도 정상 응답에 {"ok": false, "error": "..."}를 담아 돌려준다
    if isinstance(out, dict) and out.get("ok") is False:
        raise McpError(f"Slack 거절: {out.get('error', '이유 없음')}")
